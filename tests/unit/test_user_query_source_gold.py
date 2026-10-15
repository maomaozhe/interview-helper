"""Adversarial provenance/intent checks; no models, network, or service writes."""
from pathlib import Path
import shutil

import pytest

from eval.common import digest, read_json, read_jsonl, write_json, write_jsonl
from evals.agent_gold.user_query_source_20261015 import REPORT, SNAPSHOT, build, validate_dataset


@pytest.fixture(scope='module')
def reference(tmp_path_factory):
    required = {
        "source snapshot facts": SNAPSHOT / "facts.json",
        "historical user messages": REPORT / "historical-user-messages.json",
        "source-only defect evidence": Path("data/gold/source-reviewed-retrieval-20261014-v2/source-review-defects.json"),
    }
    missing = [kind for kind, path in required.items() if not path.is_file()]
    if missing:
        pytest.skip("private fixture inputs unavailable: " + ", ".join(missing))
    facts = read_json(required["source snapshot facts"])
    missing_sources = sum(not (SNAPSHOT / doc["source_path"]).is_file() for doc in facts["documents"])
    if missing_sources:
        pytest.skip(f"private fixture inputs unavailable: {missing_sources} archived source documents")
    path=tmp_path_factory.mktemp('user-query-gold-reference')/'gold'
    build(path)
    return path


@pytest.fixture
def dataset(reference,tmp_path):
    target=tmp_path/'gold';shutil.copytree(reference,target)
    return target


def reseal(path):
    freeze=read_json(path/'freeze.json')
    freeze['files']={p.relative_to(path).as_posix():digest(p.read_bytes())
                     for p in sorted(path.rglob('*')) if p.is_file() and p.name!='freeze.json'}
    write_json(path/'freeze.json',freeze)


def test_valid_and_immutable(reference):
    result=validate_dataset(reference)
    assert len(result['cases'])==24
    assert result['manifest']['turn_count']==34
    assert len(result['questions'])==2773
    primary=result['scopes']['engineering_system_design']
    assert len(primary['positive_gold_ids'])==21
    assert len(primary['boundary_gold_ids'])==22
    assert set(primary['positive_gold_ids']).isdisjoint(result['scopes']['agent_application_design']['positive_gold_ids'])
    assert primary['full_corpus_recall_certified'] is False
    with pytest.raises(ValueError,match='immutable'):build(reference)


def test_frozen_bytes_are_checked(dataset):
    with (dataset/'questions.jsonl').open('a',encoding='utf-8') as f:f.write('\n')
    with pytest.raises(ValueError,match='freeze hash mismatch'):validate_dataset(dataset)


def test_missing_occurrence_cannot_claim_census(dataset):
    rows=read_jsonl(dataset/'questions.jsonl');write_jsonl(dataset/'questions.jsonl',rows[1:]);reseal(dataset)
    with pytest.raises(ValueError,match='incomplete occurrence census'):validate_dataset(dataset)


def test_duplicate_occurrence_cannot_hide_behind_the_complete_dictionary(dataset):
    from copy import deepcopy
    rows=read_jsonl(dataset/'questions.jsonl')
    duplicate=deepcopy(rows[0]);duplicate['id']='new-gold-id-same-occurrence'
    rows.append(duplicate);write_jsonl(dataset/'questions.jsonl',rows);reseal(dataset)
    with pytest.raises(ValueError,match='incomplete occurrence census'):validate_dataset(dataset)


def test_stale_facts_position_is_not_accepted_after_resealing(dataset):
    rows=read_jsonl(dataset/'questions.jsonl');rows[0]['facts_index']=2
    write_jsonl(dataset/'questions.jsonl',rows);reseal(dataset)
    with pytest.raises(ValueError,match='facts index mismatch'):validate_dataset(dataset)


def test_missing_variant_fails_after_reseal(dataset):
    rows=read_jsonl(dataset/'questions.jsonl')
    q=next(q for q in rows if len(q.get('canonical_variant_occurrence_ids',[]))>1)
    q['canonical_variant_occurrence_ids'].pop()
    write_jsonl(dataset/'questions.jsonl',rows);reseal(dataset)
    with pytest.raises(ValueError,match='missing canonical variants'):validate_dataset(dataset)


