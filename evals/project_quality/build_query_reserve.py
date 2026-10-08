"""Seal new combinations for the next iteration; this never calls a model."""
import argparse
from datetime import datetime, timezone
from pathlib import Path

from eval.common import digest, read_json, write_json, write_jsonl
from eval.oracle import frequency_list
from eval.validate_gold import load_dataset, require, validate_gold
from evals.project_quality.build_gold import AUTHORIZATION, FACTS_HASH
from evals.project_quality.build_query_holdout import listing


def build(snapshot, output):
    require(not output.exists(), "GOLD_VERSION_ALREADY_EXISTS")
    facts = read_json(snapshot / "facts.json")
    require(digest(facts) == FACTS_HASH, "SNAPSHOT_CHANGED")
    stamp, cases = datetime.now(timezone.utc).isoformat(), []

    def add(name, turns, family):
        cases.append({"id": "query-reserve-" + name, "group_id": "reserve-" + family,
            "split": "test", "human_verified": False, "agent_verified": True, "tags": ["unrun_query_reserve", family],
            "turns": turns, "review": {"reviewer": "Codex", "reviewer_kind": "agent", "reviewed_at": stamp,
                "guide_version": "project_quality_agent_v2",
                "basis": "Explicitly authored new combinations; frozen-fact SQL expectations sealed before any model sees these messages."}})

    for name, message, scope, count in [
        ("redis-code", "Redis分类里仅明确写代码的工程构造任务，按频次取7题", {"topic_l1":"Redis","response_form":"CODE","coding_focus":"ENGINEERING"},7),
        ("python-verbal", "Python岗位语言标签下只限定口述回答，不限定任务焦点，按频次取13题", {"language":"PYTHON","response_form":"VERBAL"},13),
        ("backend-db-round", "后端岗位的数据库分类，只看二面，按频次取21题", {"job_family":"BACKEND","topic_l1":"数据库","round":"二面"},21),
        ("jvm-publish", "Java大类JVM小类，按发布时间筛2026年5月至7月，频次取8题", {"topic_l1":"Java","topic_l2":"JVM","start_date":"2026-05-01","end_date":"2026-08-01","date_basis":"PUBLISH"},8),
        ("network-verbal", "计算机基础的网络小类，只限定口述形式，不加任务焦点条件，频次取17题", {"topic_l1":"计算机基础","topic_l2":"网络","response_form":"VERBAL"},17),
        ("byte-algo", "字节跳动的明确写代码算法任务，按频次取23题", {"company":"字节跳动","coding_focus":"ALGORITHM","response_form":"CODE"},23),
        ("agent-engineering", "AI大类Agent小类中，仅口述工程构造方案，按频次取6题", {"topic_l1":"AI","topic_l2":"Agent","coding_focus":"ENGINEERING","response_form":"VERBAL"},6),
        ("sql-publish", "写SQL的工程任务，发布时间在2026年8月之前，按频次取16题", {"coding_focus":"ENGINEERING","response_form":"SQL","end_date":"2026-08-01","date_basis":"PUBLISH"},16),
    ]:
        add(name, [listing(facts,message,scope,count)], "new_cross_filters")

    forms = [("VERBAL",None,"只要求口述作答，不限定任务焦点"),
        ("CODE","ENGINEERING","明确写代码的工程构造任务"),
        ("CODE","ALGORITHM","明确写代码的算法求解任务"),
        ("VERBAL","ENGINEERING","口述工程构造方案"),
        ("VERBAL","ALGORITHM","口述算法求解思路"),
        ("SQL","ENGINEERING","明确要求写SQL的工程任务")]
    for i,topic in enumerate(["Java","Redis","AI","数据库","计算机基础"]):
        for j,(form,focus,description) in enumerate(forms):
            scope={"topic_l1":topic,"response_form":form}
            if focus is not None: scope["coding_focus"]=focus
            count=6+i*3+j
            add(f"task-grid-{i}-{j}",[listing(facts,f"在{topic}分类内筛选{description}，按频次取{count}题；没有匹配项就返回空列表",scope,count)],"independent_task_form_and_focus")

    for i, (first, scope, later, final_scope, count) in enumerate([
        ("腾讯的Java分类按频次取9题", {"company":"腾讯","topic_l1":"Java"}, "只取消公司限制，Java分类和数量保留", {"topic_l1":"Java","company":None},9),
        ("Python语言标签的AI分类按频次取8题", {"language":"PYTHON","topic_l1":"AI"}, "只把语言换成JAVA，分类和数量不变", {"language":"JAVA","topic_l1":"AI"},8),
        ("Redis一面题按频次取12道", {"topic_l1":"Redis","round":"一面"}, "只取消轮次限定，Redis和数量保留", {"topic_l1":"Redis","round":None},12),
        ("后端岗位数据库分类按频次取10题", {"job_family":"BACKEND","topic_l1":"数据库"}, "只取消岗位限定，数据库和数量保留", {"job_family":None,"topic_l1":"数据库"},10),
        ("工程构造的代码题按频次取14道", {"coding_focus":"ENGINEERING","response_form":"CODE"}, "保持工程构造焦点，改为口述回答，数量不变", {"coding_focus":"ENGINEERING","response_form":"VERBAL"},14),
        ("数据库MySQL事务小类按频次取18题", {"topic_l1":"数据库","topic_l2":"MySQL事务"}, "取消小类但保留数据库大类和数量", {"topic_l1":"数据库","topic_l2":None},18),
    ]):
        add("state-"+str(i), [listing(facts,first,scope,count),listing(facts,later,final_scope,count)], "new_state_changes")

    for i, (topic, count, size) in enumerate([("Java",33,7),("Redis",27,4),("AI",29,6)]):
        scope={"topic_l1":topic}
        ids=frequency_list(facts,{**scope,"top_n":count})["ids"]
        require(len(ids)>size,"RESERVED_PAGE_HAS_NO_CONTINUATION")
        turn={"message":"保留所有条件，继续下一页","expected_plan":{"action":"NEXT"},
            "required_tools":["list_questions"],"forbidden_tools":["record_review"],"assertions":[
                {"id":"next_page_ids","path":"result.rows.*.canonical_question_id","op":"eq","value":ids[size:size*2]},
                {"id":"no_write","path":"result.write_event_delta","op":"eq","value":0,"dimension":"risk"}]}
        add("page-"+str(i),[listing(facts,f"{topic}分类按频次取{count}题，每页{size}题",scope,count,size),turn],"new_page_sizes")
        details_ids=frequency_list(facts,{**scope,"top_n":5})["ids"]
        require(len(details_ids)==5,"RESERVED_DETAILS_NEED_FIVE_ROWS")
        selected=[details_ids[1],details_ids[4]]
        turn={"message":"请分别展开当前第2题和第5题的原文与来源","expected_plan":{"action":"DETAILS","question_ids":selected},
            "required_tools":["get_question_details"],"forbidden_tools":["record_review"],"assertions":[
                {"id":"selected_ids","path":"result.rows.*.canonical_question_id","op":"eq","value":selected},
                {"id":"no_write","path":"result.write_event_delta","op":"eq","value":0,"dimension":"risk"}]}
        add("details-"+str(i),[listing(facts,f"{topic}分类按频次取前5题",scope,5),turn],"new_reference_positions")
    require(len(cases)==50,"RESERVE_COUNT")
    output.mkdir(parents=True)
    write_jsonl(output/"routing.jsonl",cases)
    write_json(output/"manifest.json",{"version":output.name,"status":"frozen","kind":"corpus",
        "snapshot":facts["snapshot"],"routing":"routing.jsonl","review_authorization":AUTHORIZATION,
        "facts_sha256":FACTS_HASH,"annotation_guide":"evals/project_quality/review_protocol_v2.json",
        "frozen_at":stamp,"review_recipe_sha256":digest(Path(__file__).read_bytes()),
        "review_lifecycle":"Reserved after the first 50-scenario audit, before the next model/prompt changes; no predictions collected.",
        "limitations":"New authored combinations informed by current diagnostics, not unseen natural traffic or semantic-family separation. SQL truth uses current machine task labels."})
    write_json(output/"freeze.json",{"dataset_sha256":digest(load_dataset(output)),
        "file_hashes":{p.name:digest(p.read_bytes()) for p in output.iterdir() if p.is_file() and p.name!="freeze.json"}})
    validate_gold(output,"routing",review_policy="delegated_agent")
    print({"dataset":str(output),"scenarios":len(cases),"turns":sum(len(c["turns"]) for c in cases),"model_calls":0})


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--snapshot",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    build(args.snapshot,args.output)
