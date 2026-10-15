"""Source-read dedup freeze. Pair decisions are explicit Agent judgments, not model output."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "data/reports/resume-quality-snapshot-20261005-v1"
OUTPUT = ROOT / "data/gold/source-reviewed-dedup-20261014-v1"
REPORT = ROOT / "data/reports/source-reviewed-dedup-20261014-v1"

# (left occurrence position in immutable facts.json, right position, label, basis)
# Populated only after opening and reading both complete original Markdown sources.
PAIRS: list[tuple[int | str, int | str, str, str]] = [
    (1819, 2241, "SAME", "两篇一面均口头问 volatile 的作用，均未限定某一操作或要求代码。"),
    (2665, 2385, "SAME", "原文 scynonize/reantlock 为明显拼写错误；两端均比较 synchronized 与 ReentrantLock。"),
    (2385, 2041, "SAME", "两端均要求比较 Java 的 synchronized 与 ReentrantLock，主体与回答范围一致。"),
    (2665, 2041, "SAME", "两篇均比较同一组 Java 锁机制；拼写与名称排列顺序不改变比较任务。"),
    (390, 2265, "SAME", "均枚举线程池核心参数，没有额外业务取值或拒绝策略要求。"),
    (812, 458, "SAME", "两端均询问 Redis 持久化方式，未附高可用或具体业务约束。"),
    (812, 2708, "SAME", "飞书原文 redi 是 Redis 的拼写省略；两端核心任务均解释持久化方式。"),
    (458, 2708, "SAME", "Redis 持久化方式与 redi 持久化均为同一口头知识题。"),
    (690, 2366, "SAME", "上下文均为 MySQL 八股；两端要求事务隔离级别，不额外指定并发异常场景。"),
    (690, 187, "SAME", "美团原文事务等级级别位于 MySQL 问题中；与 MySQL 事务隔离级别回答边界一致。"),
    (187, 2366, "SAME", "均位于数据库八股段落，原文事物/事务写法差异不改变隔离级别任务。"),
    (676, 2047, "SAME", "MVCC 的思想与说一下 MVCC 均为无额外约束的原理介绍。"),
    (1538, 2565, "SAME", "均询问 MySQL 选择 B+ 树作为索引的原因，没有具体 SQL 或不同树结构限制。"),
    (585, 2565, "SAME", "去哪儿上下文为数据库索引；均解释 B+ 树优于其他索引结构的选型原因。"),
    (1538, 585, "SAME", "两端均要求解释数据库索引为什么选择 B+ 树，原文没有另加实现题。"),
    (2172, 1127, "SAME", "SQL 慢怎么排查与线上慢查询分析如何做均询问慢 SQL 的排查流程。"),
    (2392, 210, "SAME", "ACID 各自如何保证与事务特性如何实现均要求列出并解释事务四特性及其实现。"),
    (1242, 501, "SAME", "两端均在算法/手撕环节求最长无重复子串，未追加字符集或复杂度差异。"),
    (1122, 645, "SAME", "拼多多全文明确为手撕；两端均实现最长无重复字符子串。"),
    (645, 470, "SAME", "腾讯与小米原文同为手撕无重复字符最长子串，任务完全一致。"),
    (470, 501, "SAME", "两端同为编程求最长无重复字符子串长度，未包含其他联合任务。"),
    (1242, 1122, "SAME", "两端均为最长无重复子串算法题；面试公司和日期不同不改变求解目标。"),
    (1122, 470, "SAME", "两篇原文手撕目标均为最长无重复字符子串，无不同输入限制。"),
    (1242, 645, "SAME", "算法环节均求最长无重复字符子串，可用同一完整求解回答。"),
    (501, 645, "SAME", "两篇同为手撕最长无重复字符子串长度，表述顺序差异不影响任务。"),
    (1242, 470, "SAME", "两端均在算法环节要求最长无重复子串实现，未加变式条件。"),
    (501, 1122, "SAME", "手撕最大无重复子串长度与手撕最长无重复子串为同一长度求解任务。"),
    (1559, 807, "SAME", "两端均在明确算法/手撕段落反转链表；作者报错描述不是额外题目约束。"),
    (314, 807, "SAME", "小红书替换算法题与飞书手撕题均为反转链表，无区间或分组变式。"),
    (1559, 314, "SAME", "两篇算法环节均反转整条链表，没有增加输出或空间限制。"),
    (1613, 2160, "SAME", "均要求比较 ArrayList 与 LinkedList；上海原文 LinedList 为上下文可辨拼写错误。"),
    (2693, 855, "SAME", "两端均为无指定垃圾收集器的口头垃圾回收机制介绍。"),
    (917, 855, "SAME", "Java 垃圾回收算法或机制与得物垃圾回收机制均为一般 GC 知识介绍。"),
    (2693, 917, "SAME", "快手与美团均口头讲 Java 垃圾回收，未限定 G1/CMS 或故障排查。"),
    (1617, 787, "SAME", "bigo 上文持续讨论 Redis；缓存淘汰策略与 Redis 淘汰策略为同一内存淘汰知识题。"),
    (618, 1455, "SAME", "两端均询问 Redis 缓存与 MySQL 数据一致性方案；未附主从延迟或强一致追加条件。"),
    (1459, 618, "SAME", "缓存数据库一致性与数据库和缓存一致性均为同一通用双写/读写协调问题。"),
    (1455, 1459, "SAME", "两端原始主问都要求缓存数据库一致性；飞书后续强一致追问另为问题，未混入本题。"),
    (1536, 791, "SAME", "两端均解释 TCP 三次握手流程，没有为什么不是两次的附加任务。"),
    (1536, 2326, "SAME", "飞书原文上文已明确 TCP；三次握手与 TCP 三次握手整体流程回答范围一致。"),
    (791, 2326, "SAME", "均为 TCP 三次握手的口头知识题，没有数据承载或中断故障约束。"),
    (1273, 2101, "SAME", "两端均问第三次 TCP 握手可否携带数据，主体、阶段和判断目标一致。"),
    (439, 2381, "SAME", "同场面试因答错而再次追问漏桶；两问均要求解释漏桶策略，没有换成令牌桶。"),
    (1681, 333, "SAME", "两端均要求介绍 MySQL 锁机制，全文无保留下来的特定选择题条件。"),
    (1646, 333, "SAME", "说一下 MySQL 的锁与介绍 MySQL 锁机制均为一般锁知识题。"),
    (1681, 1646, "SAME", "两端主体均为 MySQL 锁，原文未增加特定 SQL 或隔离级别限制。"),
    (2731, 2479, "SAME", "均枚举数据库索引失效场景，无给定 SQL 的命中分析要求。"),
    (1329, 987, "SAME", "平时用什么 AI 写代码与使用过哪些 AI 编程工具都询问工具使用清单。"),
    (2516, 1017, "SAME", "类加载过程与类加载机制均为一般 Java 类加载知识题。"),
    (2514, 1017, "SAME", "Tiktok 最终选 Java 类加载过程；与 b站类加载机制均介绍 Java 类加载。"),
    (2514, 2516, "SAME", "两端最终实际回答对象均为 Java 类加载过程，无 C/Python 编译要求混入。"),
    (2302, 2317, "SAME", "Redis 挂了的兜底与 Redis 故障尽量不影响生产均要求服务降级/可用性保障。"),
    (1615, 2100, "SAME", "腾讯 LRU 问答与小红书明确口述 LRU 均为口头算法机制/实现思路，无手写或 TTL 条件。"),
    (1326, 1561, "RELATED", "研究生课程成绩与实验室方向培养模式都涉及研究生经历，但评价对象不同。"),
    (1819, 586, "RELATED", "volatile 作用是总览；能否保证原子性只考察其中一个性质，不是同一回答边界。"),
    (2241, 1968, "RELATED", "volatile 作用包含多种语义；禁止重排的实现是指定机制的深入追问。"),
    (586, 573, "RELATED", "是否 volatile 保证原子性与 Java 怎么保证原子性共享原子性主题，但判断指定关键字与方案枚举不同。"),
    (573, 802, "RELATED", "Java 保证原子性是通用方案；i++ 的线程安全额外限定具体非原子操作。"),
    (2265, 375, "RELATED", "列举线程池核心参数与项目中如何设置参数涉及同一对象，但知识枚举与业务调参不同。"),
    (390, 2271, "RELATED", "线程池参数列表与拒绝策略列表关联，后者只要求特定参数机制。"),
    (2265, 1662, "RELATED", "一端仅核心参数，另一端同时要求线程池原理与参数，范围扩大。"),
    (299, 500, "RELATED", "无界队列的风险与线程池何时使用阻塞队列都涉及工作队列，但风险诊断与调度条件不同。"),
    (737, 1806, "RELATED", "提交任务的方式差异与具体 submit 代码异常能否被发现相关，后者要分析真实代码及被忽略的 Future。"),
    (2769, 2700, "RELATED", "均问线程池用途，但一个限定特定个人项目，另一问一般用途，不能假定项目答案相同。"),
    (2193, "d138-cf", "RELATED", "为何选择 CompletableFuture 与为何为其选择自定义线程池相关，技术决策的对象分别是异步 API 与执行器。"),
    (47, 2410, "RELATED", "AOP 原理与设计模式加底层实现相关；一端明确额外要求设计模式，不能压缩成同一题。"),
    (2410, 606, "RELATED", "AOP 底层原理与多种实现方式及差异有共通内容，后一题还要求方案比较。"),
    (2704, 146, "RELATED", "AOP 全部失效场景与 default/static 等修饰符的失效判断相关，后一题有具体方法限制。"),
    (900, 228, "RELATED", "Spring 事务失效场景总览与 a 内调用 b 的自调用案例有包含关系，具体案例不能等同总览。"),
    (228, 2284, "SAME", "两端均为同一类加事务注解的方法内部调用另一个加事务注解方法，判断被调方法事务注解是否有效。"),
    (872, 900, "RELATED", "事务传播级别和失效场景都关联 Spring 事务，但一个是传播语义，另一个是失败原因。"),
    (1265, 495, "RELATED", "ThreadLocal 定义与 ThreadLocal 介绍加应用场景范围不同，一端明确要求用途。"),
    (2699, 1324, "RELATED", "ThreadLocal 可能问题为总览，内存泄漏原因是其中一个指定问题。"),
    (1324, 1038, "RELATED", "内存泄漏原因与原理加泄漏原因加解决方案相关，完整回答要求不同。"),
    (102, 1342, "RELATED", "父子线程共享且排除 InheritableThreadLocal 是实现方案题；新线程能否拿到原数据是可见性判断题。"),
    (496, 1342, "RELATED", "ThreadLocal 线程隔离实现与新线程数据读取相关，后一题限定跨线程场景。"),
    (2396, 2228, "RELATED", "一般 JVM 运行时区域与逐区域判断 OOM 关联，后者附加内存溢出范围要求。"),
    (1663, 126, "RELATED", "已发生 OOM 的排查与尚未 OOM 的内存上涨排查不同，后者还明确不能影响线上。"),
    (1913, 1663, "RELATED", "内存占用过多不等于已经 OOM；观测条件和排查目标不同。"),
    (288, 1663, "RELATED", "频繁 Full GC 与 OOM 都关联内存故障，但触发条件和证据要求不同。"),
    (2693, 1150, "RELATED", "一般 GC 机制与指定 CMS 的阶段过程相关，收集器范围不同。"),
    (2693, 847, "RELATED", "一般 GC 介绍与 G1 50ms 停顿目标、大对象影响的场景判断不同。"),
    (1598, 227, "RELATED", "枚举 GC roots 与解释可达性分析相关，一个问根集合，一个问追踪判定机制。"),
    (2731, 2206, "RELATED", "索引失效仅是慢 SQL 原因的一部分；后一题原文明确持续追问其他原因与解决办法。"),
    (1127, 1926, "RELATED", "慢查询排查流程与慢查询成因列表相关，证据定位和原因枚举的回答要求不同。"),
    (1142, 2172, "RELATED", "定位慢 SQL 语句只是完整慢 SQL 排查的一步，定位对象与全面排查范围不同。"),
    (540, 838, "RELATED", "口头解决深分页与给场景手写深分页 SQL 共享问题，但作答形式和实际实现要求不同。"),
    (540, 2221, "RELATED", "深分页解决总览与指定游标/延迟关联分页的利弊比较有范围差异。"),
    (1263, 2159, "RELATED", "前题要为 a>0,b=1,c in 条件建索引，后题分析 a=1,b=2,c>3,d=4 既定联合索引命中，条件与任务不同。"),
    (1538, 1129, "RELATED", "MySQL 选 B+ 树的原因与索引底层原理加查询快的原因相关，后一题范围更广。"),
    (731, 2634, "RELATED", "亿级数据可能问题与单表已放不下的优化相关，但后者有确定容量瓶颈，不能认作完全相同场景。"),
    (2586, 2562, "RELATED", "不能冷热分离时分表与必须保留冷数据查询的冷热分层设计具有相反的方案约束。"),
    (2012, 1423, "RELATED", "按时间/id 分表并设计索引查询与何时分库分表怎么分相关，前题多了指定分片轴和查询代价。"),
    (618, 1911, "RELATED", "通用缓存数据库一致性与高访问频次下 MySQL 主从延迟读旧数据相关，后者限定读源和延迟故障。"),
    (1455, 257, "RELATED", "原项目尽可能一致与明确需要强一致的业务要求不同，不可合并其保证级别。"),
    (618, 915, "RELATED", "Redis 与 MySQL 一致性和本地缓存与 Redis 一致性都为缓存协调问题，副本对象不同。"),
    (1186, 1432, "RELATED", "前题只询问三类缓存故障的解决，后题同时要求定义三者及解决，知识范围更广。"),
    (1186, 1339, "RELATED", "前题覆盖穿透、雪崩、击穿，后题仅穿透与击穿，不能吞掉雪崩要求。"),
    (1969, 1392, "RELATED", "高流量下缓存过期与指定单个热点 key 过期的击穿追问不同，故障粒度不同。"),
    (1919, 2002, "RELATED", "布隆过滤器原理及使用比纯原理多一个应用要求。"),
    (814, 384, "RELATED", "原理特点与用布隆过滤器解决指定 10万/秒消息过滤场景相关，后一题有工程上下文。"),
    (2589, 1308, "RELATED", "10亿数据空间估算与100亿数据已需2GB后继续优化约束不同，数字规模会改变方案。"),
    (2686, 784, "RELATED", "布隆过滤器重启丢失与 map 持久化掉电恢复均涉及持久化，但数据结构和并发正确性要求不同。"),
    (1615, 2653, "RELATED", "口头 LRU 实现思路与明确手撕 LRU 共享淘汰算法，但后者要求完整代码。"),
    (2100, 2653, "RELATED", "口述 LRU 和手撕 LRU 的回答形式不同，不能凭 LRU 裸词合并。"),
    (1615, 632, "RELATED", "普通 LRU 口述与 O(1)、有限容量、值替换、TTL 的 map 口头设计不同，后者附加过期语义。"),
    (2653, 2621, "RELATED", "单独 LRU 手撕与同时支持 LRU/LFU、击穿/穿透的缓存系统代码题不同，后者范围显著更广。"),
    (632, 784, "RELATED", "同场 map 第一题为内存结构设计，第二题增加磁盘持久化并发和掉电恢复要求。"),
    (1483, 2181, "RELATED", "Redis 分布式锁具体实现与一般分布式锁介绍相关，但技术主体限定范围不同。"),
    (2541, 2665, "RELATED", "setnx 与 synchronized 比较跨进程协调，synchronized 与 ReentrantLock 比较 Java 进程内锁，比较对象不同。"),
    (2770, 2611, "RELATED", "分布式锁考虑哪些维度与指定抢购业务锁粒度相关；业务与决策范围不同。"),
    (477, 997, "RELATED", "接口幂等方案口述与明确手撕接口幂等共享目标，但后一题要求代码产物。"),
    (477, 1510, "RELATED", "通用接口幂等与扣款场景上下游消息不重复相关，消息边界和支付约束不同。"),
    (1387, 2129, "RELATED", "重复消费处理与重复消息加消息丢失同时处理相关，后一题多可靠性要求。"),
    (206, 2554, "RELATED", "一般 MQ 不丢失与 Kafka 三端不丢失且不重复不同，技术范围和可靠性性质不同。"),
    (1327, 206, "RELATED", "Kafka 选型加防丢失与一般 MQ 防丢失相关，前题包含独立选型原因。"),
    (825, 1274, "RELATED", "RocketMQ 延迟消息底层原理与自行设计延迟消息队列不同，一个解释既有实现，一个构造新系统。"),
    (1274, 324, "RELATED", "延时队列整体设计与消费时点/调度者的追问相关，问答边界不同。"),
    (1536, 2208, "RELATED", "三次握手流程与为什么需要三次是事实过程和设计原因的不同任务。"),
    (2208, 689, "RELATED", "为何需要三次与明确比较两次和四次的问法相关，后者附加四次替代方案分析。"),
    (193, 1139, "RELATED", "TCP 可靠性与 HTTP3 在 UDP 上可靠性都涉及可靠传输，但协议、传输栈不同。"),
    (2379, 2469, "RELATED", "HTTPS 具体通信过程与 HTTP/HTTPS 差异比较有交集，前者需要完整握手流程。"),
    (712, 387, "RELATED", "对称非对称加密差异及选型与差异及常见算法清单不同，附加要求分别是决策和枚举。"),
    (1333, 400, "RELATED", "MCP/Skills 区别与区别加 Skills 性能、架构、渐进披露优势范围不同。"),
    (1610, 1357, "RELATED", "MCP 工具数量是否越多越好与按业务域为多个外部团队隔离交付工具相关，后一题有明确多租户设计约束。"),
    (2182, 1835, "RELATED", "个人上传文件问答知识库与公司 SDK 信息问答 Agent 都依赖知识，但用户、语料和产品需求不同。"),
    (452, 1237, "RELATED", "客户端提供代码修复与已具日志/告警自动恢复不同，输入信号和恢复闭环要求不同。"),
    (9, 1045, "RELATED", "普通链表交点与明确升级为有环链表不同，环会改变判定和遍历终止条件。"),
    (478, 1409, "RELATED", "两个交叉链表找交点与追问不使用额外空间相同母题但多了空间限制。"),
    (132, 467, "RELATED", "口述合并1000条有序链表与手撕合并k条链表相关，固定规模与作答形式不同。"),
    (127, 1751, "RELATED", "不用递归快排与普通快排手撕相关，禁止递归是关键实现约束。"),
    (841, 2294, "RELATED", "给先序中序求后序遍历与凭前序判断完全二叉树相关，但输入信息和输出判定不同。"),
    (2255, 362, "RELATED", "同场最长递增子序列先求个数，后改最大长度，返回目标发生变化不能合并。"),
    (345, 794, "RELATED", "最大子数组和与明确湍流子数组最大和不同，后题增加相邻升降交替约束。"),
    ("d145-grid", 921, "RELATED", "二值网格普通最短路径与允许拆除一堵墙的最短路径不同，障碍可穿越预算是关键约束。"),
    ("d145-grid", "d145-risk", "RELATED", "同场网格变式由最短步数改为路径最大数最小化，输入值和优化目标不同。"),
    ("d145-median", 2285, "RELATED", "100GB文件/1GB内存与未定大小整数文件/2GB内存都求中位数，实际容量和内存约束不同。"),
    (480, 2732, "RELATED", "TB日志文件 top100 受32GB内存及16字符行限制，5亿消费记录求销量top100的聚合主体和输入限制不同。"),
    (1111, 649, "RELATED", "两个线程hello/world交替打印与未给定线程数和内容的规定顺序多线程打印相关，前题约束更具体。"),
    (1332, 1333, "DIFFERENT", "前题问项目使用什么应用层协议，后题比较 MCP 与 Skills；协议一词不代表相同答案。"),
    (1487, 2514, "DIFFERENT", "同场由 C 编译执行换成 Java 类加载；语言执行链与 JVM 加载阶段的技术主体不同。"),
    (1753, 1242, "DIFFERENT", "Redis 滑动窗口限流工程实现与最长无重复子串求解不同，窗口词汇不能替代任务主体。"),
    (1576, 1242, "DIFFERENT", "限流策略总览与字符串子串算法分别约束请求速率和寻找最长字符区间。"),
    (1617, 1307, "DIFFERENT", "前题在 Redis 上文中问内存缓存淘汰，后题原文指定 MySQL 数据淘汰，不将数据库对象改成 Redis。"),
    (2653, 2733, "DIFFERENT", "LRU 缓存代码需要哈希索引和淘汰语义，链表归并排序需要有序重排；链表实现交集不使目标等价。"),
    (1559, 2733, "DIFFERENT", "反转链表与链表归并排序的输出顺序和比较规则不同，不能用反转回答排序。"),
    (9, 314, "DIFFERENT", "链表交点判定与链表反转修改链接方向是两个独立求解任务。"),
    (467, 1514, "DIFFERENT", "合并k条链表与合并两个数组的数据表示、容器数量、输出形式不同，不能只按合并有序识别同题。"),
    (467, 2733, "DIFFERENT", "已排序k链表合并与对单条链表做归并排序不同，输入前提与算法整体目标不同。"),
    (841, 1522, "DIFFERENT", "先序中序推后序与层序遍历有不同输入信息和输出顺序，没有同一完整求解回答。"),
    (68, 1522, "DIFFERENT", "有向图拓扑排序依据入度约束，树层序遍历依据深度顺序，广度遍历实现交集不等于同题。"),
    (898, 1122, "DIFFERENT", "数组中重复数据与字符串最长不重复子串不同，输出重复元素和最优连续区间是不同目标。"),
    (1490, 921, "DIFFERENT", "螺旋打印矩阵与拆墙后网格最短路径虽均二维输入，输出遍历序列与路径长度任务不同。"),
    (2480, 811, "DIFFERENT", "字符串表示数的加法与数组数字拼接得到最大整数不同，运算与最优排列目标不相同。"),
    (1882, 1122, "DIFFERENT", "反转字符串元音与最长无重复子串共享字符串输入但输出修改字符串和长度目标不同。"),
    (180, 2187, "DIFFERENT", "求最小k个数与删除100亿QQ号中最大10个分别选择最小集合与删除最大集合，规模和目标不同。"),
    (1714, 1608, "DIFFERENT", "牛顿迭代平方根与数组最大最小值的最大公约数没有相同求解目标，数值词汇不构成相关母题。"),
    (1363, 54, "DIFFERENT", "跳过4后数字展开序列的第N位与统计0到n数字1出现次数不同，序列定义、输出对象均不同。"),
    (1976, 54, "DIFFERENT", "把n拆成2的幂之和与计数数字1的出现次数不同，二进制分解和十进制统计无法互替。"),
    (1611, 839, "DIFFERENT", "排列是否为另一串子串与交错字符串判定分别使用置换和保持子串相对次序的交织约束。"),
    (397, 345, "DIFFERENT", "统计和为k的子数组组合数与寻找最大子数组和有不同条件及输出指标。"),
    (210, 872, "DIFFERENT", "数据库ACID特性与 Spring 事务传播级别分别考察存储保证和框架调用关系，事务词汇不能直接合并。"),
    (2684, 872, "DIFFERENT", "JVM四种引用与 Spring 事务传播级别是独立运行时/事务机制问题。"),
    (2517, 35, "DIFFERENT", "JVM内存区域与 JMM 并发可见性模型在原文分别列为不同题，内存模型字面相同不能混为一谈。"),
    (2181, 1646, "DIFFERENT", "分布式锁协调多个服务，MySQL锁管理数据库并发访问，锁的作用对象与机制边界不同。"),
    (1770, 2671, "DIFFERENT", "CAS原子比较交换与雪花ID生成算法分别解决并发更新和唯一ID，底层实现并不构成同题。"),
    (2385, 2374, "DIFFERENT", "Java同步锁比较与进程间通信方式分别关注互斥机制和消息/数据传递机制。"),
    (2213, 2374, "DIFFERENT", "线程间通信与进程间通信共享通信词汇，但地址空间边界及可用机制不同。"),
    (2621, 784, "DIFFERENT", "LRU/LFU与穿透击穿缓存系统代码和已有map的磁盘持久化读写优化分别是缓存产品实现与存储恢复机制。"),
    (2662, 2708, "DIFFERENT", "热点缓存更新时如何只允许一个线程与 Redis 持久化方式各自解决并发击穿和落盘恢复，不能按Redis归同题。"),
    (1886, 746, "DIFFERENT", "集群分片下Lua是否失效与写防超卖Lua及命令方案不同，一个判断跨槽执行约束，一个库存原子操作。"),
    (1008, 812, "DIFFERENT", "Redis动态配置实现与 Redis持久化方式分别关注配置管理和数据落盘。"),
    (650, 2302, "RELATED", "Redis宕机后找回动态配置与 Redis整体故障兜底相关，前题限定数据对象为配置。"),
    (2732, 480, "RELATED", "消费记录按商品聚合销量与日志行频率聚合有共同topk思路，但数据粒度和内存约束不同。"),
    (163, 470, "RELATED", "前题真实原文同时要求二叉树直径与最长无重复子串，是复合题；后题仅子串，不将复合题整体合并。"),
    (1339, 1945, "DIFFERENT", "缓存穿透击穿处理与RAG文档切分分别涉及缓存访问防护和知识索引，切/穿字面无关。"),
    (1968, 2622, "DIFFERENT", "volatile禁止重排与重载重写区别分别关注内存顺序和方法绑定，重字词汇不能混同。"),
    (996, 1475, "DIFFERENT", "Redis为什么快与消费者少收到一条消息的故障排查需要不同技术证据，性能与可靠性目标不同。"),
    (961, 2379, "DIFFERENT", "HTTP2/HTTP3差别与HTTPS通信具体过程分别涉及HTTP版本与TLS加密协商，版本数字不等于安全层。"),
    (2102, 712, "RELATED", "加密与加签区别同对称/非对称差异有关，但前题按安全目的区分，后题按密码体制及选型区分。"),
    (205, 2292, "RELATED", "进程线程区别与进程线程协程定义有关，后一题额外包括协程且任务是定义。"),
    (629, 1484, "RELATED", "进程线程资源共享私有与进程切换开销都涉及上下文，但一个问资源归属，一个问调度成本。"),
    (1521, 1200, "RELATED", "双Token版本校验与双Token存储及RS256签名有关，具体持久位置和签名算法不是版本方案。"),
    (252, 98, "DIFFERENT", "Cookie生成机制与JWT组成原理及负载分别关注浏览器状态和令牌编码签名结构。"),
    (1333, 2240, "RELATED", "MCP/Skills区别与MCP工具调用介绍共享MCP主题，但比较两种能力与介绍调用不同。"),
    (2667, 2604, "RELATED", "模型如何知道操作MCP与和模型交互用何传输协议相关，但工具决策机制与传输协议不相同。"),
    (2013, 1699, "RELATED", "文档分块方式及效果选择与切块大小如何确定相关，策略选择与尺寸选择边界不同。"),
    (597, 2234, "DIFFERENT", "上下文压缩保证准确性与Agent memory设计分别关注当前上下文缩短和跨交互记忆组织。"),
    (545, 334, "DIFFERENT", "rerank原理与KNN/BM25召回原理属于排序和候选获取阶段，不能以检索一词归并。"),
    (2666, 2631, "RELATED", "意图识别选择子agent并汇总与识别后选择单/多个工具相关，但执行主体和结果汇总要求不同。"),
    (971, 1333, "RELATED", "Skills/MCP/Function Call三者介绍比仅比较MCP/Skills多了Function Call，答案边界不同。"),
    (352, 1206, "RELATED", "AI应用整体好坏评判与evaluation如何评判输出有关，后者明确限定输出评判边界。"),
    (1701, 1075, "RELATED", "Agent能力评测体系及指标与ground truth是否线上采集及处理有关，前题设计测评，后题审查数据来源。"),
    (2072, 882, "RELATED", "一般模型幻觉成因与知识库无准确答案时防止胡编相关，后一题指定证据缺失场景且问缓解。"),
    (2698, 1526, "RELATED", "RAG检索准确性策略与RAG减少幻觉策略不同，检索质量不能直接等同生成真实性。"),
    (598, 2570, "DIFFERENT", "监控埋点大量日志请求处理与流日志等概率抽取分别关注系统吞吐和随机样本均匀性。"),
    (480, 2570, "DIFFERENT", "受限内存统计日志top100与等概率抽取日志分别输出高频集合和均匀样本，目标相反。"),
    (1920, 2511, "DIFFERENT", "黄牛恶意下单防护与TCP SYN Flood处理各自位于业务订单层与传输握手层。"),
    (747, 1366, "DIFFERENT", "服务端账号密码登录处理与浏览器输入URL到显示页面各自关注鉴权业务与网页网络加载。"),
    (1069, 2589, "DIFFERENT", "Redis滑动窗口内存预估与10亿元素布隆过滤器空间估算的数据结构与计算模型不同。"),
    (1994, 2358, "DIFFERENT", "虚拟内存还有什么作用与有序数组最近元素虽都可能提到空间，根本对象和目标不同。"),
    (1192, 1294, "DIFFERENT", "Nginx容灾设计与MinIO可靠性涉及不同服务、故障切换和数据恢复保证。"),
    (1090, 2302, "RELATED", "熔断状态流转与Redis故障兜底有降级机制关联，但一个解释组件状态机，一个提出依赖故障方案。"),
    (324, 707, "RELATED", "延迟消息准点消费调度与项目定时推送相关，但任务对象与已有上下文不同。"),
    (2077, 1927, "RELATED", "项目最大表的数据量与项目索引配置都涉数据库结构，但一个问规模，一个问加了哪些索引。"),
    (1124, 838, "DIFFERENT", "学生两门课超过分数阈值计数SQL与深分页SQL不同，聚合统计和分页检索不可互替。"),
    (1258, 1378, "RELATED", "new String对象数量与new/赋值String equals结果有关，分别是分配计数和相等性判断。"),
    (2733, 420, "RELATED", "链表归并排序手写与归并快排复杂度稳定性口头比较有关，作答形式和比较范围不同。"),
    (1805, 433, "SAME", "两篇均仅要求介绍实习经历，无限定某个技术问题或业务模块，原文上下文没有追加约束。"),
    (1906, 1455, "RELATED", "旁路缓存机制及读取问题与通用Redis/MySQL一致性有关，前题限定cache-aside并只关注读取。"),
    (2294, 921, "DIFFERENT", "前序信息判断完全二叉树与拆墙网格最短路径的图结构、输出类型和约束均不同。"),
    (1526, 2485, "RELATED", "RAG幻觉优化策略与项目召回率相关但不同，生成错误治理和检索指标读数不可当同题。"),
    (2072, 2288, "RELATED", "大模型幻觉几个原因与如何解决幻觉是成因和缓解两个独立回答目标。"),
    (2117, 2319, "RELATED", "向量化如何实现及topk设置与向量化模型底层算法有共同向量主题，附加参数和模型原理要求不同。"),
    (1309, 1445, "DIFFERENT", "Agent token成本减少测算与Redis提高MySQL效率测算涉及不同系统指标与对照基线。"),
    (182, 672, "RELATED", "AI coding兼顾自身能力与企业产出和用AI开发大型项目都涉及开发流程，但前题还有个人能力保留目标。"),
    (2371, 2295, "DIFFERENT", "是否了解操作系统网络与使用过哪些数据库是不同知识领域及经验清单。"),
    (1925, 1974, "RELATED", "大促稳定性保障与高/突发流量多级缓存方案相关，前题包含整体保障，后题限定流量和缓存。"),
    (925, 722, "RELATED", "批量COS第10条特别大仅回9条与平台大量数据下载相关，前题有部分成功和慢任务约束。"),
]

READ_DOCUMENTS = [25, 26, 29, 30, 36, 41, 43, 48, 50, 53, 60, 63, 70, 74,
                  78, 79, 83, 89, 91, 92, 103, 104, 105, 114, 116, 117, 128,
                  129, 130, 131, 133, 138, 139, 143, 145, 147, 148, 149, 150,
                  151, 158, 160, 161, 163, 171, 173, 180, 181]

# These prompts were missing from facts; only original interview sections are used.
MANUAL = {
    "d138-cf": (138, "项目中使用了 CompletableFuture，你用了自定义线程池吗？为什么使用自定义线程池？"),
    "d145-grid": (145, "题目1 一个由01组成的二维数组，1是墙，0是路，希望找到从左上角到右下角的最短路径"),
    "d145-risk": (145, "题目2 为题目1变式加难度，上面数组不只是01，而是任何整数，定义一个路径的风险值为这个路径上面的最大数，计算从左上角到右下角的路径中最小的风险值是多少。"),
    "d145-median": (145, "题目3 思考题 一个100GB 全是数字的无序数组的文件，如何在1GB 内存下找到中位数。"),
}


def _load_section(root: Path, manifest: dict, section: str):
    value = manifest.get(section, [])
    if isinstance(value, str):
        return [json.loads(line) for line in (root / value).read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    return value


def previous_inventory():
    """Read identities/evidence only; never return any previous semantic labels."""
    pairs, sources, occurrences, by_version = set(), set(), set(), []
    for path in sorted((ROOT / "data/gold").glob("*/manifest.json")):
        if path.parent == OUTPUT or path.parent.name.startswith("source-reviewed-"):
            continue
        manifest = json.loads(path.read_text(encoding="utf-8-sig"))
        local_pairs, local_sources, local_questions = set(), set(), set()
        for section in ("extraction", "task_labels", "dedup", "retrieval", "routing", "sql"):
            for row in _load_section(path.parent, manifest, section):
                if row.get("source_hash"):
                    local_sources.add(row["source_hash"])
                if row.get("occurrence_id"):
                    local_questions.add(row["occurrence_id"])
                for q in row.get("questions", []):
                    local_questions.add(q.get("occurrence_id", q["id"]))
                if section == "dedup" and row.get("left") and row.get("right"):
                    ids = [row[side].get("occurrence_id", row[side]["id"]) for side in ("left", "right")]
                    local_pairs.add(tuple(sorted(ids)))
                    local_questions.update(ids)
                    local_sources.update(row[side]["source_hash"] for side in ("left", "right") if row[side].get("source_hash"))
        pairs.update(local_pairs)
        sources.update(local_sources)
        occurrences.update(local_questions)
        by_version.append({"version": manifest.get("version", path.parent.name), "pairs": local_pairs,
                           "sources": local_sources, "occurrences": local_questions})
    return pairs, sources, occurrences, by_version


def endpoint(key: int | str, value: dict, by_revision: dict):
    if isinstance(key, int):
        q = value["questions"][key]
        revisions = {s["revision_id"] for s in q["source_spans"]}
        if len(revisions) != 1:
            raise ValueError(f"Multiple sources in occurrence {key}")
        doc = by_revision[next(iter(revisions))]
        spans = [{k: s[k] for k in ("start_char", "end_char", "quote")} for s in q["source_spans"]]
        identity, occurrence_id = q["id"], q["id"]
    else:
        number, quote = MANUAL[key]
        doc = value["documents"][number]
        raw_text = decode((SNAPSHOT / doc["source_path"]).read_bytes())
        if raw_text.count(quote) != 1:
            raise ValueError(f"Manual quote nonunique: {key}")
        start = raw_text.index(quote)
        spans = [{"start_char": start, "end_char": start + len(quote), "quote": quote}]
        identity, occurrence_id = f"source-{doc['source_hash'][:16]}-{start}", None
    original = (SNAPSHOT / doc["source_path"]).read_bytes()
    if sha(original) != doc["source_hash"]:
        raise ValueError("Snapshot source hash mismatch")
    text = decode(original)
    if isinstance(key, int) and key == 632:
        # The source title says "following requirements"; facts omitted all four.
        start = spans[0]["start_char"]
        quote_end = "key value是有有效期的，过期需要丢掉。"
        end = text.index(quote_end, start) + len(quote_end)
        spans = [{"start_char": start, "end_char": end, "quote": text[start:end]}]
    if isinstance(key, int) and key == 1806:
        # A code-inspection task cannot omit the actual code under examination.
        start = spans[0]["start_char"]
        code_start = text.index("```java", start)
        end = text.index("```", code_start + 7) + 3
        spans = [{"start_char": start, "end_char": end, "quote": text[start:end]}]
    for span in spans:
        if text[span["start_char"]:span["end_char"]] != span["quote"]:
            raise ValueError(f"Raw evidence mismatch: {key}")
        span["start_line"] = text[:span["start_char"]].count("\n") + 1
        span["end_line"] = text[:span["end_char"] - 1].count("\n") + 1
    raw = "\n".join(span["quote"] for span in spans)
    if "\ufffd" in raw:
        raise ValueError(f"Source replacement character in {key}")
    return {"id": identity, "occurrence_id": occurrence_id, "text": raw,
            "raw_question": raw, "source_path": doc["source_path"], "source_hash": doc["source_hash"],
            "source_spans": spans, "original_path": doc["path"], "facts_position": key if isinstance(key, int) else None,
            "source_only_id": key if isinstance(key, str) else None,
            "full_document_reviewed": True,
            "span_repaired_from_original": key in {632, 1806},
            "source_revision_id": doc["revision_id"]}


def freeze_dataset():
    from eval.common import digest, write_json, write_jsonl
    from eval.validate_gold import load_dataset, validate_gold

    if (OUTPUT / "manifest.json").exists():
        raise ValueError("Output version exists; inspect with --verify; create a new version for revisions")
    value, by_revision = facts()
    old_pairs, old_sources, old_occurrences, by_version = previous_inventory()
    stamp = datetime.now(timezone(timedelta(hours=8))).isoformat()
    endpoints, selected, excluded, seen = {}, [], [], set()
    for position, (left_key, right_key, label, basis) in enumerate(PAIRS):
        for key in (left_key, right_key):
            endpoints.setdefault(key, endpoint(key, value, by_revision))
        left, right = endpoints[left_key], endpoints[right_key]
        pair = tuple(sorted((left["id"], right["id"])))
        if pair in old_pairs or pair in seen:
            excluded.append({"recipe_position": position, "left_id": left["id"], "right_id": right["id"],
                             "reason": "previous_gold_pair" if pair in old_pairs else "recipe_duplicate_or_reverse"})
            continue
        seen.add(pair)
        selected.append({"id": f"source-dedup-20261014-{position:03d}", "group_id": f"dedup-{pair[0]}-{pair[1]}",
                         "split": "test", "tags": ["dedup", "raw_source_review", "diagnostic_regression", label.lower()],
                         "human_verified": False, "agent_verified": True,
                         "left": left, "right": right, "label": label,
                         "review": {"reviewer": "codex-gold-dedup-20261014", "reviewer_kind": "agent",
                                    "reviewed_at": stamp, "guide_version": "annotation_v2",
                                    "basis": basis, "method": "full_original_markdown_read_then_pair_answer_boundary_review"}})
    if len(selected) < 150 or set(row["label"] for row in selected) != {"SAME", "RELATED", "DIFFERENT"}:
        raise ValueError("Formal dedup size/classes incomplete")
    used_questions = {side["id"] for row in selected for side in (row["left"], row["right"])}
    used_sources = {side["source_hash"] for row in selected for side in (row["left"], row["right"])}
    used_existing = {side["occurrence_id"] for row in selected for side in (row["left"], row["right"]) if side["occurrence_id"]}
    source_docs = [doc for doc in value["documents"] if doc["source_hash"] in used_sources]
    read_revision = {value["documents"][i]["revision_id"] for i in READ_DOCUMENTS}
    if any(side["source_revision_id"] not in read_revision for row in selected for side in (row["left"], row["right"])):
        raise ValueError("Pair contains source not in complete-source reading ledger")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    REPORT.mkdir(parents=True, exist_ok=True)
    for doc in source_docs:
        target = OUTPUT / doc["source_path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SNAPSHOT / doc["source_path"], target)
    overlap = {"previous_gold_pair_overlap": 0, "previous_pairs_excluded": sum(r["reason"] == "previous_gold_pair" for r in excluded),
               "reversed_recipe_duplicates_excluded": sum(r["reason"] == "recipe_duplicate_or_reverse" for r in excluded),
               "previous_gold_source_overlap": len(used_sources & old_sources),
               "previous_gold_occurrence_overlap": len(used_existing & old_occurrences),
               "all_sources_from_previously_processed_corpus": True,
               "new_source_generalization": False,
               "scope": "New raw-source pair judgments on old r293 corpus; original sources and individual occurrences may already have been used for development.",
               "by_version": [{"version": version["version"], "source_overlap": len(used_sources & version["sources"]),
                               "occurrence_overlap": len(used_existing & version["occurrences"]),
                               "pair_overlap": len(seen & version["pairs"])} for version in by_version]}
    manifest = {"version": OUTPUT.name, "status": "frozen", "kind": "source_reviewed_delegated_agent_diagnostic",
                "source_evidence_contract": "raw_source_spans_v1", "snapshot": value["snapshot"],
                "annotation_guide": "docs/annotation-guide.md", "annotation_guide_sha256": sha((ROOT / "docs/annotation-guide.md").read_bytes()),
                "source_snapshot": SNAPSHOT.relative_to(ROOT).as_posix(), "facts_sha256": sha((SNAPSHOT / "facts.json").read_bytes()),
                "recipe_sha256": sha(Path(__file__).read_bytes()), "frozen_at": stamp,
                "review_authorization": {"policy": "delegated_agent", "thread_id": os.environ.get("CODEX_THREAD_ID", "01a13aca-ae5e-74e0-b458-e7f81d368769"),
                                         "user_instruction": "你先把你能做的能完善的完善好，先就由你自己来审核，找gold set，但是一定要真实的去面经里找了"},
                "sampling": {"complete_sources_read": len(READ_DOCUMENTS), "used_sources": len(used_sources),
                             "used_questions": len(used_questions), "class_counts": dict(Counter(row["label"] for row in selected)),
                             "candidate_count": len(PAIRS), "class_distribution_is_diagnostic_not_natural_prevalence": True},
                "leakage": overlap, "extraction": [], "task_labels": [], "dedup": "dedup.jsonl",
                "retrieval": [], "routing": [], "sql": []}
    write_jsonl(OUTPUT / "dedup.jsonl", selected)
    write_json(OUTPUT / "source-read-ledger.json", {"reviewer": "codex-gold-dedup-20261014", "reviewed_at": stamp,
               "documents": [{"document_position": i, "original_path": value["documents"][i]["path"],
                              "source_hash": value["documents"][i]["source_hash"], "read_scope": "entire_original_markdown"} for i in READ_DOCUMENTS]})
    write_json(OUTPUT / "manifest.json", manifest)
    write_json(OUTPUT / "freeze.json", {"dataset_sha256": digest(load_dataset(OUTPUT)), "frozen_at": stamp,
               "file_hashes": {p.relative_to(OUTPUT).as_posix(): sha(p.read_bytes()) for p in sorted(OUTPUT.rglob("*")) if p.is_file() and p.name != "freeze.json"}})
    validate_gold(OUTPUT, "dedup", review_policy="delegated_agent")
    write_json(REPORT / "excluded-pairs.json", excluded)
    write_json(REPORT / "leakage.json", overlap)
    write_json(REPORT / "source-evidence-validation.json", {"dataset_sha256": digest(load_dataset(OUTPUT)),
               "freeze_sha256": sha((OUTPUT / "freeze.json").read_bytes()), "pairs": len(selected),
               "endpoint_references_checked": 2 * len(selected), "unique_question_ids": len(used_questions),
               "source_documents": len(used_sources), "replacement_chars_in_selected_raw_questions": 0,
               "full_sources_read": len(READ_DOCUMENTS), "status": "PASS", "external_model_calls": 0})
    # This inspection deliberately happens after the independent labels are sealed.
    canonical = {q["id"]: q["canonical_question_id"] for q in value["questions"]}
    grouping_rows, disagreements, merged = [], [], Counter()
    for row in selected:
        left = canonical.get(row["left"]["occurrence_id"])
        right = canonical.get(row["right"]["occurrence_id"])
        grouped = (left == right) if left and right else None
        record = {"id": row["id"], "gold_label": row["label"], "left_canonical_id": left,
                  "right_canonical_id": right, "published_grouped_together": grouped,
                  "left_question": row["left"]["text"], "right_question": row["right"]["text"],
                  "review_basis": row["review"]["basis"], "left_source": row["left"]["original_path"],
                  "right_source": row["right"]["original_path"]}
        grouping_rows.append(record)
        if grouped is not None:
            merged[(row["label"], str(grouped))] += 1
        if (grouped and row["label"] != "SAME") or (grouped is False and row["label"] == "SAME"):
            disagreements.append(record)
    write_json(REPORT / "published-grouping-audit.json", {"kind": "published_canonical_consistency_audit_not_judge_model_predictions",
               "snapshot": value["snapshot"], "gold_sealed_before_mapping_inspection": True,
               "gold_dataset_sha256": digest(load_dataset(OUTPUT)), "external_model_calls": 0,
               "comparable_pairs": sum(row["published_grouped_together"] is not None for row in grouping_rows),
               "noncomparable_source_only_pairs": sum(row["published_grouped_together"] is None for row in grouping_rows),
               "counts": {f"{label}_grouped_{grouped}": count for (label, grouped), count in merged.items()},
               "disagreements": disagreements, "all_pairs": grouping_rows})
    print(json.dumps({"dataset": OUTPUT.as_posix(), "pairs": len(selected), "classes": manifest["sampling"]["class_counts"],
                     "used_sources": len(used_sources), "used_questions": len(used_questions), "excluded": len(excluded),
                     "mapping_disagreements": len(disagreements)}, ensure_ascii=False))


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def facts():
    value = json.loads((SNAPSHOT / "facts.json").read_text(encoding="utf-8"))
    by_revision = {d["revision_id"]: d for d in value["documents"]}
    return value, by_revision


def decode(raw: bytes) -> str:
    return raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")


def read_documents(numbers: list[int]):
    value, _ = facts()
    for number in numbers:
        doc = value["documents"][number]
        text = decode((SNAPSHOT / doc["source_path"]).read_bytes())
        matches = {}
        for position, q in enumerate(value["questions"]):
            for span in q["source_spans"]:
                if span["revision_id"] == doc["revision_id"]:
                    line = text[:span["start_char"]].count("\n") + 1
                    matches.setdefault(line, []).append(position)
        print(f"\nDOCUMENT {number}: {doc['path']} / {doc['source_hash']}")
        for line, content in enumerate(text.splitlines(), 1):
            keys = sorted(set(matches.get(line, [])))
            print(f"{line:03d} Q={keys}: {content}")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--read", type=int, nargs="+")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.read:
        read_documents(args.read)
        return
    if args.verify:
        from eval.validate_gold import validate_gold
        manifest = validate_gold(OUTPUT, "dedup", review_policy="delegated_agent")
        print(f"Dataset valid: {manifest['version']}; {len(manifest['dedup'])} source-reviewed pairs")
        return
    freeze_dataset()


if __name__ == "__main__":
    main()
