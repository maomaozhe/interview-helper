---
id: PIT-008
status: current
resolution: mitigated
resolution_scope: complete-sql-scope-and-audited-algorithm-semantics
scope: historical-report-and-workspace
verified_at: 2026-10-15
runtime_verified: false
tags: [query, sql, retrieval, frequency, semantic-scope, annotation, completeness]
sources:
  - path: md/issue.md
  - path: docs/verification/2026-10-10-query-metadata.md
  - path: docs/verification/2026-10-11-byte-algorithm-ssh.md
  - path: docs/verification/2026-10-12-byte-algorithm-semantic.md
  - path: docs/verification/2026-10-14-byte-algorithm-all.md
  - path: docs/verification/2026-10-14-query-timeout-frequency.md
  - path: src/interview_intelligence/analytics/semantic_scope.py
    symbols: [semantic_input_hash, semantic_base_filters, load_semantic_scope]
  - path: src/interview_intelligence/agent/query_contract.py
    symbols: [QuerySpec.validate_action]
  - path: src/interview_intelligence/agent/query_service.py
    symbols: [QueryService._execute, QueryService.finish]
tests:
  - tests/integration/test_query_agent.py
  - tests/integration/test_query_metadata_scope.py
  - tests/integration/test_byte_algorithm_regression.py
  - tests/integration/test_semantic_scope_listing.py
  - tests/integration/test_full_algorithm_query.py
  - tests/integration/test_search_frequency_ranking.py
---

# PIT-008：把候选列表当全集，或把旧标签当真实语义

[返回索引](README.md) · [最终完整范围验收](../verification/2026-10-14-byte-algorithm-all.md)

## 30 秒回忆

“找些相关题”“这个类别全部题”“按频次排”是不同契约：排序正确，也可能只排了有限候选。
另一个独立问题是类别本身：旧 `ALGORITHM` 或 `KNOWN` 标签不能证明原文要求一道算法题。
处理顺序是先确定请求全集，再证明每个 occurrence 的语义资格，最后聚合、计数和分页。
完整 SQL 能力现已存在；缺少范围审计时必须拒绝完整性承诺，不能悄悄退到 Top50。

## 原始事件与症状

- [Q01. 类别枚举被当成检索 Top K，数量与排名语义不正确](../../md/issue.md)：自然语言要 Top40，默认候选/页面 20 条却被解释成完整排名。
- [Q02. “手撕代码”与“算法题”混淆，旧标签不能直接用于新需求](../../md/issue.md)：知识主题、回答形式和编码任务混在一个旧类型里；同一标准题的不同来源要求也不同。
- [10 月 10 日元数据验收](../verification/2026-10-10-query-metadata.md)：公司别名及旧主题交集令结果为零；这是范围表达错误，不是“库里没有”。
- [10 月 12 日语义验收](../verification/2026-10-12-byte-algorithm-semantic.md)：元数据结果有原理题/设计题，也漏掉 UNKNOWN 中真实算法题。
- [10 月 14 日全量验收](../verification/2026-10-14-byte-algorithm-all.md)：追问“全部”仍走有界检索；相关性不可用又被包装成成功空列表。

这些事件相邻，但不能合成“换一个 prompt 就修好”的单根因。

## 已证实的原因与边界

| 子事件 | 已证实原因 | 不能据此推断 |
| --- | --- | --- |
| Q01 全局排名 | 检索 Top K、Top N 结果集、当前页混用 | 页里频次降序就代表全库排名 |
| Q02 类别口径 | 旧标签混合主题、形式、任务；canonical 不能替代 occurrence 的原文要求 | 有 CODE 或算法主题就一定有算法求解任务 |
| 公司查询为零 | 别名未归一，模型又继承旧 topic 硬筛；这些条件实际求交 | SQL 查零等于真实零题 |
| 语义精度 | 引文存在，但引文没有证明本题要求具体算法操作或计算任务 | “引用可定位”等于“判断正确” |
| 全部追问 | 有界候选无法证明全量；核验不可用仍成功属于另一项状态契约错误 | 上游 APIError 的根因已查明 |

Q02 后续采用 occurrence 级 `response_form` / `coding_focus`，保留旧类型以兼容历史。
当时手撕工程代码与算法求解按不同口径查询；历史回填数量和 KNOWN 数不是当前语义正确率。

## 实际修复过程与失败尝试

1. Q01 改为 SQL 在有效 occurrence 范围内汇总标准题频次，再做全局 Top N 和稳定分页；区分 `matching_total`、结果集总数与当前页。
2. 元数据修复增加公司别名/有效值目录，历史重查以原始用户目标为准；旧模型 filters 只能提供参考，显式 UI 条件仍是约束。
3. 10 月 12 日改为语义候选核验：旧类型作为软召回线索，逐题核当前题的任务与连续原文引文，不能借邻题补出要求。
4. 10 月 14 日为“完整算法范围”补充持久化正/负审计，再让 LIST/COUNT 走 SQL；限定当前来源范围和版本。
5. 核验不可用改为 `QUERY_RELEVANCE_UNAVAILABLE`、失败回执及 API 503；真正核验后的零结果仍可成功。

