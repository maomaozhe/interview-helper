"""Independent negative adjudication; neutral sources, no private gold labels."""
from copy import deepcopy
import json
import time

import httpx
import pytest

from test_business_scope_verification import Provider, QUERY, candidate, scope
from interview_intelligence.providers.budget import CallBudget
from interview_intelligence.providers.runtime import RequestLimits, current_limits
from interview_intelligence.search.business_scope import BUSINESS_SCOPE_CONTRACT, CLARIFIED_ADJUDICATION_CONTRACT
from interview_intelligence.search.reranker import LLMReranker, RerankedCandidates


def source(identity,text):
    row=candidate(identity,text)
    row['source_context_required']=True
    row['source_context'][0]['occurrence_id']='occ-'+identity
    row['source_context_coverage']={'qualified_occurrence_count':1,'qualified_occurrence_ids':['occ-'+identity],'complete':True}
    return row


def negative_provider(unit='BUSINESS_APPLICATION',*,verification=None,failure=None):
    class Negative(Provider):
        def create(self,**request):
            if self.requests and failure:
                self.requests.append(request)
                raise failure
            return super().create(**request)
    return Negative({'未见':unit},primary_overrides={'semantic_unit':'FOUNDATION_COMPONENT',
        'delivery_role':'TECHNICAL_CAPABILITY','technical_goal_evidence':'s0o2','rejection_basis':'TARGET_UNIT',
        'candidate_task':'LOCAL_DETAIL'},scope_overrides=verification)


def relation():
    return {'o':'INSTANCE','f':'SAME','e':'s0o0','h':'s0o1','g':3}


@pytest.mark.parametrize('unit',['BUSINESS_APPLICATION','AI_APPLICATION'])
def test_negative_can_be_recovered_only_by_fresh_source_bound_delivery_and_query_relation(unit):
    text='设计一个未见的领用功能，居民提交申请并查看处理状态，要求低延迟。'
    provider=negative_provider(unit,verification={'scope_query_relation':relation()})
    result=LLMReranker(client=provider,model='fixture').rerank(QUERY,[source('recover',text)],relevance_scope=scope())
    assert len(provider.requests)==2 and result[0]['relevance_scope_status']=='CONFIRMED'
    diagnostic=result.audit['candidates'][0]
    assert diagnostic['primary_diagnostic']['business_assessment']['outcome']=='NEGATIVE'
    assert diagnostic['primary_diagnostic']['decision']=='TASK_REJECTED'
    assert diagnostic['business_assessment']['outcome']=='POSITIVE'
    assert diagnostic['scope_adjudication_contract']==CLARIFIED_ADJUDICATION_CONTRACT
    assert diagnostic['scope_adjudication_status']=='VERIFIED'
    assert diagnostic['scope_task_recovery']=='RECOVERED'
    assert diagnostic['effective_query_relation_source']=='scope_independent_reassessment'


def test_null_fresh_query_relation_does_not_reuse_axes_of_the_overturned_negative():
    provider=negative_provider()
    result=LLMReranker(client=provider,model='fixture').rerank(QUERY,[source('no-q',
        '设计一个未见的借还功能，居民登记借用并确认归还结果，要求低延迟。')],relevance_scope=scope())
    assert len(provider.requests)==3 and result[0]['relevance_scope_status']=='PENDING'
    diagnostic=result.audit['candidates'][0]
    assert diagnostic['primary_diagnostic']['relevance_grade']==3
    assert diagnostic['scope_host_reason']=='NEGATIVE_REVERSAL_REQUIRES_FRESH_QUERY_RELATION'
    assert diagnostic['scope_adjudication_status']=='UNRESOLVED'


