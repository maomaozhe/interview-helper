"""Freeze independent calibration and test requests before local-model inference.

Labels describe the product contract, not historical user traffic. Expected
plans are authored directly; they are not obtained by replaying the fast decoder.
"""
import argparse
from pathlib import Path

from eval.common import digest, read_json, write_json
from interview_intelligence.agent.query_contract import QuerySpec


def row(key, message, *, action="LIST", filters=None, count=None, sort="frequency", group="question",
        session=None, explicit=None, expected=None, page_size=None):
    filters = filters or {}
    plan = None if action == "FALLBACK" else QuerySpec(action=action, filters=filters, top_n=count,
        sort=sort, page_size=page_size or min(100, count or 20), group_by=group).model_dump(mode="json")
    choices = {"action": action, **{k: v for k, v in filters.items()
               if k in {"company", "topic_l1", "round", "response_form", "coding_focus"}}}
    choices.update(sort=sort, group_by=group)
    if expected: choices.update(expected)
    return {"id": key, "group_id": key, "message": message, "expected": choices, "expected_plan": plan,
        "session": session or {}, "explicit_filters": explicit or {}, "human_verified": False,
        "agent_verified": True, "review": {"reviewer_kind": "agent", "guide": "product QuerySpec contract",
            "basis": "Authored final scope, count, sorting, inheritance and supported-action decision before model inference."}}


