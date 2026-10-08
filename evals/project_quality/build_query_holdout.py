"""Freeze new compositional query scenarios and exact SQL expectations before inference."""
import argparse
from datetime import datetime, timezone
from pathlib import Path

from eval.common import digest, read_json, write_json, write_jsonl
from eval.oracle import frequency_list
from eval.validate_gold import load_dataset, require, validate_gold
from evals.project_quality.build_gold import AUTHORIZATION, FACTS_HASH


def listing(facts, message, filters, count=None, size=None, **extra):
    size = size or min(count or extra.get("default_page_size", 20), 100)
    request = {**filters, "top_n": count, "page_size": size}
    if filters.get("response_form") or filters.get("coding_focus"):
        request["annotation_status"] = "KNOWN"
    expected = frequency_list(facts, request)
    plan = {"action": "LIST", "sort": "frequency", "top_n": count, "page_size": size,
            **{"filters." + k: v for k, v in filters.items()}}
    return {"message": message, "expected_plan": plan, "required_tools": ["list_questions"],
        "forbidden_tools": ["record_review"], **extra, "assertions": [
            {"id": "ordered_source_ids", "path": "result.rows.*.canonical_question_id", "op": "eq", "value": expected["ids"][:size]},
            {"id": "frequency_counts", "path": "result.rows.*.occurrence_count", "op": "eq", "value": expected["counts"][:size]},
            {"id": "no_write", "path": "result.write_event_delta", "op": "eq", "value": 0, "dimension": "risk"}]}


