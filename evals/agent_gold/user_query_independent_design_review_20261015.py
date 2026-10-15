"""Independent read-only audit of frozen query design positives and history."""
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from eval.common import digest, inside, read_json, write_json
from evals.agent_gold.user_query_source_20261015 import validate_dataset


DATASET = Path("data/gold/user-query-source-reviewed-20261015-v1")
REPORT = Path("data/reports/user-query-source-reviewed-20261015-v1")
ARCHIVE = Path("data/reports/resume-quality-snapshot-20261005-v1")

POSITIVE = {
 "uq-occ-0214": "第19-21行在项目八股中提出多机房、各自Redis的整体限流约束；当前query scope允许闭合工程机制，可支持ACCEPT。这里审的是跨机房整体机制，不把它强行转成coding_focus=ENGINEERING，也不把所有限流知识题一并纳入。",
 "uq-occ-0219": "第18行要求实现类似商品搜索框的实时名称模糊搜索功能，第19-20行继续输入联想与协议问题；新功能对象和可观察搜索行为明确，属于主设计。Agent岗位标题不改变该传统搜索功能性质。",
 "uq-occ-0244": "二面第48行明确场景题：构造Cache、并发高QPS读与新条目插入；是新缓存服务的行为约束，不是Redis数据结构知识或前一轮磁盘交集算法。原文未要求额外TTL/一致性，审核不补。",
 "uq-occ-0321": "第19行独立系统设计要求RocketMQ十万条/秒输入、ID/status字段、两类写入条件、5%更新及大量无效消息；新数据集成系统闭环明确。第20-24行布隆原理/容量追问不替代主任务，也不自动变成新的主设计。",
 "uq-occ-0599": "第19行‘假如要实现’监控埋点系统并处理大量日志请求，有新系统及运行职责；前后线程池知识只作背景，没有强加线程池实现或额外存储约束。支持宽系统/基础设施设计范围。",
 "uq-occ-0649": "帆软第24行要求构造多类请求队列，保证公平并避免一类请求独占资源，是独立调度机制。与第23行资源总体规划相关但有清楚的队列行为要求，允许独立occurrence；不声称两题来自两个独立场景。",
 "uq-occ-0733": "第23行给N类请求、已知峰值占用、依赖不变时结果复用、有限内存，并要求统筹并发/延迟/稳定性；具有具体资源处理系统约束，区别于泛问调度策略。",
 "uq-occ-0785": "TEG第22行明确承接第16-20行容量/TTL内存KV，新增周期持久化、并发读写和掉电正确恢复目标，可作为闭合存储机制。父题是新构造练习，不是介绍已有项目实现；仍应保留与内存map边界项0633的口径说明。",
 "uq-occ-0894": "第14行为独立热点榜单场景：实时更新、高并发、数据结构、数据库选型；完整主设计明确。第15行两栈队列代码及线程安全是另一任务，不能并入榜单。它是宽工程设计的一个例子，不构成宽查询的唯一主题过滤。",
 "uq-occ-1053": "第17行明确设计高速Cache并给高吞吐、低延迟、可扩展目标；与第16行回忆实习高并发、第19行链表算法分开。支持缓存系统主设计，不继承相邻Redis细节为强制技术选型。",
 "uq-occ-1193": "第28行在解释ngix作用之后切换为‘如果是你’设计容灾系统，有新的高可用基础设施对象。不能因所在八股章节一律排除新设计，也不能补出原文未记载的具体容灾级别或切换时间。",
 "uq-occ-1275": "第35行先问用过RocketMQ，随后明确‘如果自己设计一个延时消息队列’；第37行调度/准时消费追问支持延时服务职责。它不同于解释RocketMQ内建延迟消息原理的0826。",
 "uq-occ-1338": "第51-54行独立购票场景明确十万人/一千座、长路程优先及流量高可用；gold多行span保留全部要求，未将第56行链表手撕并入。支持业务系统设计。",
 "uq-occ-1375": "第47行游戏积分系统给千万玩家、签到发分、过期和优先扣快过期积分，形成状态管理闭环。第49行位图登录判断是局部追问；原文没有排行榜排序要求，不能因‘积分’把它当排行榜题。",
 "uq-occ-1514": "第11行明确假设让候选人设计SSE断联后的恢复、存储及Redis容量处理，是带多个运行约束的闭合恢复机制；支持当前宽scope。原文没有‘补发接口’等细节，不能把review里的自然概括当成额外原题条件；与裸WebSocket重连0202应保留边界说明。",
 "uq-occ-1653": "wxg第32行要求实现跨服务器向客户端推送的整个方式，并明确不是单机；是完整消息交付链路。第30行已有SSE十秒断线的问题不同，不应用相邻局部故障描述压缩本题主范围。",
 "uq-occ-2093": "第22行明示开发关注功能，支持关注/取消/粉丝及关注列表，考虑大用户量与高并发；第23-26行表结构/操作/计数追问支持该主设计。第28行时延尖刺为独立排障题，不混为设计正例。",
 "uq-occ-2322": "第9行独立场景要求从头到尾做join.qq.com网页并应对访问量增长；按本次宽业务系统设计定义支持ACCEPT。没有代码要求和细分页面业务，不能自行添加招聘流程/数据库等未记载约束。",
 "uq-occ-2478": "第21行要求把现有项目重新改造成可上线大规模使用的分布式系统并分配服务器节点，是新部署/架构改造任务，不是单问过去如何实现。前后模型与Agent业务背景不自动把它变为Agent应用设计。",
 "uq-occ-2563": "第145行明确设计冷热分层且冷数据仍需查询，有存储容量与查询能力两项硬约束。第147-157行作者可答建议不应提升为面试官已给的90天/QPS/技术栈要求；gold target未强加这些内容。",
 "uq-occ-2624": "归档第8-16行逐项记载停车入场记录/车位分配、查询时长、计费支付、出场释放，明确排除预约/月租/优惠券/车牌识别，并给1000车位、并发、避免重复分位及收费、模型接口模块异常处理要求。完整闭环及限制均真实存在，未见gold补写。",
}
EXTRA = {
 "uq-occ-0319": ("BOUNDARY", "第16行仅‘分布式id的设计’，无唯一性/时序/节点/吞吐等题干约束；保留BOUNDARY合理，不能靠常见雪花算法补齐。它在leaderboard面板也为BOUNDARY，但原文没有排序/榜单证据：对纯排行榜范围可另作主题裁决，当前不能借边界宣称相关。"),
 "uq-occ-2131": ("BOUNDARY", "无标题归档第18行确有高并发服务器设计，第19-20行又有并发下载问题；技术上像主设计，但缺具体公司/场次归属，现有BOUNDARY保守可接受。不能断言无标题就是伪造，也不能仅靠机器INCLUDED认证具体面试事件。"),
 "uq-occ-0202": ("BOUNDARY", "帆软第16行仅问WebSocket断开重连，未展开数据恢复/持久化行为；与1514明确恢复存储容量的SSE机制存在证据量差异。是否将裸协议恢复也纳入闭合工程机制仍需统一政策，保留边界，不自动判负。"),
 "uq-occ-0633": ("BOUNDARY", "第16-20行有get/put、O(1)、容量/LRU、TTL且明确不写代码，已是完整KV行为；BOUNDARY可保留，但它比0244高QPS Cache题的功能要求更具体。不能只因写成‘数据结构/map’就排除闭合机制，应与0785持久化父子任务统一边界。"),
 "uq-occ-1358": ("BOUNDARY", "B站第25行有多团队/业务域的工具集合差异、拆分隔离交付和统一服务，可能属于独立基础设施；‘MCP/Agent’领域本身不足以排除。边界当前不计负，但后续应以交付对象和范围裁定，不能按岗位或技术词。"),
 "uq-occ-2479": ("BOUNDARY", "第17行确有周报自动发送系统、至少5个AI子任务、人工介入及未写周报异常；设计对象与AI工作拆分同时存在。保留任务重心争议合理，不能把它当已有项目解释或因为AI词一律排除。"),
 "uq-occ-2496": ("BOUNDARY", "第32行明确AI Coding构造push/MR前代码风格/安全校验器，目标与模块完整。manifest说设计scope与作答形式独立，因此‘AI Coding与口述设计边界’的review理由存在合同张力；BOUNDARY尚未形成错误负例，不应在当前冻结测试中改标。"),
 "uq-occ-2622": ("BOUNDARY", "第39行确要求缓存系统高效读写、LRU/LFU及防穿透击穿；仅因为它是代码题而排除会与response_form独立的scope定义冲突。当前是BOUNDARY而非REJECT，保留待统一口径，不将其作为错误检索结果。"),
 "uq-occ-2649": ("BOUNDARY", "第17行有限GPU并行推理/微调及吞吐时延目标构成资源服务约束，与0733资源机制相近；同时有模型服务领域边界。保留待裁定合理，不能从Agent岗位或题名‘算法面经’直接排除工程基础设施。"),
 "uq-occ-0826": ("REJECT", "xhs第11行问RocketMQ内建延迟消息底层原理及分布式任务处理，第14行作者明确说消息队列底层八股。未要求自行构造服务；与1275‘如果自己设计延时队列’及0321数据集成系统不同，支持REJECT。"),
 "uq-occ-1354": ("REJECT", "飞书第19行项目里‘排行榜，score怎么算的’，后接作者评论/点赞加权回答；仅计算权重的局部解释，不是热点榜单实时更新/并发/存储主设计。工程设计及leaderboard主设计面板均排除合理。"),
 "uq-occ-1955": ("REJECT", "第16行回忆‘实习遇到过高并发吗，怎么解决’，实际经历说明；第17行才独立要求高速Cache设计。不能因高并发词命中将回忆问题升级为主设计。"),
 "uq-occ-1077": ("REJECT", "第28行给压测中已观察到的50ms时延尖刺，问排查思路，属于线上故障诊断；不是第22行新关注系统构造。高并发既可出现在设计也可出现在排障，不能关键词合并scope。"),
}


