"""Explicit Agent decisions after reading fresh arm candidates and source.

These are tied to the 2026-10-14 sealed experiment hashes, not to old published
outputs. Gold is unchanged. Null remains a real, scored unmatched decision.
"""
from pathlib import Path

from eval.common import read_json, read_jsonl, write_json
from eval.validate_gold import require


ROOT = Path("data/reports/source-optimization-20261014-v1/review")
HASHES = {"baseline":"bfba7d8079d6f2a5ade3e9c6a10f9e722795387c03b4eeed8a133ca9b389325d",
          "candidate":"fb92456b42eff3a106d35de31ac45133316d545f17d377edf6b4680b4ec0fa96"}

# Gold suffix -> actual fresh prediction ID; every nonexact candidate was read.
BASELINE_MATCHES = {
 "dee8c70df3c5-039":("interview_1/q30","同一场次正文Max Value与OCR最大值完全等义，窗口大小k和输出要求均保留。"),
 "05c5117fc1a7-011":("interview_1/q10","SSE原理任务一致；raw多带候选人的gRPC评论，normalized排除评论，无独立任务增删。"),
 "2ae881c215b3-003":("interview_1/q3","附括号说明自研Agent框架，仍为同一通信实现任务，不是新增约束。"),
 "2ae881c215b3-005":("interview_1/q5","URL输入到渲染任务完整一致；一面也问过是回忆评论，不是本场另一个任务。"),
 "2ae881c215b3-007":("interview_1/q7","文件第四列userid及top10目标一致；多带候选人忘命令评论，normalized没有改变任务。"),
 "2ae881c215b3-009":("interview_1/q9","补问编号与候选人回答尾部不改变未来规划问题，normalized保持原意。"),
 "89a73496fa57-009":("interview_1/q8","同一面第一段实习项目优化任务；仅多带聊了很久的评论，保持同一意图。"),
 "31bee07b8745-003":("i1/q3","整体工作介绍和广告链路图为已冻结同一任务；多带候选人评论但normalized保持完整要求。"),
 "22f55936b83d-001":("interview_1/q1","原文明确要求谈实习经历；机器raw附没有自我介绍及评价评论，未变成新的提问。"),
 "b21ef96ac227-003":("i1/q2","项目高可用任务一致；raw附作者回答的容灾示例，normalized不补该答案为要求。"),
}
for gold, pred in [("001",1),("002",2),("003",3),("008",6),("011",8),("012",9),
                  ("013",10),("014",11),("015",12),("016",13),("017",14),("018",15),
                  ("021",17),("022",18),("023",19),("024",20),("025",21),("026",22)]:
    BASELINE_MATCHES["feb46aa8bbcf-"+gold]=(f"i1/q{pred}","逐字任务一致；唯一差别为原文紧接文字的列表数字编号，主体和条件未变。")
for gold, pred in [("005",5),("006",6),("008",8),("009",9),("010",10),("012",12),
                  ("013",13),("015",15),("016",16),("020",20)]:
    BASELINE_MATCHES["8e7c88f2afee-"+gold]=(f"interview_1/q{pred}","原文追问标志仅影响关系证据，问题本身的对象、故障/流量条件与完整要求一致。")

BASELINE_SESSIONS = {
 "f490e8859cbb":["interview_1"], "9f6069fa67a0":["i1"], "dee8c70df3c5":["interview_1"],
 "6d2b25ee4cac":["interview_1","interview_2",None], "05c5117fc1a7":["interview_1"],
 "d3e713a6755b":[None,None], "2ae881c215b3":["interview_1"], "89a73496fa57":["interview_1",None,None],
 "22b689ef4d58":["interview_1"], "a1f730f7b65f":["interview_1"], "bc95d875cdf7":["i1"],
 "54424b155e92":["interview_1"], "6ea21d1f2cd8":["i1"], "9d8c0147783d":["interview_1"],
 "feb46aa8bbcf":["i1"], "8e7c88f2afee":["interview_1"], "e03e96433274":["pdd_second_round"],
 "6a559eecded4":["interview_1"], "d8c9c83c36fd":["i1"], "ff26c73c49df":["i1"],
 "31bee07b8745":["i1","i2"], "22f55936b83d":["interview_1"], "377b55fc75c6":["interview_1"],
 "600f370d4a8d":["interview_1"], "e42e40eae1a1":["interview_1"], "1acb80963445":["jd_llm_1"],
 "b21ef96ac227":["i1"], "4ba41a322255":["interview_1"], "1c57bc62c368":["i1"],
 "e21ddda69d4f":[], "b32d199485c1":[None,None],
}

