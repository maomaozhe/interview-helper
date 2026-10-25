"""Capacity limits consume an ordered prefix without changing the SQL universe."""
from copy import deepcopy
from contextlib import contextmanager
import time

import pytest
from interview_intelligence.providers.runtime import RequestLimits,current_limits
from interview_intelligence.search.reranker import LLMReranker
from interview_intelligence.search.service import process_engineering_workset_batch
from test_engineering_candidate_workset import setup_workset,prepare,ProofReranker


class AdaptiveProofReranker(ProofReranker):
    def __init__(self):
        super().__init__()
        self.preparer=LLMReranker(client=object(),model='NO_PROVIDER_PREPARE_ONLY')
        self.capacity_calls=[]
        self.transform=None
    def select_workset_prefix(self,query,rows,*,relevance_scope,max_tokens,max_calls):
        result=self.preparer.select_workset_prefix(query,rows,relevance_scope=relevance_scope,
            max_tokens=max_tokens,max_calls=max_calls)
        self.capacity_calls.append({'max_tokens':max_tokens,'max_calls':max_calls,'result':deepcopy(result)})
        if self.transform:self.transform(result)
        return result


@contextmanager
def limits(tokens=96000,calls=6,*,used_tokens=0,used_calls=0,reserved=0):
    value=RequestLimits(deadline=time.monotonic()+60,max_calls=calls,max_tokens=tokens,
        tokens=used_tokens,calls=used_calls,token_reservations=reserved)
    token=current_limits.set(value)
    try:yield value
    finally:current_limits.reset(token)


def fixture(tmp_path,monkeypatch):
    db,retriever,client,_,scope,filters=setup_workset(tmp_path,monkeypatch,extra=12)
    ranker=AdaptiveProofReranker();retriever.reranker=ranker
    # Preparing the complete universe itself is provider-free in this fixture.
    manifest=prepare(db,retriever,scope,filters)['workset']
    return db,retriever,client,ranker,scope,filters,manifest


def process(values,ids):
    db,retriever,_,_,scope,filters,manifest=values
    with db.session() as session:
        return process_engineering_workset_batch(session,retriever,'具体业务系统设计',filters,
            workset=manifest,batch_ids=ids,relevance_scope=scope)


def ready_inputs(values,ids):
    # Capture exact hydrated inputs using the old deterministic adapter, then
    # restore the real pure capacity method. No provider or encoder is invoked.
    db,retriever,_,ranker,scope,filters,manifest=values
    original=ranker.select_workset_prefix
    ranker.select_workset_prefix=None
    try:process(values,ids)
    finally:ranker.select_workset_prefix=original
    rows=ranker.calls.pop()
    return rows


def test_actual_prepared_prefix_overflow_leaves_tail_unprocessed_and_all_scope_conditions(tmp_path,monkeypatch):
    values=fixture(tmp_path,monkeypatch);ranker=values[3];ids=values[-1]['candidate_ids'][:8]
    rows=ready_inputs(values,ids)
    prepared=ranker.preparer.select_workset_prefix('具体业务系统设计',rows[:3],relevance_scope=values[4],max_tokens=96000,max_calls=6)
    cap=prepared['required_tokens']
    with limits(cap):result=process(values,ids)
    assert result['processed_candidate_ids']==ids[:3]
    assert [r['canonical_question_id'] for r in ranker.calls[-1]]==ids[:3]
    assert [o['canonical_question_id'] for o in result['outcomes']]==ids[:3]
    assert not set(ids[3:])&{row['canonical_question_id'] for row in result['data']}
    audit=result['meta']['workset_batch_audit']
    assert audit['offered_candidate_ids']==ids and audit['unprocessed_candidate_ids']==ids[3:]
    assert audit['capacity_selection']['required_tokens']<=cap
    assert audit['capacity_selection']['scope_hash']==values[4]['scope_hash']
    assert result['meta']['relevance_scope_hash']==values[4]['scope_hash']
    assert result['meta']['candidate_universe_count']==len(values[-1]['candidate_ids'])
    assert 'offered_candidate_ids' not in result['meta'] and 'processed_candidate_ids' not in result['meta']
    assert result['meta']['batch_size']==3 and result['meta']['offered_batch_size']==8