def audit_question(question, basis, section):
    path = inside(DATASET, question["source_path"])
    archive = inside(ARCHIVE, question["source_path"])
    raw = path.read_bytes()
    assert raw == archive.read_bytes() and digest(raw) == question["source_hash"]
    text = raw.decode("utf-8")
    for span in [*question["source_spans"], *question["context_spans"]]:
        assert text[span["start_char"]:span["end_char"]] == span["quote"]
        assert text.count("\n", 0, span["start_char"]) + 1 == span["start_line"]
        assert text.count("\n", 0, span["end_char"]-1) + 1 == span["end_line"]
    return {"id": question["id"], "occurrence_id": question["occurrence_id"],
        "canonical_question_id": question["canonical_question_id"], "review_section": section,
        "raw_question": question["raw_question"], "source_file": str(path), "archive_source_file": str(archive),
        "source_hash": question["source_hash"], "original_path": question["original_path"],
        "source_revision_id": question["source_revision_id"], "source_spans": question["source_spans"],
        "context_spans": question["context_spans"], "source_bytes_equal_archive": True,
        "source_hash_and_all_spans_valid": True, "full_source_read": True,
        "frozen_judgment": question["scope_judgments"]["engineering_system_design"],
        "independent_status": "SUPPORTED" if section == "positive" else "SUPPORTED_WITH_BOUNDARY_NOTES" if question["scope_judgments"]["engineering_system_design"]["grade"] == "BOUNDARY" else "SUPPORTED",
        "independent_basis": basis, "relabel_proposed": False}


