"""Frozen user-query intent and source-witness gold; no model or service calls.

The numbered decisions below were written after reading all 2771 raw questions
and opening the source contexts of potential main designs and hard neighbors.
Numbers are immutable one-based facts.questions positions, never runtime rules.
The census is ternary: unresolved boundaries prevent certified full-corpus recall.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil

from eval.common import digest, inside, read_json, read_jsonl, write_json, write_jsonl

SNAPSHOT = Path('data/reports/resume-quality-snapshot-20261005-v1')
REPORT = Path('data/reports/user-query-source-reviewed-20261015-v1')
DATASET = Path('data/gold/user-query-source-reviewed-20261015-v1')
FACTS_DIGEST = '0b8fd1b94127c90f0c0c463295c1dc8045184d73eb8475bb91f6eca88c67501a'

# Independent semantic judgments, not inherited old-reference grades.
POSITIVE = {
214:'多机房各自Redis的全局限流，需要跨节点协调与全局额度机制。',
219:'从输入到实时商品名称模糊联想的独立搜索功能。',
244:'构造Cache并同时满足高QPS并发读与新增写入。',
321:'10万消息/秒、状态条件过滤和MySQL写入组成闭合数据集成系统。',
599:'构造监控埋点系统，同时处理大量日志接入与存储消费。',
649:'面向多类请求的公平任务队列与资源隔离，是独立调度机制。',
733:'资源有界且结果可复用，需统筹并发、延迟与稳定性的请求处理机制。',
785:'承接有容量/过期约束KV，设计持久化并发读写与掉电正确恢复的闭合机制。',
894:'热点榜单实时更新、高并发、数据结构和数据库选型的主设计题。',
1053:'独立高速Cache，要求高吞吐、低延迟和可扩展。',
1193:'明确重新构造ngix容灾系统，而非解释已有ngix作用。',
1275:'自行设计延时消息队列，后续调度/准时消费支持其独立服务范围。',
1338:'购票系统10万人/1000座、长途优先、高可用，原题保留完整需求。',
1375:'千万玩家积分系统，签到发分、到期与优先扣快过期积分形成闭环。',
1514:'明确构造SSE断线恢复，包含数据补发、存储和Redis容量边界。',
1653:'明确实现跨服务器到客户端推送的完整链路，不是局部SSE追问。',
2093:'关注/取消关注/双向列表、大用户量与高并发的主功能构造。',
2322:'从头构造join.qq.com网页全链路并应对访问量增长。',
2478:'把现有系统重新构造为可上线大规模分布式系统，要求服务器节点部署。',
2563:'设计冷热数据分层，同时保留冷数据查询能力，是完整存储机制。',
2624:'停车场入场、分车位、查询、收费、离场闭环，并发与重复收费约束齐全。',
}
BOUNDARY = {
54:'恶意URL判别有业务目标，未明确系统构造还是分类算法，保留范围边界。',
90:'作者注明另一条件遗忘，长连接要求不完整。',
202:'仅WebSocket重连局部组件，是否纳入闭合机制缺完整约束。',
250:'无标题题单未给可核具体面试事件；不把机器INCLUDED当真实面试资格。',
319:'只有分布式ID设计标题，无功能/约束证据，不继承旧主集正标签。',
633:'有get/put/容量/TTL等数据结构约束，仍是内存map题；主系统范围边界。',
744:'可靠UDP可能是知识解释或协议构造，原问无额外任务条件。',
827:'百万连接服务拆分虽像架构题，但无标题清单缺具体面试事件证据。',
1085:'QQ账号拦截可能为局部数据结构算法，且无标题题单资格未核实。',
1147:'大文档令牌公平性是既有解析服务追问，闭合调度与局部优化边界。',
1297:'只有分布式任务恢复队列标题，后面主要为既有队列故障追问。',
1358:'MCP按业务域隔离/交付可为独立基础设施，也在Agent工具应用相邻范围。',
1572:'缓存设计裸问依赖上一资源调度语境，不能自动与完整高速Cache等价。',
2036:'大量任务线程池缺新功能约束，可能为参数知识或局部调度设计。',
2104:'仅令牌桶标题，缺配额/状态/边界条件，不把设计二字当完整工程场景。',
2131:'无标题题单的高并发服务器构造缺具体面试事件，保留资格边界。',
2138:'并发安全按需加载是局部功能/模式练习，与闭合工程机制边界未裁定。',
2216:'数组链表优点笼统，无明确行为或约束，不强推工程构造。',
2479:'周报发送系统明确，但题目重心是AI/人工任务拆分，服务设计范围边界。',
2496:'MR前风格/安全校验器明确，但实际要求AI Coding实现，与口述设计边界。',
2622:'功能完整的缓存代码题；代码实现需求与宽系统设计题边界，独立列出。',
2649:'GPU推理/微调调度具资源约束，处于Agent应用与独立基础设施边界。',
}
AGENT_POSITIVE = [97,467,453,1238,1836,1865,1917,2183,2696,1593,2485,1316]
REDIS_POSITIVE = [214,1037,756,1624,2040,2318,2303,2681,1970,1393]
BYTE_POSITIVE = [742,1011,2460,2766,1448,1801,808,2213]
INCIDENT_POSITIVE = [1077,2318,2303,2681]
FAMILIES = {214:'global_rate_limit',219:'autocomplete',244:'cache',321:'data_integration',
599:'telemetry',649:'request_scheduler',733:'request_scheduler',785:'kv_persistence',
894:'leaderboard',1053:'cache',1193:'disaster_recovery',1275:'delay_queue',1338:'ticketing',
1375:'game_points',1514:'stream_recovery',1653:'distributed_push',2093:'social_follow',
2322:'web_delivery',2478:'distributed_deployment',2563:'tiered_storage',2624:'parking'}
DESIGN_COMPANIES = {214:'字节',219:'字节',244:'拼多多',321:'字节',599:'小红书',
649:'帆软',733:'帆软',785:'腾讯',894:'字节',1053:'字节',1193:'腾讯',1275:'得物',
1338:'字节',1375:'点点互动',1514:None,1653:'微信支付',2093:'字节',2322:'腾讯',
2478:'百度',2563:'腾讯',2624:'小红书'}
HARD_NEGATIVE = [62,252,259,298,378,418,453,488,547,672,680,701,830,915,
1004,1047,1101,1104,1168,1189,1248,1255,1279,1354,1423,1458,1492,
1511,1543,1579,1593,1607,1631,1674,1703,1709,1712,1714,1728,1736,
1738,1740,1785,1800,1837,1846,1865,1873,1885,1889,1890,1917,1918,
1921,1926,1955,1978,2015,2018,2029,2030,2034,2054,2057,2061,2082,
2156,2174,2180,2234,2236,2248,2301,2303,2314,2316,2317,2318,2319,
2337,2347,2406,2410,2414,2433,2442,2454,2482,2488,2500,2531,2537,
2540,2564,2580,2587,2592,2596,2598,2600,2616,2625,2631,2635,2679,
2687,2707,2718,2724,2733,2736,2740,2745,2766]

# Source windows actually displayed and read during this review; also includes
# every canonical variant of these positions. Remaining rows have raw-screen
# evidence, never a false full-original-read assertion.
CONTEXT_INDICES = sorted(set(POSITIVE)|set(BOUNDARY)|set(HARD_NEGATIVE)|set(AGENT_POSITIVE)|set(REDIS_POSITIVE)|set(BYTE_POSITIVE)|set(INCIDENT_POSITIVE)|{2419,1187,1473,789,1746,1752,1560,2281})
RAW_REJECT_BASIS = ('逐条读取原问后排除：未提出独立工程系统/闭合机制的构造任务；'
 '概念解释、算法/数据结构、已有项目实现或评估、故障排查、局部追问不自动升级为主设计。'
 '这是原问级审核，不声明已读该条全部原始上下文。')

def _text(path: Path) -> str:
    return path.read_bytes().decode('utf-8-sig').replace('\r\n','\n').replace('\r','\n')

def _span(text: str, a: int, b: int) -> dict:
    return {'start_char':a,'end_char':b,'start_line':text.count('\n',0,a)+1,
            'end_line':text.count('\n',0,max(a,b-1))+1,'quote':text[a:b]}

def _review(basis: str, now: str, level: str) -> dict:
    return {'reviewer_kind':'agent','reviewer':'Codex / user_query_source_20261015',
            'reviewed_at':now,'basis':basis,'evidence_level':level,
            'human_verified':False,'agent_verified':True}

def _history_origin(history: dict, index: int, quote: str | None, derived: str | None = None) -> dict:
    item=history['entries'][index]; msg=item['messages'][0]
    origin={'kind':'derived' if derived else 'historical_user_substring',
            'history_id':f'history-{index}', 'thread_id':item['thread_id'],
            'turn_id':item['turn_id'],'message_id':msg['message_id']}
    if quote is not None:
        a=msg['text'].index(quote)
        origin.update(quote=quote,start_char=a,end_char=a+len(quote))
    if derived: origin['derivation']=derived
    return origin

def _cases(history: dict, scopes: dict) -> list[dict]:
    cases=[]
    def add(cid, history_index, turns):
        case={'id':cid,'case_id':cid,'schema':'user_query_scope_case_v1',
              'human_verified':False,'agent_verified':True,'turns':[]}
        for i,t in enumerate(turns):
            message, scope, action, options=t
            options=dict(options)
            quote=options.pop('historical_quote',None)
            turn_history_index=options.pop('history_index',history_index)
            origin=_history_origin(history,turn_history_index,quote,
                None if quote==message else options.pop('derivation','基于历史需求改写或构造多轮承接；不是用户原话。'))
            s=scopes[scope]
            expected={'semantic_action':action,'accepted_actions':[action],
                'scope_id':scope,'required_filters':{},
                'forbidden_inferred_filters':['coding_focus','response_form','topic_l1','topic_l2','question_type'],
                'accepted_sort':[],'requested_completeness':'page',
                'page_size':20,'min_positive_results':min(10,len(s['positive_gold_ids'])),
                'max_negative_results':0,'require_source_support':True,
                'positive_gold_ids':s['positive_gold_ids'],
                'hard_negative_gold_ids':s['hard_negative_gold_ids'],
                'unknown_result_policy':'review_required_not_automatic_negative',
                'scope_certification':s['certification'],
                'expected_certification':s['certification']}
            if scope==eng and action=='SEARCH': expected['min_distinct_families']=4
            expected.update(options)
            if expected.get('required_filters') or action!='SEARCH':
                expected['min_distinct_families']=min(1,expected['min_positive_results'])
            if action in {'CLARIFY','ANSWER','COUNT'}:
                expected['min_positive_results']=0
                expected['accepted_sort']=[]
            turn={'case_id':cid,'turn_index':i,'message':message,'origin':origin,
                  'explicit_filters':expected.get('required_filters',{}),'expected':expected}
            case['turns'].append(turn)
        case['origin']=case['turns'][0]['origin']
        cases.append(case)
    eng='engineering_system_design'; agent='agent_application_design'; redis='redis_scenarios'; byte='bytedance_algorithm_tasks'; inc='incident_troubleshooting'
    original='我要的是工程系统设计题，类似设计一个排行榜这种'
    add('uq-engineering-original',0,[(original,eng,'SEARCH',{'historical_quote':original,'example_is_not_filter':True})])
    for cid,message in [
      ('uq-engineering-wide','帮我找工程系统设计题，像排行榜、购票系统、停车场这类'),
      ('uq-engineering-no-agent','找真实业务系统和基础设施的设计题，不要Agent应用设计题'),
      ('uq-engineering-not-code-label','我要系统设计场景题，口述方案也可以，不用限定手写代码'),
      ('uq-engineering-examples','类似设计一个排行榜这样的工程设计题，还有哪些不同系统？')]:
        add(cid,0,[(message,eng,'SEARCH',{'example_is_not_filter':True})])
    add('uq-leaderboard-only',0,[('只找排行榜系统设计题', 'leaderboard_design','SEARCH',{'min_positive_results':1})])
    add('uq-clarify-business',1,[('场景设计题',eng,'CLARIFY',{'historical_quote':'场景设计题','min_positive_results':0,'clarification_dimensions':['Agent应用','业务或工程系统','线上排障','全部']}),('业务工程系统设计，不是Agent应用','engineering_system_design','SEARCH',{'requires_conversation_continuity':True})])
    add('uq-clarify-agent',1,[('场景设计题',eng,'CLARIFY',{'historical_quote':'场景设计题'}),('Agent应用设计的',agent,'SEARCH',{'requires_conversation_continuity':True,'min_positive_results':5})])
    add('uq-clarify-incident',1,[('场景设计题',eng,'CLARIFY',{'historical_quote':'场景设计题'}),('线上故障排查这一类',inc,'SEARCH',{'requires_conversation_continuity':True,'min_positive_results':2})])
    add('uq-correct-agent-to-business',0,[('给我Agent应用的场景设计题',agent,'SEARCH',{'min_positive_results':5}),('不是这个方向，我要的是工程业务系统设计，类似排行榜',eng,'SEARCH',{'requires_conversation_continuity':True,'replace_scope':True,'example_is_not_filter':True})])
    add('uq-narrow-example-followup',0,[(original,eng,'SEARCH',{'historical_quote':original,'example_is_not_filter':True}),('那就只看排行榜设计','leaderboard_design','SEARCH',{'requires_conversation_continuity':True,'min_positive_results':1,'replace_scope':True})])
    add('uq-broaden-followup',0,[('找排行榜系统设计题','leaderboard_design','SEARCH',{'min_positive_results':1}),('放宽到各种工程系统设计，不只排行榜',eng,'SEARCH',{'requires_conversation_continuity':True,'replace_scope':True})])
    add('uq-engineering-next',0,[(original,eng,'SEARCH',{'historical_quote':original}),('下一页',eng,'NEXT',{'requires_prior_results':True,'no_repeat_previous_results':True,'inherits_scope_from_turn':0,'requires_conversation_continuity':True,'min_positive_results':1})])
    for cid,msg,action in [('uq-engineering-all','把这个范围的所有题列出来','LIST'),('uq-engineering-count','这个范围一共有多少题？','COUNT')]:
        add(cid,0,[(original,eng,'SEARCH',{'historical_quote':original}),(msg,eng,action,{'requested_completeness':'all','requires_conversation_continuity':True,'inherits_scope_from_turn':0,'expected_certification':'NOT_CERTIFIED','certification_blocker':'未裁定边界和来源资格边界仍存在，不能把21个确定正例声称穷尽总数。','min_positive_results':0,'accepted_sort':['relevance','frequency']})])
    add('uq-engineering-bytedance',0,[('找字节跳动问过的工程系统设计题',eng,'SEARCH',{'required_filters':{'company':'字节'},'min_positive_results':3,'scope_filter_policy':'positive occurrences must have source-backed company evidence; do not infer task/code filters'})])
    add('uq-answer-vs-find',0,[('讲一下排行榜系统怎么设计','leaderboard_design','ANSWER',{'accepted_actions':['ANSWER'],'min_positive_results':0,'answer_must_not_be_presented_as_retrieved_interview':True})])
    redis_raw='给我redis相关场景题'
    add('uq-redis-original',2,[(redis_raw,redis,'SEARCH',{'historical_quote':redis_raw,'min_positive_results':5})])
    add('uq-redis-not-definitions',2,[('找Redis的故障、热点和容量场景题，不要只讲数据类型的八股',redis,'SEARCH',{'min_positive_results':5})])
    add('uq-agent-design',1,[('给我Agent相关设计场景题，比如企业知识库助手怎么搭',agent,'SEARCH',{'min_positive_results':5,'example_is_not_filter':True})])
    bmsg='字节跳动算法题，按照出现频率排名'
    add('uq-byte-frequency-original',3,[(bmsg,byte,'LIST',{'historical_quote':bmsg,'required_filters':{'company':'字节'},'accepted_sort':['frequency'],'requested_completeness':'page','min_positive_results':0,'expected_certification':'MISSING_ANNOTATION','semantic_scope_required':'ALGORITHM_TASK','forbidden_inferred_filters':['response_form','topic_l1','topic_l2','question_type']})])
    allmsg='我要的是所有的算法题，字节出现过的。'
    add('uq-byte-all-original',4,[(allmsg,byte,'LIST',{'historical_quote':allmsg,'required_filters':{'company':'字节'},'requested_completeness':'all','accepted_sort':[],'min_positive_results':0,'expected_certification':'MISSING_ANNOTATION','semantic_scope_required':'ALGORITHM_TASK'})])
    q='面经里没有字节跳动的算法题吗？'
    add('uq-byte-absence-original',5,[(q,byte,'LIST',{'historical_quote':q,'accepted_actions':['SEARCH','LIST'],'required_filters':{'company':'字节'},'min_positive_results':0,'expected_certification':'MISSING_ANNOTATION','must_check_corpus':True,'must_not_claim_no_matching_questions':True,'semantic_scope_required':'ALGORITHM_TASK'})])
    add('uq-byte-all-followup',3,[(bmsg,byte,'LIST',{'historical_quote':bmsg,'required_filters':{'company':'字节'},'accepted_sort':['frequency'],'min_positive_results':0,'expected_certification':'MISSING_ANNOTATION'}),(allmsg,byte,'LIST',{'history_index':4,'historical_quote':allmsg,'required_filters':{'company':'字节'},'requested_completeness':'all','accepted_sort':['frequency'],'min_positive_results':0,'requires_conversation_continuity':True,'inherits_scope_from_turn':0,'expected_certification':'MISSING_ANNOTATION','semantic_scope_required':'ALGORITHM_TASK'})])
    return cases

def build(dataset: Path = DATASET) -> dict:
    if (dataset/'freeze.json').exists():
        raise ValueError('frozen gold is immutable; use a new version directory')
    facts=read_json(SNAPSHOT/'facts.json')
    if digest(facts)!=FACTS_DIGEST: raise ValueError('unexpected source facts')
    now=datetime.now(timezone.utc).isoformat()
    dataset.mkdir(parents=True,exist_ok=True)
    shutil.copy2(SNAPSHOT/'facts.json',dataset/'facts.json')
    history=read_json(REPORT/'historical-user-messages.json')
    write_json(dataset/'history.json',history)
    docs={d['revision_id']:d for d in facts['documents']}
    texts={d['revision_id']:_text(SNAPSHOT/d['source_path']) for d in facts['documents']}
    for d in facts['documents']:
        target=dataset/d['source_path'];target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(SNAPSHOT/d['source_path'],target)
    variants=defaultdict(list)
    for i,q in enumerate(facts['questions'],1): variants[q['canonical_question_id']].append(i)
    context_indices=set()
    windows=defaultdict(list)
    for i in CONTEXT_INDICES:
        for j in variants[facts['questions'][i-1]['canonical_question_id']]:
            q=facts['questions'][j-1];rev=q['source_spans'][0]['revision_id']
            a=min(s['start_char'] for s in q['source_spans']);b=max(s['end_char'] for s in q['source_spans'])
            windows[rev].append((max(0,a-180),min(len(texts[rev]),b+180)))
            context_indices.add(j)
    questions=[]
    for i,q in enumerate(facts['questions'],1):
        rev=q['source_spans'][0]['revision_id'];d=docs[rev];text=texts[rev]
        a=min(s['start_char'] for s in q['source_spans']);b=max(s['end_char'] for s in q['source_spans'])
        context_read=any(x<=a and y>=b for x,y in windows[rev])
        if context_read: context_indices.add(i)
        verdict='ACCEPT' if i in POSITIVE else 'BOUNDARY' if i in BOUNDARY else 'REJECT'
        basis=POSITIVE.get(i,BOUNDARY.get(i,RAW_REJECT_BASIS))
        if verdict=='REJECT' and i in HARD_NEGATIVE:
            basis='回原文及相邻任务核对：'+basis.replace('这是原问级审核，不声明已读该条全部原始上下文。','本条有实际上下文证据；局部追问不能冒充独立主设计。')
        row={'id':f'uq-occ-{i:04d}','facts_index':i,'occurrence_id':q['id'],
             'canonical_question_id':q['canonical_question_id'],'interview_id':q['interview_id'],
             'source_revision_id':rev,'source_hash':d['source_hash'],
             'source_path':d['source_path'],'original_path':d['path'],
             'raw_question':q['raw_question'],'source_spans':q['source_spans'],
             'context_spans':[_span(text,max(0,a-180),min(len(text),b+180))] if context_read else [],
             'human_verified':False,'agent_verified':True,
             'review':_review(basis,now,'SOURCE_CONTEXT' if context_read else 'RAW_QUESTION_SCREEN'),
             'scope_judgments':{'engineering_system_design':{'grade':verdict,'basis':basis}},
             'canonical_variant_occurrence_ids':[facts['questions'][j-1]['id'] for j in variants[q['canonical_question_id']]]}
        questions.append(row)
        if i in FAMILIES: row['design_family_id']=FAMILIES[i]
        if i in DESIGN_COMPANIES:
            company=DESIGN_COMPANIES[i];row['reviewed_company']=company
            row['company_evidence']=[_span(text,text.index(company),text.index(company)+len(company))] if company else []
    # Source-only positives stay unmapped; source revision exists but no actual
    # occurrence intersects either question. They measure extraction coverage.
    defects=read_json(Path('data/gold/source-reviewed-retrieval-20261014-v2/source-review-defects.json'))
    for defect in defects:
        if defect['id'] not in {'ad-system','distributed-flow'}: continue
        e=defect['source_evidence'][0];d=next(d for d in docs.values() if d['source_hash']==e['source_hash'])
        questions.append({'id':'uq-source-only-'+defect['id'],'occurrence_id':None,
            'canonical_question_id':None,'source_revision_id':d['revision_id'],
            **{k:e[k] for k in ['source_path','source_hash','original_path','raw_question','source_spans','context_spans']},
            'human_verified':False,'agent_verified':True,'source_only':True,
            'review':_review(defect['basis'],now,'SOURCE_CONTEXT'),
            'scope_judgments':{'engineering_system_design':{'grade':'ACCEPT','basis':defect['basis']}}})
        questions[-1]['context_spans']=[_span(texts[d['revision_id']],s['start_char'],s['end_char']) for s in e['context_spans']]
    by_id={q['id']:q for q in questions}
    def gids(indices): return [f'uq-occ-{i:04d}' for i in sorted(set(indices))]
    scopes={}
    def scope(sid,positives,negatives,boundaries=(),census=False):
        pos=gids(positives);neg=gids(negatives);bound=gids(boundaries)
        source_only=[q['id'] for q in questions if q.get('source_only')] if sid=='engineering_system_design' else []
        s={'id':sid,'positive_gold_ids':pos,'negative_gold_ids':neg,
           'hard_negative_gold_ids':[x for x in gids(HARD_NEGATIVE) if x in neg] if census else neg,
           'boundary_gold_ids':bound,'reviewed_gold_ids':pos+neg+bound,
           'source-only_positive_gold_ids':source_only,'source_only_positive_gold_ids':source_only,
           'reference_completeness':'corpus_occurrence_census_with_boundaries' if census else 'source_reviewed_panel_only',
           'certification':'NOT_CERTIFIED' if census else 'PANEL_ONLY',
           'full_corpus_recall_certified':False,
           'unknown_result_policy':'review_required_not_automatic_negative',
           'human_verified':False,'agent_verified':True}
        for grade,ids in [('ACCEPT',pos),('REJECT',neg),('BOUNDARY',bound)]:
            for gid in ids:
                if sid not in by_id[gid]['scope_judgments']:
                    by_id[gid]['scope_judgments'][sid]={'grade':grade,
                        'basis':'Agent依据该条原问和已打开来源独立审核该查询范围；panel只涵盖列出的证据，不推断未审条目。'}
        scopes[sid]=s
    allindices=set(range(1,len(facts['questions'])+1))
    scope('engineering_system_design',POSITIVE,allindices-set(POSITIVE)-set(BOUNDARY),BOUNDARY,True)
    scopes['engineering_system_design']['family_by_gold_id']={f'uq-occ-{i:04d}':family for i,family in FAMILIES.items()}
    scope('leaderboard_design',[894],[1354,1375,2093,2624,2460,97],[319])
    scope('agent_application_design',AGENT_POSITIVE,list(POSITIVE)+[1354,2766,1746,1752])
    scope('redis_scenarios',REDIS_POSITIVE,[2419,1516,1953,2709,2572,1748,1275],[1473,1187,789,2325,171,2317])
    scope('incident_troubleshooting',INCIDENT_POSITIVE,list(POSITIVE)+[2766,2419])
    scope('bytedance_algorithm_tasks',BYTE_POSITIVE,list(POSITIVE)+[1746,1752,1560,2281])
    scopes['bytedance_algorithm_tasks'].update(certification='MISSING_ANNOTATION',
        missing_annotation='r293/a30 lacks later semantic task_scope annotations; raw-source positive panel proves existence but cannot certify all/filter frequency counts.')
    write_jsonl(dataset/'questions.jsonl',questions)
    write_json(dataset/'scopes.json',scopes)
    cases=_cases(history,scopes)
    for case in cases:
        for turn in case['turns']:
            e=turn['expected']
            if case['case_id']=='uq-engineering-no-agent':
                e['excluded_positive_gold_ids']=gids(AGENT_POSITIVE)
                e['hard_negative_gold_ids']=sorted(set(e['hard_negative_gold_ids'])|set(gids(AGENT_POSITIVE)))
                e['exclusion_basis']='Agent应用面板的12个设计题均非本scope主设计；岗位标题含Agent不构成排除条件。'
            if case['case_id']=='uq-engineering-bytedance':
                retained=[g for g in e['positive_gold_ids'] if by_id[g].get('reviewed_company')=='字节']
                excluded=[g for g in e['positive_gold_ids'] if by_id[g].get('reviewed_company') not in {'字节',None}]
                uncertain=[g for g in e['positive_gold_ids'] if by_id[g].get('reviewed_company') is None]
                e['positive_gold_ids']=retained;e['excluded_positive_gold_ids']=excluded
                e['case_boundary_gold_ids']=uncertain
                e['hard_negative_gold_ids']=sorted(set(e['hard_negative_gold_ids'])|set(excluded))
                e['filter_evidence']='reviewed_company + company_evidence raw source spans; unknown company is boundary, not negative'
    write_jsonl(dataset/'cases.jsonl',cases)
    census={'schema':'user_query_occurrence_census_v1','facts_sha256':digest(facts),
        'raw_questions_read':len(facts['questions']),'canonical_groups_examined':len(variants),
        'source_context_read_occurrence_ids':[facts['questions'][i-1]['id'] for i in sorted(context_indices)],
        'method':'逐条读取所有原问；潜在主设计和难邻题回到真实冻结Markdown上下文，逐条保留canonical所有variants。原问已明确排除者不虚称全文审核。',
        'grades':dict(Counter(q['scope_judgments']['engineering_system_design']['grade'] for q in questions if not q.get('source_only'))),
        'boundary_not_negative':True,'screen_order':'one-based frozen facts.questions order',
        'machine_labels_used_as_semantic_gold':False,'canonical_membership_implies_relevance':False}
    write_json(dataset/'census.json',census)
    manifest={'schema':'user_query_scope_gold_v1','dataset_id':dataset.name,'created_at':now,
        'human_verified':False,'agent_verified':True,'fresh_source':False,'release_eligible':False,
        'reviewer_kind':'agent','source_evidence_contract':'user_query_raw_source_spans_v1',
        'snapshot':facts['snapshot'],'source_facts_path':'facts.json','facts_sha256':digest(facts),
        'facts_file_sha256':digest((dataset/'facts.json').read_bytes()),
        'questions_path':'questions.jsonl','scopes_path':'scopes.json','cases_path':'cases.jsonl','history_path':'history.json',
        'case_count':len(cases),'turn_count':sum(len(c['turns']) for c in cases),
        'occurrence_count':len(facts['questions']),'canonical_count':len(variants),
        'source_only_count':2,'scope_certification':'ternary_census_and_explicit_panels_no_certified_full_corpus_recall',
        'authorization':{'thread_id':'01a138d3-ea4b-79c1-b545-e89dc5613a20',
            'quote':'你先把你能做的能完善的完善好，先就由你自己来审核，找gold set，但是一定要真实的去面经里找了'},
        'exposure':{'prior_seed':'engineering-design reference 23 positives/46 neighbors used only for discovery; reviewed independently; two old positives now boundary.',
            'sources':'old frozen r293 sources; no claim of 2026-10-15 production freshness',
            'queries':'six historical user messages plus marked derived variants; diagnostic, not fresh held-out generalization',
            'predictions_seen_before_freeze':False},
        'scope_definition':{'engineering_system_design':'独立业务系统、基础设施服务或闭合工程机制的构造任务；排行榜是例子而非隐式主题过滤。设计任务与coding_focus/response_form独立。概念、算法、既有项目说明、排障和局部追问不自动归主设计。',
            'filter_contract':'required_filters为字段到期望值的字典；forbidden_inferred_filters为禁止从自然语言擅自添加的字段名数组；accepted_sort为允许的真实服务sort值集合。'},
        'guide_path':'docs/annotation-guide.md','guide_sha256':digest(Path('docs/annotation-guide.md').read_bytes())}
    write_json(dataset/'manifest.json',manifest)
    lines=['# 用户查询与真实面经证据集','',
      f'冻结 r293 / i293 / a30；{len(cases)} cases / {manifest["turn_count"]} turns；2771 原问、2452 canonical 全量 screen。',
      'Agent 审核，human_verified=false。21 个确定主设计、22 个边界、2 个原文存在但漏抽。边界保留，未认证全库 Recall/COUNT。',
      '逐条原问 screen 与实际上下文审核分别标记；所有原始文件、facts、历史原话和字符跨度均可重验。',
      '同canonical仅作发现锚点；实际展示必须由同一occurrence及源revision/hash/span支持。', '', '## 确定的工程主设计题','']
    for i,basis in POSITIVE.items():
        q=questions[i-1]
        lines+= [f'- **{q["id"]}** {q["raw_question"]}',f'  - 来源：[{q["original_path"]}]({q["source_path"]})；occurrence `{q["occurrence_id"]}`；{basis}']
    lines+=['','## 边界（不能当负例，也不能宣称全库穷尽）','']
    for i,basis in BOUNDARY.items(): lines.append(f'- {questions[i-1]["id"]}：{questions[i-1]["raw_question"]} — {basis}')
    lines+=['','## 原文漏抽','']
    for q in questions:
        if q.get('source_only'): lines.append(f'- {q["id"]}：{q["raw_question"]}；{q["original_path"]}；保持无映射。')
    lines+=['','## 可复跑查询','']
    for c in cases:
        lines.append(f'- {c["case_id"]}')
        for t in c['turns']: lines.append(f'  - {t["turn_index"]}: {t["message"]} → {t["expected"]["accepted_actions"]} / {t["expected"]["scope_id"]} / {t["origin"]["kind"]}')
    (dataset/'README.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    write_json(dataset/'freeze.json',{'schema':'user_query_gold_freeze_v1','frozen_at':now,
        'files':{p.relative_to(dataset).as_posix():digest(p.read_bytes()) for p in sorted(dataset.rglob('*')) if p.is_file() and p.name!='freeze.json'}})
    return validate_dataset(dataset)

def validate_dataset(dataset: Path) -> dict:
    """Return manifest/cases/questions/scopes after strict local provenance checks."""
    dataset=Path(dataset)
    def require(test, message):
        if not test: raise ValueError(message)
    manifest=read_json(dataset/'manifest.json');freeze=read_json(dataset/'freeze.json')
    require(manifest.get('schema')=='user_query_scope_gold_v1','unsupported schema')
    require(manifest.get('human_verified') is False and manifest.get('agent_verified') is True,'review flags')
    require(manifest.get('release_eligible') is False,'diagnostic gold cannot approve releases')
    actual_files={p.relative_to(dataset).as_posix() for p in dataset.rglob('*') if p.is_file() and p.name!='freeze.json'}
    require(actual_files==set(freeze['files']),'freeze file inventory mismatch')
    for path,sha in freeze['files'].items(): require(digest(inside(dataset,path).read_bytes())==sha,f'freeze hash mismatch: {path}')
    facts=read_json(inside(dataset,manifest['source_facts_path']))
    require(digest(facts)==manifest['facts_sha256']==FACTS_DIGEST,'facts canonical digest mismatch')
    require(digest(inside(dataset,manifest['source_facts_path']).read_bytes())==manifest['facts_file_sha256'],'facts file digest mismatch')
    require(manifest['snapshot']==facts['snapshot'],'snapshot mismatch')
    questions=read_jsonl(inside(dataset,manifest['questions_path']))
    cases=read_jsonl(inside(dataset,manifest['cases_path']));scopes=read_json(inside(dataset,manifest['scopes_path']))
    history=read_json(inside(dataset,manifest['history_path']))
    docs={d['revision_id']:d for d in facts['documents']}; fq={q['id']:q for q in facts['questions']}
    texts={rev:_text(inside(dataset,d['source_path'])) for rev,d in docs.items()}
    for rev,d in docs.items(): require(digest(inside(dataset,d['source_path']).read_bytes())==d['source_hash'],'source bytes mismatch')
    byid={q['id']:q for q in questions}
    require(len(byid)==len(questions),'duplicate gold id')
    occurrence_rows=[q for q in questions if q['occurrence_id'] is not None]
    occ={q['occurrence_id']:q for q in occurrence_rows}
    require(set(occ)==set(fq) and len(occurrence_rows)==len(occ)==manifest['occurrence_count']==len(facts['questions']),'incomplete occurrence census')
    facts_order={q['id']:index for index,q in enumerate(facts['questions'],1)}
    variants=defaultdict(set)
    for q in fq.values():variants[q['canonical_question_id']].add(q['id'])
    require(len(variants)==manifest['canonical_count'],'canonical census count mismatch')
    for q in questions:
        require(q.get('human_verified') is False and q.get('agent_verified') is True,'question review flags')
        require(q['review'].get('reviewer_kind')=='agent' and bool(q['review'].get('basis')) and bool(q['review'].get('reviewed_at')),'missing review')
        require(q['review'].get('human_verified') is False and q['review'].get('agent_verified') is True,'nested review flags')
        rev=q['source_revision_id'];require(rev in docs,'unknown source revision')
        d=docs[rev];require(q['source_path']==d['source_path'] and q['source_hash']==d['source_hash'] and q['original_path']==d['path'],'source identity mismatch')
        for s in q['source_spans']+q.get('context_spans',[]):
            a=s['start_char'];b=s['end_char'];require(type(a) is int and type(b) is int and 0<=a<b<=len(texts[rev]),'invalid source span bounds')
            require(texts[rev][a:b]==s['quote'],'fabricated source span')
            require(s.get('revision_id',rev)==rev,'span revision mismatch')
            require(s['start_line']==texts[rev].count('\n',0,a)+1 and s['end_line']==texts[rev].count('\n',0,b-1)+1,'span line mismatch')
        for s in q.get('company_evidence',[]):
            require(texts[rev][s['start_char']:s['end_char']]==s['quote']==q['reviewed_company'],'company lacks source evidence')
        if q['occurrence_id'] is not None:
            f=fq[q['occurrence_id']]
            require(q['facts_index']==facts_order[q['occurrence_id']],'facts index mismatch')
            require(q['raw_question']==f['raw_question'] and q['canonical_question_id']==f['canonical_question_id'],'facts identity mismatch')
            require(q['source_spans']==f['source_spans'],'occurrence spans mismatch')
            require(set(q['canonical_variant_occurrence_ids'])==variants[f['canonical_question_id']],'missing canonical variants')
        else:
            require(q.get('source_only') is True and q['canonical_question_id'] is None,'source-only forced mapping')
            require(q['raw_question']==''.join(s['quote'] for s in q['source_spans']),'source-only raw question mismatch')
            require(not any(any(s['revision_id']==rev and max(s['start_char'],g['start_char'])<min(s['end_char'],g['end_char']) for s in f['source_spans'] for g in q['source_spans']) for f in fq.values()),'source-only overlaps an extracted occurrence')
    require(len([q for q in questions if q.get('source_only')])==manifest['source_only_count'],'source-only count mismatch')
    for sid,s in scopes.items():
        sets={grade:set(s[field]) for grade,field in [('ACCEPT','positive_gold_ids'),('REJECT','negative_gold_ids'),('BOUNDARY','boundary_gold_ids')]}
        require(not any(sets[a]&sets[b] for a,b in [('ACCEPT','REJECT'),('ACCEPT','BOUNDARY'),('REJECT','BOUNDARY')]),'scope grades overlap')
        require(set(s['reviewed_gold_ids'])==set.union(*sets.values()),'scope reviewed set mismatch')
        require(set(s['hard_negative_gold_ids'])<=sets['REJECT'],'hard negative not reviewed reject')
        require(s['full_corpus_recall_certified'] is False,'unjustified full corpus recall claim')
        for grade,ids in sets.items():
            for gid in ids:
                require(gid in byid and byid[gid]['scope_judgments'][sid]['grade']==grade,'grade/evidence mismatch')
                require(bool(byid[gid]['scope_judgments'][sid].get('basis')),'scope review basis missing')
        if sid=='engineering_system_design':require(set(s['reviewed_gold_ids'])=={q['id'] for q in questions if not q.get('source_only')},'scope census incomplete')
        source_pos=s['source-only_positive_gold_ids'];source_pos_alias=s['source_only_positive_gold_ids']
        source_bound=s.get('source_only_boundary_gold_ids',[])
        require(all(type(ids) is list and len(ids)==len(set(ids)) for ids in [source_pos,source_pos_alias,source_bound]),'source-only list malformed/duplicate')
        require(set(source_pos)==set(source_pos_alias),'source-only positive aliases disagree')
        require(not set(source_pos)&set(source_bound),'source-only grades overlap')
        expected_source_only={q['id'] for q in questions if q.get('source_only') and sid in q['scope_judgments']}
        require(set(source_pos)|set(source_bound)==expected_source_only,'source-only scope coverage incomplete')
        require(not expected_source_only&set.union(*sets.values()),'source-only mixed into occurrence grades')
        for grade,ids in [('ACCEPT',source_pos),('BOUNDARY',source_bound)]:
            for gid in ids:
                require(gid in byid and byid[gid].get('source_only') and byid[gid]['scope_judgments'][sid]['grade']==grade,'source-only grade/evidence mismatch')
                require(bool(byid[gid]['scope_judgments'][sid].get('basis')),'source-only scope review basis missing')
    require(len(cases)==manifest['case_count'] and sum(len(c['turns']) for c in cases)==manifest['turn_count'],'case counts mismatch')
    require(len({c['case_id'] for c in cases})==len(cases),'duplicate case id')
    for c in cases:
        require(c['human_verified'] is False and c['agent_verified'] is True,'case review flags')
        for i,t in enumerate(c['turns']):
            require(t['case_id']==c['case_id'] and t['turn_index']==i,'unstable turn identity')
            o=t['origin'];require(o['kind'] in {'derived','historical_user_substring'},'untracked query origin')
            h=history['entries'][int(o['history_id'].removeprefix('history-'))]
            require(h['thread_id']==o['thread_id'] and h['turn_id']==o['turn_id'],'history reference mismatch')
            m=next((m for m in h['messages'] if m['message_id']==o['message_id']),None);require(m is not None,'unknown history message')
            if 'quote' in o:require(m['text'][o['start_char']:o['end_char']]==o['quote'],'fabricated historical quote')
            if o['kind']=='historical_user_substring':require(t['message']==o['quote'],'original query differs from history')
            else:require(bool(o.get('derivation')),'derived query missing explanation')
            e=t['expected'];require(e['scope_id'] in scopes,'unknown scope')
            s=scopes[e['scope_id']]
            require(bool(e['accepted_actions']) and set(e['accepted_actions'])<= {'SEARCH','LIST','CLARIFY','ANSWER','COUNT','NEXT'},'invalid actions')
            require(type(e['required_filters']) is dict and type(e['forbidden_inferred_filters']) is list,'filter contract malformed')
            require(not set(e['required_filters'])&set(e['forbidden_inferred_filters']),'required/forbidden filters contradict')
            require(e['require_source_support'] is True and e['max_negative_results']==0,'witness/precision policy weakened')
            require(type(e['min_positive_results']) is int and 0<=e['min_positive_results']<=len(s['positive_gold_ids']),'impossible result threshold')
            require(e.get('min_distinct_families',0)<=len(set(s.get('family_by_gold_id',{}).values())) or e.get('min_distinct_families',0)<=1,'impossible family threshold')
            expected_pos=set(s['positive_gold_ids']);extra_neg=set(e.get('excluded_positive_gold_ids',[]))
            if e['scope_id']=='engineering_system_design' and e['required_filters'].get('company')=='字节':
                expected_pos={gid for gid in expected_pos if byid[gid].get('reviewed_company')=='字节'}
                require(extra_neg=={gid for gid in s['positive_gold_ids'] if byid[gid].get('reviewed_company') not in {'字节',None}},'company exclusion evidence mismatch')
                require(set(e.get('case_boundary_gold_ids',[]))=={gid for gid in s['positive_gold_ids'] if byid[gid].get('reviewed_company') is None},'unknown company silently rejected')
            else:require(extra_neg<=set(s['negative_gold_ids']),'excluded positive lacks independent scope rejection')
            require(set(e['positive_gold_ids'])==expected_pos and set(e['hard_negative_gold_ids'])==set(s['hard_negative_gold_ids'])|extra_neg,'turn scope evidence mismatch')
            require(e['min_positive_results']<=len(expected_pos),'filtered positive threshold impossible')
            if e['requested_completeness']=='all' or 'COUNT' in e['accepted_actions']:
                require(e['expected_certification'] in {'NOT_CERTIFIED','MISSING_ANNOTATION'},'uncertified census count passed')
            if s['certification']=='MISSING_ANNOTATION':require(e['expected_certification']=='MISSING_ANNOTATION','missing annotation downgraded to pass')
            elif set(e['accepted_actions'])&{'SEARCH','LIST','NEXT'} and e['requested_completeness']=='page':
                require(e['min_positive_results']>0,'page retrieval has no positive evidence requirement')
            if 'NEXT' in e['accepted_actions']:
                require(i>0 and e.get('no_repeat_previous_results') is True and e.get('requires_prior_results') is True and e.get('requires_conversation_continuity') is True,'NEXT continuity contract missing')
                require(0<=e.get('inherits_scope_from_turn',i)<i and c['turns'][e['inherits_scope_from_turn']]['expected']['scope_id']==e['scope_id'],'NEXT scope changed')
    return {'manifest':manifest,'cases':cases,'questions':questions,'scopes':scopes}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--dataset',type=Path,default=DATASET);parser.add_argument('--build',action='store_true')
    args=parser.parse_args();out=build(args.dataset) if args.build else validate_dataset(args.dataset)
    print(json.dumps({'valid':True,'cases':len(out['cases']),'turns':out['manifest']['turn_count'],'questions':len(out['questions']),'scopes':{k:{'positive':len(v['positive_gold_ids']),'negative':len(v['negative_gold_ids']),'boundary':len(v['boundary_gold_ids']),'certification':v['certification']} for k,v in out['scopes'].items()}},ensure_ascii=False,indent=2))

if __name__=='__main__': main()
