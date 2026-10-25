"""Complete SQL worksets preserve every member across fixed verification batches."""
from copy import deepcopy
import hashlib
from types import SimpleNamespace

import pytest
from sqlalchemy import update

from interview_intelligence.contracts import FilterSpec
from interview_intelligence.domain.models import (
    CanonicalQuestion, CorpusState, QuestionOccurrence, SourceRevision, QuestionRelation, OccurrenceTaskAnnotation,
)
from interview_intelligence.analytics.semantic_scope import semantic_input_hash
from interview_intelligence.relevance_scope import compile_relevance_scope
from interview_intelligence.search.catalog_selector import CatalogSoftSelector
from interview_intelligence.search.business_scope import BUSINESS_SCOPE_CONTRACT, CLARIFIED_ADJUDICATION_CONTRACT
from interview_intelligence.search.elasticsearch import ElasticsearchRetriever, SCHEMA_VERSION
from interview_intelligence.search.service import (
    prepare_engineering_workset, process_engineering_workset_batch, validate_engineering_workset,
)
from interview_intelligence.search.workset import workset_digest
from test_engineering_scope_listing import engineering_corpus


class Response:
    def __init__(self, value): self.value = value
    def json(self): return self.value
    def raise_for_status(self): pass


class WorksetES:
    def __init__(self, rows): self.rows, self.calls, self.drop, self.mutate = rows, [], set(), None
    def post(self, path, **kwargs):
        self.calls.append((path, kwargs))
        if path.endswith('/_pit'): return Response({'id': 'workset-pit'})
        body = kwargs['json']
        query = body['query']
        identities = query.get('ids', {}).get('values')
        if identities is None:
            identities = query.get('bool', {}).get('filter', [{}])[0].get('terms', {}).get('_id', sorted(self.rows))
        hits = [{'_id': identity, 'sort': [index], '_source': {'canonical_text': self.rows[identity],
            'corpus_revision': 7, 'schema_version': SCHEMA_VERSION, 'embedding_version': 'workset-vector'}}
            for index, identity in enumerate(identities) if identity not in self.drop]
        value = {'hits': {'total': {'value': len(hits), 'relation': 'eq'}, 'hits': hits}, '_shards': {'failed': 0}}
        if self.mutate: self.mutate(value)
        return Response(value)
    def delete(self, path, **kwargs):
        self.calls.append((path, kwargs)); return Response({'succeeded': True})


class Ranked(list): pass


class ProofReranker:
    version = 'fixed-workset-source-proof'
    def __init__(self): self.calls, self.fail, self.omit, self.partial_negative = [], None, set(), False
    def rerank(self, query, rows, *, relevance_scope):
        self.calls.append(deepcopy(rows))
        if self.fail: raise self.fail
        output, proofs = Ranked(), []
        for row in rows:
            identity = row['canonical_question_id']
            status = 'EXCLUDED' if identity == 'cache' else 'PENDING' if identity in {'agent', 'other'} else 'CONFIRMED'
            source_ids = [source['occurrence_id'] for source in row['source_context']]
            proofs.append({'canonical_question_id': identity, 'relevance_scope_status': status,
                'relevance_scope_hash': relevance_scope['scope_hash'],
                'decision': 'SCOPE_REJECTED' if status == 'EXCLUDED' else 'ACCEPTED' if status == 'CONFIRMED' else 'INVALID_BUSINESS_EVIDENCE',
                **({'source_verified_negative_occurrence_ids': source_ids[:1] if self.partial_negative else source_ids,
                    'scope_adjudication_contract': CLARIFIED_ADJUDICATION_CONTRACT,
                    'scope_adjudication_status': 'VERIFIED', 'scope_verification_status': 'SCOPE_REJECTED'}
                    if status == 'EXCLUDED' else {}),
                'business_assessment': {'outcome': 'NEGATIVE' if status == 'EXCLUDED' else 'POSITIVE' if status == 'CONFIRMED' else 'INSUFFICIENT',
                    'grounded': status != 'PENDING', 'in_scope': status == 'CONFIRMED',
                    'invalid_evidence_fields': [], 'contract': BUSINESS_SCOPE_CONTRACT}})
            if status != 'EXCLUDED' and identity not in self.omit:
                output.append({**row, 'relevance_scope_status': status, 'relevance_scope_hash': relevance_scope['scope_hash'],
                    'relevance_grade': 3 if status == 'CONFIRMED' else 0})
        output.audit = {'candidates': proofs}
        return output


