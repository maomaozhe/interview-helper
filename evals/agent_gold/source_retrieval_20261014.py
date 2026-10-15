"""Reproduce delegated source review anchors. No model or production writes.

QUERIES are explicit reading decisions, not keyword-derived labels. Facts are
used only to discover immutable occurrence bindings and ALL canonical variants.
Review the emitted variants before finalizing their independent mapping decisions.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
import os
import shutil
from pathlib import Path

from eval.common import digest, read_json, write_json, write_jsonl
from interview_intelligence.ingestion.snapshot import decode_source

SNAPSHOT = Path('data/reports/resume-quality-snapshot-20261005-v1')
# id, source SHA prefix, exact source quote, source-grounded paraphrase,
# family, independent semantic judgment (written after opening complete source).
QUERIES = [
('agent-auth','b4abbbf0','如何做接口鉴权，如果是在Agent层面不想把token暴露给agent,一般怎么处理','怎样让 Agent 调接口又不把鉴权 token 暴露给它','AI_AGENT','问题要求接口鉴权与凭证隔离，不能用一般登录鉴权替代。'),
('mcp-fc','b4abbbf0','怎么理解MCP协议，和function calling的区别，MCP有哪些优缺点','MCP 和 function calling 有什么差别以及优缺点','AI_AGENT','原文同时问协议区别和利弊；只解释一个名词不完全满足。'),
('sse-recovery','b4abbbf0','sse协议断联，会丢数据，如果让你设计，你会怎么设计，前端数据传输中断了，后续要怎么恢复，你会怎么存储数据，如果存储在Redis数据过大怎么办','设计 SSE 断线恢复：如何补发数据、存储并应对 Redis 数据太大','ENGINEERING_DESIGN','明确构造断联恢复机制，保留存储和数据过大约束。'),
('mysql-redis-sync','b4abbbf0','Mysql和Redis的数据不一致要怎么解决？如果Redis主从不一致，导致数据不一致，要怎么解决？','MySQL 与 Redis 以及 Redis 主从数据不一致怎么处理','REDIS_DB_CONCURRENCY','同时包含缓存数据库和Redis主从一致性两个范围。'),
('agent-eval-process','b4abbbf0','如果让你来设计一个评测系统，你会怎么设计？对于agent的中间的执行过程，比如工具的调用次数，执行流程，这些你怎么去评测','怎么设计 Agent 评测系统来检查工具次数和执行流程','AI_AGENT','原文明确要求中间过程评价，不能仅判最终答案。'),
('multi-agent-need','b182e9d5','为什么要做多 agent，单 agent 满足不了现在的业务场景吗。','什么业务限制会让单 Agent 不够用而需要多 Agent','AI_AGENT','原问是架构选型理由，不是通信机制或多Agent名词解释。'),
('react-context','b182e9d5','ReAct 循环越多、上下文加载越多，碰到上下文膨胀或超限怎么处理。','ReAct 多轮执行导致上下文超限怎么处理','AI_AGENT','原文具体触发为循环次数和上下文增长。'),
('summary-fidelity','b182e9d5','通用做法是 summary，但每个模型压缩完结果不一样、也可能丢失原意，怎么让压缩过程更符合原意。','上下文 summary 压缩会丢信息，怎么保证原意','AI_AGENT','保留压缩结果因模型而异和信息失真条件，不等同一般超限治理。'),
('eval-no-omission','b182e9d5','准确率是怎么测评的，测评过程中怎么保证不重不漏。','Agent 准确率测评如何保证样本不重复不遗漏','AI_AGENT','准确率与测评覆盖完整性一起问，不能只报准确率值。'),
('skill-adherence','b182e9d5','大模型不一定按你编写的流程走，怎么提高 skill 里流程的执行命中率。','怎样提高大模型对 skill 指定流程的执行遵循率','AI_AGENT','关注实际执行流程的命中，区别于检索skill或工具描述。'),
('fake-analysis','b182e9d5','假设流程是「取数据 → 用 pandas 分析 → 给结论」，实际会出三种问题：\n模型跳过分析那一步、给假分析（拿上一次结果直接返回）、每次分析结果都不稳定。有什么工程化的办法解决。','pandas 分析 Agent 跳步骤、沿用旧结果和结果不稳定怎么用工程手段解决','AI_AGENT','须覆盖三种失败模式及工程治理，逐字包含换行的题干约束。'),
('harness-hallucination','b182e9d5','这是 harness 架构，在 harness 工程里怎么降低大模型幻觉。','Harness 工程怎么降低大模型幻觉','AI_AGENT','问题对象限定harness工程，不把相邻workflow追问误当完整答案。'),
('model-regression','426bc499','不换基础模型是怎么考虑的？如果更换新的基础模型，要如何验证它对原有常规任务的满足情况？','更换基础模型后如何验证常规任务没有退化','AI_AGENT','必须验证原有任务满足情况；不是单纯推荐模型。'),
('prompt-context-harness','426bc499','对 Prompt Engineering、Context Engineering 和 Harness Engineering 这几者之间的区别是怎么理解的？','Prompt、Context、Harness Engineering 三者如何区分','AI_AGENT','原文比较三个工程范式，单个范式介绍不完全相关。'),
('tool-upgrade-regression','426bc499','项目一的问答 Agent在迭代升级工具时怎么确保能力不劣化？','问答 Agent 升级工具以后如何避免能力退化','AI_AGENT','触发是工具迭代；与更换基础模型是相关但不同测试对象。'),
('agent-judge-agreement','1d91254e','大模型的判断和人工评估的一致性有评估过吗？可用的标准是怎么定性的？','如何评价大模型裁判和人工评价的一致性并定义标准','AI_AGENT','涉及人机一致性与标准定义，原文没有可报告一致性数值。'),
('judge-compromise','60a19fb0','有没有存在裁判agent妥协，认同执行agent错误判断的情况，如何预防该问题？','裁判 Agent 认同执行 Agent 错误判断的妥协现象怎么防止','AI_AGENT','具体是评审妥协，不泛化为全部幻觉问题。'),
('subjective-eval','60a19fb0','如果抽象来看，如何评测这类主观指标？','Agent 评测报告中的主观指标怎么评估','AI_AGENT','指代由前两问Agent评测报告的主观指标确定，已读全文补回主语。'),
('follow-service','1d91254e','假设你是工程师，要开发一个关注功能，支持关注、取消关注，查看自己的粉丝列表和关注列表。考虑用户量多的情况，技术选型要注意什么？高并发请求下有哪些优化措施来保证系统性能和稳定性？','设计高并发关注系统，支持取关、粉丝列表和关注列表','ENGINEERING_DESIGN','保留四个功能和高并发/大量用户条件，确认为系统设计任务。'),
('follow-sharding','1d91254e','基础表结构到底是什么样的？水平分表怎么分？为什么要分表？','关注系统的表结构和水平分表如何设计，为什么分表','ENGINEERING_DESIGN','由上一问关注系统绑定语境；裸分表百科不构成完整满足。'),
('follow-count','1d91254e','那统计用户粉丝数的时候要怎么做？','关注系统分表后怎样统计粉丝数量','ENGINEERING_DESIGN','结合上一段水平分表和下问全分表扫描明确计数语境。'),
('ad-system','e1616492','广告推送系统设计；','广告推送系统怎么设计','ENGINEERING_DESIGN','OCR场景题明确是广告推送系统设计，不补流量规模等未知要求。'),
('distributed-flow','e1616492','大流量分布式架构设计；','大流量服务的分布式架构如何设计','ENGINEERING_DESIGN','原文只给大流量分布式架构，不凭空增加业务或具体QPS。'),
('delay-queue','37acb3a2','用过rocketmq吗，如果自己设计一个延时消息队列，要考虑哪些点？','自己设计延时消息队列需要考虑什么','ENGINEERING_DESIGN','明确要求自己设计队列；下一问准时消费为相关追问。'),
('fair-task-queue','2be0f376','如何设计任务队列，处理多类请求，保证公平性，防止一类请求把资源占满？','多类请求的任务队列怎样保证公平并防止资源被一类占满','ENGINEERING_DESIGN','保留公平性与资源独占失败模式，不泛化成普通消息队列。'),
('bounded-compute','2be0f376','系统有 N 类请求，每次计算峰值占用量已知；同类请求在依赖资源未变化时结果一致，机器内存有限，如何兼顾并发、延迟与稳定性？','计算内存峰值已知、同类结果可复用时如何设计有限内存服务','ENGINEERING_DESIGN','保留N类请求、依赖变化影响复用和机器内存限制；原文场景任务。'),
('websocket-reconnect','2be0f376','WebSocket 断开重连如何设计？','设计 WebSocket 断开后的重连机制','ENGINEERING_DESIGN','明确协议为WebSocket，不能与SSE不加区分地合并。'),
('fast-cache','6ea21d1f','如何设计一个高速cache，要求高吞吐，低延时，可扩展','怎么设计高吞吐低延时可扩展的缓存','ENGINEERING_DESIGN','保留性能和扩展约束，是新组件设计。'),
('ticket-priority','61ef2ec8','背景：设计一个购票系统，10万人抢票，只有1000个座位\n2. 要求：（1）同一个座位，优先分配给路程长的用户；（2）要考虑系统承受的流量，保证高可用性','10万人抢1000座位且长途用户优先，购票系统怎么设计','ENGINEERING_DESIGN','跨两行背景/要求一起标注，保留长途优先与高可用，不归并普通秒杀。'),
('mysql-prefix','61ef2ec8','如果查询条件是c = 1，b > 2，a = 3，索引还会生效吗？怎么验证呢？','联合索引 a,b,c 遇到 c=1、b>2、a=3 是否生效怎么验证','REDIS_DB_CONCURRENCY','从上一问补回索引字段a,b,c；保留不等号约束。'),
('mysql-leaf','61ef2ec8','MySQL二级索引叶子节点和非叶子节点存的数据有什么区别？','MySQL 二级索引的叶子与非叶子节点存什么','REDIS_DB_CONCURRENCY','明确索引为二级索引，不能用主键聚簇索引直接代替。'),
('txn-rollback','61ef2ec8','在可重复读的隔离条件下，开启一个事务，执行到一半发生错误开始回滚，这之间都涉及哪些操作？','可重复读事务执行一半出错回滚时有哪些操作','REDIS_DB_CONCURRENCY','保留隔离级别和回滚过程条件，不等同仅列隔离级别。'),
('cf-failure','61ef2ec8','讲一下CompletableFuture？如果三个方法并行，其中有一个方法失败了怎么办？','CompletableFuture 三个并行方法中一个失败怎么处理','REDIS_DB_CONCURRENCY','原问明确并行三方法和失败传播，不把运行总耗时问题等同。'),
('synchronized-target','61ef2ec8','如果在一个方法上加synchronized，锁住的是什么？如果在static修饰的方法上加synchronized呢？','实例方法和 static 方法上 synchronized 各锁什么','REDIS_DB_CONCURRENCY','对比实例与静态两种锁对象，保留成一个问答任务。'),
('redis-memory-full','61ef2ec8','如果redis的内存有1G，现在已经存了999M了，还能存吗？','Redis 1G 内存已用999M还能写入吗','REDIS_DB_CONCURRENCY','具体容量边界场景，不补原文未给的maxmemory配置。'),
('redis-rehash','7bd6552a','redis渐进式hash是什么？','Redis 的渐进式 rehash 是什么','REDIS_DB_CONCURRENCY','原文写渐进式hash，按上下文Redis结构解释为rehash且保存原词。'),
('redis-skiplist','7bd6552a','讲一下跳表，redis为什么用跳表，不用B+树','Redis 为什么选跳表而不是 B+ 树','REDIS_DB_CONCURRENCY','任务比较数据结构选型；单讲跳表插入仅部分相关。'),
('redis-io','7bd6552a','讲一下redis的多路复用机制','Redis 的 I/O 多路复用机制怎么工作','REDIS_DB_CONCURRENCY','网络IO机制题，不能与数据结构选型归为等价。'),
('stock-oversell','7bd6552a','高并发场景扣减库存如何防止超卖？','高并发扣库存怎样保证不超卖','REDIS_DB_CONCURRENCY','保留库存扣减场景，与长途优先购票系统区别明确。'),
('mq-consumed','7bd6552a','mq 中的消息被消费者接收后如何保证消息会被完整处理？','MQ 消息到消费者以后如何保证处理完整','REDIS_DB_CONCURRENCY','消费完成可靠性，与发送端投递可靠性区分。'),
('hot-key','6ea21d1f','热key怎么解决？','Redis 热 key 应该怎么治理','REDIS_DB_CONCURRENCY','前文Redis副本/分片提供语境；没有要求所有热点类型。'),
('pool-unbounded','f2b86ff7','无界队列会有什么问题','线程池采用无界队列会有什么问题','REDIS_DB_CONCURRENCY','从同一行线程池原理/参数补回主语，独立风险问题不与原理参数合并。'),
('pool-params','f2b86ff7','线程池原理，参数','线程池的工作原理与核心参数是什么','REDIS_DB_CONCURRENCY','原文同一行独立原理/参数部分，另一任务是无界队列风险；两者单独标注。'),
('k-reverse','1d91254e','算法：k个一组翻转链表。','链表每 k 个节点一组翻转的算法题','ALGORITHM','原文算法环节明确k组翻转，没有添加题号或不足k处理约定。'),
('max-subarray','f2b86ff7','算法一，最大子数组和','求最大子数组和的算法题','ALGORITHM','原文明确算法一最大子数组和，没有加入湍流/长度限定。'),
('sum-k','f2b86ff7','算法二，和为k的子数组变种题，求最多有多少种组合，让子数组和为k','子数组和等于 k 有多少种组合的算法题','ALGORITHM','保留计数组合要求，不误归并只寻找一组下标版本。'),
('digit-skip4','9d8c0147','把0，1，2，3，4，5...10，11拍扁成单个数字，比如11拍成1，1。并且跳过4，问第N个数字是多少？要求考虑性能限制。','自然数拍成数字序列且跳过4，如何求第N位并考虑性能','ALGORITHM','题意跳过4有解释歧义，原文约束照存，不自行定为去除所有含4数字。'),
('power-two-sum','9d8c0147','把给定的正整数n拆成若干个2的N次方数字相加的和，n≤2^32-1。','把不超过2^32-1的正整数拆为2的幂之和','ALGORITHM','保留正整数和上界，不补最少项或去重约束。'),
('permutation-substring','9d8c0147','给定串a和串b，问a的所有排列里是否有任意一个排列能是b的子串，要求空间和时间均为O(N)。','判断 a 的某个排列是否为 b 子串，时间空间 O(N)','ALGORITHM','保留排列与复杂度双约束。'),
('max-less-n','7bd6552a','算法：小于n的最大数（秒了）','小于 n 的最大数这道面试算法题','ALGORITHM','原文题干短缺，不能补数字集合/不重复等常见版本条件。'),
('add-list-inplace','6ea21d1f','算法题:链表相加 ，要求原地操作','要求原地操作的链表相加算法题','ALGORITHM','原地操作是关键限制，不与普通可新建链表版本等同。'),
('reverse-list','377b55fc','反转链表（鼠鼠不知道哪一步出错了，但是思路一定没问题，最后问hr应该是挂在这里了）','反转整个链表的手撕题','ALGORITHM','全文手撕阶段明确反转链表；后面的个人评论不是算法约束。'),
('longest-unique','60a19fb0','说一下最长无重复字符子串这道题的解题思路','最长无重复字符子串如何求解','ALGORITHM','原文要求解题思路，检索相关性不强行补代码作答要求。'),
('islands','0a513fb1','算法: 岛屿问题 (dfs)','DFS 岛屿问题的面试算法题','ALGORITHM','原文只明确岛屿/dfs，不能声称已确认LeetCode题号或网格规则。'),
('horses','0a513fb1','智力题: 25匹马，5条跑道，最快几次选出前3匹快的马','25匹马5跑道求最快选出前三匹的比赛次数','ALGORITHM','保留匹数/跑道数/目标，不补秒表或唯一速度约束。'),
('latency-spikes','1d91254e','假设我们有一个微服务，实现简单功能，但要应对高并发请求。压测时发现，随着流量上涨，服务的响应时延会间歇性出现尖刺（正常响应时间 50 毫秒，尖刺时时延大幅增加），且稳定出现。作为服务负责人，你有怎样的排查思路？','高并发压测中50毫秒服务周期性延迟尖刺该怎么排查','TROUBLESHOOTING','原文间歇性且稳定尖刺，保留正常时延基线，未自行断定GC根因。'),
('slow-query','1d194ee9','线上慢查询分析如何做','线上 SQL 慢查询怎样分析','TROUBLESHOOTING','由后续SQL/索引上下文确认数据库慢查询，非RAG检索延迟。'),
('high-memory','377b55fc','内存占用过多，如何排查','内存占用过高怎么排查','TROUBLESHOOTING','原问未确定已经OOM，不能以崩溃现场替代。'),
('oom','37acb3a2','发生oom如何排查','JVM 发生 OOM 后如何排查','TROUBLESHOOTING','前文JVM内存区域确定对象，条件是已发生OOM。'),
('growing-memory','37acb3a2','还没有发生oom但内存不断上涨怎么排查，怎么排查不影响线上','JVM 内存不断上涨但尚未 OOM，怎样低影响排查','TROUBLESHOOTING','强调尚未OOM与排查不能影响线上，区别于事后OOM定位。'),
('redis-outage','37acb3a2','redis故障了如何处理能尽量不影响生产？','Redis 故障怎样尽量保障生产业务','TROUBLESHOOTING','任务是故障降级/业务保障，不是仅解释正常高可用结构。'),
('agent-no-reply','5ce430dd','用户查询订单状态但一直没有回复，你会从哪里排查？','电商 Agent 查询订单不回复时从哪里排查','TROUBLESHOOTING','前问电商Agent模块提供语境，不泛化成任意网络超时。'),
('rag-wrong-year','5ce430dd','RAG 知识库增强中，问的是 2024 年，却生成了 2023 年，你会从哪两个方面排查？','RAG 问2024却答2023应从哪些方面排查','TROUBLESHOOTING','保留年份错配与原文两方面要求，不臆造原因。'),
('rag-peak-latency','5ce430dd','RAG 高峰期响应很慢，你会从哪几个方向排查？','RAG 高峰期变慢如何排查','TROUBLESHOOTING','具体是高峰RAG延迟，区别于数据库等值范围慢查。'),
('large-db-slow','2be0f376','有一个千万级的数据库，做字段的等值过滤、范围查询慢，怎么排查优化？','千万级数据库等值过滤和范围查询都慢怎么优化','TROUBLESHOOTING','保留规模与两种查询类型；不能只用索引失效列表代替。'),
]

# These are deliberate source-based refusals, made after opening every variant.
# They are not removed from the source review inventory or relabeled to improve
# retrieval scores. They cannot bind to a possibly incorrect production group.
REJECTED = {
 'ad-system': ('missing_occurrence','OCR场景题广告推送系统设计在原文存在，r293该文档仅提取4题，没有相交来源跨度；保持无映射。'),
 'distributed-flow': ('missing_occurrence','OCR场景题大流量分布式架构在原文存在，r293该文档仅提取4题，没有相交来源跨度；保持无映射。'),
 'multi-agent-need': ('non_equivalent_canonical','同一canonical中快手三战两条追问明确增加成本/延迟权衡以及复杂任务准确率下降的技术原理要求，与普通为何用多Agent不满足严格SAME；不强制映射。'),
 'stock-oversell': ('ambiguous_variant_context','得物variant只有高并发如何不超买，前后在延时消息调度与双写一致性之间，原文无库存/购买业务；不能靠生产canonical证明超买=库存超卖，保留疑似错误归并。'),
 'reverse-list': ('unverified_interview_provenance','同canonical的无标题-你的至尊宝原文是两套业务题清单，缺具体面试事件叙述；不把机器INCLUDED当作真实面经资格证明。其余variant虽确实是面试算法，完整canonical映射暂拒绝。'),
 'redis-outage': ('non_equivalent_canonical','wxg variant来自分布式解析队列和Redis锁，后续追问锁失效、任务全部重回主队列与永久失败循环，关键上下文比通用故障生产保障更窄；不可强行当SAME。'),
}

EXTRA_RELEVANCE = [
 ('sse-recovery','websocket-reconnect',1,'都涉及连接恢复，但WebSocket没有SSE补发、Redis过大等约束，只能算部分相关。'),
 ('websocket-reconnect','sse-recovery',1,'都是长连接断联恢复工程，SSE协议与题干额外约束使其不能当完全同题。'),
 ('mcp-fc','prompt-context-harness',0,'都是Agent术语但比较对象不同，三工程范式问题没有MCP/function calling协议差别。'),
 ('agent-eval-process','eval-no-omission',1,'测评准确率和不重不漏相关，但未直接要求工具次数/流程过程评测。'),
 ('agent-eval-process','subjective-eval',1,'都属评测指标设计，主观指标题未覆盖中间执行工具过程。'),
 ('summary-fidelity','react-context',1,'超限治理与压缩保真相关，但只问超限没有模型间压缩失真约束。'),
 ('react-context','summary-fidelity',1,'压缩保真是超限治理的一个方面，未覆盖ReAct循环增长整体策略。'),
 ('skill-adherence','fake-analysis',1,'都问流程遵循工程，pandas跳步骤等是具体失败场景，范围更窄。'),
 ('fake-analysis','skill-adherence',1,'流程命中率是具体三种失败治理的一部分，没有旧结果造假和不稳定的全部约束。'),
 ('fake-analysis','judge-compromise',0,'裁判妥协是评审错误，不能替代pandas执行跳步骤/复用旧结果/波动的治理。'),
 ('harness-hallucination','summary-fidelity',1,'压缩信息失真属于上下文引起的幻觉风险，但并未询问整个harness治理。'),
 ('model-regression','tool-upgrade-regression',1,'同为回归验证但变更对象分别为基础模型和工具。'),
 ('tool-upgrade-regression','model-regression',1,'基础模型更换测试可提供回归方法参考，但未直接问工具升级。'),
 ('agent-judge-agreement','judge-compromise',1,'裁判认同错误与人机一致性有联系，但未定义一致性测量和标准。'),
 ('judge-compromise','agent-judge-agreement',1,'人机一致性测量帮助发现评审错误，但没有直接问裁判妥协预防。'),
 ('follow-service','follow-sharding',1,'关注系统分表是整体设计的一部分，未独立覆盖四种功能和高并发稳定性。'),
 ('follow-service','follow-count',1,'粉丝计数属于关注业务局部，不能作为全系统完整满足。'),
 ('follow-sharding','mysql-leaf',0,'二级索引节点存储问题没有关注关系表结构和水平分表设计。'),
 ('fair-task-queue','bounded-compute',1,'内存有限多类请求是任务调度上下文相关，公平和防单类占满没有直接要求。'),
 ('bounded-compute','fair-task-queue',1,'公平队列可涉及资源治理，但缺峰值已知、结果复用和依赖变化约束。'),
 ('fair-task-queue','delay-queue',1,'同为队列设计，延时队列侧重定时消费而不是多类请求公平。'),
 ('delay-queue','fair-task-queue',1,'队列调度相关但公平性任务没有延时/准时消费要求。'),
 ('ticket-priority','follow-service',0,'同为高并发业务设计，关注关系系统没有座位数和长途优先分配约束。'),
 ('mysql-prefix','mysql-leaf',1,'二级索引结构是联合索引判断的基础话题，未直接涉及abc范围条件。'),
 ('mysql-prefix','txn-rollback',0,'同是MySQL，事务回滚没有索引字段与范围条件生效问题。'),
 ('cf-failure','synchronized-target',0,'同为Java并发，锁住哪个对象不能满足异步并行失败处理。'),
 ('redis-skiplist','redis-io',0,'同为Redis，IO多路复用不是跳表与B+树的数据结构选型。'),
 ('redis-memory-full','pool-unbounded',1,'都涉及内存压力资源风险，但线程池无界队列没有Redis容量/写入条件。'),
 ('pool-unbounded','pool-params',1,'线程池原理参数是相关背景，但未要求无界队列风险。'),
 ('pool-params','pool-unbounded',1,'无界队列风险只覆盖参数设计的局部，不满足整个原理参数问题。'),
 ('k-reverse','add-list-inplace',0,'都是链表算法，但k组翻转与原地链表相加的目标不同。'),
 ('max-subarray','sum-k',1,'都求子数组性质，最大和与目标和计数求解目标不同，只能部分相关。'),
 ('sum-k','max-subarray',1,'都涉及子数组，但最大和不是和为k的组合数。'),
 ('digit-skip4','power-two-sum',0,'都是整数算法，数字序列第N位与2的幂分解没有共同求解目标。'),
 ('permutation-substring','longest-unique',1,'同为子串求解可涉及窗口，但排列匹配与无重复最长目标不同。'),
 ('longest-unique','permutation-substring',1,'子串条件相关，但排列是否存在不能满足最长无重复子串。'),
 ('latency-spikes','rag-peak-latency',1,'都诊断响应延迟，高峰RAG题没有稳定间歇尖刺和50ms基线。'),
 ('slow-query','large-db-slow',1,'同为数据库慢查排障，千万级等值/范围具体条件限制更窄。'),
 ('large-db-slow','slow-query',1,'一般线上慢查排查只部分覆盖千万规模两种查询条件。'),
 ('high-memory','oom',1,'已发生OOM的定位与内存过高相关，但故障状态更窄。'),
 ('oom','growing-memory',1,'都是内存排障，但尚未OOM和线上无影响限定不同。'),
 ('growing-memory','oom',1,'OOM事后定位可以参考，缺逐渐上涨且不可影响线上条件。'),
 ('agent-no-reply','rag-peak-latency',1,'Agent无回复与RAG高峰延迟相关，但不同业务触发/故障状态。'),
 ('rag-wrong-year','rag-peak-latency',0,'同为RAG排障，年份错答和高峰慢响应的故障目标不同。'),
]


def catalogue(output: Path):
    facts = json.loads((SNAPSHOT / 'facts.json').read_text(encoding='utf-8'))
    docs = {d['revision_id']: d for d in facts['documents']}
    interviews = {i['id']: i for i in facts['interviews']}
    by_canonical = defaultdict(list)
    for q in facts['questions']:
        by_canonical[q['canonical_question_id']].append(q)
    decisions, candidates = [], []
    for key, prefix, quote, query, family, basis in QUERIES:
        found_docs = [d for d in facts['documents'] if d['source_hash'].startswith(prefix)]
        assert len(found_docs) == 1, key
        doc = found_docs[0]
        text = decode_source((SNAPSHOT / doc['source_path']).read_bytes())
        assert text.count(quote) == 1, (key, 'quote missing/ambiguous')
        start = text.index(quote)
        end = start + len(quote)
        matches = []
        for q in facts['questions']:
            spans = q.get('source_spans', [])
            if not spans or spans[0].get('revision_id') != doc['revision_id']:
                continue
            overlap = sum(max(0,min(end,s['end_char'])-max(start,s['start_char'])) for s in spans)
            if overlap:
                matches.append({'occurrence_id':q['id'],'canonical_question_id':q['canonical_question_id'],
                                'raw_question':q['raw_question'], 'source_spans':spans,'overlap':overlap})
        span = {'start_char':start,'end_char':end,'start_line':text[:start].count('\n')+1,
                'end_line':text[:end].count('\n')+1,'quote':quote}
        anchor = {'id':key,'source_hash':doc['source_hash'],'source_path':doc['source_path'],
                  'original_path':doc['path'],'raw_question':quote,'source_spans':[span],
                  'query':query,'family':family,'basis':basis,'binding_candidates':matches}
        decisions.append(anchor)
        for m in matches:
            for variant in by_canonical[m['canonical_question_id']]:
                spans = variant.get('source_spans', [])
                revision = spans[0].get('revision_id') if spans else None
                vdoc = docs.get(revision)
                if vdoc is None:
                    candidates.append({'anchor_id':key,'canonical_question_id':m['canonical_question_id'],
                                       'variant':variant,'source_missing':True})
                    continue
                vtext = decode_source((SNAPSHOT / vdoc['source_path']).read_bytes())
                # Inspection pack contains the complete original document, not
                # machine topic labels, normalizations, or generated answers.
                candidates.append({'anchor_id':key,'canonical_question_id':m['canonical_question_id'],
                    'occurrence_id':variant['id'],'raw_question':variant['raw_question'],
                    'source_path':vdoc['source_path'],'source_hash':vdoc['source_hash'],
                    'original_path':vdoc['path'],'source_spans':spans,'full_original':vtext})
    write_json(output / 'independent-query-decisions.json', decisions)
    write_json(output / 'all-variants-inspection.json', candidates)
    print(f'{len(decisions)} independently written queries; {len(candidates)} variant bindings to review')


def finalize(report: Path, output: Path):
    assert not output.exists(), 'Create a new version; do not overwrite frozen gold'
    decisions = json.loads((report / 'independent-query-decisions.json').read_text(encoding='utf-8'))
    variants = json.loads((report / 'all-variants-inspection.json').read_text(encoding='utf-8'))
    facts = json.loads((SNAPSHOT / 'facts.json').read_text(encoding='utf-8'))
    snapshot_manifest = json.loads((SNAPSHOT / 'manifest.json').read_text(encoding='utf-8'))
    expected_questions = defaultdict(set)
    for q in facts['questions']:
        expected_questions[q['canonical_question_id']].add(q['id'])
    stamp = datetime.now(timezone(timedelta(hours=8))).isoformat()
    def review(basis):
        return {'reviewer':'Codex / source_retrieval_20261014','reviewer_kind':'agent',
                'reviewed_at':stamp,'guide_version':'annotation_v2_delegated_source_20261014',
                'basis':basis,'full_original_read':True}
    referenced_sources = set()
    def evidence(source, basis):
        path = source['source_path']
        raw = (SNAPSHOT / path).read_bytes()
        assert digest(raw) == source['source_hash']
        text = decode_source(raw)
        spans = source['source_spans']
        for s in spans:
            assert text[s['start_char']:s['end_char']] == s['quote'], (source.get('id'),source.get('occurrence_id'),'span mismatch')
        # Context is also immutable source evidence: adjacent original lines
        # resolve pronouns, protocol names, and preceding design constraints.
        first = min(s['start_char'] for s in spans)
        last = max(s['end_char'] for s in spans)
        line_ranges = []
        pos = 0
        for line in text.splitlines(keepends=True):
            line_ranges.append((pos,pos+len(line)))
            pos += len(line)
        first_line = text[:first].count('\n')
        last_line = text[:last].count('\n')
        cstart = line_ranges[max(0,first_line-3)][0]
        cend = line_ranges[min(len(line_ranges)-1,last_line+2)][1]
        context = {'start_char':cstart,'end_char':cend,'start_line':max(0,first_line-3)+1,
                   'end_line':text[:cend].count('\n')+1,'quote':text[cstart:cend]}
        referenced_sources.add(path)
        item = {k:source[k] for k in ('source_path','source_hash','original_path')}
        item.update(raw_question='\n'.join(s['quote'] for s in spans), source_spans=spans,
                    context_spans=[context],review=review(basis))
        if source.get('occurrence_id'):
            item['occurrence_id'] = source['occurrence_id']
        return item
    by_anchor = defaultdict(list)
    for v in variants:
        by_anchor[v['anchor_id']].append(v)
    evidence_map, mapping, rows, defects = {}, [], [], []
    used_canonicals = {}
    for anchor in decisions:
        key = anchor['id']
        se = evidence(anchor,anchor['basis'])
        if key in REJECTED:
            defect_kind, reason = REJECTED[key]
            defect_variants = [evidence(v,'已打开完整来源审核；'+reason) for v in by_anchor[key]]
            defects.append({'id':key,'kind':defect_kind,'query':anchor['query'],
                            'decision':'KEEP_UNMAPPED','basis':reason,'source_evidence':[se],
                            'canonical_variants_review':defect_variants,
                            'candidate_canonicals':sorted({v['canonical_question_id'] for v in by_anchor[key]}),
                            'human_verified':False,'agent_verified':True,'review':review(reason)})
            continue
        binds = anchor['binding_candidates']
        assert len(binds) == 1, (key,'binding requires explicit adjudication')
        cid = binds[0]['canonical_question_id']
        assert cid not in used_canonicals, (key,'distinct gold groups unexpectedly merged')
        used_canonicals[cid] = key
        checked = by_anchor[key]
        assert {v['occurrence_id'] for v in checked} == expected_questions[cid]
        basis = anchor['basis'] + ' 已打开该canonical全部variants完整原文，只依据任务主体/意图/约束复核；未读机器topic/type推导标签。'
        if key == 'k-reverse':
            basis += ' 三处均明确k组翻转；10分钟是面试作答期限，未改变算法对象及数学限制，按同一计算任务绑定。'
        if key == 'longest-unique':
            basis += ' 八处均求最长无重复字符子串；口述思路/代码形式属于作答元数据，检索任务主体相同，未把快排另一任务合入。'
        if key == 'pool-params':
            basis += ' 两处均线程池原理/工作流程及核心参数；无界队列风险独立属于另一gold。'
        if key == 'hot-key':
            basis += ' 两处完整原文都明确Redis热key治理，题库推广截图中泛化标题不纳入。'
        variant_evidence = [evidence(v,basis) for v in checked]
        gid = 'sr14-g-'+key
        se['occurrence_id'] = binds[0]['occurrence_id']
        evidence_map[gid] = {'source_evidence':[se], 'canonical_variants_review':variant_evidence,
                            'mapping_decision':'equivalent','review':review(basis)}
        mapping.append({'gold_question_id':gid,'canonical_question_id':cid,'equivalence_group':gid,
                        'occurrence_ids':sorted(expected_questions[cid]),'review':review(basis)})
        rows.append({'id':'sr14-r-'+key,'group_id':anchor['source_hash'],'split':'test',
                     'tags':['retrieval','source_review',anchor['family']],
                     'human_verified':False,'agent_verified':True,'review':review(anchor['basis']),
                     'source_path':anchor['source_path'],'source_hash':anchor['source_hash'],
                     'query':anchor['query'],'filters':{},'sample_kind':'positive',
                     'relevance':{gid:2}, 'relevance_reviews':{gid:{'grade':2,'basis':anchor['basis']}},
                     'fresh_source':False,'judgment_scope':'anchor_only','release_eligible':False})
    row_by_key = {r['id'].removeprefix('sr14-r-'):r for r in rows}
    for query_key, gold_key, grade, basis in EXTRA_RELEVANCE:
        assert query_key in row_by_key and gold_key in row_by_key
        row = row_by_key[query_key]
        gid = 'sr14-g-'+gold_key
        row['relevance'][gid] = grade
        row['relevance_reviews'][gid] = {'grade':grade,'basis':basis,'review':review(basis)}
    assert len(rows) >= 50
    output.mkdir(parents=True)
    for path in sorted(referenced_sources):
        dest = output / path
        dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(SNAPSHOT / path,dest)
    # Audit prior gold source exposure. This reads prior labels/provenance only,
    # never model predictions, and is not evidence of outside source novelty.
    all_hashes = {Path(p).stem for p in referenced_sources}
    overlaps = []
    prior_datasets = sorted(Path('data/gold').glob('*/manifest.json'))
    for old_manifest in prior_datasets:
        if old_manifest.parent == output:
            continue
        old_root = old_manifest.parent
        found = set()
        for p in old_root.rglob('*.md'):
            if len(p.stem) == 64 and p.stem in all_hashes:
                found.add(p.stem)
        # Manifest or labels may refer to external snapshot source hashes.
        for p in [old_manifest,*sorted(old_root.glob('*.jsonl'))]:
            text = p.read_text(encoding='utf-8-sig')
            found.update(h for h in all_hashes if h in text)
        if found:
            overlaps.append({'dataset':old_root.as_posix(),'source_hashes':sorted(found),
                             'count':len(found)})
    exposure = {'fresh_source':False,'source_snapshot_as_of':'2026-10-05',
                'purpose':'exposed_source_regression_diagnostic',
                'prediction_access_before_freeze':'none_new_predictions',
                'facts_usage':'source occurrence IDs/spans and complete canonical variants discovery only',
                'prior_dataset_overlaps':overlaps,
                'source_hashes_in_prior_gold':sorted({h for x in overlaps for h in x['source_hashes']}),
                'outside_repository_exposure':'unknown; no novelty claim',
                'negative_queries':0,'pooled_candidate_judging':False,
                'corpus_exhaustive_relevance':False,
                'unjudged_candidates':'unknown, not assumed nonrelevant'}
    write_json(output / 'source-review-defects.json',defects)
    write_json(output / 'exposure-audit.json',exposure)
    write_jsonl(output / 'retrieval.jsonl',rows)
    manifest = {'version':output.name,'status':'frozen','kind':'corpus',
        'snapshot':facts['snapshot'],'physical_indices':facts['physical_indices'],
        'facts_sha256':snapshot_manifest['facts_sha256'],
        'facts_file_sha256':digest((SNAPSHOT / 'facts.json').read_bytes()),
        'source_snapshot_manifest_sha256':digest((SNAPSHOT / 'manifest.json').read_bytes()),
        'source_snapshot':SNAPSHOT.as_posix(),'id_namespace':'gold',
        'source_evidence_contract':'raw_source_spans_v1','retrieval_evidence_scope':'anchor_only',
        'judgment_scope':'anchor_only','release_eligible':False,'fresh_source':False,
        'review_authorization':{'policy':'delegated_agent',
            'thread_id':os.environ.get('CODEX_SESSION_ID','01a138d3-ea4b-79c1-b545-e89dc5613a20'),
            'user_instruction':'你先把你能做的能完善的完善好，先就由你自己来审核，找gold set，但是一定要真实的去面经里找了'},
        'canonical_mapping':{'corpus_revision':facts['snapshot']['corpus_revision'],
            'items':mapping,'review':review('逐条打开全部variants原文及上下文，对主体意图和关键约束决定映射；疑似归并/来源问题留独立缺陷。')},
        'relevance_evidence':evidence_map,'retrieval':'retrieval.jsonl',
        'source_review_defects':'source-review-defects.json','exposure_audit':'exposure-audit.json',
        'annotation_recipe':{'path':'evals/agent_gold/source_retrieval_20261014.py',
                            'sha256':digest(Path(__file__).read_bytes())},
        'annotation_guide_sha256':digest(Path('docs/annotation-guide.md').read_bytes()),
        'reviewed_at':stamp,'extraction':[],'task_labels':[],'dedup':[],'routing':[],'sql':[],
        'limitations':['59 source-confirmed positive retrieval anchors, not a complete corpus relevance census',
                       'No four-channel candidate pool; no negative absence certification',
                       'r293 dated 2026-10-05, not current production quality',
                       'Agent review is not human verification or independent two-reviewer agreement',
                       'Exposed source regression set; new inference scores require separate future collection']}
    write_json(output / 'manifest.json',manifest)
    expanded = dict(manifest)
    expanded['retrieval'] = rows
    hashes = {p.relative_to(output).as_posix():digest(p.read_bytes())
              for p in sorted(output.rglob('*')) if p.is_file()}
    write_json(output / 'freeze.json',{'dataset_sha256':digest(expanded),'file_hashes':hashes,
                                     'frozen_at':stamp,'before_new_predictions':True})
    counts = {'queries':len(rows),'raw_query_decisions':len(decisions),'mapped_gold_questions':len(mapping),
              'rejected_mapping_cases':len(defects),'source_files':len(referenced_sources),
              'canonical_variants':sum(len(e['canonical_variants_review']) for e in evidence_map.values()),
              'grade_counts':dict(Counter(str(g) for r in rows for g in r['relevance'].values())),
              'families':dict(Counter(r['tags'][-1] for r in rows)),
              'known_prior_source_exposure':len(exposure['source_hashes_in_prior_gold'])}
    write_json(report / 'review-summary.json',counts)
    write_json(report / 'mapping-decisions.json',{'accepted':mapping,'rejected':defects})
    summary = '\n'.join([
        '# 真实原面经检索锚点审核（2026-10-14）','',
        f'已完整阅读{len(referenced_sources)}篇不可变面经，独立写出{len(decisions)}条查询；{len(rows)}条可靠映射正查询冻结，{len(defects)}条保持无映射缺陷。',
        '本次是用户授权的 Agent 审核：human_verified=false、agent_verified=true。来源固定为2026-10-05的r293，不代表10月14日生产最新状态。','',
        '逐题保存逐字原问、原文件名、SHA-256、字符跨度、行号、邻近上下文及审核理由；映射前打开canonical全部variants原文，机器分类/主题/答案不作为语义标签。',
        f'可靠映射涉及{counts["canonical_variants"]}个实际occurrence；相关性判断分布为{counts["grade_counts"]}。',
        f'覆盖：{counts["families"]}。','',
        '本数据的relevance是anchor_only。没有四路候选池和全库池外补查，不得将锚点命中率表述为全库Recall，不认证“全库没有相关题”的负查询。release_eligible=false，必须禁止发布门禁自动放行。',
        '没有调用外部模型，没有生产写入，没有在冻结前读取新预测。已有r293原文在旧gold里的实际重合另存exposure-audit.json；fresh_source=false。','',
        '拒绝映射的缺陷保留原始证据，未为了满足最低规模或提高成绩而塞入现有canonical：','',
        *['- '+d['id']+'：'+d['basis'] for d in defects], '',
        '后续应在独立新版本修复语料归并/抽取，冻结新的物理索引，再重绑；对候选池全量人工式源审核完成后才能另作召回门禁数据。',
        ''])
    (report / 'summary.md').write_text(summary,encoding='utf-8')
    print(json.dumps(counts,ensure_ascii=False))


def correct_metadata(base: Path, output: Path, report: Path):
    """Version-only provenance correction; all semantic labels stay byte-identical."""
    from eval.validate_gold import load_dataset, validate_gold
    assert not output.exists(), 'Create a new version; do not overwrite frozen gold'
    original = validate_gold(base,'retrieval',review_policy='delegated_agent')
    snapshot_manifest = read_json(SNAPSHOT / 'manifest.json')
    snapshot_facts = read_json(SNAPSHOT / 'facts.json')
    canonical_hash = digest(snapshot_facts)
    assert canonical_hash == snapshot_manifest['facts_sha256']
    file_hash = digest((SNAPSHOT / 'facts.json').read_bytes())
    assert original['facts_sha256'] == file_hash, 'The v1 correction only applies to the documented byte/canonical digest mixup'
    parent_manifest_hash = digest(original)
    parent_freeze_hash = digest((base / 'freeze.json').read_bytes())
    shutil.copytree(base,output)
    shutil.copyfile(SNAPSHOT / 'facts.json',output / 'facts.json')
    manifest = read_json(output / 'manifest.json')
    stamp = datetime.now(timezone(timedelta(hours=8))).isoformat()
    revision = {'kind':'metadata_only_correction','parent_dataset':base.as_posix(),
                'parent_dataset_sha256':parent_manifest_hash,'parent_freeze_file_sha256':parent_freeze_hash,
                'reviewed_at':stamp,'reviewer_kind':'agent',
                'reason':'facts_sha256 follows eval snapshot canonical JSON digest; v1 incorrectly stored the serialized file byte digest in that field',
                'old_facts_sha256':original['facts_sha256'],'new_facts_sha256':canonical_hash,
                'facts_file_sha256':file_hash,'source_facts_path':'facts.json','semantic_labels_changed':False,
                'retrieval_jsonl_sha256':digest((base / 'retrieval.jsonl').read_bytes())}
    manifest.update(version=output.name,facts_sha256=canonical_hash,facts_file_sha256=file_hash,
                    source_facts_path='facts.json',
                    parent_dataset=base.as_posix(),parent_dataset_sha256=parent_manifest_hash,
                    metadata_revision=revision)
    manifest['annotation_recipe']['sha256'] = digest(Path(__file__).read_bytes())
    write_json(output / 'metadata-revision.json',revision)
    write_json(output / 'manifest.json',manifest)
    unchanged = [p for p in base.rglob('*') if p.is_file() and p.name not in {'manifest.json','freeze.json'}]
    assert all((output / p.relative_to(base)).read_bytes() == p.read_bytes() for p in unchanged)
    expanded = load_dataset(output)
    hashes = {p.relative_to(output).as_posix():digest(p.read_bytes())
              for p in sorted(output.rglob('*')) if p.is_file() and p.name != 'freeze.json'}
    write_json(output / 'freeze.json',{'dataset_sha256':digest(expanded),'file_hashes':hashes,
        'frozen_at':stamp,'before_new_predictions':True,'parent_dataset_sha256':parent_manifest_hash})
    validate_gold(output,'retrieval',review_policy='delegated_agent')
    write_json(report / 'metadata-revision.json',revision)
    old_summary = (base.parent.parent / 'reports' / base.name / 'summary.md').read_text(encoding='utf-8')
    summary = old_summary + '\n本v2仅修正facts_sha256字段的规范JSON哈希约定，另存facts_file_sha256文件字节哈希。全部59条查询、103项相关性判断、source/variant证据和来源文件字节未改变；不构成质量变化。父版本及修订理由见metadata-revision.json。\n'
    (report / 'summary.md').write_text(summary,encoding='utf-8')
    print(json.dumps(revision,ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=Path('data/reports/source-reviewed-retrieval-20261014-v1'))
    parser.add_argument('--finalize',type=Path)
    parser.add_argument('--correct-metadata',type=Path)
    args = parser.parse_args()
    if args.correct_metadata:
        assert args.finalize, '--finalize is the new gold version destination'
        correct_metadata(args.correct_metadata,args.finalize,args.output)
    elif args.finalize:
        finalize(args.output,args.finalize)
    else:
        catalogue(args.output)
