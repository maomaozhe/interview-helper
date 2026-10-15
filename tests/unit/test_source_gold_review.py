"""Independent adversarial review of source gold provenance (synthetic fixtures).

The source questions here are intentionally test fixtures. No real frozen labels
are modified, and neither predictions nor remote services are invoked.
"""
from copy import deepcopy

import pytest

from eval.common import digest, read_json, write_json
from eval.source_gold import release_eligibility_issues, validate_source_contract
from eval.validate_gold import GoldValidationError


def original(root, name, text, occurrence_id, *, revision_id=None):
    raw = text.encode('utf-8')
    sha = digest(raw)
    path = 'sources/' + name + '.md'
    (root / 'sources').mkdir(exist_ok=True)
    (root / path).write_bytes(raw)
    span = {'start_char':0,'end_char':len(text),'start_line':1,'end_line':1,'quote':text}
    if revision_id:
        span['revision_id'] = revision_id
    return {'id':occurrence_id,'occurrence_id':occurrence_id,'text':text,
            'raw_question':text,'original_path':name+'.md','source_path':path,
            'source_hash':sha,'source_spans':[span],
            'review':{'reviewer':'review-test-agent','reviewer_kind':'agent',
                      'reviewed_at':'2026-10-14T20:00:00+08:00',
                      'guide_version':'annotation_v2','basis':'Synthetic fixture only'}}


def gold(root, *, source_facts=False, second_actual_variant=False):
    first = original(root,'first','解释 Redis RDB 和 AOF 的差别','occ-1',revision_id='rev-1')
    other = original(root,'second','反转链表','occ-2',revision_id='rev-2')
    reference = {'source_evidence':[deepcopy(first)],'canonical_variants_review':[deepcopy(first)]}
    row = {'id':'query','group_id':'first-group','split':'dev','query':'RDB/AOF差别',
           'relevance':{'gold-1':2},'relevance_reviews':{'gold-1':{'grade':2,'basis':'Synthetic fixture'}},
           'sample_kind':'positive'}
    manifest = {'source_evidence_contract':'raw_source_spans_v1',
                'retrieval_evidence_scope':'anchor_only','retrieval':[row],
                'canonical_mapping':{'items':[{'gold_question_id':'gold-1',
                    'canonical_question_id':'can-1','equivalence_group':'group-1','occurrence_ids':['occ-1']}]},
                'relevance_evidence':{'gold-1':reference},'dedup':[],
                'snapshot':{'corpus_revision':1,'indexed_revision':1,'task_annotation_revision':1,'as_of':'2026-10-14'}}
    if source_facts:
        facts = {'snapshot':manifest['snapshot'],'documents':[
            {'id':'doc-1','revision_id':'rev-1','source_hash':first['source_hash'],'source_path':first['source_path']},
            {'id':'doc-2','revision_id':'rev-2','source_hash':other['source_hash'],'source_path':other['source_path']}],
            'questions':[{'id':'occ-1','canonical_question_id':'can-1',
                'raw_question':first['raw_question'],'source_spans':deepcopy(first['source_spans'])}]}
        if second_actual_variant:
            facts['questions'].append({'id':'occ-2','canonical_question_id':'can-1',
                'raw_question':other['raw_question'],'source_spans':deepcopy(other['source_spans'])})
        write_json(root / 'facts.json',facts)
        manifest.update(source_facts_path='facts.json',facts_sha256=digest(facts),
                        facts_file_sha256=digest((root / 'facts.json').read_bytes()))
    return manifest,first,other


def check(root,manifest,section='retrieval'):
    validate_source_contract(root,manifest,[section],split='dev',verify_sources=True)


def test_anchor_release_override_is_always_ignored():
    assert release_eligibility_issues({'retrieval_evidence_scope':'anchor_only','release_eligible':True},'retrieval')


def test_legacy_dataset_without_source_opt_in_stays_replayable(tmp_path):
    check(tmp_path,{'dedup':[{'not':'a source-reviewed row'}]},'dedup')


def test_dedup_scored_text_cannot_disagree_with_original_span(tmp_path):
    manifest,first,other = gold(tmp_path)
    first['text'] = 'fake different problem supplied to scorer'
    manifest['dedup'] = [{'id':'pair','split':'dev','left':first,'right':other}]
    with pytest.raises(GoldValidationError):
        check(tmp_path,manifest,'dedup')