class LastForty:
    def prioritize_complete(self, query, rows, scope):
        ordered = sorted(row['canonical_question_id'] for row in rows)
        return {'candidate_ids': ordered[-40:] + ordered[:-40], 'audit': {'status': 'SOFT_PRIORITY_ONLY'}}


def authorization(scope, filters, message='列出所有工程系统设计题'):
    return {'version': 'engineering_collection_authorization_v1', 'semantic_task': 'ENGINEERING_DESIGN',
        'source': 'original_user_collection_request', 'source_messages': [message],
        'source_evidence': [{'message_index': 0, 'quote': message, 'start_char': 0, 'end_char': len(message),
            'input_sha256': hashlib.sha256(message.encode()).hexdigest()}],
        'scope_hash': scope['scope_hash'], 'filters': filters.model_dump(mode='json')}


def setup_workset(tmp_path, monkeypatch, *, extra=45, filters=None):
    db = engineering_corpus(extra=extra)
    with db.session() as session, session.begin():
        state = session.get(CorpusState, 1)
        state.current_revision, state.indexed_revision = 7, 7
        state.canonical_revision, state.task_annotation_revision = 2, 3
        rows = {row.id: row.canonical_text for row in session.query(CanonicalQuestion)}
        for revision in session.query(SourceRevision):
            # Fixture source text equals the single occurrence in its revision.
            raw = next(row.raw_question for row in session.query(QuestionOccurrence)
                if row.source_spans[0]['revision_id'] == revision.id)
            (tmp_path / (revision.raw_file_hash + '.md')).write_text(raw, encoding='utf8')
    import interview_intelligence.config as config
    monkeypatch.setattr(config, 'load_settings', lambda: SimpleNamespace(snapshot_root=tmp_path))
    encoder = SimpleNamespace(version='workset-vector', embed_query=lambda query: (_ for _ in ()).throw(AssertionError('no embedding')))
    client, ranker = WorksetES(rows), ProofReranker()
    retriever = ElasticsearchRetriever('http://unused', encoder, reranker=ranker, client=client)
    scope = compile_relevance_scope(['列出所有工程系统设计题', '具体业务系统设计'], candidate_mode=True)
    filters = filters or FilterSpec()
    return db, retriever, client, ranker, scope, filters


def prepare(db, retriever, scope, filters):
    with db.session() as session:
        return prepare_engineering_workset(session, retriever, '具体业务系统设计', filters,
            relevance_scope=scope, engineering_authorization=authorization(scope, filters))


def test_complete_union_past_fifty_survives_soft40_and_every_batch_has_outcomes(tmp_path, monkeypatch):
    db, retriever, _, ranker, scope, filters = setup_workset(tmp_path, monkeypatch, extra=67)
    retriever.catalog_selector = LastForty()
    result = prepare(db, retriever, scope, filters)
    manifest = result['workset']
    assert len(manifest['candidate_ids']) == 71 and len(set(manifest['candidate_ids'])) == 71
    assert manifest['qualification']['count'] == 71 and manifest['batch_size'] == 20
    assert 'other' in manifest['candidate_ids']  # engineering source review missing -> pending remains eligible
    assert manifest['candidate_ids'][:40] == sorted(manifest['candidate_ids'])[-40:]
    seen = []
    for start in range(0, 71, 20):
        batch = manifest['candidate_ids'][start:start + 20]
        with db.session() as session:
            processed = process_engineering_workset_batch(session, retriever, '具体业务系统设计', filters,
                workset=manifest, batch_ids=batch, relevance_scope=scope)
        seen.extend(row['canonical_question_id'] for row in processed['outcomes'])
        assert len(processed['outcomes']) == len(batch)
        assert all(row['status'] in {'CONFIRMED', 'PENDING', 'EXCLUDED'} for row in processed['outcomes'])
    assert seen == manifest['candidate_ids']
    assert [len(rows) for rows in ranker.calls] == [20, 20, 20, 11]
    cache = next(row for rows in ranker.calls for row in rows if row['canonical_question_id'] == 'cache')
    assert {row['occurrence_id'] for row in cache['source_context']} == {'o-001', 'o-002'}


