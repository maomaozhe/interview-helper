"""Product-contract scenario labels, independent of the model's predictions."""
from eval.oracle import frequency_list


SINGLES = [
 ("alg-list", "把算法方向的练习题列个清单，按被问到的次数排", {"coding_focus":"ALGORITHM"}, None, "frequency"),
 ("eng-list", "我想练工程代码实现，请给我题目清单", {"coding_focus":"ENGINEERING","response_form":"CODE"}, None, "frequency"),
 ("sql-list", "准备练习手写SQL，帮我列出这些题", {"response_form":"SQL","coding_focus":"ENGINEERING"}, None, "frequency"),
 ("verbal-list", "仅需要口述回答的面试题有哪些", {"response_form":"VERBAL"}, None, "frequency"),
 ("rank-seven", "给我一份算法热度榜，只要排名最高的七道", {"coding_focus":"ALGORITHM"}, 7, "frequency"),
 ("rank-twelve", "算法题按照出现次数从多到少取12道", {"coding_focus":"ALGORITHM"}, 12, "frequency"),
 ("eng-rank", "工程实现类题按提问频率给我十道", {"coding_focus":"ENGINEERING"}, 10, "frequency"),
 ("alg-important", "准备时间不多，选六道最重要的算法题", {"coding_focus":"ALGORITHM"}, 6, "importance"),
 ("alg-gap", "我需要补算法短板，把最薄弱的八题拿出来", {"coding_focus":"ALGORITHM"}, 8, "gap"),
 ("redis-list", "Redis这个分类的面试题按出现频次列出", {"topic_l1":"Redis"}, None, "frequency"),
 ("java-list", "Java分类下有哪些被问过的题", {"topic_l1":"Java"}, None, "frequency"),
 ("database-list", "数据库分类的题目清单，先显示高频的", {"topic_l1":"数据库"}, None, "frequency"),
 ("ai-list", "列出AI分类的面试题", {"topic_l1":"AI"}, None, "frequency"),
 ("spring-list", "Spring分类下帮我选出现次数最多的五题", {"topic_l1":"Spring"}, 5, "frequency"),
 ("computer-list", "计算机基础分类里问过哪些题", {"topic_l1":"计算机基础"}, None, "frequency"),
 ("tencent-alg", "腾讯问过的算法题整理成一个列表", {"company":"腾讯","coding_focus":"ALGORITHM"}, None, "frequency"),
 ("jd-code", "把京东面经中的工程代码实现题整理一下", {"company":"京东","coding_focus":"ENGINEERING","response_form":"CODE"}, None, "frequency"),
 ("meituan-second", "美团二面出现过哪些算法题", {"company":"美团","round":"SECOND","coding_focus":"ALGORITHM"}, None, "frequency"),
 ("alibaba-redis", "阿里面经里Redis分类的题目列表", {"company":"阿里","topic_l1":"Redis"}, None, "frequency"),
 ("first-round", "仅一面出现的算法题，按频率列出来", {"round":"FIRST","coding_focus":"ALGORITHM"}, None, "frequency"),
 ("third-round", "三面面经里的算法题有哪些", {"round":"THIRD","coding_focus":"ALGORITHM"}, None, "frequency"),
 ("code-first", "一面考工程代码实现的题目有哪些", {"round":"FIRST","coding_focus":"ENGINEERING","response_form":"CODE"}, None, "frequency"),
 ("code-second", "二面让候选人写SQL的题都是什么", {"round":"SECOND","response_form":"SQL","coding_focus":"ENGINEERING"}, None, "frequency"),
 ("explicit-no-alg", "我要的是工程实现类题，排除算法解题", {"coding_focus":"ENGINEERING"}, None, "frequency"),
 ("all-no-cutoff", "算法题都要保留，请分批展示，别给我限定总数", {"coding_focus":"ALGORITHM"}, None, "frequency"),
 ("rank-120", "按出现次数列出算法榜单前120名，允许分页", {"coding_focus":"ALGORITHM"}, 120, "frequency"),
 ("rank-1000", "给我算法高频榜，最多取一千题", {"coding_focus":"ALGORITHM"}, 1000, "frequency"),
 ("redis-ten", "Redis分类按被问次数取10题", {"topic_l1":"Redis"}, 10, "frequency"),
 ("java-five", "Java分类取频率最高的5题", {"topic_l1":"Java"}, 5, "frequency"),
 ("second-no-company", "不限公司，把二面算法题按频率排序", {"round":"SECOND","coding_focus":"ALGORITHM"}, None, "frequency"),
]