def test_an_all_negative_batch_still_executes_scope_and_keeps_complete_audit():
    rows=[source('negative-'+str(i),f'设计一个未见的通用缓冲组件{i}，支持并发投递，要求低延迟。') for i in range(4)]
    provider=negative_provider('FOUNDATION_COMPONENT',verification={'technical_goal_evidence':'s0o2'})
    result=LLMReranker(client=provider,model='fixture').rerank(QUERY,rows,relevance_scope=scope())
    assert len(provider.requests)==2 and result==[]
    assert len(json.loads(provider.requests[1]['messages'][-1]['content'])['candidates'])==4
    assert result.audit['scope_adjudication']['primary_negative_candidate_count']==4
    for diagnostic in result.audit['candidates']:
        assert diagnostic['relevance_scope_status']=='EXCLUDED'
        assert diagnostic['scope_verification_status']=='SCOPE_REJECTED'
        assert diagnostic['scope_adjudication_status']=='VERIFIED'
        assert diagnostic['source_verified_negative_occurrence_ids']==['occ-'+diagnostic['canonical_question_id']]


def test_no_remaining_scope_budget_keeps_primary_negative_pending_and_visible():
    provider=negative_provider('FOUNDATION_COMPONENT')
    result=LLMReranker(client=provider,model='fixture',budget=CallBudget(max_calls=1,max_tokens=1000000)).rerank(
        QUERY,[source('deferred','设计一个未见的通用缓冲组件，支持并发投递，要求低延迟。')],relevance_scope=scope())
    assert len(provider.requests)==1 and result[0]['relevance_scope_status']=='PENDING'
    row=result.audit['candidates'][0]
    assert row['primary_diagnostic']['business_assessment']['outcome']=='NEGATIVE'
    assert row['scope_adjudication_status']=='DEFERRED_BUDGET_OR_TIME'
    assert row['source_verified_negative_occurrence_ids']==[]
    assert result.audit['scope_adjudication']['deferred_candidate_ids']==['deferred']


@pytest.mark.parametrize('failure',[ValueError('MODEL_OUTPUT_TRUNCATED'),httpx.ReadTimeout('controlled transport failure')])
def test_independent_failure_never_falls_back_to_the_primary_negative(failure):
    provider=negative_provider('FOUNDATION_COMPONENT',failure=failure)
    result=LLMReranker(client=provider,model='fixture').rerank(QUERY,[source('failed',
        '设计一个未见的通用缓冲组件，支持并发投递，要求低延迟。')],relevance_scope=scope())
    assert len(provider.requests)==2 and result[0]['relevance_scope_status']=='PENDING'
    row=result.audit['candidates'][0]
    assert row['scope_adjudication_status']=='FAILED' and row['scope_host_reason']=='SCOPE_PROVIDER_FAILURE'
    assert row['primary_diagnostic']['business_assessment']['outcome']=='NEGATIVE'


def test_direct_finalizer_cannot_promote_a_primary_negative_without_a_scope_receipt():
    original={'canonical_question_id':'unreviewed','decision':'SCOPE_REJECTED',
        'business_assessment':{'outcome':'NEGATIVE','grounded':True,'in_scope':False,'contract':BUSINESS_SCOPE_CONTRACT},
        'business_evidence_references':{'unit_evidence':'s0o0','technical_goal_evidence':'s0o1'}}
    result=LLMReranker._clarified_result(RerankedCandidates([],{'candidates':[original]}),[source('unreviewed',
        '设计一个未见的通用组件，要求低延迟。')],scope())
    assert result[0]['relevance_scope_status']=='PENDING'
    assert result.audit['candidates'][0]['scope_host_reason']=='NEGATIVE_INDEPENDENT_SCOPE_NOT_VERIFIED'
    assert result.audit['candidates'][0]['source_verified_negative_occurrence_ids']==[]


def test_negative_source_unavailable_never_gets_a_verified_marker_or_silent_exclusion():
    row=source('missing','设计一个未见的通用组件，支持并发投递，要求低延迟。')
    row['source_context'][0].update(source_context_status='UNAVAILABLE')
    provider=negative_provider('FOUNDATION_COMPONENT')
    result=LLMReranker(client=provider,model='fixture').rerank(QUERY,[row],relevance_scope=scope())
    assert len(provider.requests)==1 and result[0]['relevance_scope_status']=='PENDING'
    assert result.audit['candidates'][0]['scope_adjudication_status']=='SOURCE_UNAVAILABLE'


