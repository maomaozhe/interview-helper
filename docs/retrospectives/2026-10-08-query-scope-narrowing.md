---
id: PIT-002
status: current
resolution: mitigated
resolution_scope: broad-query-scope
scope: historical-report-and-workspace
verified_at: 2026-10-15
runtime_verified: false
tags: [query-scope, retrieval, prompt, pi-schema, provenance, regression]
sources:
  - path: md/issue.md
  - path: docs/verification/2026-10-08-engineering-design.md
  - path: docs/verification/2026-10-08-conversation-retrieval.md
  - path: docs/verification/2026-10-08-retrieval-evidence.md
  - path: docs/verification/2026-10-08-retrieval-fixes.md
  - path: docs/bad-cases.md
  - path: src/interview_intelligence/agent/query_service.py
    symbols: [QueryService._search_question_type, QueryService._search_semantic_dimensions, QueryService._execute]
  - path: src/interview_intelligence/agent/harness.py
    symbols: [ContextCompiler.compile]
  - path: src/interview_intelligence/agent/query_contract.py
    symbols: [QuerySpec]
  - path: services/pi-agent/runtime.mjs
    symbols: [runQuery]
  - path: src/interview_intelligence/search/elasticsearch.py
    symbols: [ElasticsearchRetriever.retrieve, _preferred_candidate_pool]
  - path: prompts/query_agent_v13.md
tests:
  - tests/unit/test_engineering_design_prompt.py
  - tests/integration/test_query_question_type_scope.py
  - tests/integration/test_query_semantic_filter_scope.py
  - tests/node/pi-runtime.test.mjs
  - tests/unit/test_elasticsearch_retriever.py
  - tests/integration/test_query_partial_rerank.py
  - tests/integration/test_conversation_quality.py
  - tests/integration/test_query_intent.py
---

# PIT-002：宽类别被示例和旧范围逐层收窄

[返回踩坑回顾索引](README.md) · [完整历史验收](../verification/2026-10-08-engineering-design.md)

## 30 秒回忆

用户要“工程系统设计题，类似设计一个排行榜这种”，系统却只给一道排行榜题。
问题沿链路叠加：示例变成必选对象，旧隐式标签变成 SQL 硬筛；补救时新增的软召回参数又被 Pi 工具 schema 漏掉。
这次修复的关键是保留条件来源，并验证参数从模型可见 schema 一直传到实际候选分支。
事故已缓解，剩余漏召回、邻近题和延迟仍保留；本页不关闭 H01 或完整 V1 质量门禁。

## 症状与定位依据

- 触发上下文是先查 Agent 设计，再纠正到工程系统设计；原句中的排行榜只是在举例。
- 原线上 v12 同时限定排行榜对象及 `SYSTEM_DESIGN + coding_focus=ENGINEERING + KNOWN`，最终只返回一道。
- 当时固定参考中的漏题分布在不同阶段：有题未进入资格范围，有题未入 Top50，也有已召回题被错判为邻近题。
- 因此“返回少”不能直接归因 embedding，也不能靠增加显示条数解决；应逐段定位丢失位置。