def test_retrieval_cannot_mount_another_document_on_a_reviewed_occurrence_id(tmp_path):
    manifest,_,other = gold(tmp_path)
    forged = deepcopy(other)
    forged['occurrence_id'] = 'occ-1'
    manifest['relevance_evidence']['gold-1']['source_evidence'] = [forged]
    with pytest.raises(GoldValidationError):
        check(tmp_path,manifest)


def test_retrieval_cannot_mount_a_nonoverlapping_neighbor_on_a_reviewed_occurrence(tmp_path):
    manifest,first,_ = gold(tmp_path)
    text = first['raw_question'] + '\n反转链表'
    raw = text.encode()
    (tmp_path / first['source_path']).write_bytes(raw)
    reference = manifest['relevance_evidence']['gold-1']
    for item in reference['source_evidence'] + reference['canonical_variants_review']:
        item['source_hash'] = digest(raw)
    anchor = reference['source_evidence'][0]
    start = text.index('反转链表')
    anchor.update(raw_question='反转链表',source_spans=[{'start_char':start,'end_char':len(text),
        'start_line':2,'end_line':2,'quote':'反转链表'}])
    with pytest.raises(GoldValidationError):
        check(tmp_path,manifest)


def test_independent_anchor_boundary_can_be_a_subset_of_machine_source_span(tmp_path):
    manifest,_,_ = gold(tmp_path)
    reference = manifest['relevance_evidence']['gold-1']
    anchor = reference['source_evidence'][0]
    text = anchor['raw_question']
    start = text.index('Redis')
    anchor.update(raw_question=text[start:],source_spans=[{'start_char':start,'end_char':len(text),
        'start_line':1,'end_line':1,'quote':text[start:]}])
    check(tmp_path,manifest)


def test_consistently_omitted_variant_is_detected_against_frozen_actual_facts(tmp_path):
    manifest,_,_ = gold(tmp_path,source_facts=True,second_actual_variant=True)
    # Both the declared occurrence IDs and review list omit occ-2, so comparing
    # these two self-declarations is insufficient to establish completeness.
    with pytest.raises(GoldValidationError):
        check(tmp_path,manifest)


def test_real_facts_file_digest_cannot_be_stale(tmp_path):
    manifest,_,_ = gold(tmp_path,source_facts=True)
    manifest['facts_sha256'] = '0'*64
    with pytest.raises(GoldValidationError):
        check(tmp_path,manifest)


def test_consistent_forged_variant_still_has_to_belong_to_actual_occurrence(tmp_path):
    manifest,_,other = gold(tmp_path,source_facts=True)
    forged = deepcopy(other)
    forged['occurrence_id'] = 'occ-1'
    manifest['relevance_evidence']['gold-1'].update(source_evidence=[deepcopy(forged)],
                                                 canonical_variants_review=[deepcopy(forged)])
    with pytest.raises(GoldValidationError):
        check(tmp_path,manifest)


def test_boolean_relevance_review_is_not_a_numeric_grade(tmp_path):
    manifest,_,_ = gold(tmp_path)
    manifest['retrieval'][0]['relevance_reviews']['gold-1']['grade'] = True
    with pytest.raises(GoldValidationError):
        check(tmp_path,manifest)


def test_occurrence_identity_spans_cannot_mask_review_of_a_different_neighbor(tmp_path):
    manifest,first,_ = gold(tmp_path,source_facts=True)
    text = first['raw_question'] + '\n反转链表'
    raw = text.encode()
    (tmp_path / first['source_path']).write_bytes(raw)
    fact_path = tmp_path / 'facts.json'
    facts = read_json(fact_path)
    facts['documents'][0]['source_hash'] = digest(raw)
    write_json(fact_path,facts)
    manifest['facts_sha256'] = digest(facts)
    manifest['facts_file_sha256'] = digest(fact_path.read_bytes())
    reference = manifest['relevance_evidence']['gold-1']
    start = text.index('反转链表')
    neighbor_spans = [{'start_char':start,'end_char':len(text),'start_line':2,'end_line':2,'quote':'反转链表'}]
    original_spans = deepcopy(first['source_spans'])
    for item in reference['source_evidence'] + reference['canonical_variants_review']:
        item.update(source_hash=digest(raw),raw_question='反转链表',source_spans=deepcopy(neighbor_spans))
    reference['canonical_variants_review'][0]['occurrence_source_spans'] = original_spans
    with pytest.raises(GoldValidationError):
        check(tmp_path,manifest)