def test_independent_negative_still_needs_every_qualified_occurrence_covered():
    row=source('multi','设计一个未见的通用缓冲组件，支持并发投递，要求低延迟。')
    row['source_context'].append({**row['source_context'][0],'occurrence_id':'occ-other'})
    row['source_context_coverage'].update(qualified_occurrence_count=2,
        qualified_occurrence_ids=['occ-multi','occ-other'])
    provider=negative_provider('FOUNDATION_COMPONENT',verification={'technical_goal_evidence':'s0o2'})
    result=LLMReranker(client=provider,model='fixture').rerank(QUERY,[row],relevance_scope=scope())
    assert len(provider.requests)==2 and result[0]['relevance_scope_status']=='PENDING'
    diagnostic=result.audit['candidates'][0]
    assert diagnostic['scope_adjudication_status']=='VERIFIED'
    assert diagnostic['scope_host_reason']=='NEGATIVE_CANONICAL_SOURCE_COVERAGE_UNRESOLVED'
    assert diagnostic['source_verified_negative_occurrence_ids']==['occ-multi']


def test_partial_review_is_fair_across_confirmations_negatives_and_late_ranks():
    identities=[f'a{i}' for i in range(12)]+[f'n{i}' for i in range(12)]
    rows=[source(key,'设计一个未见的交付对象，支持处理请求，要求低延迟。') for key in identities]
    class CostRanker(LLMReranker):
        def _primary_request(self,*args,**kwargs):return {'token_upper_bound':1}
        def _rank_candidates(self,query,candidates,**kwargs):
            diagnostics=[{'canonical_question_id':row['canonical_question_id'],'input_rank':i+1,'model_rank':i+1,
                'relevance_grade':3,'quote_grounded':True,'decision':'ACCEPTED' if key.startswith('a') else 'SCOPE_REJECTED',
                'business_assessment':{'outcome':'POSITIVE' if key.startswith('a') else 'NEGATIVE'}}
                for i,(key,row) in enumerate(zip(identities,candidates))]
            return RerankedCandidates(candidates[:12],{'candidates':diagnostics,'candidate_verification_status':'COMPLETE','invalid_candidate_count':0})
        def _scope_request(self,query,candidates,**kwargs):return {'token_upper_bound':len(candidates)*20}
        def _verify_scope(self,query,candidates,**kwargs):
            return RerankedCandidates([],{'candidates':[{'canonical_question_id':row['canonical_question_id'],
                'decision':'INVALID_BUSINESS_EVIDENCE','scope_verification_status':'INVALID_BUSINESS_EVIDENCE'} for row in candidates],
                'invalid_candidate_count':len(candidates),'scope_verification_status':'PARTIAL'})
    result=CostRanker(client=object(),model='fixture',budget=CallBudget(max_calls=6,max_tokens=80)).rerank(QUERY,rows,relevance_scope=scope())
    assert result.audit['scope_adjudication']['selected_candidate_ids']==['a0','n0','a10','n8']
    assert len(result)==24 and all(row['relevance_scope_status']=='PENDING' for row in result)


def test_primary_forged_review_markers_are_overwritten_by_host_deferred_status():
    class Forged(LLMReranker):
        def _primary_request(self,*args,**kwargs):return {'token_upper_bound':1}
        def _rank_candidates(self,query,candidates,**kwargs):
            row={'canonical_question_id':'forged','input_rank':1,'model_rank':1,'relevance_grade':3,
                'decision':'SCOPE_REJECTED','business_assessment':{'outcome':'NEGATIVE'},
                'scope_adjudication_status':'VERIFIED','scope_adjudication_contract':CLARIFIED_ADJUDICATION_CONTRACT,
                'scope_verification_status':'SCOPE_REJECTED'}
            return RerankedCandidates([],{'candidates':[row],'candidate_verification_status':'COMPLETE','invalid_candidate_count':0})
    limits=RequestLimits(deadline=time.monotonic()+180,max_calls=0,max_tokens=96000)
    token=current_limits.set(limits)
    try:
        result=Forged(client=object(),model='fixture').rerank(QUERY,[source('forged',
            '设计一个未见的通用组件，要求低延迟。')],relevance_scope=scope())
    finally:current_limits.reset(token)
    diagnostic=result.audit['candidates'][0]
    assert result[0]['relevance_scope_status']=='PENDING'
    assert diagnostic['scope_adjudication_status']=='DEFERRED_BUDGET_OR_TIME'
    assert diagnostic['primary_diagnostic']['scope_adjudication_status']=='VERIFIED'


