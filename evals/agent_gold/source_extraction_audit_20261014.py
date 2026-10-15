"""Recorded source adjudication and post-freeze historical-output audit.

Adjudication is explicit and versioned. It does not use published labels as
reference decisions. Unnamed exercises without a recorded task are omitted.
"""
from __future__ import annotations

import argparse
import copy
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from eval.common import digest, inside, read_json, read_jsonl, write_json, write_jsonl
from eval.validate_gold import load_dataset, require, validate_gold
from evals.agent_gold.source_extraction_20261014 import review, span_at
from interview_intelligence.ingestion.snapshot import decode_source

CORRECTIONS = [
 {"sample":"f490e8859cbb","gold_id":"g-new-f490e8859cbb-005","line":25,"quote":"线程之间怎么实现安全通信？再从Java内存模型的角度解释一下",
  "remove":"g-new-f490e8859cbb-006","topic_l1":"Java","topic_l2":"并发","question_type":"PRINCIPLE",
  "basis":"从JMM角度解释安全通信是同一要求的解释视角，依照指南同一任务附带理由/改述保留一题。v1误拆两题，源复核合并。"},
 {"sample":"6d2b25ee4cac","gold_id":"g-new-6d2b25ee4cac-010","line":19,"quote":"aop使用了什么设计模式，底层怎么实现的",
  "remove":"g-new-6d2b25ee4cac-011","topic_l1":"Spring","topic_l2":"AOP","question_type":"PRINCIPLE",
  "basis":"设计模式与底层实现是同一AOP原理问题的两个描述角度，源复核按同一任务合并，避免多拆题增大分母。"},
 {"sample":"dee8c70df3c5","gold_id":"g-new-dee8c70df3c5-001","line":65,"quote":"先简单介绍一下自己吧。",
  "basis":"逐字问题span停止在介绍要求后；v1误把作者附带的回答‘我是中南大学计算机专业的...’包含进raw。"},
 {"sample":"e03e96433274","remove":"g-new-e03e96433274-004",
  "basis":"原文只说‘算法是一道原创题’，没有任何题干/求解目标。保留文档缺失题干审计记录，不能当作一条可独立评分的gold问题。"},
 {"sample":"b32d199485c1","remove":"g-new-b32d199485c1-002",
  "basis":"原文只称‘做了一个数据库的小题目’，没有具体要求。不能用这一概述构造题目、题型或任务标签；保留未记题干缺口。"},
]