CANDIDATE_MATCHES = {
 "05c5117fc1a7-011":("I1/Q10","同一SSE原理问题；raw附候选人评论，normalized完整保留问题而未引入评论为约束。"),
 "2ae881c215b3-003":("I1/Q3","同一自研Agent通信框架的实现说明；括号是已明确的项目指代，未改变任务。"),
 "2ae881c215b3-005":("I1/Q5","URL到页面渲染整个流程一致；一面也问过为回忆评论，不另造问题。"),
 "2ae881c215b3-007":("I1/Q7","第四列userid/top10要求完整一致；raw带回答忘记的评论，normalized未改目标。"),
 "2ae881c215b3-009":("I1/Q9","未来规划提问完整一致；编号和候选人作答尾部不变成新要求。"),
 "89a73496fa57-009":("i1/q8","第一轮的项目优化要求一致；仅附讨论时长评论。"),
 "89a73496fa57-016":("i2/q15","第二轮Agent框架与跨部门规范设计要求完整一致；括号是作者评论，normalized未改变任务。"),
 "89a73496fa57-023":("i2/q21","第二轮在Momenta实习做什么的完整回忆一致；前部概述属相同已有项目背景，不是另一独立要求。"),
 "89a73496fa57-025":("i2/q23","二面二叉树右视图手写任务一致；机器raw额外含原文手撕章节标题，无额外算法。"),
 "31bee07b8745-003":("i1/q3","一面整体工作与广告推送链路图任务完整一致；附作者评论未变成约束。"),
 "31bee07b8745-009":("i2/q5","二面知识库随需求改动动态更新的设计任务完整一致；raw末尾话题标签被normalized排除。"),
 "22f55936b83d-001":("I1/Q1","原文确为要求谈实习经历；raw带作者对面试风格的复盘，normalized保持该要求。"),
 "377b55fc75c6-013":("I1/Q13","反转链表任务一致，原文全文明确最后是手撕环节；作者编译错误评论不增加算法条件。"),
 "b21ef96ac227-003":("I1/Q2","项目高可用提问一致；raw含作者答案举例，normalized不把这些实现当题干条件。"),
 "b32d199485c1-003":("I2/Q1","二面回忆的前端按钮数据传输协议相同；‘一些’只是叙述修饰，无新任务或条件。"),
 "b32d199485c1-005":("I2/Q3","二面通信协议提问相同；‘特别爱问’为作者回忆叙述，无新任务。"),
 "b32d199485c1-007":("I2/Q5","二面要求现场登录自己网站抓包相同；‘甚至’是作者叙述，不改变实际操作要求。"),
}
for gold, pred in [("003",3),("007",7),("008",8),("009",9),("010",10),("011",11),
                  ("012",12),("013",13),("014",14),("015",15),("016",16),("017",17),
                  ("019",19),("020",20),("023",23),("024",24),("026",26)]:
    CANDIDATE_MATCHES["8e7c88f2afee-"+gold]=(f"i1/q{pred}","逐字提问与源中全部对象/故障条件一致；raw额外包含作者回答或感受，normalized没有新增其内容为任务。")

CANDIDATE_SESSIONS = {
 "f490e8859cbb":["I1"], "9f6069fa67a0":["i1"], "dee8c70df3c5":[None],
 "6d2b25ee4cac":["i1","i2",None], "05c5117fc1a7":["I1"], "d3e713a6755b":[None,None],
 "2ae881c215b3":["I1"], "89a73496fa57":["i1","i2","i3"], "22b689ef4d58":["I1"],
 "a1f730f7b65f":["I1"], "bc95d875cdf7":["I1"], "54424b155e92":["i1"],
 "6ea21d1f2cd8":["I1"], "9d8c0147783d":["I1"], "feb46aa8bbcf":["I1"],
 "8e7c88f2afee":["i1"], "e03e96433274":["i1"], "6a559eecded4":[None],
 "d8c9c83c36fd":["I1"], "ff26c73c49df":["I1"], "31bee07b8745":["i1","i2"],
 "22f55936b83d":["I1"], "377b55fc75c6":["I1"], "600f370d4a8d":["I1"],
 "e42e40eae1a1":["i1"], "1acb80963445":[None], "b21ef96ac227":["I1"],
 "4ba41a322255":["I1"], "1c57bc62c368":["I1"], "e21ddda69d4f":[], "b32d199485c1":["I1","I2"],
}