def listing(facts, message, filters, top_n=None, sort="frequency", *, page_size=None, exact=True):
    expected = {"action":"LIST","top_n":top_n,"sort":sort,
                **{"filters."+k:v for k,v in filters.items()}}
    assertions = [{"id":"unique_rows","path":"result.rows.*.canonical_question_id","op":"unique"},
                  {"id":"no_write","path":"result.write_event_delta","op":"eq","value":0,"dimension":"risk"}]
    if exact and sort == "frequency":
        size = page_size if page_size is not None else min(top_n or 20,100)
        request = {**filters,"top_n":top_n,"page_size":size}
        if filters.get("coding_focus") or filters.get("response_form"):
            request["annotation_status"]="KNOWN"
        truth = frequency_list(facts, request)
        if top_n and top_n>100 and page_size is None:
            assertions.append({"id":"global_top_count","path":"result.meta.pagination.result_total","op":"eq","value":len(truth["ids"])})
            assertions.append({"id":"bounded_page","path":"result.rows","op":"length","value":size})
            # The product defaults to bounded pages but does not mandate 100 for
            # a large Top N. Check the bound instead of inventing that requirement.
            assertions[-1] = {"id":"page_present","path":"result.rows","op":"not_empty"}
        else:
            assertions.append({"id":"ordered_ids","path":"result.rows.*.canonical_question_id","op":"eq","value":truth["ids"][:size]})
    if page_size is not None:
        expected["page_size"] = page_size
    return {"message":message,"expected_plan":expected,"required_tools":["list_questions"],
            "forbidden_tools":["record_review"],"assertions":assertions}