# A second reviewer challenged whether "怎么/设计/实现" alone proves a new
# engineering task. All 20 v1 VERBAL/ENGINEERING rows were reread with the
# complete original source; published labels/scores are not decision inputs.
BOUNDARY_REREVIEW = {
 "g-new-f490e8859cbb-003":("VERBAL","ENGINEERING","明确‘设计一个功能（比如线程池启动）’并提出并发安全、按需加载约束，是构造新功能，基础口头环节无手写要求。"),
 "g-new-f490e8859cbb-004":("VERBAL","ENGINEERING","紧承上题设计新启动功能，要求给出懒加载或其他实现方法；复用上题对象，属于口头构造同类工程功能。"),
 "g-new-f490e8859cbb-007":("VERBAL","ENGINEERING","要求实现多线程规定顺序交替打印这一具体功能，区别于上题现有JMM通信原理；手撕章节另列，无代码要求。"),
 "g-new-9f6069fa67a0-011":("VERBAL","ENGINEERING","明示‘设计Agent架构支持工具可插拔扩展’，给出新框架能力和扩展约束；原理为该构造任务的解释，保持口头工程。"),
 "g-new-6d2b25ee4cac-018":("VERBAL","ENGINEERING","未指已有项目，明示用Redis实现滑动窗口，要求一个确定组件/功能的实现方案；没有手撕，判口头工程，不因为‘算法’词判算法。"),
 "g-new-d3e713a6755b-036":("VERBAL","NONE","承接生产100条/消费99条的排障、后接现成Java指标系统询问，只问如何监控流量与稳定性，没有构造新监控组件的要求；同意第二审核者，改为NONE。"),
 "g-new-2ae881c215b3-007":("VERBAL","ENGINEERING","明示Linux命令读取文件第四列userid并返回top10，要求给实际命令完成具体操作；无手写代码环节，不推断算法求解。"),
 "g-new-89a73496fa57-016":("VERBAL","ENGINEERING","二面独立‘如果让你设计一个Agent开发框架’并规范跨部门使用，明确新框架构造任务；长聊说明口头方案，保留工程。"),
 "g-new-a1f730f7b65f-018":("VERBAL","ENGINEERING","在现有秒杀项目上假设新做智能客服，要求设计新增功能；没有手写要求，智能客服体系构造是工程任务。"),
 "g-new-bc95d875cdf7-026":("VERBAL","ENGINEERING","前面问已做的端侧监控，此题切为‘如果让你做监控或对账’，明确新承担的检测/对账功能方案；具体要发现上文多端一致性问题。"),
 "g-new-6ea21d1f2cd8-009":("VERBAL","ENGINEERING","明示设计一个新高速cache，给高吞吐、低延时、可扩展约束，口头缓存组件构造；旁边算法题是独立题，不能串入。"),
 "g-new-e03e96433274-002":("VERBAL","UNKNOWN","原文只有‘服务请求多服务响应汇总场景’，全篇称场景题，支持口头作答，却没有记录要求新实现汇总器还是解释已有流程/故障，焦点保留UNKNOWN。"),
 "g-new-d8c9c83c36fd-002":("VERBAL","ENGINEERING","明示‘如果让你设计’差旅助手，给用户对话和安排行程的业务目标，是新增Agent应用构造；无手写要求。"),
 "g-new-31bee07b8745-009":("VERBAL","ENGINEERING","harness二面提出需求改动后的业务知识库动态更新，并明确‘你怎么设计’，要求这一新增自动更新功能的口头方案。"),
 "g-new-22f55936b83d-002":("VERBAL","UNKNOWN","原文仅‘分布式id的设计’，前题实习、后题原理；无法确定解释现有Snowflake机制还是构造新ID组件，不仅凭‘设计’二字判工程。"),
 "g-new-22f55936b83d-005":("VERBAL","UNKNOWN","明确要求设计数组/链表优点结合的数据结构，但没功能目标/接口，也没说算法求解环节；可能是工程组件或抽象数据结构设计，保留焦点UNKNOWN，后面独立LRU手撕不用于推断。"),
 "g-new-600f370d4a8d-010":("VERBAL","NONE","位于八股列表，询问分库分表适用情况及策略，未给要构造的新组件/业务目标；同意第二审核者，这是机制/使用解释，改为NONE。"),
 "g-new-1c57bc62c368-018":("VERBAL","ENGINEERING","明示设计新游戏积分系统，给1000万玩家、签到发分、过期及优先扣快过期约束；是口头业务系统构造，题干约束完整保留。"),
 "g-new-1c57bc62c368-019":("VERBAL","ENGINEERING","跟随游戏系统题明示用位图构造昨天登录判断功能并‘讲实现’，有具体输入/判定目标；没有代码要求，是口头工程。"),
 "g-new-b32d199485c1-007":("UNKNOWN","NONE","要求现场登录个人网站抓包，是操作演示；既非口头回答也非代码/SQL，枚举不能表达实际操作，response_form保留UNKNOWN；没构造组件，不标工程。"),
}

SUMMARY_OMISSIONS = ["g-new-6d2b25ee4cac-021","g-new-2ae881c215b3-002","g-new-89a73496fa57-018",
 "g-new-e03e96433274-001","g-new-6a559eecded4-002","g-new-377b55fc75c6-002","g-new-1acb80963445-003","g-new-b32d199485c1-001"]
SUMMARY_QUOTE_REFINEMENTS = {
 "g-new-89a73496fa57-016": "如果让你设计一个Agent开发框架，并且让你规范其他部门使用框架进行ai开发的泛式，请你详细聊一下你会怎么做",
 "g-new-89a73496fa57-023": "在Momenta实习做了些什么",
 "g-new-22f55936b83d-001": "上来直接问实习经历",
 "g-new-b32d199485c1-003": "前端按钮数据传输的协议",
}