def test_source_fault_missing_projection_and_omitted_model_row_are_pending_not_lost(tmp_path, monkeypatch):
    db, retriever, client, ranker, scope, filters = setup_workset(tmp_path, monkeypatch, extra=0)
    manifest = prepare(db, retriever, scope, filters)['workset']
    client.drop = {'agent'}
    ranker.omit = {'orders'}
    with db.session() as session:
        result = process_engineering_workset_batch(session, retriever, '具体业务系统设计', filters,
            workset=manifest, batch_ids=manifest['candidate_ids'], relevance_scope=scope)
    statuses = {row['canonical_question_id']: row['status'] for row in result['outcomes']}
    assert statuses == {'agent': 'PENDING', 'cache': 'EXCLUDED', 'orders': 'PENDING', 'other': 'PENDING'}
    assert {row['canonical_question_id'] for row in result['data']} == {'agent', 'orders', 'other'}
    assert all(row['relevance_scope_hash'] == scope['scope_hash'] for row in result['data'])


@pytest.mark.parametrize('failure', [ValueError('QUERY_TOKEN_BUDGET_EXCEEDED'), RuntimeError('private provider payload')])
def test_whole_verifier_failure_retains_every_member_pending(tmp_path, monkeypatch, failure):
    db, retriever, _, ranker, scope, filters = setup_workset(tmp_path, monkeypatch, extra=0)
    manifest = prepare(db, retriever, scope, filters)['workset']
    ranker.fail = failure
    with db.session() as session:
        result = process_engineering_workset_batch(session, retriever, '具体业务系统设计', filters,
            workset=manifest, batch_ids=manifest['candidate_ids'], relevance_scope=scope)
    assert len(result['data']) == len(result['outcomes']) == 4
    assert {row['status'] for row in result['outcomes']} == {'PENDING'}
    assert 'private provider payload' not in str(result)


def test_cached_validation_has_no_backend_or_model_calls_and_detects_source_edit_without_revision(tmp_path, monkeypatch):
    db, retriever, client, ranker, scope, filters = setup_workset(tmp_path, monkeypatch, extra=0)
    manifest = prepare(db, retriever, scope, filters)['workset']
    count = len(client.calls)
    with db.session() as session:
        validate_engineering_workset(session, retriever, filters, workset=manifest, relevance_scope=scope)
        session.execute(update(QuestionOccurrence).where(QuestionOccurrence.id == 'o-003')
            .values(raw_question='changed raw question').execution_options(synchronize_session=False))
        with pytest.raises(ValueError, match='SNAPSHOT_CHANGED'):
            validate_engineering_workset(session, retriever, filters, workset=manifest, relevance_scope=scope)
    assert len(client.calls) == count and not ranker.calls


def add_filtered_reference_context(db, tmp_path, *, with_relation=False):
    """A valid preceding source ask that is outside the current type filter."""
    with db.session() as session, session.begin():
        current = session.get(QuestionOccurrence, 'o-003')
        revision = session.get(SourceRevision, 'r-3')
        previous = '请介绍会员权益的领取流程。'
        source = previous + '\n' + current.raw_question
        digest = hashlib.sha256(source.encode()).hexdigest()
        revision.raw_file_hash = revision.decoded_text_hash = digest
        revision.snapshot_path = digest + '.md'
        (tmp_path / (digest + '.md')).write_text(source, encoding='utf8')
        current.question_order = 2
        current.source_spans = [{'revision_id': revision.id, 'quote': current.raw_question,
            'start_char': len(previous) + 1, 'end_char': len(source)}]
        annotation = session.get(OccurrenceTaskAnnotation, current.id)
        evidence = deepcopy(annotation.evidence)
        for proof in evidence['semantic_scopes'].values():
            proof['input_hash'] = semantic_input_hash(current, revision)
        annotation.evidence = evidence
        session.add(QuestionOccurrence(id='outside-filter-context', interview_id=current.interview_id,
            canonical_question_id='project', raw_question=previous, normalized_question=previous,
            question_order=1, source_spans=[{'revision_id': revision.id, 'quote': previous,
                'start_char': 0, 'end_char': len(previous)}], topic_id=current.topic_id,
            taxonomy_version=current.taxonomy_version, question_type='SCENARIO', confidence=.9))
        if with_relation:
            session.add(QuestionRelation(id='context-edge', relation_type='OBSERVED_FOLLOWUP',
                source_occurrence_id='outside-filter-context', target_occurrence_id=current.id,
                source_interview_id=current.interview_id, evidence_spans=deepcopy(current.source_spans),
                provenance='SOURCE', confidence=.9, producer_version='fixture'))