def main():
    files = ["manifest.json", "freeze.json", "scopes.json", "questions.jsonl", "cases.jsonl", "history.json"]
    before = {name: digest((DATASET / name).read_bytes()) for name in files}
    validated = validate_dataset(DATASET)
    assert before["manifest.json"] == "9ab5c9671da5d648f436e49c0fc59477f5156fef6a4aef1009c507cbb7426a09"
    assert before["freeze.json"] == "d5c0236e71d8fc06abe8d160af9ab9ec381b1dd3a6e93289ab843cb695fe7262"
    by_id = {q["id"]: q for q in validated["questions"]}
    scope = validated["scopes"]["engineering_system_design"]
    assert len(POSITIVE) == 21 and set(POSITIVE) == set(scope["positive_gold_ids"])
    reviews = [audit_question(by_id[gid], basis, "positive") for gid, basis in POSITIVE.items()]
    for gid, (grade, basis) in EXTRA.items():
        assert by_id[gid]["scope_judgments"]["engineering_system_design"]["grade"] == grade
        reviews.append(audit_question(by_id[gid], basis, "boundary_or_hard_neighbor"))
    direct = read_json(REPORT / "independent-history-provenance.json")
    direct_by_id = {entry["message_id"]: entry for entry in direct["entries"]}
    history = read_json(DATASET / "history.json")
    history_checks = []
    for record in history["entries"]:
        for message in record["messages"]:
            actual = direct_by_id[message["message_id"]]
            assert actual["text"] == message["text"]
            assert all(actual[k] == record[k] for k in ("thread_id", "thread_title", "turn_id", "started_at_unix"))
            origins = []
            for case in validated["cases"]:
                for turn in case["turns"]:
                    origin = turn["origin"]
                    if origin.get("message_id") == message["message_id"] and origin["kind"] == "historical_user_substring":
                        assert actual["text"][origin["start_char"]:origin["end_char"]] == origin["quote"] == turn["message"]
                        origins.append({"case_id": case["id"], "turn_index": turn["turn_index"], "origin": origin})
            assert origins
            history_checks.append({**actual, "independent_tool_provenance": "mcp__codex_app__read_thread:userMessage",
                "full_message_exact_match": True, "historical_query_substrings_exact": True, "origins": origins})
    assert len(history_checks) == len(direct_by_id) == 6
    assert before == {name: digest((DATASET / name).read_bytes()) for name in files}
    result = {"created_at": datetime.now(timezone.utc).isoformat(), "reviewer_kind": "agent",
        "human_verified": False, "agent_verified": True, "status": "SUPPORTED_WITH_SCOPE_NOTES",
        "dataset": str(DATASET), "before_and_after_file_hashes": before,
        "source_evidence_contract": "user_query_raw_source_spans_v1", "frozen_gold_changed": False,
        "material_source_or_provenance_errors_found": [],
        "counts": {"positive_questions_reviewed": 21,
            "positive_unique_sources": len({r["source_hash"] for r in reviews if r["review_section"] == "positive"}),
            "boundary_questions_reviewed": 9, "hard_neighbors_reviewed": 4,
            "total_questions_reviewed": len(reviews), "unique_sources_reviewed": len({r["source_hash"] for r in reviews}),
            "direct_historical_user_messages_verified": 6},
        "scope_notes": [
            {"id": "FORM_INDEPENDENCE", "affected_ids": ["uq-occ-2496", "uq-occ-2622"],
             "basis": "manifest design scope与response_form独立，边界review却以AI Coding/代码vs口述为一部分理由；原文已给完整技术目标。当前BOUNDARY不产生错误负例，但今后不能只凭代码作答排除主设计。"},
            {"id": "CLOSED_KV_MECHANISM", "affected_ids": ["uq-occ-0633", "uq-occ-0785", "uq-occ-0244"],
             "basis": "有get/put/容量/TTL的内存KV为边界，持久化扩展及高QPS Cache为正；应明确数据结构构造与闭合基础设施机制的最低界线，当前保持待裁定。"},
            {"id": "DESIGN_IS_NOT_CODING_FOCUS", "affected_ids": ["uq-occ-0214", "uq-occ-1514"],
             "basis": "查询设计scope可包含跨机房整体限流或闭合恢复机制，不意味着这些题在旧task_labels中必须ENGINEERING，也不授权用coding_focus预过滤。两种目标维度分别评测。"},
            {"id": "NOT_FULL_RECALL", "affected_ids": list(scope["boundary_gold_ids"]),
             "basis": "本审核不是2771 occurrence的全语义复审，22边界仍在，source-only两条不等于库中occurrence。保持NOT_CERTIFIED和release_eligible=false。"},
        ],
        "question_reviews": reviews, "history_reviews": history_checks,
        "independent_history_receipt_sha256": digest((REPORT / "independent-history-provenance.json").read_bytes()),
        "formal_validator": "validate_dataset passed: frozen files/facts/all occurrence provenance/canonical variants/history spans/scope contracts",
        "predictions_read_for_this_audit": False, "external_model_calls": 0, "production_writes": 0,
        "script_sha256": digest(Path(__file__).read_bytes()),
        "limits": ["Source authenticity checked against frozen r293 Markdown bytes, not a new live social-post crawl.",
            "All21 positives and listed13 hard boundaries/neighbors were source-read; no claim of full184-document or2771-task semantic recertification.",
            "Only source/provenance and the declared semantic contract were audited; no current query result or metric was used to adjust gold."]}
    write_json(REPORT / "independent-design-review.json", result)
    lines = ["# 工程系统设计 gold 独立只读审核", "",
        "结论：21个正例均有真实r293归档题干及任务要求支持，未发现来源拼写、条件补造、span错位或历史原问provenance错误。保留scope合同边界说明，当前冻结集不修改、不重标，也不据此认证全库召回。", "",
        "审核21个正例（20份去重原文）、9个重点边界、4个难邻题；六条历史原问另通过四个真实聊天的直接userMessage重新核对。只保存指定六条消息，未复制无关聊天记录。当前查询预测未用于本次审核。", "",
        "## 来源与边界结论", "",
        "- 排行榜：热点榜单实时更新/高并发/结构与选型确为主设计；项目里score怎么算只是局部解释。原问‘类似设计一个排行榜’给的是例子，不能推导只检索排行榜、要求写代码或限定机器题型。",
        "- 分布式ID：只有设计标题，没有功能/节点/时序等原题要求，保留BOUNDARY；不得补雪花算法。纯排行榜面板亦不应把此边界误解为已证实相关。",
        "- 停车场：入场到支付离场闭环、1000车位、并发分配/重复收费限制和排除项全部在原文第8-16行，未见gold新增。",
        "- RocketMQ：十万条/秒、status条件、5%更新是数据集成主系统要求；自设计延时队列与解释现成RocketMQ延迟消息原理有明确任务差别。",
        "- 高并发：新Cache、整体限流、关注功能等设计可纳入；既有实习经历或时延尖刺排查不能只因出现高并发就纳入。无标题高并发服务器的面试归属仍保留边界。", "",
        "## 需要保留的合同说明", "",
        "manifest说设计scope与response_form独立，而MR校验器2496、缓存代码题2622的边界依据含代码/口述区别。两题原文均有工程交付行为；BOUNDARY当前不会被当已知负例，因此未判定为已发生的错负，但未来统一政策不能只凭CODE排除。", "",
        "内存map0633具get/put/容量/TTL且不写代码，持久化扩展0785及高QPS Cache0244为正：数据结构练习与闭合存储机制的界线应解释清楚。MCP工具基础设施、GPU调度等边界也不能只按Agent词排除。保留冻结标注，需修订时另建版本。", "",
        "跨机房限流/SSE恢复作为宽设计scope正例，不自动使coding_focus=ENGINEERING；查询意图与任务分类维度独立。不要因这套scope而回改旧分类gold，或用机器coding_focus/response_form作隐式过滤。", "",
        "## 21个正例逐项复核", "",
        "| ID / 来源 | 独立原文判断 |", "| --- | --- |"]
    for review in reviews:
        if review["review_section"] != "positive":
            continue
        path = Path(review["source_file"]).resolve().as_posix();line=review["source_spans"][0]["start_line"]
        lines.append(f"| {review['id']}<br>[原文第{line}行](<{path}:{line}>) | {review['independent_basis']} |")
    lines += ["", "## 重点边界与难邻题", ""]
    for review in reviews:
        if review["review_section"] == "positive":
            continue
        path=Path(review["source_file"]).resolve().as_posix();line=review["source_spans"][0]["start_line"]
        lines += [f"- `{review['id']}` / {review['frozen_judgment']['grade']} / [原文第{line}行](<{path}:{line}>)：{review['independent_basis']}"]
    lines += ["", "## 六条历史原问", "",
        "| 真实聊天 / turn | exact用户消息核验 |", "| --- | --- |"]
    for record in history_checks:
        quotes = " / ".join(item["origin"]["quote"] for item in record["origins"])
        lines.append(f"| {record['thread_title']}<br>`{record['turn_id']}` | thread/turn/message ID、startedAt、完整正文及query字符substring均一致：{quotes} |")
    lines += ["", "## 证据与限制", "",
        "`independent-design-review.json`保存每例原问、occurrence/canonical、归档source/hash/revision/spans/context、旧判断与独立依据，以及六条read_thread直接消息核验。`independent-history-provenance.json`仅保存指定六条消息的独立读取证据。所有已审题source bytes与原始snapshot archive一致，source与context引用、行号均校验通过。", "",
        "正式validate_dataset检查通过；manifest/freeze/scopes/questions/cases/history审前审后hash完全一致。manifest SHA256 `"+before['manifest.json']+"`；freeze SHA256 `"+before['freeze.json']+"`。", "",
        "这仍是r293已暴露来源的诊断gold，非10月15日线上最新库，未认证社交站实时原页，也未重审全部2771题的范围判定。22条BOUNDARY及source-only映射限制保留，release_eligible=false、全库Recall NOT_CERTIFIED。没有模型调用、生产标签写入或对冻结集的任何变更。", ""]
    (REPORT / "independent-design-review.md").write_text("\n".join(lines),encoding="utf-8")
    print(result["counts"])


if __name__ == "__main__":
    main()