def qualify(base,output,reports):
    require(not output.exists(),"new version required")
    import shutil
    shutil.copytree(base,output)
    extraction=read_jsonl(output/"extraction.jsonl");labels=read_jsonl(output/"task_labels.jsonl")
    labelmap={r["gold_question_id"]:r for r in labels};decisions=[]
    omitted=set(SUMMARY_OMISSIONS)
    basis="原文仅概括某阶段‘拷打/深挖/问项目’，未给独立提问任务或完整问题意图；不凭区域概述制造一个可评分题。"
    for row in extraction:
        text=decode_source(inside(output,row["source_path"]).read_bytes())
        for q in row["questions"]:
            if q["id"] in omitted:
                decision={"gold_question_id":q["id"],"raw":q["raw_question"],"source_spans":q["source_spans"],"basis":basis}
                row.setdefault("omitted_source_summaries",[]).append(decision);decisions.append(decision)
            if q["id"] in SUMMARY_QUOTE_REFINEMENTS:
                previous=copy.deepcopy(q);quote=SUMMARY_QUOTE_REFINEMENTS[q["id"]];old=q["source_spans"][0]
                span=span_at(text,old["start_line"],quote,old["revision_id"])
                q.update(raw_question=quote,normalized_question=quote,source_spans=[span],review_basis="逐字提问或明确要求的原文span；排除同句候选人评论/段落范围叙述，不补写主语。")
                label=labelmap[q["id"]];label.update(raw_question=quote,source_spans=[span])
                if q["id"]=="g-new-89a73496fa57-023":
                    q.update(topic_l1="项目",topic_l2="项目深挖",question_type="PROJECT")
                decisions.append({"gold_question_id":q["id"],"previous":previous,"replacement":copy.deepcopy(q),"basis":q["review_basis"]})
        row["questions"]=[q for q in row["questions"] if q["id"] not in omitted]
    labels=[r for r in labels if r["gold_question_id"] not in omitted]
    manifest=read_json(output/"manifest.json")
    manifest.update(version=output.name,supersedes=base.name,parent_dataset_sha256=digest(load_dataset(base)),
       blind_review="v1 labels preceded predictions; v2 and v3 are explicit source rereview after historical prediction inspection. Neither adjudicated version is an untouched holdout.",
       adjudication="v2 reconciled task boundaries and 5 source decisions; v3 omitted 8 nonspecific phase summaries and trimmed 4 question spans. Source evidence governs every decision.")
    write_jsonl(output/"extraction.jsonl",extraction);write_jsonl(output/"task_labels.jsonl",labels);write_json(output/"manifest.json",manifest)
    write_json(output/"question-qualification-rereview.json",{"parent_freeze_sha256":digest((base/"freeze.json").read_bytes()),"reviewed_at":datetime.now(timezone.utc).isoformat(),
            "stage":"postfreeze_source_qualification_review","decisions":decisions})
    write_json(output/"freeze.json",{"dataset_sha256":digest(load_dataset(output)),"frozen_at":datetime.now(timezone.utc).isoformat(),
              "file_hashes":{p.relative_to(output).as_posix():digest(p.read_bytes()) for p in sorted(output.rglob("*")) if p.is_file() and p.name!="freeze.json"}})
    write_json(reports/"source-qualification-receipt.json",{"dataset_sha256":digest(load_dataset(output)),"questions":len(labels),"omitted_phase_summaries":len(omitted),
              "refined_quotes":len(SUMMARY_QUOTE_REFINEMENTS),"task_distribution":dict(Counter(r["response_form"]+"/"+r["coding_focus"] for r in labels))})
    for section in ("extraction","task_labels"):validate_gold(output,section,review_policy="delegated_agent")
    print(read_json(reports/"source-qualification-receipt.json"))