def mixed_verification(**changes):
    return {'semantic_unit':'MIXED_OR_AMBIGUOUS','delivery_role':'UNKNOWN','keep':False,
        'rejection_basis':'UNKNOWN','unit_evidence':'s0o0','negative_task_evidence':'',**changes}


def test_valid_source_bound_mixed_boundary_is_stable_pending_without_binary_repair():
    provider=negative_provider(verification=mixed_verification())
    result=LLMReranker(client=provider,model='fixture').rerank(QUERY,[source('mixed',
        '设计一个未见的平台，支持处理请求，要求低延迟。')],relevance_scope=scope())
    assert len(provider.requests)==2 and result[0]['relevance_scope_status']=='PENDING'
    row=result.audit['candidates'][0]
    assert row['primary_diagnostic']['business_assessment']['outcome']=='NEGATIVE'
    assert row['scope_uncertainty_status']=='MIXED_BOUNDARY_UNRESOLVED'
    assert row['evidence_repair_eligible'] is False
    assert row['scope_adjudication_status']=='UNRESOLVED'


@pytest.mark.parametrize('reference',['s9o0','s1o1'])
def test_bad_or_cross_source_mixed_proof_cannot_claim_stable_semantic_uncertainty(reference):
    row=source('bad-mixed','设计一个未见的平台，支持处理请求，要求低延迟。')
    row['source_context'].append({**row['source_context'][0],'occurrence_id':'occ-other'})
    provider=negative_provider(verification=mixed_verification(negative_task_evidence=reference))
    result=LLMReranker(client=provider,model='fixture').rerank(QUERY,[row],relevance_scope=scope())
    assert len(provider.requests)==3 and result[0]['relevance_scope_status']=='PENDING'
    assert 'scope_uncertainty_status' not in result.audit['candidates'][0]


def test_authorized_domain_exclusion_is_not_suppressed_by_a_mixed_unit_or_absent_goal():
    selected=scope(['所有工程系统设计题','具体业务系统设计，排除AI'])
    provider=negative_provider(verification=mixed_verification(rejection_basis='EXCLUDED_DOMAIN',
        excluded_domain='AI',domain_evidence='s0o0'))
    result=LLMReranker(client=provider,model='fixture').rerank(QUERY,[source('domain',
        '设计一个AI未见的平台，支持处理请求，要求低延迟。')],relevance_scope=selected)
    assert len(provider.requests)==2 and result==[]
    row=result.audit['candidates'][0]
    assert row['business_assessment']['host_reason']=='AUTHORIZED_DOMAIN_EXCLUSION'
    assert row['scope_adjudication_status']=='VERIFIED' and row['relevance_scope_status']=='EXCLUDED'
    assert 'scope_uncertainty_status' not in row


def test_authorized_independent_boundary_is_not_suppressed_by_a_mixed_unit():
    selected=scope(['所有工程系统设计题','具体业务系统设计，只看独立完整系统'])
    provider=negative_provider(verification=mixed_verification(design_unit='NEW_OR_REBUILT_SYSTEM',
        construction_evidence='s0o0'))
    result=LLMReranker(client=provider,model='fixture').rerank(QUERY,[source('parent',
        '设计一个未见协作系统的撤销功能，参与者取消申请后释放名额并通知处理结果，要求低延迟。')],relevance_scope=selected)
    assert len(provider.requests)==2 and result==[]
    row=result.audit['candidates'][0]
    assert row['business_assessment']['host_reason']=='AUTHORIZED_INDEPENDENT_UNIT_REJECTION'
    assert row['scope_adjudication_status']=='VERIFIED' and row['relevance_scope_status']=='EXCLUDED'
    assert 'scope_uncertainty_status' not in row