@pytest.mark.parametrize('change', ['raw_question', 'question_order', 'source_spans',
    'add_relation', 'edit_relation', 'remove_relation'])
def test_reference_source_and_graph_edits_invalidate_cached_workset_without_revision(tmp_path, monkeypatch, change):
    filters = FilterSpec(question_type='KNOWLEDGE')
    db, retriever, client, ranker, scope, filters = setup_workset(tmp_path, monkeypatch, extra=0, filters=filters)
    add_filtered_reference_context(db, tmp_path, with_relation=change in {'edit_relation', 'remove_relation'})
    manifest = prepare(db, retriever, scope, filters)['workset']
    assert set(manifest['candidate_ids']) == {'agent', 'cache', 'orders', 'other'}
    assert 'project' not in manifest['candidate_ids']
    count = len(client.calls)
    with db.session() as session:
        validate_engineering_workset(session, retriever, filters, workset=manifest, relevance_scope=scope)
        if change in {'raw_question', 'question_order', 'source_spans'}:
            values = {'raw_question': '请介绍权益续期流程。'} if change == 'raw_question' else (
                {'question_order': 3} if change == 'question_order' else {'source_spans': []})
            session.execute(update(QuestionOccurrence).where(QuestionOccurrence.id == 'outside-filter-context')
                .values(**values).execution_options(synchronize_session=False))
        elif change == 'add_relation':
            current = session.get(QuestionOccurrence, 'o-003')
            session.add(QuestionRelation(id='context-edge', relation_type='OBSERVED_FOLLOWUP',
                source_occurrence_id='outside-filter-context', target_occurrence_id=current.id,
                source_interview_id=current.interview_id, evidence_spans=deepcopy(current.source_spans),
                provenance='SOURCE', confidence=.9, producer_version='fixture'))
            session.flush()
        elif change == 'edit_relation':
            session.execute(update(QuestionRelation).where(QuestionRelation.id == 'context-edge')
                .values(evidence_spans=[]).execution_options(synchronize_session=False))
        else:
            session.delete(session.get(QuestionRelation, 'context-edge'))
            session.flush()
        with pytest.raises(ValueError, match='SNAPSHOT_CHANGED'):
            validate_engineering_workset(session, retriever, filters, workset=manifest, relevance_scope=scope)
    assert len(client.calls) == count and not ranker.calls


def test_reference_cache_binding_does_not_expand_to_unrelated_filtered_interviews(tmp_path, monkeypatch):
    filters = FilterSpec(question_type='KNOWLEDGE')
    db, retriever, client, ranker, scope, filters = setup_workset(tmp_path, monkeypatch, extra=0, filters=filters)
    with db.session() as session, session.begin():
        session.execute(update(QuestionOccurrence).where(QuestionOccurrence.id == 'o-005')
            .values(question_type='SCENARIO').execution_options(synchronize_session=False))
    manifest = prepare(db, retriever, scope, filters)['workset']
    count = len(client.calls)
    with db.session() as session:
        session.execute(update(QuestionOccurrence).where(QuestionOccurrence.id == 'o-005')
            .values(raw_question='changed unrelated source').execution_options(synchronize_session=False))
        validate_engineering_workset(session, retriever, filters, workset=manifest, relevance_scope=scope)
    assert len(client.calls) == count and not ranker.calls


def test_effective_scoped_prompt_identity_invalidates_cache_instead_of_legacy_prompt(tmp_path, monkeypatch):
    db, retriever, client, ranker, scope, filters = setup_workset(tmp_path, monkeypatch, extra=0)
    identity = {'reranker_prompt_hash': '1' * 64, 'scope_verifier_prompt_hash': '2' * 64}
    ranker.clarified_prompt_identity = lambda: deepcopy(identity)
    manifest = prepare(db, retriever, scope, filters)['workset']
    assert all(manifest['proof_policy'][key] == value for key, value in identity.items())
    count = len(client.calls)
    with db.session() as session:
        ranker.system_prompt = 'Legacy text is not the prepared scoped prompt.'
        validate_engineering_workset(session, retriever, filters, workset=manifest, relevance_scope=scope)
        identity['scope_verifier_prompt_hash'] = '3' * 64
        with pytest.raises(ValueError, match='SNAPSHOT_CHANGED'):
            validate_engineering_workset(session, retriever, filters, workset=manifest, relevance_scope=scope)
    assert len(client.calls) == count and not ranker.calls