def adjudicate(base, output, reports):
    require(not output.exists(),"new version required")
    import shutil
    shutil.copytree(base,output)
    extraction=read_jsonl(output/"extraction.jsonl");labels=read_jsonl(output/"task_labels.jsonl")
    by_sample={r["id"]:r for r in extraction};by_q={q["id"]:q for r in extraction for q in r["questions"]}
    by_label={r["gold_question_id"]:r for r in labels};removed=set();decisions=[]
    for correction in CORRECTIONS:
        row=by_sample["extract-new-"+correction["sample"]]
        text=decode_source(inside(output,row["source_path"]).read_bytes())
        if "remove" in correction:
            removed.add(correction["remove"])
            q=by_q[correction["remove"]]
            row.setdefault("omitted_source_summaries",[]).append({"raw":q["raw_question"],"source_spans":q["source_spans"],"basis":correction["basis"]})
        if "gold_id" in correction:
            q=by_q[correction["gold_id"]];label=by_label[q["id"]]
            original=copy.deepcopy(q)
            span=span_at(text,correction["line"],correction["quote"],q["source_spans"][0]["revision_id"])
            q.update(raw_question=span["quote"],normalized_question=span["quote"],source_spans=[span],review_basis=correction["basis"])
            q.update({k:correction[k] for k in ("topic_l1","topic_l2","question_type") if k in correction})
            label.update(raw_question=span["quote"],source_spans=[span],review=review(correction["basis"]))
            decisions.append({**correction,"previous":original,"replacement":copy.deepcopy(q)})
        else: decisions.append(correction)
    for row in extraction:row["questions"]=[q for q in row["questions"] if q["id"] not in removed]
    labels=[r for r in labels if r["gold_question_id"] not in removed]
    boundary=[]
    for key,(form,focus,basis) in BOUNDARY_REREVIEW.items():
        row=by_label[key]
        boundary.append({"gold_question_id":key,"raw_question":row["raw_question"],"source_hash":row["source_hash"],
             "source_spans":row["source_spans"],"previous":{"response_form":row["response_form"],"coding_focus":row["coding_focus"]},
             "response_form":form,"coding_focus":focus,"basis":basis,"review_basis_input":"complete_original_source_only"})
        row.update(response_form=form,coding_focus=focus,review=review(basis))
        if key=="g-new-d3e713a6755b-036":
            by_q[key].update(question_type="SCENARIO",review_basis=basis)
        if key=="g-new-600f370d4a8d-010":
            by_q[key].update(question_type="KNOWLEDGE",review_basis=basis)
        if key=="g-new-e03e96433274-002":
            by_q[key].update(question_type="SCENARIO",review_basis=basis)
        if key=="g-new-22f55936b83d-002":
            by_q[key].update(question_type="OTHER",attributes_to_review=["question_type"],review_basis=basis)
    manifest=read_json(output/"manifest.json")
    manifest.update(version=output.name,supersedes=base.name,parent_dataset_sha256=digest(load_dataset(base)),
       blind_review="v1 was sealed before predictions. v2 explicitly rechecks 5 source decisions after prediction inspection; preserve the v1 receipt and do not claim v2 as an untouched holdout.",
       adjudication="Two same-task explanation splits merged; one answer tail removed; two unnamed exercise summaries omitted. All changes have exact original spans and source-based rationale.")
    write_jsonl(output/"extraction.jsonl",extraction);write_jsonl(output/"task_labels.jsonl",labels);write_json(output/"manifest.json",manifest)
    write_json(output/"source-adjudication.json",{"parent_freeze_sha256":digest((base/"freeze.json").read_bytes()),"reviewed_at":datetime.now(timezone.utc).isoformat(),
                     "stage":"source_rereview_after_historical_prediction_inspection","decisions":decisions})
    write_jsonl(output/"task-boundary-rereview.jsonl",boundary)
    write_json(output/"freeze.json",{"dataset_sha256":digest(load_dataset(output)),"frozen_at":datetime.now(timezone.utc).isoformat(),
                 "file_hashes":{p.relative_to(output).as_posix():digest(p.read_bytes()) for p in sorted(output.rglob("*")) if p.is_file() and p.name!="freeze.json"}})
    write_json(reports/"source-adjudication-receipt.json",{"dataset_sha256":digest(load_dataset(output)),"questions":len(labels),"decisions":len(decisions),
                           "stage":"postfreeze_source_rereview","task_distribution":dict(Counter(r["response_form"]+"/"+r["coding_focus"] for r in labels))})
    for section in ("extraction","task_labels"):validate_gold(output,section,review_policy="delegated_agent")
    print(read_json(reports/"source-adjudication-receipt.json"))