def build():
    calibration = []
    def add(message, **kwargs):
        calibration.append(row(f"cal-{len(calibration):02}", message, **kwargs))
    for topic in ("Java", "Spring", "数据库", "计算机基础", "AI", "Redis"):
        add(f"给我{topic}这个大分类的题目清单", filters={"topic_l1": topic})
    for count, sort, wording in ((9,"frequency","出现最多"),(17,"frequency","高频"),
        (23,"importance","最重要"),(14,"gap","最薄弱"),(32,"frequency","最常考"),
        (48,"importance","最值得练习"),(72,"frequency","按被问次数"),(160,"frequency","按频次")):
        add(f"从算法题中取{wording}的{count}道", filters={"coding_focus":"ALGORITHM"}, count=count,
            sort=sort, expected={"top_n":"n0"})
    for message, filters in (
        ("把工程实现类题列出来", {"coding_focus":"ENGINEERING"}),
        ("练工程代码，请给我清单", {"coding_focus":"ENGINEERING","response_form":"CODE"}),
        ("我想练手写SQL语句，列出题库里的题", {"coding_focus":"ENGINEERING","response_form":"SQL"}),
        ("找一下明确要求口述回答的题", {"response_form":"VERBAL"}),
        ("列出手写算法实现的题", {"coding_focus":"ALGORITHM","response_form":"CODE"}),
        ("把所有算法方向的题按频次列出，不设总数", {"coding_focus":"ALGORITHM"})):
        add(message, filters=filters)
    for group, noun in (("company","公司"),("topic","大分类"),("round","轮次")):
        add(f"按{noun}分组统计题目数", action="STATS", group=group)
        add(f"算法题在各{noun}分别出现多少次", action="STATS", group=group, filters={"coding_focus":"ALGORITHM"})
    for round_name, code in (("一面","FIRST"),("二面","SECOND"),("三面","THIRD"),("HR面","HR")):
        add(f"整理{round_name}出现的题目，按次数排列", filters={"round":code})
    prior={"filters":{"coding_focus":"ALGORITHM","round":"FIRST"},"sort":"frequency",
        "last_plan":{"action":"LIST","top_n":37,"page_size":20}}
    for message, filters, count in (
        ("继续这份清单，换成二面",{"coding_focus":"ALGORITHM","round":"SECOND"},37),
        ("保持算法范围，这次不要限定轮次",{"coding_focus":"ALGORITHM"},37),
        ("其他条件保留，改成三面",{"coding_focus":"ALGORITHM","round":"THIRD"},37),
        ("重开一份清单，只列Java分类，不限题目总数",{"topic_l1":"Java"},None),
        ("换个方向，现在看工程代码题，任何轮次都可以",{"coding_focus":"ENGINEERING","response_form":"CODE"},None),
        ("接着这份榜单，只取6道",{"coding_focus":"ALGORITHM","round":"FIRST"},6)):
        add(message, filters=filters, count=count, session=prior, page_size=20 if count==37 else None)
    add("把最近三个月的算法题列成清单", filters={"coding_focus":"ALGORITHM","start_date":"2026-07-05","end_date":"2026-10-06"})
    add("给我近三个月的Redis分类题目", filters={"topic_l1":"Redis","start_date":"2026-07-05","end_date":"2026-10-06"})
    add("分类随便，列出当前UI选中的工程题", filters={"coding_focus":"ENGINEERING"}, explicit={"coding_focus":"ENGINEERING"})
    add("把当前页翻过去", action="NEXT", session=prior)
    unsupported = [
        "查找线上Redis缓存突然耗尽的排障题", "检索Agent上下文越来越长的解决方案类题目", "哪些题和线程池拒绝任务导致雪崩相关",
        "把第一题改成薄弱", "把当前页全部保存为已掌握", "第二题的完整原文和来源给我", "展开上一道题",
        "2024年阿里面经有哪些题", "从2025年一月到五月的Java题", "近两周的题目", "最近半年算法题",
        "力扣146这道题有哪些公司问过", "按后端开发职位筛选题目", "只看Java八股里的泛型擦除细分题",
        "列出腾讯问过的Java题", "阿里三面有哪些题", "从第七页开始", "把题目导出CSV并写进我指定的目录",
        "先比较公司分布，再给我薄弱题清单", "我忘了上一题，帮我标记一下"]
    for message in unsupported: add(message, action="FALLBACK")
    assert len(calibration) == 60
    test = []
    def hold(message, **kwargs):
        test.append(row(f"test-{len(test):02}", message, **kwargs))
    for topic in ("Redis", "Java", "Spring", "AI", "数据库"):
        hold(f"我复习{topic}分类，请展示出现过的题，保持完整范围", filters={"topic_l1":topic})
    for count, sort, noun in ((11,"frequency","算法高频"),(21,"importance","算法重要"),(41,"gap","算法薄弱"),
        (65,"frequency","算法热门"),(110,"frequency","工程代码高频"),(7,"importance","工程实现重要"),
        (28,"frequency","算法高频"),(35,"gap","工程代码薄弱")):
        is_code="代码" in noun
        filters={"coding_focus":"ENGINEERING" if "工程" in noun else "ALGORITHM"}
        if is_code: filters["response_form"]="CODE"
        hold(f"这次只要{count}道{noun}题", filters=filters, count=count, sort=sort, expected={"top_n":"n0"})
    for message, filters in (
        ("我正在准备工程代码实现面试，题库里有哪些可练的",{"coding_focus":"ENGINEERING","response_form":"CODE"}),
        ("工程实现相关的题完整列一遍",{"coding_focus":"ENGINEERING"}),
        ("练习手撕算法，给我题目目录",{"coding_focus":"ALGORITHM","response_form":"CODE"}),
        ("练习写SQL，给我SQL语句题列表",{"coding_focus":"ENGINEERING","response_form":"SQL"}),
        ("挑出口述类型的面试题，不限制数量",{"response_form":"VERBAL"}),
        ("力扣方向的题都有哪些，按频次展示",{"coding_focus":"ALGORITHM"})):
        hold(message,filters=filters)
    for group,noun in (("company","面试公司"),("topic","技术分类"),("round","面试轮次")):
        hold(f"按{noun}统计提问次数",action="STATS",group=group)
        hold(f"只统计工程代码题，按{noun}分组",action="STATS",group=group,
             filters={"coding_focus":"ENGINEERING","response_form":"CODE"})
    for name, code in (("一面","FIRST"),("二面","SECOND"),("三面","THIRD"),("四面","FOURTH_PLUS")):
        hold(f"给我{name}的算法题列表",filters={"coding_focus":"ALGORITHM","round":code})
    old={"filters":{"response_form":"CODE","coding_focus":"ENGINEERING","round":"SECOND"},
        "sort":"frequency","last_plan":{"action":"LIST","top_n":25,"page_size":20}}
    hold("其他不变，只看一面",filters={"response_form":"CODE","coding_focus":"ENGINEERING","round":"FIRST"},count=25,session=old,page_size=20)
    hold("不练工程代码了，重新列出算法题，取消题数限制",filters={"coding_focus":"ALGORITHM"},session=old)
    hold("给这份清单翻一页",action="NEXT",session=old)
    hold("现在按照UI筛选展示题目",filters={"response_form":"SQL","coding_focus":"ENGINEERING"},
         explicit={"response_form":"SQL","coding_focus":"ENGINEERING"})
    hold("只列最近三个月的工程实现类题",filters={"coding_focus":"ENGINEERING","start_date":"2026-07-05","end_date":"2026-10-06"})
    hold("二面中属于Java分类、要求口述的题有哪些",filters={"topic_l1":"Java","round":"SECOND","response_form":"VERBAL"})
    for message in ["有哪些缓存击穿之后服务雪崩的排查问法", "找到不影响线上业务的OOM分析题", "如何设计Agent记忆的相关面试问法",
        "把最后一题保存为复习过", "请显示第三题的原始面经", "2023年字节问过什么", "最近四个月的数据库题", "力扣21有哪些面经提到",
        "只看大模型算法工程师这个岗位", "查询美团的手写SQL题", "筛选Java集合里的红黑树细分题",
        "发一份题目清单到邮箱", "先分析轮次，再挑十道薄弱题", "从第十一页往后翻", "看下前面那题到底是什么"]:
        hold(message,action="FALLBACK")
    assert len(test)==50
    assert len({r["message"] for r in calibration+test})==110
    return calibration,test


if __name__ == "__main__":
    p=argparse.ArgumentParser();p.add_argument("--output",type=Path,required=True);args=p.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    calibration,test=build()
    for split,cases in (("calibration",calibration),("test",test)):
        write_json(args.output/(split+".json"),{"split":split,"cases":cases,"origin":"authored business requests; not historical user traffic"})
    write_json(args.output/"manifest.json",{"schema":"fast_decision_gold_v1","status":"frozen","as_of":"2026-10-05",
        "version":args.output.name,
        "calibration_samples":60,"test_samples":50,"human_verified":False,"reviewer_kind":"agent",
        "threshold_policy":"Fit only on calibration; require at least 20 accepted and zero accepted errors. Do not relax for test.",
        "expected_plan_origin":"Directly authored QuerySpec; independent of JevPlanner.decode",
        "authorization":"Agent review authorized; private instruction omitted.",
        "contract_adjudication":"Three calibration continuations and one unseen test continuation retain saved page_size=20. v1 omitted that contract; retain its results. v2 is sealed before v5 inference and before any test inference."})
    write_json(args.output/"freeze.json",{"file_hashes":{file.name:digest(file.read_bytes()) for file in sorted(args.output.iterdir()) if file.name!="freeze.json"}})
    print({"dataset":str(args.output),"calibration":60,"test":50})