def write_decisions(arm, matches, session_choices):
    output=ROOT/arm
    decisions=read_json(output/"decisions.template.json")
    require(decisions["predictions_sha256"]==HASHES[arm],"fresh arm hash changed")
    todo=read_jsonl(output/"question.review.jsonl");session_rows=read_jsonl(output/"session.review.jsonl")
    decisions["exact_proposals_reviewed"]=True
    for item in todo:
        suffix=item["gold_id"].removeprefix("g-new-")
        if suffix in matches:
            pid,basis=matches[suffix]
            require(pid in {c["id"] for c in item["candidates"]},"reviewed fresh match outside source candidates")
        else:
            pid=None
            basis=("本arm真实模型在有界重试后失败，本篇没有可对齐的成功输出；保留全部FN，不用其它arm或旧预测替补。" if item["prediction_failed"] else
                   "已逐条阅读原文和本arm同证据候选：没有对应提问输出，保持缺失。" if not item["candidates"] else
                   "已逐条阅读原文及本arm全部同跨度候选：候选合并多个独立任务或分裂/遗漏整体条件，不能以部分词重合代替严格一对一。")
        decisions["question_decisions"][item["gold_id"]]={"prediction_id":pid,"basis":basis}
    for row in session_rows:
        prefix=row["id"].removeprefix("extract-new-")
        choices=session_choices[prefix]
        require(len(choices)==len(row["gold_sessions"]),"explicit session list does not cover original rounds")
        for gold, pid in zip(row["gold_sessions"],choices):
            basis=("本arm无对应独立场次（失败、漏场或将多个明确轮次压成一个），保持未匹配，不合并gold场次。" if pid is None else
                   "逐原文场次标题与本arm题目实际行范围审核，绑定同一个真实事件；元信息错误另评分，不借对齐修正标签。")
            if arm=="baseline" and prefix=="89a73496fa57" and gold["id"]=="s1":
                basis="机器把一面/二面/HR合为一个未定轮次场次；仅锚定首个有明确原文证据的一面，后续两轮均留空，绝不把三个gold场次映射同一个机器场次。"
            decisions["session_decisions"][row["id"]][gold["id"]]={"prediction_id":pid,"basis":basis}
    if arm=="baseline":
        # Exact string identity does not override a missing round boundary.
        for row in read_jsonl(output/"alignment.proposals.jsonl"):
            if row["id"]!="extract-new-89a73496fa57":continue
            for pair in row["pairs"]:
                number=int(pair["gold_id"].rsplit("-",1)[1])
                if number>=16:
                    decisions["question_decisions"][pair["gold_id"]]={"prediction_id":None,
                      "basis":"逐字题干虽存在，但机器将二面/HR放入已锚定一面的同一场次；不能通过对齐合并明确的不同轮次。"}
        for item in todo:
            if item["gold_id"].startswith("g-new-89a73496fa57-") and int(item["gold_id"].rsplit("-",1)[1])>=16:
                decisions["question_decisions"][item["gold_id"]]={"prediction_id":None,
                    "basis":"本题属于原文明示二面/HR，但本arm仅有一个跨轮场次，已仅锚定首轮；为保持独立场次严格一对一，保留未匹配。"}
    write_json(output/"decisions.json",decisions)
    print({"arm":arm,"explicit_question_decisions":len(decisions["question_decisions"]),"manual_nonexact_matches":len(matches)})


if __name__=="__main__":
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument("--arm",choices=("baseline","candidate"),required=True)
    args=parser.parse_args()
    if args.arm=="baseline":write_decisions(args.arm,BASELINE_MATCHES,BASELINE_SESSIONS)
    else:write_decisions(args.arm,CANDIDATE_MATCHES,CANDIDATE_SESSIONS)