def bare(text):
    return re.sub(r"\s+","",re.sub(r"^[\s\d.、）)（(•\-*一二三四五六七八九十]+", "",text)).rstrip("。；;")

def propose(dataset,predictions,reports):
    manifest=validate_gold(dataset,"extraction",review_policy="delegated_agent")
    observed={p["id"]:p for p in read_jsonl(predictions)}
    alignments,todo=[],[]
    for row in manifest["extraction"]:
        pred=observed[row["id"]]["result"];text=decode_source(inside(dataset,row["source_path"]).read_bytes())
        def valid(spans):return all(text[s["start_char"]:s["end_char"]]==s["quote"] for s in spans)
        def overlap(g,p):
            spans=g["source_spans"]+g.get("corroborating_spans",[])
            require(valid(spans),"frozen source corroboration corrupted")
            return any(a["start_char"]<b["end_char"] and b["start_char"]<a["end_char"] for a in spans for b in p["source_spans"])
        pairs,used=[],set()
        for g in row["questions"]:
            candidates=[p for p in pred["questions"] if overlap(g,p)]
            identical=[p for p in candidates if bare(g["raw_question"])==bare(p["raw_question"])]
            if len(identical)==1 and identical[0]["id"] not in used:
                p=identical[0];used.add(p["id"])
                pairs.append({"gold_id":g["id"],"prediction_id":p["id"],"basis":"逐字提问（忽略编号/空白）一致，已封存主证据或同事件正文/OCR corroboration跨度一致。"})
            else:todo.append({"index":len(todo),"sample_id":row["id"],"gold_id":g["id"],"gold":g["raw_question"],
                    "candidates":[{"id":p["id"],"raw":p["raw_question"],"normalized":p.get("normalized_question"),"session_id":p["session_id"]} for p in candidates]})
        alignments.append({"id":row["id"],"prediction_sha256":digest(pred),"pairs":pairs,"sessions":[],
             "human_verified":False,"agent_verified":False,"review":{}})
    write_jsonl(reports/"alignment.proposals.jsonl",alignments);write_jsonl(reports/"alignment.review.jsonl",todo)
    print({"identical_source_matches":sum(len(r["pairs"]) for r in alignments),"to_review":len(todo)})

MANUAL_MATCHES = {
 "g-new-dee8c70df3c5-039":("aa2f56c2-d230-4f6c-bb64-86c168765af9","同场次正文Max Value与已封存OCR最大值为同一窗口最大值题，k约束一致，无增删任务。"),
 "g-new-05c5117fc1a7-011":("63afe1b2-5851-47a8-9224-ebe5d06b435a","SSE原理任务一致；机器raw多带候选人的gRPC评论，normalized未改变任务。"),
 "g-new-2ae881c215b3-003":("204d3b4f-a509-488f-87e4-4f0dbe3b1068","同一自研通信框架任务；机器附‘问的是我自研’上下文，没有追加约束。"),
 "g-new-2ae881c215b3-005":("e73b2b27-a195-4baa-87bb-d8b316b398f9","URL全流程任务一致；机器raw附一面问过的回忆评论，不是另一次该场任务。"),
 "g-new-2ae881c215b3-007":("00389069-fdcb-4bcf-81ce-4abbb1d82030","Linux第四列userid/top10目标和全部约束一致；机器raw多带候选人忘命令评论。"),
 "g-new-2ae881c215b3-008":("72432988-4241-4582-bcce-584a33c42b51","仅‘11.补问1’标题编号范围不同，同一分布式/单机通信问题完整一致。"),
 "g-new-31bee07b8745-004":("32c284bf-5cf0-4ac5-bce7-40033ed0e262","‘算法:’为题目章节标志，机器保留全部字符串切回文及回溯要求；任务一致。"),
}