@pytest.mark.parametrize('identity', [None, {}, {'reranker_prompt_hash': '1' * 64},
    {'reranker_prompt_hash': '1' * 64, 'scope_verifier_prompt_hash': 'not-a-digest'}])
def test_malformed_effective_prompt_identity_fails_before_preparation_calls(tmp_path, monkeypatch, identity):
    db, retriever, client, ranker, scope, filters = setup_workset(tmp_path, monkeypatch, extra=0)
    ranker.clarified_prompt_identity = lambda: identity
    with db.session() as session, pytest.raises(ValueError, match='WORKSET_PROOF_POLICY_INVALID'):
        prepare_engineering_workset(session, retriever, '具体业务系统设计', filters, relevance_scope=scope,
            engineering_authorization=authorization(scope, filters))
    assert not client.calls and not ranker.calls


@pytest.mark.parametrize('message', ['所有具体业务系统设计题', '不要所有工程系统设计题', '系统设计题'])
def test_only_original_explicit_engineering_all_authorizes_the_hard_universe(tmp_path, monkeypatch, message):
    db, retriever, client, _, scope, filters = setup_workset(tmp_path, monkeypatch, extra=0)
    with db.session() as session, pytest.raises(ValueError, match='ENGINEERING_WORKSET_NOT_AUTHORIZED'):
        prepare_engineering_workset(session, retriever, '具体业务系统设计', filters, relevance_scope=scope,
            engineering_authorization=authorization(scope, filters, message))
    assert not client.calls


def test_unselected_semantic_mode_cannot_prepare_complete_workset(tmp_path, monkeypatch):
    db, retriever, client, _, _, filters = setup_workset(tmp_path, monkeypatch, extra=0)
    scope = compile_relevance_scope(['列出所有工程系统设计题', '具体业务系统设计'], candidate_mode=False)
    with db.session() as session, pytest.raises(ValueError, match='ENGINEERING_WORKSET_NOT_AUTHORIZED'):
        prepare_engineering_workset(session, retriever, '具体业务系统设计', filters, relevance_scope=scope,
            engineering_authorization=authorization(scope, filters))
    assert not client.calls


def test_manifest_tampering_and_filter_change_cannot_expand_membership(tmp_path, monkeypatch):
    db, retriever, _, _, scope, filters = setup_workset(tmp_path, monkeypatch, extra=0)
    manifest = prepare(db, retriever, scope, filters)['workset']
    changed = deepcopy(manifest)
    changed['candidate_ids'].append('project')
    changed['manifest_hash'] = workset_digest(changed)
    with db.session() as session:
        with pytest.raises(ValueError): validate_engineering_workset(session, retriever, filters, workset=changed, relevance_scope=scope)
        with pytest.raises(ValueError): validate_engineering_workset(session, retriever, FilterSpec(company='字节'), workset=manifest, relevance_scope=scope)


def test_real_selector_priority_wrapper_retains_all_unselected_without_new_proofs():
    selector = CatalogSoftSelector(None)
    selector.select = lambda *args: {'applied': True, 'selected_ids': ['q-4', 'q-2']}
    rows = [{'canonical_question_id': f'q-{i}', 'canonical_text': '题目'} for i in range(6)]
    result = selector.prioritize_complete('query', rows, {})
    assert result['candidate_ids'] == ['q-4', 'q-2', 'q-0', 'q-1', 'q-3', 'q-5']
    assert result['audit']['complete_membership_preserved'] is True