def build(snapshot, output):
    require(not output.exists(), "GOLD_VERSION_ALREADY_EXISTS")
    facts = read_json(snapshot / "facts.json")
    require(digest(facts) == FACTS_HASH, "SNAPSHOT_CHANGED")
    cases, stamp = [], datetime.now(timezone.utc).isoformat()
    def add(name, turns, family):
        cases.append({"id": "query-holdout-" + name, "group_id": "query-holdout-" + family,
            "split": "test", "human_verified": False, "agent_verified": True,
            "tags": ["query_compositional_holdout", family], "turns": turns,
            "review": {"reviewer": "Codex", "reviewer_kind": "agent", "reviewed_at": stamp,
                "guide_version": "project_quality_agent_v2",
                "basis": "New authored scope/state requirements; ordered IDs and occurrence counts independently derived from frozen source facts before prediction."}})
    definitions = [
        ("db-index", "数据库里的MySQL索引分类取频次最高的11题", {"topic_l1":"数据库","topic_l2":"MySQL索引"},11),
        ("redis-lock", "Redis大分类下只要分布式锁小类，按被问次数取13题", {"topic_l1":"Redis","topic_l2":"分布式锁"},13),
        ("java-jvm", "Java里的JVM这个小类，先给我频率最高的8道", {"topic_l1":"Java","topic_l2":"JVM"},8),
        ("ai-mcp", "AI分类的MCP小类按频次取4道", {"topic_l1":"AI","topic_l2":"MCP"},4),
        ("ai-agent", "AI里的Agent小类取19道最高频题", {"topic_l1":"AI","topic_l2":"Agent"},19),
        ("spring-aop", "Spring分类的AOP小类，只取频次最高的6道", {"topic_l1":"Spring","topic_l2":"AOP"},6),
        ("db-verbal", "数据库分类中仅口述回答的题，按频次给16道", {"topic_l1":"数据库","response_form":"VERBAL"},16),
        ("redis-verbal", "Redis分类里只要口述形式，先取频次最高的9道", {"topic_l1":"Redis","response_form":"VERBAL"},9),
        ("java-code", "Java大分类里只要工程编程代码题，按频次取14道", {"topic_l1":"Java","coding_focus":"ENGINEERING","response_form":"CODE"},14),
        ("alg-code", "明确让候选人写代码的算法求解题，按频次取18道", {"coding_focus":"ALGORITHM","response_form":"CODE"},18),
        ("backend", "岗位族只选后端，按出现次数给17道题", {"job_family":"BACKEND"},17),
        ("backend-java", "只看后端岗位族且使用Java语言的面经题，按频次给22道", {"job_family":"BACKEND","language":"JAVA"},22),
        ("java-lang", "面经使用的语言限定Java，不限定主题，按出现次数取21题", {"language":"JAVA"},21),
        ("job-first", "后端岗位族的一面题目按频次取23道", {"job_family":"BACKEND","round":"FIRST"},23),
        ("db-second", "二面出现的数据库分类题，按频次给27道", {"round":"SECOND","topic_l1":"数据库"},27),
        ("ai-third", "三面的AI大分类题按出现次数取15道", {"round":"THIRD","topic_l1":"AI"},15),
        ("sql-verbal", "只要写SQL这种作答形式的题，按出现次数取24道", {"response_form":"SQL"},24),
        ("july-publish", "按发布日期筛2026年7月1日到7月31日含当天的题，按频次取25道", {"date_basis":"PUBLISH","start_date":"2026-07-01","end_date":"2026-08-01"},25),
        ("aug-interview", "只按实际面试日期，筛2026年8月的题，按频次取26道", {"date_basis":"INTERVIEW","start_date":"2026-08-01","end_date":"2026-09-01"},26),
        ("publish-redis", "发布日期在2026年6月的Redis题，按频次取28道", {"topic_l1":"Redis","date_basis":"PUBLISH","start_date":"2026-06-01","end_date":"2026-07-01"},28),
        ("hr-round", "HR轮出现的面试题按次数取29道", {"round":"HR"},29),
        ("java-second", "Java大分类且二面出现的题，按频次取33道", {"topic_l1":"Java","round":"SECOND"},33),
        ("eng-verbal", "工程构造任务中仅口头方案的题，按频次给31道", {"coding_focus":"ENGINEERING","response_form":"VERBAL"},31),
        ("alg-verbal", "算法求解任务里只要求口头思路的题，按频次给34道", {"coding_focus":"ALGORITHM","response_form":"VERBAL"},34),
        ("network", "计算机基础大分类的网络小类按被问次数取36道", {"topic_l1":"计算机基础","topic_l2":"网络"},36),
    ]
    for key, message, filters, count in definitions:
        add(key, [listing(facts,message,filters,count)], "cross_filters")
    for i, (ui, message, filters, size) in enumerate([
        ({"topic_l1":"Redis"},"按被问次数给我清单，页大小沿用界面的设置",{"topic_l1":"Redis"},7),
        ({"round":"SECOND"},"范围沿用界面，只按被问次数取10道",{"round":"SECOND"},10),
        ({"response_form":"SQL"},"当前筛选不变，按频次取12题",{"response_form":"SQL"},12),
        ({"job_family":"BACKEND"},"按频次展示当前界面范围，不限题目总数",{"job_family":"BACKEND"},11),
        ({"topic_l1":"AI","topic_l2":"Agent"},"按频次给当前范围的前5道",{"topic_l1":"AI","topic_l2":"Agent"},5),
    ]):
        count = [None,10,12,None,5][i]
        add(f"ui-{i}",[listing(facts,message,filters,count,size,explicit_filters=ui,default_page_size=size)],"explicit_ui")
    for i, (initial, filters, later, updates, count) in enumerate([
        ("数据库分类的MySQL事务小类按频次取9题",{"topic_l1":"数据库","topic_l2":"MySQL事务"},"仅小类改成MySQL锁，其他保持",{"topic_l2":"MySQL锁"},9),
        ("Java语言的后端岗位族题按频次取8题",{"job_family":"BACKEND","language":"JAVA"},"取消语言限制，其余保持",{"language":None},8),
        ("二面数据库分类题按频次取7道",{"round":"SECOND","topic_l1":"数据库"},"取消轮次限制，其他不动",{"round":None},7),
        ("AI分类的Agent小类按频次取6道",{"topic_l1":"AI","topic_l2":"Agent"},"只取消小类限定，继续原榜单",{"topic_l2":None},6),
        ("Redis的口述题按频次取5道",{"topic_l1":"Redis","response_form":"VERBAL"},"只取消回答形式限定，其他保留",{"response_form":None},5),
        ("工程代码题按频次取11道",{"coding_focus":"ENGINEERING","response_form":"CODE"},"题目焦点不变，这次不限定是否写代码",{"response_form":None},11),
        ("算法代码题按频次取12道",{"coding_focus":"ALGORITHM","response_form":"CODE"},"保持写代码要求，任务焦点改成工程实现",{"coding_focus":"ENGINEERING"},12),
        ("后端岗位族题按频次取13道",{"job_family":"BACKEND"},"范围不动，改为只取前4道",{},4),
        ("Redis分类题按频次取14道",{"topic_l1":"Redis"},"重新开一个列表，只看数据库分类且不限总数",{"topic_l1":"数据库"},None),
        ("Java里的JVM小类按频次取15道",{"topic_l1":"Java","topic_l2":"JVM"},"重开列表，只看AI大分类，不限小类和总数",{"topic_l1":"AI","topic_l2":None},None),
    ]):
        initial_count = 13 if i==7 else 14 if i==8 else 15 if i==9 else count
        final_filters = updates if i in {8,9} else {**filters,**updates}
        add(f"state-{i}",[listing(facts,initial,filters,initial_count),listing(facts,later,final_filters,count)],"field_clear_and_inherit")
    for i, (topic, count, size) in enumerate([("Redis",19,6),("Java",25,8),("AI",31,9),("数据库",22,7),("计算机基础",17,5)]):
        scope={"topic_l1":topic};ids=frequency_list(facts,scope|{"top_n":count})["ids"]
        turn={"message":"继续展示下一页，其他条件不变","expected_plan":{"action":"NEXT"},"required_tools":["list_questions"],
            "forbidden_tools":["record_review"],"assertions":[
                {"id":"continuation_ids","path":"result.rows.*.canonical_question_id","op":"eq","value":ids[size:size*2]},
                {"id":"no_write","path":"result.write_event_delta","op":"eq","value":0,"dimension":"risk"}]}
        add(f"page-{i}",[listing(facts,f"{topic}分类按频次取{count}道，每页{size}道",scope,count,size),turn],"new_page_boundaries")
    for i, topic in enumerate(["Java","Redis","AI","数据库","计算机基础"]):
        scope={"topic_l1":topic};ids=frequency_list(facts,scope|{"top_n":4})["ids"]
        turn={"message":"请展开这页第1题和第3题的原文与来源","expected_plan":{"action":"DETAILS","question_ids":[ids[0],ids[2]]},
            "required_tools":["get_question_details"],"forbidden_tools":["record_review"],"assertions":[
                {"id":"selected_ids","path":"result.rows.*.canonical_question_id","op":"eq","value":[ids[0],ids[2]]},
                {"id":"no_write","path":"result.write_event_delta","op":"eq","value":0,"dimension":"risk"}]}
        add(f"references-{i}",[listing(facts,f"{topic}分类按频次给我前4道题",scope,4),turn],"multi_identity_reference")
    require(len(cases)==50,"HOLDOUT_SCENARIO_COUNT")
    output.mkdir(parents=True)
    write_jsonl(output/"routing.jsonl",cases)
    manifest={"version":output.name,"status":"frozen","kind":"corpus","snapshot":facts["snapshot"],
        "routing":"routing.jsonl","review_authorization":AUTHORIZATION,"facts_sha256":FACTS_HASH,
        "annotation_guide":"evals/project_quality/review_protocol_v2.json","frozen_at":stamp,
        "review_lifecycle":"New combinations, exact counts, UI scope and multi-turn state authored before predictions. Remain held out until the planned baseline run.",
        "limitations":"Same product contract/corpus, authored scenarios rather than unseen real traffic. SQL truth uses current published machine task labels; semantic task-label quality is tested separately."}
    write_json(output/"manifest.json",manifest)
    write_json(output/"freeze.json",{"dataset_sha256":digest(load_dataset(output)),
        "file_hashes":{p.name:digest(p.read_bytes()) for p in output.iterdir() if p.is_file() and p.name!="freeze.json"}})
    validate_gold(output,"routing",review_policy="delegated_agent")
    print({"dataset":str(output),"scenarios":len(cases),"turns":sum(len(r['turns']) for r in cases)})


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--snapshot",type=Path,required=True);parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args();build(args.snapshot,args.output)