SESSION_MATCHES = {
 "extract-new-6d2b25ee4cac":{"s1":"0960cfd5-9c45-4233-b85d-b82bf5693bb4","s2":"47867f8b-7f67-466d-90c5-d76e3c5e145a"},
 "extract-new-d3e713a6755b":{"s1":"5090f483-1538-4ac5-b1f9-f3b989ef0542","s2":"f8f691dc-1f30-45b3-8add-f405f63bc22e"},
 "extract-new-89a73496fa57":{"s1":"68ba04a7-a05f-4b6e-bd7f-79a6b71de37f"},
 "extract-new-31bee07b8745":{"s1":"ad21c964-613d-47c9-ab33-ce7e02dca810","s2":"9901b0d0-35f4-4094-bb40-48913a15719f"},
 "extract-new-b32d199485c1":{"s1":"0ca1e08d-2da2-40b9-a75e-de888664775f","s2":"ba6d0614-dd24-4402-845d-a51c2169df62"},
}

def finalize(dataset,predictions,reports):
    manifest=validate_gold(dataset,"extraction",review_policy="delegated_agent")
    observed={p["id"]:p for p in read_jsonl(predictions)}
    proposals=read_jsonl(reports/"alignment.proposals.jsonl");todo=read_jsonl(reports/"alignment.review.jsonl")
    decisions={}
    for row in todo:
        if row["gold_id"] in MANUAL_MATCHES:
            target,basis=MANUAL_MATCHES[row["gold_id"]]
            require(target in {c["id"] for c in row["candidates"]},"manual choice outside reviewed source evidence")
        else:
            target=None
            basis=("已阅读完整原文和全部同跨度候选；没有此独立问题的published输出，不用相邻/相似题补配。" if not row["candidates"] else
                   "已阅读原文与全部同跨度候选；候选合并多个独立任务或只保留gold整体任务的片段，意图/粒度不一致，严格一对一保持未匹配。")
        decisions[row["gold_id"]]={"prediction_id":target,"basis":basis,"review":review(basis)}
    require(set(MANUAL_MATCHES)<=set(decisions),"manual decision list missing reviewed items")
    write_json(reports/"alignment.decisions.json",decisions)
    by_sample={r["id"]:r for r in proposals};goldmap={r["id"]:r for r in manifest["extraction"]}
    for row in todo:
        choice=decisions[row["gold_id"]]
        if choice["prediction_id"]:by_sample[row["sample_id"]]["pairs"].append({"gold_id":row["gold_id"],**choice})
    for row in proposals:
        gold=goldmap[row["id"]];pred=observed[row["id"]]["result"]
        pairs=row["pairs"]
        require(len({p["gold_id"] for p in pairs})==len(pairs)==len({p["prediction_id"] for p in pairs}),"alignment not one-to-one")
        mapping=SESSION_MATCHES.get(row["id"],{})
        if len(gold["sessions"])==len(pred["sessions"])==1:mapping={gold["sessions"][0]["id"]:pred["sessions"][0]["id"]}
        row.update(agent_verified=True,human_verified=False,review=review("逐项审核原文证据、同场次和任务意图；合并/分裂导致粒度不一致不配对。37个gold场次按真实章节审核，其中3个缺失的场次保持未匹配。"),
                   sessions=[{"gold_id":g,"prediction_id":p,"basis":"明确原文场次标题与题目跨度的一对一审核；未因元信息错误而改动gold标签。"} for g,p in mapping.items()],
                   decisions_sha256=digest((reports/"alignment.decisions.json").read_bytes()))
    write_jsonl(reports/"alignments.jsonl",proposals)
    ppath=reports/"extraction.predictions.jsonl"
    ppath.write_bytes(predictions.read_bytes())
    write_json(ppath.with_suffix(".collection.json"),{"adapter":"published_output_audit","section":"extraction","split":"test","review_policy":"delegated_agent",
         "snapshot":manifest["snapshot"],"samples":len(observed),"failed":0,"budget":{"model_calls":0},"predictions_sha256":digest(ppath.read_bytes()),
         "scope":"Frozen r293 historical output audit; elapsed collection timings are local audit costs, not model/request performance."})
    mapping={p["gold_id"]:p["prediction_id"] for a in proposals for p in a["pairs"]}
    pred_q={q["id"]:q for p in observed.values() for q in p["result"]["questions"]}
    labels=manifest["task_labels"];task_preds=[];mapping_rows=[]
    for gold in labels:
        pid=mapping.get(gold["gold_question_id"]);annotation=pred_q.get(pid,{}).get("task_annotation")
        task_preds.append({"id":gold["id"],"snapshot":manifest["snapshot"],"failed":not bool(annotation),
            "error_code":None if annotation else "UNMAPPED_PUBLISHED_OCCURRENCE","result":annotation or {},"model_calls":[],
            "adapter":"published_occurrence_and_task_label_audit","published_occurrence_id":pid})
        mapping_rows.append({"gold_question_id":gold["gold_question_id"],"published_occurrence_id":pid,
             "status":"MAPPED" if pid else "UNMAPPED","basis":"Same strict reviewed one-to-one extraction alignment; gold labels never changed to fit a published occurrence."})
    write_jsonl(reports/"published-occurrence-mapping.jsonl",mapping_rows)
    taskpath=reports/"task_labels.predictions.jsonl";write_jsonl(taskpath,task_preds)
    write_json(taskpath.with_suffix(".collection.json"),{"adapter":"published_occurrence_and_task_label_audit","section":"task_labels","split":"test","review_policy":"delegated_agent",
       "snapshot":manifest["snapshot"],"samples":len(labels),"failed":sum(p["failed"] for p in task_preds),"budget":{"model_calls":0},
       "predictions_sha256":digest(taskpath.read_bytes()),"scope":"End-to-end source-to-published-occurrence-to-label audit; unmapped source tasks count as missing, not classification model failures."})
    from eval.scoring import score_labels
    selected=[(g,p) for g,p in zip(labels,task_preds) if not p["failed"]]
    metrics,rows=score_labels([g for g,p in selected],[p for g,p in selected])
    write_json(reports/"mapped-task-label-metrics.json",{"scope":"Conditional classification audit restricted to uniquely mapped published occurrences; not the full source recall or a unbiased population estimate.",
         "dataset_sha256":digest(load_dataset(dataset)),"samples":len(selected),"total_gold_tasks":len(labels),"unmapped":len(labels)-len(selected),
         "source_coverage":len(selected)/len(labels),"metrics":metrics})
    write_jsonl(reports/"mapped-task-label-badcases.jsonl",[r for r in rows if r["status"]!="PASS"])
    print({"aligned":len(mapping),"gold_questions":len(labels),"unmapped":len(labels)-len(mapping),
           "mapped_sessions":sum(len(a["sessions"]) for a in proposals),"manual_matches":len(MANUAL_MATCHES),"reviewed_unresolved_decisions":len(decisions)})

def main():
    parser=argparse.ArgumentParser();parser.add_argument("action",choices=("adjudicate","qualify","propose","finalize"))
    parser.add_argument("--base",type=Path,default=Path("data/gold/source-reviewed-extraction-20261014-v1"))
    parser.add_argument("--output",type=Path,default=Path("data/gold/source-reviewed-extraction-20261014-v2"))
    parser.add_argument("--reports",type=Path,default=Path("data/reports/source-reviewed-extraction-20261014-v2"))
    parser.add_argument("--predictions",type=Path,default=Path("data/reports/source-reviewed-extraction-20261014-v1/extraction.predictions.jsonl"))
    args=parser.parse_args()
    if args.action=="adjudicate":adjudicate(args.base,args.output,args.reports)
    elif args.action=="qualify":qualify(args.base,args.output,args.reports)
    elif args.action=="propose":propose(args.output,args.predictions,args.reports)
    else:finalize(args.output,args.predictions,args.reports)

if __name__=="__main__":main()