@pytest.mark.parametrize('case', ['source_file_missing', 'stale_projection', 'unavailable_projection'])
def test_source_or_projection_fault_keeps_a_pending_placeholder_with_separate_engineering_status(tmp_path, monkeypatch, case):
    db, retriever, client, ranker, scope, filters = setup_workset(tmp_path, monkeypatch, extra=0)
    manifest = prepare(db, retriever, scope, filters)['workset']
    if case == 'source_file_missing':
        with db.session() as session:
            revision = session.get(SourceRevision, 'r-3')
            (tmp_path / (revision.raw_file_hash + '.md')).unlink()
    elif case == 'stale_projection':
        client.mutate = lambda value: value['hits']['hits'][0]['_source'].update(corpus_revision=6)
    else:
        client.mutate = lambda value: value.update(timed_out=True)
    with db.session() as session:
        result = process_engineering_workset_batch(session, retriever, '具体业务系统设计', filters,
            workset=manifest, batch_ids=['orders', 'other'], relevance_scope=scope)
    assert result['outcomes'][0]['status'] == 'PENDING'
    assert [row['canonical_question_id'] for row in result['data']] == ['orders', 'other']
    other = result['data'][1]
    assert other['semantic_verification'] == 'PENDING' and other['confirmed_occurrence_count'] == 0
    assert other['pending_occurrence_count'] == 1 and other['pending_sources'][0]['reason']
    assert other['relevance_scope_status'] == 'PENDING'
    assert other['frequency_basis'] == 'engineering_qualified_occurrences'


@pytest.mark.parametrize('case', ['hash', 'offset', 'quote', 'ordinal'])
def test_raw_authorization_bindings_cannot_be_replaced_by_hash_only_assertions(tmp_path, monkeypatch, case):
    db, retriever, client, _, scope, filters = setup_workset(tmp_path, monkeypatch, extra=0)
    auth = authorization(scope, filters)
    if case == 'hash': auth['source_evidence'][0]['input_sha256'] = '0' * 64
    elif case == 'offset': auth['source_evidence'][0]['end_char'] -= 1
    elif case == 'quote': auth['source_evidence'][0]['quote'] = '业务题'
    else: auth['source_evidence'][0]['message_index'] = True
    with db.session() as session, pytest.raises(ValueError, match='ENGINEERING_WORKSET_NOT_AUTHORIZED'):
        prepare_engineering_workset(session, retriever, '具体业务系统设计', filters,
            relevance_scope=scope, engineering_authorization=auth)
    assert not client.calls


def test_twenty_is_processing_budget_not_display_page_size_and_query_scope_cannot_change(tmp_path, monkeypatch):
    db, retriever, client, ranker, scope, filters = setup_workset(tmp_path, monkeypatch, extra=30)
    manifest = prepare(db, retriever, scope, filters)['workset']
    count = len(client.calls)
    with db.session() as session:
        with pytest.raises(ValueError, match='INVALID_ENGINEERING_WORKSET_BATCH'):
            process_engineering_workset_batch(session, retriever, '具体业务系统设计', filters,
                workset=manifest, batch_ids=manifest['candidate_ids'][:21], relevance_scope=scope)
        with pytest.raises(ValueError, match='WORKSET_QUERY_CHANGED'):
            process_engineering_workset_batch(session, retriever, '换一个业务目标', filters,
                workset=manifest, batch_ids=manifest['candidate_ids'][:20], relevance_scope=scope)
    assert len(client.calls) == count and not ranker.calls


@pytest.mark.parametrize('failure', ['QUERY_CANCELLED', 'QUERY_DEADLINE_EXCEEDED'])
def test_cancel_and_deadline_do_not_commit_a_fake_processed_pending_batch(tmp_path, monkeypatch, failure):
    db, retriever, _, ranker, scope, filters = setup_workset(tmp_path, monkeypatch, extra=0)
    manifest = prepare(db, retriever, scope, filters)['workset']
    ranker.fail = ValueError(failure)
    with db.session() as session, pytest.raises(ValueError, match=failure):
        process_engineering_workset_batch(session, retriever, '具体业务系统设计', filters,
            workset=manifest, batch_ids=manifest['candidate_ids'], relevance_scope=scope)


def test_combined_occurrence_filters_do_not_acquire_the_other_occurrences_design_membership(tmp_path, monkeypatch):
    filters = FilterSpec(company='字节', round='FIRST')
    db, retriever, _, _, scope, filters = setup_workset(tmp_path, monkeypatch, extra=0, filters=filters)
    manifest = prepare(db, retriever, scope, filters)['workset']
    assert set(manifest['candidate_ids']) == {'orders', 'agent'}
    assert manifest['qualification']['coverage']['rejected'] == 2