| 实际尝试 | 结果与下一步 |
| --- | --- |
| 只改公司别名 | 仍受旧主题/任务口径影响；修正数量不等于语义已正确 |
| 10 月 12 日首个语义候选 | 5 组中 1 组历史重查把泛泛数组/链表设计误判为算法实现；未部署，收紧任务证据 |
| 10 月 14 日继续重复检索 | 首次 APIError 后再规划/重复调用，未知用量保守计费，后续预留超过预算；不能靠重试证明全集 |
| 全量验收 v2 | 合法 page_size=100 返回全部，但脚本硬期待 20；修正测试输入，保留失败记录 |
| 全量验收 v3 | COUNT 数值正确，却额外读工具并触发 fallback；收束为终止 COUNT，不靠加预算绕过 |

这些尝试详见各原验收；未记录原因的上游 APIError 仍未知。发布脚本/环境差异另查对应部署复盘。

## 当前实现如何守住契约

- [QuerySpec.validate_action](../../src/interview_intelligence/agent/query_contract.py) 限制 SEARCH 候选大小且不接受全局 `top_n`；`semantic_task=ALGORITHM_PROBLEM` 只用于 LIST/NEXT/COUNT/STATS。
- [load_semantic_scope](../../src/interview_intelligence/analytics/semantic_scope.py) 核验当前匹配范围内**全部** occurrence 的 ACCEPT/REJECT，包括负例；缺失、过期、无效均抛出 `SEMANTIC_SCOPE_INCOMPLETE`。
- `semantic_input_hash` 绑定 occurrence、原问题、source spans、来源版本和解码文本 hash；审计版本、连续引文及 reason 也须有效。
- `semantic_base_filters` 拒绝与显式旧 `coding_focus` / `annotation_status` 冲突的组合；不会静默清掉用户条件。
- SQL 只按接受的 occurrence 聚合：同一 canonical 的工程来源不能增加算法频次；`scope_hash` 参与分页一致性。
- SEARCH 的 frequency 模式先对已核验候选按过滤后的 SQL occurrence 次数排序，再截页；仍是候选池内频次，不是全库榜。

自然语言 SQL 请求仍可调用模型规划；结构化列表/计数的事实执行不依赖检索，不能笼统写成所有 SQL 请求“无模型”。

## 历史验证能说明什么

| 阶段 | 当时结论 | 限制 |
| --- | --- | --- |
| Q01 | Top40 与独立 PostgreSQL 查询一致；20+20 无重复 | 证明数据库口径一致，不证明标签语义 |
| 10 月 10–11 日 | 公司算法旧标签范围 27；COUNT/NEXT/过滤一致 | 10 日是本地，11 日另有 SSH 发布；27 不是语义真值 |
| 10 月 12 日 | 5 组隔离加 2 组公开语义验证；固定参考命中 24/26 | 有 2 个已知漏题，不能称完整总数 |
| 10 月 14 日 | 32 个来源范围内当前 565 条 occurrence 全部有正负审计；31 个正例归并为 28 道标准题 | Agent 审核，非人工金标；不代表公司所有历史来源 |

全量修复还恢复了同一来源版本下两个被错误新 active build 遮蔽的旧有效 build；旧坏 build 标记待核验，未靠重复插题补数。
最终 v4 记载隔离 7 组、公开 7 组及全局约束通过；这些是历史证据，不是本次运行结果。
与[频次验收](../verification/2026-10-14-query-timeout-frequency.md)中的候选排序共存；新增来源未经审计时不得沿用旧“完整”声明。

## 防复发与排查顺序

1. 先确认用户要全量、Top N 还是相关候选；读取真实 action/semantic_task 和响应完整性元数据。
2. 核显式筛选、继承来源、公司别名及同一 occurrence 的联合条件；再检查 active build 和来源版本。
3. 看正负审计是否全覆盖、hash/版本是否有效；缺口不能用旧 KNOWN 标签补齐。
4. 比较完整范围计数、标准题计数、occurrence 频次、分页并集；最后才检查页面展示。
5. 核验失败要查持久化失败状态和预算回执；不把成功 HTTP、空数组或自动重试当作业务成功。

已有守护：[范围审计/负例/新来源](../../tests/integration/test_semantic_scope_listing.py)、[全量多轮/超过 50 分页](../../tests/integration/test_full_algorithm_query.py)、[候选频次](../../tests/integration/test_search_frequency_ranking.py)。本次只静态核对，未运行。
待补证据是更多未见来源的独立语义审核，以及新来源进入后的审计运维闭环；现有样例不能替代它们。

## 自测两问

1. 20 条结果按频次严格降序，为什么仍可能不满足“全公司最常问的算法题”？
2. 当前范围有 31 个正例，负例中一条审计过期，能先返回“完整 28 道”吗？

<details><summary>查看答案</summary>

1. 可能只排了有限 SEARCH 候选；还可能使用了不等价的旧标签。必须证明完整来源范围、语义资格和 SQL 全范围聚合。
2. 不能。负例也属于完整性证明；过期后可能变成正例，必须拒绝完整声明并重新审计。

</details>
