"""Source-first Agent review of 30 new extraction sources, plus one negative.

The authored rows below were made after reading each complete frozen Markdown
source. No published question/session/task output is read while materializing.
Line/quote selection is an authoring convenience; frozen rows use real spans.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from eval.common import digest, inside, read_json, read_jsonl, write_json, write_jsonl
from eval.validate_gold import load_dataset, require, validate_gold
from evals.agent_gold.reviewed_extraction import TOPICS, TYPES
from interview_intelligence.ingestion.snapshot import decode_source

SEED = "source-review-20261014-v1:"
INSTRUCTION = "你先把你能做的能完善的完善好，先就由你自己来审核，找gold set，但是一定要真实的去面经里找了"
GUIDE = "annotation_v2_delegated_source_review_20261014"
FACTS_HASH = "0b8fd1b94127c90f0c0c463295c1dc8045184d73eb8475bb91f6eca88c67501a"

# rank, company, literal role, sessions (id, round, first, last), authored questions.
# question DSL: line TOPIC:TYPE[:task]; absent task = VERBAL/NONE.
# C = CODE/ALGORITHM; CE = CODE/ENGINEERING; E = VERBAL/ENGINEERING;
# A = VERBAL/ALGORITHM; U = UNKNOWN/ALGORITHM; SQL = SQL/ENGINEERING.
DOCUMENTS = [
 (0,"小米","Java",[("s1","SECOND",1,51)],
  "17 HR:H;19 PJ:J;21 JT:D:E;23 JT:D:E;25 JT:P;27 JT:D:E;29 MM:J;31 SB:P;33 PJ:J;35 IA:J;37 IM:I;39 AC:A:C;41 HR:H;43 HR:H"),
 (1,"腾讯","agent",[("s1","FIRST",1,19)],
  "7 IA:J;8 IS:S;9 IR:I;10 IR:I;11 IR:I;12 IR:I;13 IS:S;14 IS:I;15 IA:D:E;16 IA:I;17 IM:I;18 IA:I"),
 (2,"阿里","Java Agent研发",[("s1","FIRST",1,211)],
  "65 HR:H;71 JV:P;75 JC:P;77 JC:P;79 JT:K;83 JT:K;87 JB:P;89 JV:K;95 IA:J;101 IA:J;105 IA:J;109 IA:J;117 SB:P;121 SI:P;127 SA:P;131 SI:K;133 SI:J;137 RD:K;141 RD:K;145 RC:K;149 RC:S;153 IS:J;157 DI:P;161 DQ:S;167 IA:I;171 IA:I;175 IA:S;179 IM:I;183 IA:I;189 AA:A:C"),
 (3,"唯品会","Java",[("s1","FIRST",9,28),("s2","SECOND",30,34),("s3","HR",35,36)],
  "10 JT:K;11 JT:P;12 JT:K;13 JT:K;14 JT:K;15 JT:S;16 JT:K;17 JT:P;18 JT:K;19 SA:P;20 SA:S;21 SA:S;22 ST:S;23 ST:P;24 DA:S;25 DA:S;26 FR:D:E;27 RF:K;28 RH:S;32 PJ:J;33 SH:A:CE"),
 (4,"字节","后端",[("s1","FIRST",1,26)],
  "8 IR:I;9 IR:J;10 IR:J;11 IR:J;12 IR:J;13 IR:J;14 IR:I;15 IS:J;16 IS:J;17 CN:P;18 RC:K;19 RC:K;20 RD:P;21 AS:A:C;22 CO:K;23 JV:K;24 JV:P"),
 (5,"bigo","后端开发",[("s1","SECOND",14,34),("s2","FIRST",37,95)],
  "16 HR:H;20 PJ:J;21 PJ:J;22 PJ:J;23 PJ:J;24 PJ:J;25 RL:J;26 RL:J;27 PJ:J;28 PJ:J;29 PJ:J;30 PP:J;31 PP:J;32 FR:J;33 SH:J;39 HR:H;45 IA:J;46 PJ:J;47 MM:K;48 MM:S;49 PP:J;50 RD:P;51 RL:K;52 RH:J;53 FS:K;54 RP:K;55 RF:S;56 RD:K;60 DI:K;61 DQ:S;62 RC:S;63 RC:S;64 DT:K;65 DV:P;66 SF:S;67 SAH:D:E;68 JV:K;86 CN:K;87 CN:P;88 CN:K;89 JB:K;90 JC:P;91 JT:P;92 JC:K;93 AS:A:A"),
 (6,"百度","后端",[("s1","SECOND",1,23)],
  "11 HR:H;12 PJ:J;13 IA:J;14 FR:K;15 CN:P;16 AC:D:A;17 CL:D:E;21 IA:J;22 HR:H"),
 (7,"字节","大模型agent开发",[("s1","FIRST",35,51),("s2","SECOND",55,65),("s3","HR",78,85)],
  "37 PJ:J;38 RC:J;39 RL:J;40 PP:J;41 SH:J;43 PJ:J;44 IA:J;45 PP:J;46 IA:J;47 IA:I;48 IA:I;49 IA:I;50 IA:I;51 AT:A:C;56 IS:D:E;57 IA:I;58 IA:J;59 IL:S;60 IL:I;61 IS:I;62 PJ:J;63 HR:H;65 AT:A:C;79 HR:H;80 HR:H;81 HR:H;82 HR:H;83 HR:H;84 HR:H"),
 (8,"百度","后端开发",[("s1","SECOND",1,53)],
  "13 HR:H;15 HR:H;17 HR:H;19 AT:A:C;21 PJ:J;23 IA:J;25 IA:J;27 IA:J;29 PJ:J;31 CO:J;33 PJ:J;35 SF:S;37 SF:S;39 DA:J;41 HR:H;43 IL:S;45 HR:H;47 HR:H;49 HR:H;51 HR:H"),
 (9,"小红书","agent",[("s1","FIRST",1,30)],
  "13 HR:H;14 IA:J;15 IR:S;16 IR:I;17 IR:I;18 SC:J;19 IR:J;20 IA:J;21 IM:I;22 IR:I;23 JV:P;24 SF:S;25 JB:K;26 RH:K;27 RH:K;28 IS:D:E;29 RC:K;30 AG:A:C"),
 (10,"拼多多","服务端",[("s1","FIRST",1,48)],
  "10 HR:H;11 PJ:J;12 PJ:J;13 PJ:J;14 PJ:J;15 CN:P;16 HR:H;17 PJ:J;19 IA:J;20 PJ:J;21 IM:J;22 IM:J;24 DA:J;25 SB:J;26 PJ:J;27 PJ:J;28 PJ:J;30 PJ:J;31 PJ:J;32 PJ:J;34 PF:J;35 PF:J;36 PF:J;37 SF:D:E;39 HR:H"),
 (11,"百度","后端",[("s1","FIRST",1,25)],
  "7 IL:I;8 IL:I;9 IL:I;10 CO:P;11 CO:P;12 CO:P;13 CO:P;14 JT:P;15 JT:S;16 CN:K;17 CN:P;18 CN:P;19 CN:P;20 CN:S;21 FS:P;22 RP:K;23 RH:P;24 DQ:K;25 AA:A:C"),
 (12,"字节","后端",[("s1","THIRD",1,20)],
  "9 RD:P;10 RD:K;11 RD:K;12 RH:K;13 RH:P;14 RH:K;15 RF:S;16 SH:J;17 SC:D:E;18 PJ:J;19 AL:A:U"),
 (13,"字节","后端",[("s1","SECOND",1,17)],
  "7 HR:H;8 PJ:J;9 CO:P;10 CO:K;11 CO:S;12 UN:K;13 AA:A:C;14 AA:A:C;15 AC:A:C"),
 (14,"腾讯","后端开发",[("s1","FIRST",1,34)],
  "13 HR:H;14 PJ:J;15 PJ:J;16 IR:J;17 IR:J;18 MK:J;19 IM:J;20 IL:I;21 IL:J;22 IA:I;23 HR:H;24 CN:K;25 MK:P;26 MK:K;27 RD:P;28 RP:P;29 JT:P;30 JT:K;31 JT:P;32 CN:S;33 CN:J;34 AC:A:C"),
 (15,"字节","后端",[("s1","FIRST",1,67)],
  "13 HR:H;15 PJ:J;17 MM:J;19 FR:J;21 FR:J;23 FR:K;25 FR:J;27 FR:K;29 FR:P;31 FR:P;33 RC:J;35 RC:S;37 FC:S;39 RC:S;41 RC:S;43 RC:S;45 IR:I;47 IL:S;49 JI:P;51 JI:S;53 PJ:J;55 IA:K;57 IA:I;59 DQ:S;61 DI:S;63 MM:K;65 CN:P;67 AC:A:C"),
 (16,"拼多多",None,[("s1","SECOND",1,22)],
  "9 FT:J;10 SH:D:E;11 FS:S;12 UN:A:U"),
 (17,"字节","后端",[("s1","FIRST",1,25)],
  "18 HR:H;19 PJ:J;20 JT:K;21 JT:S;22 DT:K;23 DI:K;24 DQ:A:SQL;25 AC:A:C"),
 (18,"去哪儿旅行","AI应用开发",[("s1","FIRST",1,16)],
  "7 IL:I;8 IS:D:E;9 IA:S;10 IS:I;11 DT:K;12 DT:P;13 JT:K;14 JT:P;15 DT:K;16 AA:A:U"),
 (19,"字节","后端",[("s1","FIRST",1,27)],
  "7 PJ:J;8 IA:J;9 IA:I;10 IM:I;11 IM:I;12 IM:J;13 IA:I;14 IA:J;15 IA:J;16 IA:J;17 PJ:J;18 RL:J;19 FC:S;20 FC:K;21 RH:P;22 RH:P;23 RH:K;25 AL:A:C;26 AL:A:C"),
 (20,"滴滴","后端开发",[("s1","FIRST",9,13),("s2","SECOND",14,20)],
  "10 RH:K;11 PJ:J;12 PJ:J;13 AC:A:U;16 IA:I;17 IA:S;18 IA:I;19 IM:J;20 IS:D:E"),
 (21,"字节","后端",[("s1","FIRST",1,23)],
  "15 PJ:J;16 FI:D:E;17 RF:P;18 DQ:S;19 CD:D:E;20 IA:K;21 SC:A:C"),
 (22,"字节","后端开发",[("s1","FIRST",1,24)],
  "8 HR:H;9 PJ:J;10 CN:K;11 CN:S;12 CN:P;13 CN:S;14 CO:K;15 SF:S;16 RP:K;17 DI:P;18 DT:K;19 CO:K;20 AL:A:C"),
 (23,"去哪儿", "后端开发",[("s1",None,1,38)],
  "14 HR:H;15 PJ:J;16 JB:P;17 JV:K;18 JT:P;19 CD:K;20 DT:P;21 DI:S;22 DQ:S;23 DA:D:E;24 JC:P;25 JB:K;26 JB:K;27 RP:K;28 DI:P;29 JT:K;30 IP:S;31 IS:I;32 IR:I;33 IR:S;34 IR:S;35 IR:J;36 IR:I;37 DQ:K;38 AA:A:U"),
 (24,"字节",None,[("s1",None,1,17)],
  "7 PJ:J;8 IR:J;9 IA:J;10 IS:S;11 IL:S;12 IS:J;13 IA:I"),
 (25,"京东","大模型应用开发",[("s1","FIRST",1,24)],
  "7 HR:H;8 IR:I;9 PJ:J;10 IR:I;11 IR:S;12 IR:J;13 IR:I;14 IR:I;15 HR:H;16 IL:I;17 IL:I"),
 (26,"快手","Java后端",[("s1","FIRST",1,45)],
  "13 SH:J;15 SAH:J;17 PJ:J;19 RH:K;21 RH:P;23 RH:S;25 RH:P;27 MR:J;29 MR:K;31 MR:P;33 MP:S;35 PJ:J;37 JT:J;39 JT:K;41 DL:K;43 DQ:J;45 AC:A:C"),
 (27,None,"Agent",[("s1","THIRD",1,26)],
  "15 IA:J;16 IA:S;17 IR:J;18 AA:A:C;19 IA:I;20 IL:I;21 SC:A:CE;22 DT:K;23 CL:S;24 JT:S"),
 (28,"点点互动","后端开发",[("s1","FIRST",1,51)],
  "13 HR:H;15 PJ:J;17 IA:J;19 IA:J;21 IM:I;23 IM:J;25 IR:I;27 IR:I;29 JC:K;31 JC:P;33 JC:K;35 JV:K;37 JV:P;39 JV:P;41 JV:P;43 JV:K;45 JV:S;47 SD:D:E;49 RD:D:E"),
 (29,"快手","Java开发",[], ""),
 (30,"百度","后端",[("s1","FIRST",7,8),("s2","SECOND",10,11)], "8 PJ:J;11 CN:J"),
]

# Independent tasks in one printed/OCR line. Exact quotes prevent invented
# subjects/constraints. They replace the corresponding whole-line draft.
SPLITS = {
 (0,25): [("线程之间怎么实现安全通信？","JT:K"),("再从Java内存模型的角度解释一下","JT:P")],
 (0,29): [("实习的时候用过消息队列吗？","MM:J"),("kafka知道吗？","MK:K")],
 (0,43): [("目前手上offer？","HR:H"),("有哪些流程？","HR:H"),("base地北京可以接受吗？","HR:H")],
 (1,11): [("为什么使用向量数据库？与 MySQL 的区别是什么？","IR:I"),("PostgreSQL 对 Agent 有什么提升？","IA:I")],
 (1,13): [("如何处理大模型输出死循环问题？兜底策略是什么？","IS:S"),("如何让模型生成更优内容？","IL:I")],
 (2,71): [("说说 Java 的内存模型？","JT:P"),("堆和栈的区别是什么？","JV:K")],
 (2,75): [("HashMap 的底层实现讲一下？","JC:P"),("1.7 和 1.8 有什么区别？","JC:K")],
 (2,79): [("线程池的核心参数有哪些？","JT:K"),("拒绝策略有几种？","JT:K")],
 (2,87): [("Java 的动态代理有几种方式？","JB:K"),("你的 Agent 框架里用的是哪种？","IA:J")],
 (2,89): [("说说 JVM 的垃圾回收算法？","JV:P"),("你了解哪些垃圾收集器？","JV:K")],
 (2,109): [("你的 Agent 支持多轮对话吗？","IA:J"),("Memory 怎么管理的？","IA:J")],
 (2,121): [("Spring 的 IoC 容器启动流程你了解吗？","SI:P"),("Bean 的生命周期是怎样的？","SI:P")],
 (2,127): [("Spring AOP 的实现原理？","SA:P"),("和你项目里的 Tool 拦截有什么关系？","IA:J")],
 (2,171): [("Function Calling 的原理是什么？","IA:I"),("你怎么理解 Tool Use？","IA:I")],
 (3,19): [("aop使用了什么设计模式","SA:K"),("底层怎么实现的","SA:P")],
 (4,10): [("RAG系统是如何评估的？","IR:J"),("各模块的时延是多少？","IR:J")],
 (4,23): [("了解GC么？讲下GC的算法","JV:P"),("垃圾处理器相关","JV:K")],
 (5,45): [("ai工具的使用；superpowers使用情况；对工作有什么帮助","IA:J")],
 (5,93): [("有8个西瓜，有7个一样重，然后还有一个西瓜的重量比其他西瓜小，然后现在给你用这个天平，你最多用几次能够把它称出来。","AS:A:A"),("9个西瓜呢","AS:A:A")],
 (7,38): [("为什么扣减用 redis 做","RC:J"),("这个场景 kafka 和 rabbitmq 有什么区别","MK:J")],
 (7,60): [("讲一下Transformer架构","IL:I"),("和rag的Rerank过程的原理","IR:I")],
 (8,17): [("还在实习吗","HR:H"),("为什么离职？","HR:H")],
 (8,19): [("二叉树的直径","AT:A:C"),("最长无重复字符子串","AC:A:C")],
 (8,25): [("意图识别怎么路由？只选一个子 agent？","IA:J"),("怎么汇总？","IA:J")],
 (8,31): [("为什么双进程拆分？","CO:J"),("两进程怎么通信？","CO:J")],
 (8,45): [("为什么绩点排名高没有拿校一等/国奖？","HR:H"),("为什么没保研？","HR:H")],
 (9,18): [("你这两级缓存是怎么实现的","SC:J"),("具体性能能够提升多少","PP:J")],
 (9,23): [("讲一下什么是垃圾回收","JV:K"),("G1 和 CMS 有什么区别","JV:K")],
 (10,11): [("这个AI辅助开发项目几个人在做？","PJ:J"),("你的主要贡献是什么？","PJ:J")],
 (10,17): [("你写过Node.js服务吗？是SSR的吗？","PJ:J"),("那个全链路项目有涉及后端吗？","PJ:J")],
 (11,7): [("LLM 的推理过程大概是什么样的","IL:I"),("Prefill 和 Decode 阶段区别","IL:I")],
 (11,8): [("显存优化有哪些手段，量化了解吗","IL:I"),("int8 和 fp16 区别","IL:I")],
 (11,15): [("CAS ABA 问题怎么解决","JT:K"),("乐观锁适用场景","JT:K")],
 (11,21): [("负载均衡算法有哪些","FS:K"),("一致性哈希怎么解决扩容","FS:P")],
 (14,16): [("文档分块怎么做的","IR:J"),("怎么知道哪种方式最好","IR:J")],
 (14,17): [("向量化这块是怎么做到的，用的什么模型","IR:J"),("底层算法是啥","IR:I")],
 (14,19): [("mcp交互是怎么实现的","IM:J"),("模型怎么知道他要怎么操作mcp","IM:I")],
 (14,28): [("redis持久化实现","RP:P"),("数据结构有哪些","RD:K")],
 (18,15): [("mysql的undolog和redolog分别是干啥的","DT:K")],
 (23,30): [("如果我需要让ai查询一个sql，我该怎么做提示词优化","IP:S"),("如果查询的sql是错的怎么办","IS:S")],
 (23,32): [("rag的流程","IR:I"),("切块怎么做的","IR:I")],
 (25,8): [("顺着RAG和Multi Agent方向，说说你对它们认知和迭代是怎样的？","IR:I")],
 (25,11): [("如何处理问题输入不标准的情况？","IR:S"),("混合检索的权重怎么配置？","IR:I")],
 (25,12): [("向量检索的准召率如何保障？","IR:I"),("你使用的向量数据库之间的差异是什么？","IR:J")],
 (25,16): [("你认为AI未来的发展方向是什么？","IL:I"),("对AGI有何认知？","IL:I")],
 (26,13): [("介绍一下项目","PJ:J"),("在项目中是如何应对高流量以及突发流量的？","SH:J")],
 (26,17): [("项目中使用了哪些技术栈？","PJ:J"),("聊聊你对这些技术了解多少？","PJ:J")],
 (26,31): [("RocketMQ 的事务消息是如何实现的？","MR:P"),("有哪些场景适合使用事务消息？","MR:K"),("平时有使用到吗？","MR:J")],
 (26,39): [("线程池核心参数有哪些？","JT:K"),("你是如何评估参数值的？","JT:K"),("线程池任务提交流程是什么样的？","JT:P")],
 (27,15): [("请介绍你的Agent项目，包括所用框架、Prompt设计方法和工具调用实现方式。","IA:J")],
 (27,17): [("你们RAG的向量数据库用的什么？","IR:J"),("检索链路怎么做的？","IR:J"),("召回不准怎么办？","IR:S")],
 (27,19): [("请解释Agent的Memory机制","IA:I"),("工具调用原理","IA:I"),("ReAct框架。","IA:I")],
 (27,20): [("大模型怎么学会调用外部工具的？","IA:I"),("SFT和RLHF分别解决什么问题？","IL:I")],
 (27,22): [("请解释数据库事务的隔离级别","DT:K"),("锁机制。","DL:K")],
 (30,8): [("全程问项目","PJ:J"),("做了一个数据库的小题目","UN:O")],
 (30,11): [("一些前端按钮数据传输的协议","CN:J"),("第三方用户托管平台怎么传输请求","CN:J"),("用了什么协议通信","CN:J"),("整个的pipline流程是什么","PJ:J"),("让我登上我自己网站现场抓包","CN:J:E")],
}

# Do not include remembered responses or guesses in a question's raw span.
QUOTE_OVERRIDES = {
 (4,17): "讲下SSE的基本原理", (5,30): "这一个接口平均耗时由两秒降到300毫秒，这一个之前为什么会又耗时两秒+",
 (6,12): "实习拷打", (6,13): "多 Agent 通信怎么实现的", (6,15): "url 从输入到渲染的整个流程",
 (6,17): "linux 命令怎么读取一个文件的第四列的 userid 并且返回 top10", (6,22): "我的未来规划是啥样的？",
 (7,45): "怎么优化的，是怎么考虑的", (7,62): "拷打实习做的后端项目相关（这里有一些点被问住了，是程序熔断策略相关的），在Momenta实习做了些什么",
 (10,30): "交易埋点在后续迭代中怎么保证数据正确？从研发流程和线上反馈两方面说",
 (15,17): "简单来说整体上是通过那个MQ来进行削峰的是吧？有想过就是MQ如果挂了怎么办？",
 (15,25): "我看你这边底层的实现机制是用了滑动窗口是吧，还有其他的一些限流策略了解吗？",
 (15,27): "追问：除了固定窗口还了解其他策略吗？", (15,29): "追问：你既然说到漏桶了，讲一下漏桶的策略呗。",
 (15,31): "追问：但是刚才说的是漏桶是吧？那漏桶的话又是什么呢？",
 (15,33): "你在这个系统里面会保证redis和mysql里面的数据尽可能一致吗？怎么保持？",
 (15,35): "追问：但是如果说访问频次特别高，会不会出现从mysql里面读到旧数据呢？因为有主从延迟。",
 (15,37): "追问：那如果说就是有需要强一致性的业务呢？",
 (15,39): "如果缓存过期了，现在流量又特别高，这么多请求会不会一瞬间把数据库打崩？",
 (15,41): "追问：但是如果说现在有一个热点key过期了，是不是照样会有大量请求打到数据库上？",
 (15,43): "追问：那更新它的时候，不是还会有大量请求吗？", (15,45): "RAG有哪些策略去保证它检索的准确性？",
 (15,49): "我看你还用那个Flux做流式对吧？Flux内部是怎么实现的这个了解吗？",
 (15,51): "追问：或者不问那么具体了，比如说用户端断开连接，或者说下游超时了，这种时候Flux内部怎么处理？",
 (15,57): "有听说过harness吗？", (15,59): "有哪些原因可能造成SQL的慢查询，怎么解决？",
 (15,63): "MQ有哪些策略保证消息不丢失？", (15,55): "AI Coding有用过吗？使用的过程中有没有发现什么问题？",
 (18,16): "算法题: 两个有序列表合并，要求合并到其中一个列表上，合并的表大小为两表元素之和",
 (19,26): "2.两数相加 II（不能用额外空间、输出链表）", (20,12): "讲一下直接做的工作？画一个字节的简略广告推送图？",
 (20,20): "做完一个需求之后，业务知识库会根据改动动态更新吗？你怎么设计？",
 (22,9): "拷打实习经历（偏业务设计和业务难点理解，技术方面比较少）", (22,20): "反转链表",
 (26,15): "如何保证了项目的高可用性？",
}

# For repeated body/OCR or overlap screenshots, this is one real event with
# alternative corroboration, never a second occurrence for the same question.
CORROBORATING = {
 (2,65): [(9,"先简单介绍一下自己吧。")],
 (2,71): [(13,"说说 Java 的内存模型？堆和栈的区别是什么？")],
 (2,95): [(16,"聊聊你这个 Agent 框架吧，它解决什么问题？")],
 (2,101): [(18,"你的 Tool 注册机制是怎么实现的？")],
 (2,105): [(19,"那如果一个 Tool 执行失败了，你的框架怎么处理？")],
 (2,109): [(20,"你的 Agent 支持多轮对话吗？Memory 怎么管理的？")],
 (2,117): [(23,"Spring Boot 的自动装配原理讲一下？")],
 (2,121): [(24,"Spring 的 IoC 容器启动流程你了解吗？Bean 的生命周期是怎样的？")],
 (2,153): [(25,"你的 Agent 框架里有用到缓存吗？比如对大模型的调用结果做缓存？")],
 (2,157): [(26,"MySQL 的索引结构是什么？为什么用 B+ 树而不是 B 树或哈希表？")],
 (2,161): [(27,"慢 SQL 怎么排查和优化？")],
 (2,167): [(30,"你了解哪些 Agent 的架构模式？")],
 (2,171): [(31,"Function Calling 的原理是什么？你怎么理解 Tool Use？")],
 (2,175): [(32,"如果大模型返回的 Function Call 参数格式不对怎么办？")],
 (2,179): [(33,"你了解 MCP 吗？它和你现在框架里的 Tool 注册机制有什么区别？")],
 (2,183): [(34,"你有了解过多 Agent 协作吗？比如 Google 的 A2A 协议或者 Multi-Agent 系统的设计模式？")],
 (2,189): [(37,"给定一个整数数组和一个滑动窗口大小 k，返回每个窗口中的Max Value。")],
 **{(5,a):[(a+14,None)] for a in range(60,69)},
}

FOLLOWUPS = [(15,19,21,"追问："),(15,21,23,"追问："),(15,25,27,"追问："),
 (15,27,29,"追问："),(15,29,31,"追问："),(15,33,35,"追问："),(15,35,37,"追问："),
 (15,39,41,"追问："),(15,41,43,"追问："),(15,49,51,"追问：")]

TASKS = {None:("VERBAL","NONE"),"C":("CODE","ALGORITHM"),"CE":("CODE","ENGINEERING"),
 "E":("VERBAL","ENGINEERING"),"A":("VERBAL","ALGORITHM"),"U":("UNKNOWN","ALGORITHM"),"SQL":("SQL","ENGINEERING")}

def review(basis):
    return {"reviewer":"Codex/source-extraction-20261014", "reviewer_kind":"agent", "reviewed_at":datetime.now(timezone.utc).isoformat(),
            "guide_version":GUIDE,"basis":basis}

def span_at(text, line, quote, revision):
    lines=text.splitlines(keepends=True)
    block=lines[line-1].rstrip("\n")
    quote=block if quote is None else quote
    require(quote in block, f"source quote absent at line {line}: {quote}")
    start=sum(map(len,lines[:line-1]))+block.index(quote)
    return {"revision_id":revision,"start_char":start,"end_char":start+len(quote),
            "start_line":line,"end_line":line,"quote":quote,
            "origin":"OCR" if "## 图片内容（OCR）" in text[:start] else "TEXT"}

def materialize(snapshot, output, reports, old_dataset):
    require(not output.exists(),"new gold directory required; never overwrite frozen labels")
    raw_facts=read_json(snapshot/"facts.json")
    require(digest(raw_facts)==FACTS_HASH==read_json(snapshot/"manifest.json")["facts_sha256"],"frozen snapshot changed")
    # Deliberately project documents first. The questions, interviews and task
    # arrays are not inspected until the source labels have a freeze receipt.
    facts={k:raw_facts[k] for k in ("documents","snapshot","physical_indices")}
    del raw_facts
    old={r["source_hash"] for r in load_dataset(old_dataset)["extraction"]}
    pool=sorted((d for d in facts["documents"] if d["decision"]=="INCLUDED" and d["source_hash"] not in old),
                key=lambda d:hashlib.sha256((SEED+d["source_hash"]).encode()).hexdigest())
    write_json(reports/"selection-candidates.json",pool)
    output.mkdir(parents=True)
    extraction, labels, audit, excluded=[],[],[],[]
    for rank,company,position,specs,authored in DOCUMENTS:
        doc=pool[rank]
        raw=inside(snapshot,doc["source_path"]).read_bytes()
        require(digest(raw)==doc["source_hash"],"source fingerprint mismatch")
        target=inside(output,doc["source_path"]); target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(raw)
        text=decode_source(raw)
        base={"id":"extract-new-"+doc["source_hash"][:12],"group_id":doc["source_hash"],"split":"test",
              "source_hash":doc["source_hash"],"source_path":doc["source_path"],"original_path":doc["path"],
              "tags":["source_first_review","extraction","new_extraction_source"],"human_verified":False,"agent_verified":True,
              "review":review("完整阅读标题、正文及全部OCR，独立确定提问资格、原子任务、场次和属性；未查看机器提问/标签。"),
              "annotation_origin":"independent_agent_source_review","sample_kind":"positive","document_kind":"INTERVIEW",
              "sessions":[],"questions":[],"followups":[],"selection_rank":rank}
        if rank==29:
            base.update(sample_kind="negative",document_kind="INTERVIEW_UNSPECIFIED",
                        exclusion_basis="有快手二面叙述，但仅称聊AI理解/架构和一个未给题干的回溯题；不补造具体题目。")
            excluded.append({"rank":rank,"source_hash":doc["source_hash"],"reason":base["exclusion_basis"]})
            extraction.append(base); continue
        if rank in {7,26}: base["document_kind"]="MIXED"
        for sid,round_,first,last in specs:
            source_evidence=[span_at(text,1,None,doc["revision_id"])]
            if len(specs)>1: source_evidence.append(span_at(text,first,None,doc["revision_id"]))
            if rank==2: source_evidence.append(span_at(text,51,None,doc["revision_id"]))
            if rank==23: source_evidence.append(span_at(text,8,None,doc["revision_id"]))
            date_raw=text.splitlines()[3 if rank!=25 else 2].split("：",1)[-1]
            publish={3:"2025-01-21",13:"2025-10-14"}.get(rank)
            interview="2026-08-10" if rank==11 else None
            base["sessions"].append({"id":sid,"metadata":{"company":company,"position":position,"round":round_,
                        "interview_date":interview,"publish_date":publish},"source_evidence":source_evidence,
                        "publish_date_raw":date_raw,"publish_date_precision":"day" if publish else "unknown_year_or_relative",
                        "interview_date_raw":"20260810" if rank==11 else None,
                        "interview_date_precision":"day" if interview else "unknown",
                        "review_basis":"公司/岗位来自真实标题和明确标签；场次按明示标题；无完整年份的日期保留null，不借链接/系统日期补年。"})
        by_line={}
        for entry in authored.split(";"):
            if not entry.strip(): continue
            raw_line,code=entry.split(); line=int(raw_line)
            decisions=SPLITS.get((rank,line),[(QUOTE_OVERRIDES.get((rank,line)),code)])
            for quote,actual_code in decisions:
                parts=actual_code.split(":");topic,kind=parts[:2];task=parts[2] if len(parts)>2 else None
                if quote is None and (rank,line) in QUOTE_OVERRIDES: quote=QUOTE_OVERRIDES[(rank,line)]
                span=span_at(text,line,quote,doc["revision_id"])
                sid=next(s for s,_,first,last in specs if first<=line<=last)
                qid=f"g-new-{doc['source_hash'][:12]}-{len(base['questions'])+1:03}"
                form,focus=TASKS[task]
                # A vague unnamed database exercise has no stated format/focus.
                if rank==30 and line==8 and kind=="O": form,focus="UNKNOWN","UNKNOWN"
                q={"id":qid,"session_id":sid,"raw_question":span["quote"],"normalized_question":span["quote"],
                   "source_spans":[span],"topic_l1":TOPICS[topic][0],"topic_l2":TOPICS[topic][1],"question_type":TYPES[kind],
                   "evidence_kind":"INTERVIEW_QUESTION","review_basis":"实际场次记述中的逐字提问；同一要求的理由/约束/改述保持一题，独立任务按本配方拆分。"}
                corroboration=CORROBORATING.get((rank,line),[])
                if corroboration:q["corroborating_spans"]=[span_at(text,ln,qt,doc["revision_id"]) for ln,qt in corroboration]
                if rank==16 and line==12:
                    q.update(attributes_to_review=["topic_l1","topic_l2"],review_basis="确有一次原创算法题，但题干完全没有记录；不根据常识补题干或算法主题。")
                if rank==30 and line==8 and kind=="O":
                    q.update(attributes_to_review=["question_type","topic_l1","topic_l2"],review_basis="只记述一个未给题干的数据库练习；任务身份保留，领域/作答形式和题型无法确定。")
                base["questions"].append(q);by_line.setdefault(line,[]).append(qid)
                basis=(f"原文明确手撕/代码环节的算法求解（{span['quote']}）；算法题号与附加限制均不补写。" if task=="C" else
                       f"原文手写组件/接口实现（{span['quote']}）；LRU无算法环节证据时按工程组件，O(1)本身不改焦点。" if task=="CE" else
                       f"原文要求写SQL（{span['quote']}），作答SQL/工程，独立于相邻算法题。" if task=="SQL" else
                       f"原文明确口头构造/设计新功能或给命令（{span['quote']}）；没有手写要求，VERBAL/ENGINEERING。" if task=="E" else
                       f"原文/上下文口述求解（{span['quote']}），不因算法题型自动推断写代码。" if task=="A" else
                       f"原文确有算法求解（{span['quote']}），但仅称算法题，无明确作答方式；保留UNKNOWN/ALGORITHM。" if task=="U" else
                       f"原文讨论概念、已有项目/机制、故障或个人经历（{span['quote']}）；无明确新组件实现或算法求解任务，VERBAL/NONE。")
                if form==focus=="UNKNOWN":basis="只有数据库小题目的概括，题干和作答形式缺失；两字段均保留UNKNOWN。"
                before="\n".join(text.splitlines()[max(0,line-4):line-1]);after="\n".join(text.splitlines()[line:line+3])
                labels.append({"id":"label-"+qid,"gold_question_id":qid,"occurrence_id":qid,"occurrence_mapping_status":"unmapped_until_postfreeze_review",
                      "group_id":doc["source_hash"],"split":"test","source_path":doc["source_path"],"source_hash":doc["source_hash"],
                      "raw_question":span["quote"],"source_spans":[span],"context_before":before,"context_after":after,
                      "response_form":form,"coding_focus":focus,"tags":["source_first_review","task_labels","new_extraction_source"],
                      "human_verified":False,"agent_verified":True,"review":review(basis),"annotation_origin":"independent_agent_source_review"})
        for rank_,parent,child,marker in FOLLOWUPS:
            if rank_==rank:
                require(len(by_line[parent])==len(by_line[child])==1,"explicit followup endpoints ambiguous")
                base["followups"].append({"source_id":by_line[parent][0],"target_id":by_line[child][0],
                                            "evidence_spans":[span_at(text,child,marker,doc["revision_id"])]})
        if rank==2:
            # The original says "我追问了几个点" immediately before 2/3/4.
            parent=by_line[95][0]
            for child in (101,105,109):
                for target in by_line[child]:
                    base["followups"].append({"source_id":parent,"target_id":target,
                          "evidence_spans":[span_at(text,99,"我追问了几个点：",doc["revision_id"])]})
        extraction.append(base)
        audit.append({"rank":rank,"source_hash":doc["source_hash"],"path":doc["path"],"lines_read":len(text.splitlines()),
                      "questions":len(base["questions"]),"sessions":len(specs),"source_only_before_freeze":True,
                      "review":base["review"],"notes": {
                         2:"正文为OCR摘要同一场次，统一使用完整OCR题目，重复正文为corroboration。",
                         5:"图片2/3的MySQL列表重复，只标一次，同文不同一二面仍分别标。",
                         7:"学习笔记/推广目录、反问和聊天不标题，真实一二面和HR面各自独立。",
                         26:"47行后的备考教程、腾讯/阿里例题与PDF目录没有该场快手面试事件，不标为该场题。",
                         29:"没有具体问题；补入第30rank以保证30篇正例。",
                         30:"回忆性概述只按原文明确记忆的主题/操作，不补原问题的约束。"}.get(rank,"反问、答案和未给题干的空白手撕栏目不补造题目。")})
    require(sum(r["sample_kind"]=="positive" for r in extraction)==30,"30 positive source documents required")
    require(len(labels)>=200,"200 task labels required")
    write_jsonl(output/"extraction.jsonl",extraction);write_jsonl(output/"task_labels.jsonl",labels)
    write_json(output/"selection.json",{"seed":SEED,"method":"SHA256(seed + source_hash) ascending; exclude old extraction sources; source-review first 31 candidates",
                   "pool_size":len(pool),"old_extraction_source_hashes":sorted(old),"selected_ranks":list(range(31)),"negative_source_decisions":excluded})
    manifest={"version":output.name,"status":"frozen","kind":"corpus","source_evidence_contract":"raw_source_spans_v1",
              "snapshot":facts["snapshot"],"physical_indices":facts["physical_indices"],"facts_sha256":FACTS_HASH,
              "annotation_guide":"docs/annotation-guide.md","review_authorization":{"policy":"delegated_agent",
                  "thread_id":os.environ.get("CODEX_THREAD_ID","current-user-task"),"user_instruction":INSTRUCTION},
              "blind_review":"This v1 was authored by reading all 31 complete raw sources before inspecting snapshot questions/interviews/task labels or predictions.",
              "review_lifecycle":"Source-first independent Agent labels. Excluded 32 old extraction sources; other historical task/retrieval evaluations may have used these sources.",
              "evaluation_scope":"Diagnostic audit of frozen r293 published output; not current live quality and not unseen-source generalization.",
              "occurrence_identity":"Task rows use independent gold IDs. Published occurrence mapping is kept outside the frozen dataset after first seal.",
              "models":{},"extraction":"extraction.jsonl","task_labels":"task_labels.jsonl"}
    write_json(output/"manifest.json",manifest)
    write_json(output/"freeze.json",{"dataset_sha256":digest(load_dataset(output)),"frozen_at":datetime.now(timezone.utc).isoformat(),
                   "file_hashes":{p.relative_to(output).as_posix():digest(p.read_bytes()) for p in sorted(output.rglob("*")) if p.is_file() and p.name!="freeze.json"}})
    write_jsonl(reports/"source-review-audit.jsonl",audit)
    write_json(reports/"initial-seal.json",{"freeze_sha256":digest((output/"freeze.json").read_bytes()),"dataset_sha256":digest(load_dataset(output)),
                  "recipe_sha256":digest(Path(__file__).read_bytes()),"predictions_read":False,"reviewed_sources":31,"positive_sources":30,
                  "negative_sources":1,"questions":len(labels),"task_labels":len(labels),"task_distribution":dict(Counter((r["response_form"]+"/"+r["coding_focus"]) for r in labels))})
    for section in ("extraction","task_labels"):validate_gold(output,section,review_policy="delegated_agent")
    print(read_json(reports/"initial-seal.json"))

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--snapshot",type=Path,default=Path("data/reports/resume-quality-snapshot-20261005-v1"))
    parser.add_argument("--old-dataset",type=Path,default=Path("data/gold/evaluation-agent-reviewed-20261005-v5"))
    parser.add_argument("--output",type=Path,default=Path("data/gold/source-reviewed-extraction-20261014-v1"))
    parser.add_argument("--reports",type=Path,default=Path("data/reports/source-reviewed-extraction-20261014-v1"))
    args=parser.parse_args();materialize(args.snapshot,args.output,args.reports,args.old_dataset)

if __name__=="__main__":main()