def test_all_sources_displayed_does_not_make_one_occurrence_negative_a_canonical_negative(tmp_path, monkeypatch):
    db, retriever, _, ranker, scope, filters = setup_workset(tmp_path, monkeypatch, extra=0)
    manifest = prepare(db, retriever, scope, filters)['workset']
    ranker.partial_negative = True
    with db.session() as session:
        result = process_engineering_workset_batch(session, retriever, '具体业务系统设计', filters,
            workset=manifest, batch_ids=['cache'], relevance_scope=scope)
    assert result['outcomes'][0]['status'] == 'PENDING' and len(result['data']) == 1
    coverage = result['meta']['workset_batch_audit']['source_context_coverage']['cache']
    assert coverage['complete'] is True and coverage['negative_coverage_complete'] is False
    assert coverage['qualified_occurrence_count'] == 2


def test_omitted_third_qualified_source_cannot_disappear_under_two_negative_contexts(tmp_path, monkeypatch):
    from interview_intelligence.domain.models import OccurrenceTaskAnnotation
    from interview_intelligence.analytics.semantic_scope import semantic_input_hash, ENGINEERING_DESIGN_VERSION
    db, retriever, _, _, scope, filters = setup_workset(tmp_path, monkeypatch, extra=1)
    with db.session() as session, session.begin():
        occurrence = session.get(QuestionOccurrence, 'o-007')
        occurrence.canonical_question_id = 'cache'
        revision = session.get(SourceRevision, 'r-7')
        annotation = session.get(OccurrenceTaskAnnotation, occurrence.id)
        evidence = deepcopy(annotation.evidence)
        evidence['semantic_scopes']['ENGINEERING_DESIGN'].update(version=ENGINEERING_DESIGN_VERSION,
            decision='ACCEPT', input_hash=semantic_input_hash(occurrence, revision))
        annotation.evidence = evidence
    manifest = prepare(db, retriever, scope, filters)['workset']
    with db.session() as session:
        result = process_engineering_workset_batch(session, retriever, '具体业务系统设计', filters,
            workset=manifest, batch_ids=['cache'], relevance_scope=scope)
    assert result['outcomes'][0]['status'] == 'PENDING' and len(result['data']) == 1
    coverage = result['meta']['workset_batch_audit']['source_context_coverage']['cache']
    assert coverage['qualified_occurrence_count'] == 3 and coverage['omitted_occurrence_count'] == 1
    assert coverage['complete'] is False and coverage['negative_coverage_complete'] is False


@pytest.mark.parametrize('field', ['version', 'scope_verifier_version', 'system_prompt', 'scope_verifier_prompt'])
def test_cached_workset_rejects_proof_policy_or_prompt_changes_without_any_model_call(tmp_path, monkeypatch, field):
    db, retriever, client, ranker, scope, filters = setup_workset(tmp_path, monkeypatch, extra=0)
    manifest = prepare(db, retriever, scope, filters)['workset']
    count = len(client.calls)
    monkeypatch.setattr(ranker, field, 'changed-proof-policy', raising=False)
    with db.session() as session, pytest.raises(ValueError, match='SNAPSHOT_CHANGED'):
        validate_engineering_workset(session, retriever, filters, workset=manifest, relevance_scope=scope)
    assert len(client.calls) == count and not ranker.calls


@pytest.mark.parametrize('fault', [None, 'missing_ids', 'insufficient', 'ungrounded', 'in_scope',
    'missing_hash', 'wrong_hash', 'wrong_decision', 'wrong_contract', 'invalid_evidence', 'duplicate_ids', 'wrong_source',
    'missing_adjudication_contract', 'old_adjudication_contract', 'missing_adjudication_status',
    'unresolved_adjudication', 'missing_scope_verification', 'unattempted_scope_verification'])