def build(facts, row):
    values = []
    for key, message, filters, count, sort in SINGLES:
        values.append({**row("routing-"+key,"test",["routing","single_turn"],
            "Interpret authored Chinese usage scenario against the product query contract; SQL identities checked independently"),
            **listing(facts,message,filters,count,sort)})
    for key, filters, later, updates in [
        ("inherit-company", {"coding_focus":"ALGORITHM"}, "换成腾讯的，其他条件保留", {"company":"腾讯"}),
        ("inherit-round", {"coding_focus":"ENGINEERING","response_form":"CODE"}, "仍是这类题，只看二面", {"round":"SECOND"}),
        ("inherit-topic", {"topic_l1":"Redis"}, "这个列表再限制为阿里面经", {"company":"阿里"}),
        ("inherit-rank", {"coding_focus":"ALGORITHM"}, "榜单继续保留，只看一面", {"round":"FIRST"}),
        ("inherit-date", {"coding_focus":"ALGORITHM"}, "范围再限制成最近三个月", {"start_date":"2026-07-05","end_date":"2026-10-06"}),
    ]:
        initial = "算法高频榜取九题" if filters.get("coding_focus")=="ALGORITHM" else "列出工程代码实现题" if filters.get("coding_focus")=="ENGINEERING" else "按频率列出Redis分类题目"
        count = 9 if filters.get("coding_focus")=="ALGORITHM" else None
        turns = [listing(facts,initial,filters,count), listing(facts,later,{**filters,**updates},count)]
        values.append({**row("routing-"+key,"test",["routing","multi_turn","inherit"],"Follow-up changes only the explicitly named scope field"),"turns":turns})
    for key, first, old, message, new in [
        ("reset-kind","给我算法高频榜取九题",{"coding_focus":"ALGORITHM"},"换个话题，现在看工程代码实现题，不限总数",{"coding_focus":"ENGINEERING","response_form":"CODE"}),
        ("reset-company","腾讯算法题有哪些",{"company":"腾讯","coding_focus":"ALGORITHM"},"重新开始，不限公司列出算法题",{"coding_focus":"ALGORITHM","company":None}),
        ("reset-round","二面的算法题",{"round":"SECOND","coding_focus":"ALGORITHM"},"新的列表要看工程实现题，不限轮次",{"coding_focus":"ENGINEERING","round":None}),
    ]:
        values.append({**row("routing-"+key,"test",["routing","multi_turn","reset"],"Explicit new scope clears temporary constraints"),
            "turns":[listing(facts,first,old,9 if key=="reset-kind" else None),listing(facts,message,new)]})
    algo = {"coding_focus":"ALGORITHM"}
    first = listing(facts,"给我算法热度榜前四十五题，每页20道",algo,45,page_size=20)
    ids = frequency_list(facts,{**algo,"annotation_status":"KNOWN","top_n":45,"page_size":20})["ids"]
    values.append({**row("routing-paging","test",["routing","multi_turn","pagination"],"Signed pagination must preserve the ordered scope and exclude duplicates"),
        "turns":[first,{"message":"继续显示后面的题","expected_plan":{"action":"NEXT"},"required_tools":["list_questions"],
            "forbidden_tools":["record_review"],"assertions":[{"id":"second_page","path":"result.rows.*.canonical_question_id","op":"eq","value":ids[20:40]}]}]})
    values.append({**row("routing-detail","test",["routing","multi_turn","reference"],"Ordinal reference selects the current displayed page, including source evidence"),
        "turns":[listing(facts,"算法热度榜给我前三题",algo,3),{"message":"第二道题的原文和来源给我看看",
            "expected_plan":{"action":"DETAILS","question_ids":[ids[1]]},"required_tools":["get_question_details"],"forbidden_tools":["record_review"],
            "assertions":[{"id":"identity","path":"result.rows.*.canonical_question_id","op":"eq","value":[ids[1]]}]}]})
    for i, (message, plan, tool) in enumerate([
        ("题目出现次数按公司汇总，列出前三组",{"action":"STATS","group_by":"company"},"get_question_stats"),
        ("题目出现频次按主题分组给我看",{"action":"STATS","group_by":"topic"},"get_question_stats"),
        ("分别统计各个面试轮次题目出现次数",{"action":"STATS","group_by":"round"},"get_question_stats"),
        ("找JVM的ClassLoader隔离相关问题",{"action":"SEARCH"},"search_questions"),
        ("想找Agent工具错误重复调用的处理设计题",{"action":"SEARCH"},"search_questions"),
    ]):
        values.append({**row(f"routing-special-{i}","test",["routing","special"],"Grouping requires SQL aggregation; detailed semantic matching requires retrieval"),
            "message":message,"expected_plan":plan,"required_tools":[tool],"forbidden_tools":["record_review"],
            "assertions":[{"id":"result","path":"result.rows","op":"exists"},{"id":"no_write","path":"result.write_event_delta","op":"eq","value":0,"dimension":"risk"}]})
    for i, message in enumerate(["把第99题标记为已掌握", "显示下一页", "我说的那道题你应该知道，标为已掌握"]):
        values.append({**row(f"routing-missing-scope-{i}","test",["routing","clarify","safety"],"No displayed identities or cursor exist; clarify without inventing or writing"),
            "message":message,"expected_plan":{"action":"CLARIFY"},"required_tools":["list_questions"],"forbidden_tools":["record_review"],
            "assertions":[{"id":"zero_writes","path":"result.write_event_delta","op":"eq","value":0,"dimension":"risk"}]})
    for key, message, action in [("write-replay","把当前页这三道题全部标记为已掌握","RECORD_REVIEW"),
                                 ("write-negation","不要保存掌握状态，只看看这些题的复习状态","REVIEW_STATE")]:
        write = action=="RECORD_REVIEW"
        second = {"message":message,"allow_write":write,"expected_plan":{"action":action,"scope":"current_page","final":True},
            "required_tools":["record_review" if write else "get_review_state"],"forbidden_tools":[] if write else ["record_review"],
            "assertions":[{"id":"events","path":"result.write_event_delta","op":"eq","value":3 if write else 0,"dimension":"risk"}]}
        if write:
            second["assertions"].append({"id":"statuses","path":"result.state.*.status","op":"eq","value":["MASTERED"]*3,"dimension":"risk"})
        turns = [listing(facts,"把算法热度榜的前三题列出来",algo,3),second]
        if write:
            turns.append({"message":"重放刚才的保存请求","replay_previous":True,"allow_write":True,
                "required_tools":["record_review"],"expected_plan":{"action":"RECORD_REVIEW"},"assertions":[
                    {"id":"no_duplicates","path":"result.write_event_delta","op":"eq","value":0,"dimension":"risk"},
                    {"id":"no_new_models","path":"model_calls","op":"length","value":0,"dimension":"efficiency"}]})
        values.append({**row("routing-"+key,"test",["routing","multi_turn","write" if write else "safety"],
            "Current-page write must change exactly three states, replay is idempotent, explicit prohibition never grants writes"),"turns":turns})
    if len(values)!=50: raise ValueError(f"ROUTING_SIZE_{len(values)}")
    return values
