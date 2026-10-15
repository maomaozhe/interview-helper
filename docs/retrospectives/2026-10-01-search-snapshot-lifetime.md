---
id: PIT-019
status: current
resolution: mitigated
resolution_scope: search-snapshot-lifetime-around-model-waits
scope: historical-report-and-workspace
verified_at: 2026-10-15
runtime_verified: false
tags: [elasticsearch, snapshot, model-gate, lifecycle, retrieval]
sources:
  - path: md/issue.md
  - path: src/interview_intelligence/search/elasticsearch.py
    symbols: [ElasticsearchRetriever.retrieve]
tests:
  - tests/unit/test_elasticsearch_retriever.py
---

# PIT-019：搜索快照跨模型排队而过期

[返回踩坑索引](README.md) · [共享模型锁](2026-10-04-shared-lock-filesystem.md)

## 30 秒回忆

ES point-in-time 快照只有一分钟，查询却在创建快照后排队等模型；共享锁可能被导入占用超过一分钟。
改成“先生成向量，再打开快照完成两路召回并关闭，最后重排”。
历史记录有确定性过期复现和修复测试；真实导入与检索并行长等待的验收未因此补齐。

## 原始记录与定位

来源是 `md/issue.md` #20“串行模型等待会使 Elasticsearch 检索快照过期”。
这个 PIT-019 是案例编号，ES PIT 是 point-in-time 检索快照，两者不要混淆。

| 判断 | 依据与确定性 |
| --- | --- |
| 快照生命周期包含了无关的外部等待 | 已证实：旧调用顺序及可过期快照复现 |
| 锁等待可能超过 keep_alive | 已证实为测试覆盖场景：虚拟等待 120 秒，快照 60 秒 |
| 真实在线特定故障必然由此导致 | 未证明：原条目仍要求追加真实并行验证 |

## 尝试与解决过程

旧顺序在模型等待前打开快照，使 Dense 与 Hybrid+Rerank 的复现测试失败。
没有原证据说明曾通过调大 keep_alive 获得有效修复，不能把它写成真实成功尝试。

1. 先完成 query embedding，允许此阶段等待共享模型门。
2. 再打开一个 ES PIT，在同一快照内完成需要的词法/语义候选读取。
3. 候选收齐后在 finally 中关闭快照，清理失败路径同样处理。
4. 在快照之外做模型重排，避免长输出与排队延长 ES 资源占用。
5. 保留 corpus/index revision 检查；缩短快照生命周期不替代事实版本一致性。

当前入口为 [ElasticsearchRetriever.retrieve](../../src/interview_intelligence/search/elasticsearch.py)。
两路召回共享快照的目的仍保留；不能为了避过期把两路候选随意切到不同语料版本。

## 验证范围与未闭环

原问题记录修复后两项过期场景测试及受影响回归通过，属于当次工程证据。
现有 [test_serial_model_waits_do_not_expire_search_snapshot](../../tests/unit/test_elasticsearch_retriever.py) 模拟 encoder/reranker 的长等待，检查等待不落在 ES 快照有效期内。
本次只读测试定义，没有执行测试；也没有补做真实锁竞争、ES故障或生产负载测量。
因此状态为 mitigated：已实现生命周期修复，真实并发运行的指定验证仍待补。

## 防复发与再排查顺序

1. 先对齐 open/search/close 与模型 gate 等待时序，不把过期错误直接归因 ES 不稳定。
2. 区分 keep_alive、整轮查询 deadline、模型请求超时和锁排队时间，各有不同资源范围。
3. 检查新增模型调用是否又被放进快照临界阶段；候选上下文加载也要核对时序。
4. 保留可过期快照测试，再按需要补真实 worker 与查询并行的有界验收。

已有措施是明确调用顺序、finally 清理及确定性过期测试；真实并发验收是待补证据，不应写成已经完成的守护。

## 回顾自测

1. 为什么只增加整轮查询 timeout 不能解决 ES 快照过期？
2. 把两路召回拆成不同快照是否等价？

<details><summary>查看答案</summary>

1. 两者生命周期不同；应用仍允许等待，并不会自动延长已打开快照的有效期。
2. 不等价，可能读到不同发布状态；应缩短同一快照占用，而非放弃候选的一致视图。

</details>