def test_single_source_exclusion_needs_complete_consistent_explicit_host_proof(tmp_path, monkeypatch, fault):
    db, retriever, _, ranker, scope, filters = setup_workset(tmp_path, monkeypatch, extra=0)
    manifest = prepare(db, retriever, scope, filters)['workset']
    def negative(query, rows, *, relevance_scope):
        ids = [source['occurrence_id'] for source in rows[0]['source_context']]
        proof = {'canonical_question_id': 'agent', 'decision': 'SCOPE_REJECTED',
            'relevance_scope_status': 'EXCLUDED', 'relevance_scope_hash': relevance_scope['scope_hash'],
            'scope_adjudication_contract': CLARIFIED_ADJUDICATION_CONTRACT,
            'scope_adjudication_status': 'VERIFIED', 'scope_verification_status': 'SCOPE_REJECTED',
            'source_verified_negative_occurrence_ids': ids,
            'business_assessment': {'outcome': 'NEGATIVE', 'grounded': True, 'in_scope': False,
                'invalid_evidence_fields': [], 'contract': BUSINESS_SCOPE_CONTRACT}}
        if fault == 'missing_ids': proof.pop('source_verified_negative_occurrence_ids')
        elif fault == 'insufficient': proof['business_assessment']['outcome'] = 'INSUFFICIENT'
        elif fault == 'ungrounded': proof['business_assessment']['grounded'] = False
        elif fault == 'in_scope': proof['business_assessment']['in_scope'] = True
        elif fault == 'missing_hash': proof.pop('relevance_scope_hash')
        elif fault == 'wrong_hash': proof['relevance_scope_hash'] = '0' * 64
        elif fault == 'wrong_decision': proof['decision'] = 'ACCEPTED'
        elif fault == 'wrong_contract': proof['business_assessment']['contract'] = 'old-proof-policy'
        elif fault == 'invalid_evidence': proof['business_assessment']['invalid_evidence_fields'] = ['unit_evidence']
        elif fault == 'duplicate_ids': proof['source_verified_negative_occurrence_ids'] = ids * 2
        elif fault == 'wrong_source': proof['source_verified_negative_occurrence_ids'] = ['other-occurrence']
        elif fault == 'missing_adjudication_contract': proof.pop('scope_adjudication_contract')
        elif fault == 'old_adjudication_contract': proof['scope_adjudication_contract'] = 'old-primary-only-policy'
        elif fault == 'missing_adjudication_status': proof.pop('scope_adjudication_status')
        elif fault == 'unresolved_adjudication': proof['scope_adjudication_status'] = 'UNRESOLVED'
        elif fault == 'missing_scope_verification': proof.pop('scope_verification_status')
        elif fault == 'unattempted_scope_verification': proof['scope_verification_status'] = 'NOT_ATTEMPTED'
        result = Ranked()
        result.audit = {'candidates': [proof]}
        return result
    monkeypatch.setattr(ranker, 'rerank', negative)
    with db.session() as session:
        result = process_engineering_workset_batch(session, retriever, '具体业务系统设计', filters,
            workset=manifest, batch_ids=['agent'], relevance_scope=scope)
    assert result['outcomes'][0]['status'] == ('EXCLUDED' if fault is None else 'PENDING')
    assert len(result['data']) == int(fault is not None)


@pytest.mark.parametrize('fault', ['insufficient', 'wrong_hash', 'missing_audit', 'duplicate_audit'])
def test_confirmed_row_cannot_override_missing_or_contradictory_business_audit(tmp_path, monkeypatch, fault):
    db, retriever, _, ranker, scope, filters = setup_workset(tmp_path, monkeypatch, extra=0)
    manifest = prepare(db, retriever, scope, filters)['workset']
    original = ranker.rerank
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        if fault == 'insufficient': result.audit['candidates'][0]['business_assessment']['outcome'] = 'INSUFFICIENT'
        elif fault == 'wrong_hash': result.audit['candidates'][0]['relevance_scope_hash'] = '0' * 64
        elif fault == 'missing_audit': result.audit = None
        elif fault == 'duplicate_audit': result.audit['candidates'] *= 2
        return result
    monkeypatch.setattr(ranker, 'rerank', changed)
    with db.session() as session:
        result = process_engineering_workset_batch(session, retriever, '具体业务系统设计', filters,
            workset=manifest, batch_ids=['orders'], relevance_scope=scope)
    assert result['outcomes'][0]['status'] == 'PENDING'
    assert len(result['data']) == 1


@pytest.mark.parametrize('version', ['engineering_workset_source_proof_v1', 'engineering_workset_source_proof_v2',
                                   'engineering_workset_source_proof_v3'])
def test_old_source_consumption_policy_cannot_reuse_cached_outcomes(tmp_path, monkeypatch, version):
    db, retriever, client, ranker, scope, filters = setup_workset(tmp_path, monkeypatch, extra=0)
    manifest = prepare(db, retriever, scope, filters)['workset']
    manifest['proof_policy']['workset_source_policy_version'] = version
    manifest['manifest_hash'] = workset_digest(manifest)
    count = len(client.calls)
    with db.session() as session, pytest.raises(ValueError, match='INVALID_ENGINEERING_WORKSET'):
        validate_engineering_workset(session, retriever, filters, workset=manifest, relevance_scope=scope)
    assert len(client.calls) == count and not ranker.calls