def test_fake_source_span_fails_after_reseal(dataset):
    rows=read_jsonl(dataset/'questions.jsonl');rows[213]['source_spans'][0]['quote']='伪造面试题'
    write_jsonl(dataset/'questions.jsonl',rows);reseal(dataset)
    with pytest.raises(ValueError,match='fabricated source span'):validate_dataset(dataset)


def test_scope_grade_cannot_disagree_with_evidence(dataset):
    scopes=read_json(dataset/'scopes.json');s=scopes['engineering_system_design']
    gid=s['positive_gold_ids'].pop();s['negative_gold_ids'].append(gid)
    write_json(dataset/'scopes.json',scopes);reseal(dataset)
    with pytest.raises(ValueError,match='grade/evidence mismatch'):validate_dataset(dataset)


def test_fake_user_quote_fails_after_reseal(dataset):
    rows=read_jsonl(dataset/'cases.jsonl');rows[0]['turns'][0]['origin']['quote']='编造的用户原问'
    write_jsonl(dataset/'cases.jsonl',rows);reseal(dataset)
    with pytest.raises(ValueError,match='fabricated historical quote'):validate_dataset(dataset)


def test_canonical_only_credit_cannot_disable_source_support(dataset):
    rows=read_jsonl(dataset/'cases.jsonl');rows[0]['turns'][0]['expected']['require_source_support']=False
    write_jsonl(dataset/'cases.jsonl',rows);reseal(dataset)
    with pytest.raises(ValueError,match='witness/precision policy weakened'):validate_dataset(dataset)


def test_unknown_boundaries_cannot_certify_full_recall(dataset):
    scopes=read_json(dataset/'scopes.json');scopes['engineering_system_design']['full_corpus_recall_certified']=True
    write_json(dataset/'scopes.json',scopes);reseal(dataset)
    with pytest.raises(ValueError,match='unjustified full corpus recall'):validate_dataset(dataset)


def test_next_must_preserve_continuity(dataset):
    rows=read_jsonl(dataset/'cases.jsonl');case=next(c for c in rows if c['case_id']=='uq-engineering-next')
    case['turns'][1]['expected']['no_repeat_previous_results']=False
    write_jsonl(dataset/'cases.jsonl',rows);reseal(dataset)
    with pytest.raises(ValueError,match='NEXT continuity contract missing'):validate_dataset(dataset)


def test_all_and_count_cannot_silently_pass_uncertified(dataset):
    rows=read_jsonl(dataset/'cases.jsonl');case=next(c for c in rows if c['case_id']=='uq-engineering-count')
    case['turns'][1]['expected']['expected_certification']='CERTIFIED'
    write_jsonl(dataset/'cases.jsonl',rows);reseal(dataset)
    with pytest.raises(ValueError,match='uncertified census count passed'):validate_dataset(dataset)


def test_missing_byte_annotation_not_downgraded(dataset):
    rows=read_jsonl(dataset/'cases.jsonl');case=next(c for c in rows if c['case_id']=='uq-byte-frequency-original')
    case['turns'][0]['expected']['expected_certification']='PANEL_ONLY'
    write_jsonl(dataset/'cases.jsonl',rows);reseal(dataset)
    with pytest.raises(ValueError,match='missing annotation downgraded'):validate_dataset(dataset)


def test_filtered_positive_set_is_source_backed(dataset):
    result=validate_dataset(dataset);cases=result['cases']
    e=next(c for c in cases if c['case_id']=='uq-engineering-bytedance')['turns'][0]['expected']
    assert len(e['positive_gold_ids'])==7
    assert len(e['excluded_positive_gold_ids'])==13
    assert e['case_boundary_gold_ids']==['uq-occ-1514']
    e['positive_gold_ids']=result['scopes']['engineering_system_design']['positive_gold_ids']
    write_jsonl(dataset/'cases.jsonl',cases);reseal(dataset)
    with pytest.raises(ValueError,match='turn scope evidence mismatch'):validate_dataset(dataset)


def test_unrequested_sort_not_invented(reference):
    out=validate_dataset(reference)
    for case in out['cases']:
        for turn in case['turns']:
            e=turn['expected']
            if e['semantic_action'] in {'ANSWER','CLARIFY','COUNT'}:
                assert e['accepted_sort']==[]
    for cid in ['uq-byte-absence-original','uq-byte-all-original']:
        assert next(c for c in out['cases'] if c['case_id']==cid)['turns'][0]['expected']['accepted_sort']==[]
