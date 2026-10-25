"""A legal source span cannot authenticate a different declared original ask."""
from copy import deepcopy
import hashlib
import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import select
from interview_intelligence.contracts import FilterSpec
from interview_intelligence.domain.models import QuestionOccurrence, Interview, DocumentBuild, SourceRevision
from interview_intelligence.search.evidence import candidate_source_context
from interview_intelligence.search.reranker import LLMReranker
from test_analytics import seed_corpus
_fixture_spec = importlib.util.spec_from_file_location("source_authority_provider_fixture",
    Path(__file__).parents[1] / "unit" / "test_business_scope_verification.py")
_fixture = importlib.util.module_from_spec(_fixture_spec)
_fixture_spec.loader.exec_module(_fixture)
Provider, QUERY, scope = _fixture.Provider, _fixture.QUERY, _fixture.scope

BUSINESS = "设计一个通知预约平台，居民提交预约并收到处理结果。"


def source_case(case):
    raw, text = BUSINESS, BUSINESS
    if case == 'subspan': text = '要求：' + BUSINESS + '附注。'
    elif case == 'mismatch': text = '解释一致性的定义。'
    elif case == 'rewrite': text = '设计通知预约系统，用户发起预约并接收办理结果。'
    elif case == 'outside_span': text = '解释一致性的定义。\n' + BUSINESS
    elif case == 'joined': text = '设计一个通知预约平台，\n居民提交预约并收到处理结果。'
    elif case == 'empty_original': raw = ''
    spans = [{'start_char':0,'end_char':len(text),'quote':text}]
    if case == 'outside_span':
        quote = text.splitlines()[0];spans=[{'start_char':0,'end_char':len(quote),'quote':quote}]
    elif case == 'joined':
        first,last=text.splitlines();spans=[{'start_char':0,'end_char':len(first),'quote':first},
            {'start_char':len(first)+1,'end_char':len(text),'quote':last}]
    elif case == 'foreign_revision': spans[0]['revision_id']='foreign'
    elif case == 'none': spans=None
    elif case == 'empty': spans=[]
    elif case == 'dict': spans=deepcopy(spans[0])
    elif case == 'string': spans='not-spans'
    elif case == 'none_element': spans=[None]
    elif case == 'string_element': spans=['not-a-span']
    elif case == 'missing_fields': spans=[{'quote':text}]
    elif case == 'bool_offset': spans[0]['start_char']=False
    return raw,text,spans


@pytest.mark.parametrize('case', ['exact','subspan','mismatch','rewrite','outside_span','joined','foreign_revision',
    'empty_original','none','empty','dict','string','none_element','string_element','missing_fields','bool_offset'])
def test_actual_loader_and_scoped_consumer_require_one_continuous_authenticated_original(tmp_path, case):
    db,cid,_=seed_corpus()
    raw,text,spans=source_case(case)
    digest=hashlib.sha256(text.encode()).hexdigest()
    (tmp_path/(digest+'.md')).write_bytes(text.encode())
    with db.session() as session,session.begin():
        occurrence=session.scalar(select(QuestionOccurrence).where(QuestionOccurrence.canonical_question_id==cid))
        interview=session.get(Interview,occurrence.interview_id)
        revision=session.get(SourceRevision,session.get(DocumentBuild,interview.build_id).source_revision_id)
        revision.raw_file_hash=revision.decoded_text_hash=digest
        occurrence.raw_question=raw;occurrence.source_spans=spans
        oid=occurrence.id
    with db.session() as session:
        context=candidate_source_context(session,[cid],FilterSpec(),snapshot_root=tmp_path,occurrence_ids=[oid],include_coverage=True)[cid]
    source=context['source_context'][0]
    valid=case in {'exact','subspan'}
    assert source['source_context_status']==('VERIFIED' if valid else 'INVALID_SPANS')
    if not valid:
        assert source['context_before']==source['context_after']==''
        assert context['source_context_coverage']['verified_complete_occurrence_ids']==[]
    # Canonical wording is deliberately affirmative: a bad source cannot
    # silently recover its task/business assertions from this normalized text.
    candidate={'canonical_question_id':cid,'canonical_text':BUSINESS,'source_context_required':True,**context}
    fixture=Provider({'通知预约平台':'BUSINESS_WORKFLOW'})
    result=LLMReranker(client=fixture,model='NO_PROVIDER_FIXTURE').rerank(QUERY,[candidate],relevance_scope=scope())
    assert result[0]['relevance_scope_status']==('CONFIRMED' if valid else 'PENDING')
    # Direct reranker keeps its existing primary diagnostic call, but cannot
    # admit source-based scope confirmation. The real workset path below
    # skips reranking altogether when source hydration is invalid.
    assert len(fixture.requests)==(2 if valid else 1)


def test_workset_batch_keeps_mismatched_source_as_pending_without_reranking(tmp_path, monkeypatch):
    from test_engineering_candidate_workset import setup_workset,prepare
    from interview_intelligence.search.service import process_engineering_workset_batch
    from interview_intelligence.analytics.semantic_scope import semantic_input_hash
    from interview_intelligence.domain.models import OccurrenceTaskAnnotation
    db,retriever,_,ranker,host,filters=setup_workset(tmp_path,monkeypatch,extra=0)
    text='解释一致性的定义。';digest=hashlib.sha256(text.encode()).hexdigest()
    (tmp_path/(digest+'.md')).write_bytes(text.encode())
    with db.session() as session,session.begin():
        occurrence=session.get(QuestionOccurrence,'o-003');revision=session.get(SourceRevision,'r-3')
        occurrence.raw_question=BUSINESS
        occurrence.source_spans=[{'revision_id':revision.id,'start_char':0,'end_char':len(text),'quote':text}]
        revision.raw_file_hash=revision.decoded_text_hash=digest
        annotation=session.get(OccurrenceTaskAnnotation,occurrence.id)
        evidence=deepcopy(annotation.evidence)
        for proof in evidence['semantic_scopes'].values():proof['input_hash']=semantic_input_hash(occurrence,revision)
        annotation.evidence=evidence
    manifest=prepare(db,retriever,host,filters)['workset']
    with db.session() as session:
        result=process_engineering_workset_batch(session,retriever,'具体业务系统设计',filters,
            workset=manifest,batch_ids=['orders'],relevance_scope=host)
    assert result['outcomes'][0]['status']=='PENDING'
    assert result['data'][0]['canonical_question_id']=='orders' and not ranker.calls