依据为[验收报告的原因与修复](../verification/2026-10-08-engineering-design.md#原因与修复)和[坏案例记录](../bad-cases.md#工程系统设计题被示例与旧范围窄化)。

## 已证实的因果链

1. **把例子当必要对象。** “类似排行榜这种”被改写成每题必须设计排行榜，相关性核验也按错误目标排除其他系统。
2. **混用不同任务标签。** `coding_focus=ENGINEERING` 描述工程编程任务，不能代表系统设计；旧模型推断与会话残留不应自动升级为当前硬筛。
3. **修复新增参数在跨语言边界丢失。** 第五候选已加入软偏好，但 Pi 的搜索工具 schema 白名单遗漏 `preferred_question_type`，实际偏好分支未执行。
4. **质量结果掩盖功能未生效。** 第五候选精度通过，覆盖仍不足；若只看“结果都相关”，便会错过参数根本没穿过执行链这一事实。

第三点是在修复过程中暴露的另一层阻断，不应倒推为原始一题结果的唯一原因。
用户明确的 UI 筛选、可信 SQL 范围与明确代码要求仍需保留；解决残留范围不等于清空所有条件。

## 实际失败尝试

| 尝试 | 失败表现 | 当时得到的教训 |
| --- | --- | --- |
| 候选 1–4 | 仍澄清、残留硬筛，或混入项目复述、代码与局部追问 | 仅改宽查询措辞不足以建立任务边界 |
| 候选 5 | 返回精度通过，覆盖仅 9/23；软分支未执行 | 检查功能路径，不能只检查最终精度 |
| 候选 6 | 补齐 Pi 白名单后，整批结构校验失败，再规划耗尽调用预算 | 错误需要保存并分层处理，不能无条件重规划 |
| 候选 7 | 软分支执行，覆盖仍只有 12/23 | 通用词法问法挤入 Agent 题；复核又把独立设计过窄理解为完整业务系统 |

第六候选没有保存首错的具体字段，不能事后猜测字段原因；报告也未据此提高输出或调用预算。
SSH 转发失败、旧会话 fixture 已消费导致的版本冲突另行记录，不能混入语义成功率。
完整尝试及未执行案例见[失败尝试](../verification/2026-10-08-engineering-design.md#失败尝试)。

## 修法与当前源码入口

- **拆开意图与扩展。** [query v13](../../prompts/query_agent_v13.md) 区分宽类别举例、明确“只要排行榜”和直接解答；[ContextCompiler.compile](../../src/interview_intelligence/agent/harness.py) 将旧召回扩展单列，并标明旧相关性目标是模型改写。
- **保留筛选来源。** [QueryService](../../src/interview_intelligence/agent/query_service.py) 的 `_search_question_type` / `_search_semantic_dimensions` 对模型推断、显式 UI、可信继承和代码任务分别处理，并把应用决策写入回执。
- **软偏好不改变完整资格。** 顶层 `preferred_question_type=SYSTEM_DESIGN` 影响召回排序，不能替代 `filters.question_type`；[retriever](../../src/interview_intelligence/search/elasticsearch.py) 保留宽分支，偏好候选不得越出 SQL 资格范围。
- **验证跨语言传递。** [QuerySpec](../../src/interview_intelligence/agent/query_contract.py) 与 [Pi runtime](../../services/pi-agent/runtime.mjs) 都保留该字段；检查模型可见 schema、实际 HTTP plan、偏好执行回执及候选来源。
- **按原文核验独立任务。** 对象、任务及引文都需成立，邻题不能补出本题没有的设计要求；局部坏行可隔离并标为不完整，整批身份错误仍拒绝，核验失败不返回未经核验的成功列表。

第八候选只调整软支路词法/语义 RRF 权重为 1:2，并澄清独立设计单位和引用字段；未向召回规则塞入参考题 ID 或业务名。
当前源码保留这些边界，但后续已继续演进；上面是定位入口，不是历史发布包的逐字副本。

## 证据范围与剩余问题

历史报告记载 2026-10-09 发布 query v13 / rerank r14 后，原会话同句从 1 道变为 17 道主设计题，旧历史保留。
独立覆盖请求命中固定参考 15/23，含一道邻近题；仍有 7 道参考未入池、1 道被主重排误拒。
这些参考由 Agent 审核，`human_verified=false`，已用于开发回归，既非全库穷尽集合，也不证明未见来源泛化。
原公开请求耗时约 138 秒；语义门槛通过不等于响应速度达标，也不能把多轮结果并集当作单次覆盖。
本页核对的是历史报告及 2026-10-15 工作区；未复验线上，不把当时 v13/r14 验收写成当前生产版本证明。

## 相邻子事件：H01 的无关结果与阶段性漏召回

[H01. Harness 查询缺少概念扩展，并返回无关题目](../../md/issue.md)与上述工程设计收窄同属查询范围家族，但不是同一个已证实根因。
原 Harness 查询仅用字面 `harness`，HYBRID 返回未经相关性核验的候选，其中存在明显无关题。
这能证实原结果质量不合格，不能直接证明 embedding 整体失效，也不能从已返回结果算全库召回率。

| 阶段/实际尝试 | 证据与处理 | 未解决的边界 |
| --- | --- | --- |
| [conversation-retrieval](../verification/2026-10-08-conversation-retrieval.md) 的 v8/v9 | v8 对明确 Agent 请求过度澄清；v9 动作修正后仍混入泛 Redis；补本题业务证据才排除 | 精度提高不等于漏召回消失；已知 Harness 仍有一题不在 Top15 |
| [retrieval-evidence](../verification/2026-10-08-retrieval-evidence.md) 的 r7–r10 | 同一候选池对比：引文合法但语义错、纯度升却丢正例、泛 Redis 回弹分别保留 | 仅加强核验会损害覆盖，不能把“更少”直接当“更准” |
| memory 候选缺失 | 已符合 SQL 且在 ES，但不在 Top50；增加最多三个同对象词法 facets，与主支路共享资格集合 | 词法补召回不证明全库覆盖，也不允许扩大硬范围 |
| 展示覆盖选择器 | 固定 accepted pool 重现旧顺序后，限制每个缺失 facet 最多补一次，再回语义顺序 | 固定池已知命中 1/3→2/3；新鲜实际请求仍 1/3，不能移花接木 |
| [retrieval-fixes](../verification/2026-10-08-retrieval-fixes.md) 的 r15 | 完成请求但主复核 57/63、宽类保留 16/34；候选未部署 | 调用成功、评测跑完都不等于质量门槛通过 |

后续诊断还发现两类独立排除：缓存/数据库题同时被旧 topic_l1 与 topic_l2 求交排除；短信题被推断 ENGINEERING 排除，KNOWN 不是其原因。
只清一个条件无法恢复前者；Harness 的对应漏题没有同样 SQL 排除，错误发生在主/复核语义判断，不能套用同一修法。
当前做法是保持原意与召回扩展分离、自动推断可降为软偏好、明确 UI/可信范围仍保留，并记录实际应用来源。
澄清应在检索前终止，核验不可用不得返回未经核验的成功列表；完整性能力另见 [PIT-008](2026-10-14-complete-query-scope.md)。
这些验收保留开发参考集上的已知漏题，固定池、单次新请求与多轮并集有不同分母；H01 仍不能据此宣布全量召回解决。
已有守护见[原意/扩展与澄清](../../tests/integration/test_conversation_quality.py)、[查询意图](../../tests/integration/test_query_intent.py)及原报告中的固定池对照；本次未运行。

## 再遇到时的排查顺序

1. 对照原话、纠正上下文和 UI：例子、排他要求、找题与解答分别是什么；不要先以旧 `relevance_query` 代替原意。
2. 看 plan 与主机应用后的 filters 及 provenance：候选是被 SQL 排除，还是进入了宽资格集合。
3. 沿 `QuerySpec → Pi 工具 schema → HTTP plan → QueryService → retriever` 逐跳核对软字段；回执里要有分支实际执行证据。
4. 将已知漏题分成未入池、任务/相关性拒绝、引文无效、结构异常、页面截断；对各阶段分别核原文。
5. 用新会话及旧状态的独立副本回归，再测窄对象、直接解答、Agent 与代码请求；不要复用已消费的旧 fixture。

守护入口：[prompt/context](../../tests/unit/test_engineering_design_prompt.py)、[筛选来源](../../tests/integration/test_query_question_type_scope.py)、[语义维度](../../tests/integration/test_query_semantic_filter_scope.py)、[Pi schema→HTTP](../../tests/node/pi-runtime.test.mjs)、[候选分池](../../tests/unit/test_elasticsearch_retriever.py)、[部分核验](../../tests/integration/test_query_partial_rerank.py)。本次未执行这些测试。

## 自测两问

1. “类似排行榜这种”与“只要排行榜”应如何处理？哪些已有筛选仍应保留？
2. Python 接受了新参数、返回题目也都相关，能证明修复生效吗？还缺什么证据？

<details><summary>查看答案</summary>

1. 前者的例子可辅助召回，不成为必选对象；后者才限定对象。当前显式 UI 条件均须保留，旧模型推断不能冒充显式条件。
2. 不能；还要证明 Pi 模型可见 schema 和 HTTP 保留参数、偏好分支实际执行，并独立检查覆盖与剩余漏题。

</details>