def test_source_faults_before_and_between_ready_rows_are_consumed_but_next_ready_is_not(tmp_path,monkeypatch):
    values=fixture(tmp_path,monkeypatch);_,_,client,ranker,scope,_,manifest=values
    ids=manifest['candidate_ids'][:7]
    client.drop={ids[0],ids[2]}
    ready=ready_inputs(values,ids)
    cap=ranker.preparer.select_workset_prefix('具体业务系统设计',ready[:1],relevance_scope=scope,
        max_tokens=96000,max_calls=6)['required_tokens']
    with limits(cap):result=process(values,ids)
    assert result['processed_candidate_ids']==ids[:3]
    assert [r['canonical_question_id'] for r in ranker.calls[-1]]==[ids[1]]
    assert result['outcomes'][0]['status']==result['outcomes'][2]['status']=='PENDING'
    assert result['meta']['workset_batch_audit']['unprocessed_candidate_ids']==ids[3:]


@pytest.mark.parametrize('budget',[{'tokens':0},{'tokens':1000},{'tokens':96000,'calls':1}])
def test_no_capacity_consumes_through_first_ready_as_pending_with_no_rerank(tmp_path,monkeypatch,budget):
    values=fixture(tmp_path,monkeypatch);client,ranker=values[2:4];ids=values[-1]['candidate_ids'][:5]
    client.drop={ids[0]}
    with limits(**budget):result=process(values,ids)
    assert result['processed_candidate_ids']==ids[:2]
    assert {row['status'] for row in result['outcomes']}=={'PENDING'}
    assert not ranker.calls and len(result['data'])==2
    assert result['meta']['workset_batch_audit']['capacity_selection']['status']=='NO_CAPACITY'


def test_all_source_faults_consume_full_window_pending_without_capacity_or_reranker(tmp_path,monkeypatch):
    values=fixture(tmp_path,monkeypatch);ids=values[-1]['candidate_ids'][:12]
    values[2].drop=set(ids)
    with limits(0,calls=0):result=process(values,ids)
    assert result['processed_candidate_ids']==ids and len(result['data'])==len(ids)
    assert {row['status'] for row in result['outcomes']}=={'PENDING'}
    assert not values[3].capacity_calls and not values[3].calls


def test_normal_request_uses_actual_remaining_calls_tokens_and_reservations(tmp_path,monkeypatch):
    values=fixture(tmp_path,monkeypatch);ids=values[-1]['candidate_ids'][:4]
    with limits(96000,used_tokens=100,used_calls=1,reserved=900):result=process(values,ids)
    assert result['processed_candidate_ids']==ids
    call=values[3].capacity_calls[-1]
    assert call['max_tokens']==95000 and call['max_calls']==5
    assert len(values[3].calls)==1


def test_missing_request_limits_fail_closed_without_fabricating_normal_budget(tmp_path,monkeypatch):
    values=fixture(tmp_path,monkeypatch);ids=values[-1]['candidate_ids'][:4]
    result=process(values,ids)
    assert result['processed_candidate_ids']==ids[:1]
    assert result['outcomes'][0]['status']=='PENDING' and not values[3].calls
    capacity=result['meta']['workset_batch_audit']['capacity_selection']
    assert capacity['max_tokens']==capacity['max_calls']==0
    assert capacity['budget_source']=='request_limits_unavailable'


@pytest.mark.parametrize('fault',['skip','duplicate','foreign','zero_ready','wrong_scope','budget','reason','prepared_sha','prepared_bound'])
def test_malformed_real_selector_contract_is_rejected_before_reranking(tmp_path,monkeypatch,fault):
    values=fixture(tmp_path,monkeypatch);ranker=values[3];ids=values[-1]['candidate_ids'][:4]
    def change(receipt):
        if fault=='skip':receipt['selected_candidate_ids']=ids[1:]
        elif fault=='duplicate':receipt['selected_candidate_ids']=[ids[0],ids[0]]
        elif fault=='foreign':receipt['selected_candidate_ids']=['not-offered']
        elif fault=='zero_ready':receipt['selected_candidate_ids']=[]
        elif fault=='wrong_scope':receipt['scope_hash']='0'*64
        elif fault=='budget':receipt['max_tokens']+=1
        elif fault=='reason':receipt['reason']=[]
        elif fault=='prepared_sha':receipt['prepared']['primary']['prepared_sha256']='bad'
        else:receipt['prepared']['scope']['token_upper_bound']+=1
    ranker.transform=change
    with limits(),pytest.raises(ValueError,match='INVALID_ENGINEERING_WORKSET_BATCH_RESULT'):process(values,ids)
    assert not ranker.calls


@pytest.mark.parametrize('fault',['empty','skip','tail','duplicate','extra','none','data_tail','outcome_tail','data_order','outcome_order','data_duplicate','outcome_duplicate'])
def test_sql_consumer_rejects_nonprefix_or_tail_results_without_filling_unprocessed_ids(tmp_path,monkeypatch,fault):
    values=fixture(tmp_path,monkeypatch);retriever=values[1];ids=values[-1]['candidate_ids'][:4]
    def invalid(*args,**kwargs):
        prefix=ids[:2]
        result={'processed_candidate_ids':prefix,'data':[],
            'outcomes':[{'canonical_question_id':identity,'status':'PENDING'} for identity in prefix],'meta':{}}
        if fault=='empty':result['processed_candidate_ids']=[]
        elif fault=='skip':result['processed_candidate_ids']=[ids[1]]
        elif fault=='tail':result['processed_candidate_ids']=ids[1:]
        elif fault=='duplicate':result['processed_candidate_ids']=[ids[0],ids[0]]
        elif fault=='extra':result['processed_candidate_ids']=ids+['extra']
        elif fault=='none':result['processed_candidate_ids']=None
        elif fault=='data_tail':result['data']=[{'canonical_question_id':ids[-1]}]
        elif fault=='outcome_tail':result['outcomes'].append({'canonical_question_id':ids[-1],'status':'PENDING'})
        elif fault=='data_order':result['data']=[{'canonical_question_id':identity} for identity in reversed(prefix)]
        elif fault=='data_duplicate':result['data']=[{'canonical_question_id':prefix[0]}]*2
        elif fault=='outcome_duplicate':result['outcomes']*=2
        else:result['outcomes'].reverse()
        return result
    monkeypatch.setattr(retriever,'hydrate_and_rerank_ids',invalid)
    with limits(),pytest.raises(ValueError,match='INVALID_ENGINEERING_WORKSET_BATCH_RESULT'):process(values,ids)
    assert not values[3].calls


def test_old_nonadaptive_adapter_consumes_full_offer_without_claiming_llm_capacity(tmp_path,monkeypatch):
    values=fixture(tmp_path,monkeypatch);ids=values[-1]['candidate_ids'][:4]
    monkeypatch.setattr(values[1],'hydrate_and_rerank_ids',lambda *args,**kwargs:{'data':[],'outcomes':[],
        'meta':{'workset_batch_audit':{'status':'LEGACY_NONADAPTIVE'}}})
    result=process(values,ids)
    assert result['processed_candidate_ids']==ids
    assert len(result['outcomes'])==len(result['data'])==4
    assert {row['status'] for row in result['outcomes']}=={'PENDING'}
    assert 'capacity_selection' not in result['meta']['workset_batch_audit']
    assert not values[3].calls and not values[3].capacity_calls


def test_malformed_source_context_records_are_faults_without_advancing_a_fake_ready_tail(tmp_path,monkeypatch):
    values=fixture(tmp_path,monkeypatch);_,retriever,_,ranker,scope,_,manifest=values
    ids=manifest['candidate_ids'][:4]
    with limits(0,calls=0):
        result=retriever.hydrate_and_rerank_ids('具体业务系统设计',ids,relevance_scope=scope,
            target_catalog={'corpus_revision':manifest['revisions']['corpus_revision'],
                'embedding_version':manifest['embedding_version'],'index_schema_version':manifest['index_schema_version'],
                'titles':{identity:retriever.client.rows[identity] for identity in ids}},
            candidate_context_loader=lambda offered:{identity:None for identity in offered})
    assert result['processed_candidate_ids']==ids
    assert {row['status'] for row in result['outcomes']}=={'PENDING'}
    assert not ranker.calls and not ranker.capacity_calls
