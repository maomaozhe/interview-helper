---
id: business-invariants
status: current
scope: workspace
verified_at: 2026-10-15
sources:
  - path: src/interview_intelligence/repository/corpus.py
    symbols: [active_occurrences, publish_build]
  - path: src/interview_intelligence/search/service.py
    symbols: [eligible_canonical_ids, search_questions]
  - path: src/interview_intelligence/agent/query_service.py
    symbols: [QueryService._begin, QueryService._execute, QueryService.finish]
  - path: src/interview_intelligence/agent/harness.py
    symbols: [ToolPolicy]
  - path: src/interview_intelligence/analytics/semantic_scope.py
    symbols: [load_semantic_scope]
  - path: src/interview_intelligence/providers/runtime.py
    symbols: [RequestLimits]
  - path: src/interview_intelligence/web/assets/app.js
    symbols: [escapeHTML, safeLink]
tests:
  - tests/integration/test_corpus_snapshots.py
  - tests/integration/test_query_agent.py
  - tests/integration/test_search_frequency_ranking.py
  - tests/integration/test_semantic_scope_listing.py
  - tests/unit/test_harness.py
  - tests/integration/test_web.py
  - tests/web/test_core.cjs
---

# 开发时保持的业务不变量

这里记录容易在跨模块修改中破坏的现有契约。每条给出实际入口和验证线索；它们不是本次测试通过记录。整体流程见 [入库](../architecture/ingestion-flow.md)、[查询](../architecture/query-flow.md)，返回 [知识库首页](../README.md)。

## I01：来源、提问和标准题的身份分开

SourceDocument 是稳定来源，SourceRevision 是内容版本，DocumentBuild 是处理结果；QuestionOccurrence 是真实提问，CanonicalQuestion 是归并身份。一题出现多次应保留多个 occurrence，不能通过删 occurrence 达成“去重”。

入口：[models](../../src/interview_intelligence/domain/models.py)、[DedupService.resolve](../../src/interview_intelligence/dedup/service.py)。验证：[canonicalization](../../tests/integration/test_canonicalization.py)、[corpus snapshots](../../tests/integration/test_corpus_snapshots.py)。

## I02：所有历史记录不等于当前统计范围

有效事实要求来源的 active build、构建 INCLUDED、面试 analytics eligible；公司和轮次等组合条件在同一 occurrence 及所属面试上满足。不能把某题来自公司的记录和另一场二面的记录拼成“该公司二面”。

入口：[active_occurrences](../../src/interview_intelligence/repository/corpus.py)、[stats._active_from / _conditions](../../src/interview_intelligence/analytics/stats.py)。验证：[analytics](../../tests/integration/test_analytics.py)、[query metadata scope](../../tests/integration/test_query_metadata_scope.py)。

## I03：原文证据要与准确的来源绑定

原始字节 hash 与统一解码文本的 span 是不同层次。抽取引用必须能在指定 source revision 中复核；去重或相关性核验不得借邻题补出当前题没有的任务和约束。引用真实是必要条件，不能据此宣称语义判断正确。

入口：[snapshot](../../src/interview_intelligence/ingestion/snapshot.py)、[extraction validation](../../src/interview_intelligence/extraction/validation.py)、[dedup context](../../src/interview_intelligence/dedup/context.py)、[citations](../../src/interview_intelligence/search/citations.py)。验证：[extraction contract](../../tests/unit/test_extraction_contract.py)、[source context](../../tests/unit/test_dedup_source_context.py)、[canonical citations](../../tests/unit/test_canonical_citations.py)。

## I04：缓存、构建、发布和索引就绪是不同状态

抽取缓存独立提交用于失败复用；单文档事实发布在 PG 事务中切换 active build 和 revision。ES 在事务之外构建及切换 alias，不具备 PG/ES 跨系统原子事务。保持入口的语料/索引版本检查，不能把旧索引包装成新语料查询。

入口：[ExtractionStageCache](../../src/interview_intelligence/ingestion/extraction_cache.py)、[publish_build](../../src/interview_intelligence/repository/corpus.py)、[rebuild_index](../../src/interview_intelligence/search/elasticsearch.py)。验证：[stage cache](../../tests/integration/test_extraction_stage_cache.py)、[ingestion lifecycle](../../tests/integration/test_ingestion_lifecycle.py)、[worker](../../tests/integration/test_worker.py)。

## I05：完整 SQL 范围与 SEARCH 候选池分开

LIST/COUNT/STATS 的 SQL 操作面向完整资格范围；SEARCH 先由 SQL 选 eligible canonical IDs，再在检索候选中核验。每题 occurrence_count 来自 SQL，不代表候选池包含全库所有相关题。SEARCH 频次排序覆盖本次最多 50 个候选，不能称为全库最高频榜；当前 SEARCH 也没有 SQL 那样的后续游标分页。

SQL eligibility 空集可以确定性完成 SEARCH，回执以 `verification_basis=empty_sql_scope` 区分无须核验与模型核验成功。检索/核验失败返回的空结果不能冒充这种空范围证明；回归见 `tests/integration/test_query_semantic_filter_scope.py` 和 `test_query_partial_rerank.py`。

入口：[QuerySpec](../../src/interview_intelligence/agent/query_contract.py)、[search_questions](../../src/interview_intelligence/search/service.py)、[QueryService._execute](../../src/interview_intelligence/agent/query_service.py)。验证：[frequency ranking](../../tests/integration/test_search_frequency_ranking.py)、[search](../../tests/integration/test_search.py)。

## I06：要求语义全集时，缺失审核不能悄悄变成完整结果

`semantic_task=ALGORITHM_PROBLEM` 是完整 SQL 范围的一条独立路径。`load_semantic_scope` 校验当前匹配 occurrence 的正/负决定、版本和输入证据 hash；缺失、过期或非法审核导致 `SEMANTIC_SCOPE_INCOMPLETE`。不能用已知正例、关键词或 Top50 候选冒充完整范围，也不能把全部算法问题都强制归到 SEARCH。

入口：[semantic_scope](../../src/interview_intelligence/analytics/semantic_scope.py)。验证：[algorithm scope](../../tests/unit/test_algorithm_problem_scope.py)、[semantic scope listing](../../tests/integration/test_semantic_scope_listing.py)。SQL 执行不调用模型不代表前置语义标注无需审核。

## I07：scope 的来源与值都影响行为

显式 UI 筛选、用户语言明确约束、旧 SQL 列表范围、模型推断分类、软偏好/召回扩展不是同一类信息。纠正和历史重查必须保留或清除正确来源；不能把旧模型猜测升级成用户硬条件。COUNT 的完整范围与 Top N 列表集合也应分开。

入口：[QueryService._restore_requery / model_context / _execute](../../src/interview_intelligence/agent/query_service.py)、[Harness](../../src/interview_intelligence/agent/harness.py)。验证：[filter contract](../../tests/unit/test_query_filter_contract.py)、[conversation focus](../../tests/integration/test_conversation_focus.py)、[requery](../../tests/integration/test_query_requery.py)、[count review scope](../../tests/integration/test_count_review_scope.py)。

## I08：模型不能授予自己写权限

QuerySpec 做结构校验，ToolPolicy 按本轮请求和主机状态开放能力，主机执行前再次校验。复习写入必须满足本轮授权、当前可操作题目范围和幂等要求；不能因为模型给出了合法 JSON 就允许修改任意题目的用户状态。

入口：[ToolPolicy](../../src/interview_intelligence/agent/harness.py)、[QueryService.execute](../../src/interview_intelligence/agent/query_service.py)、[ReviewService](../../src/interview_intelligence/review/service.py)。验证：[harness](../../tests/unit/test_harness.py)、[harness policy](../../tests/integration/test_harness_policy.py)、[review](../../tests/integration/test_review.py)。

## I09：请求恢复与分页绑定原上下文

重试同一个请求需保留 request_id 和原会话绑定，避免重复工具写入；游标签名绑定用户、筛选和相关版本。会话 version、corpus revision、annotation revision、user revision 是不同概念，不能混用。失效游标应按契约处理，不能绕过检查继续读另一个集合。

入口：[QueryService._begin](../../src/interview_intelligence/agent/query_service.py)、[QueryJournal](../../src/interview_intelligence/agent/journal.py)、[listing](../../src/interview_intelligence/analytics/listing.py)、[scope](../../src/interview_intelligence/analytics/scope.py)。验证：[query agent](../../tests/integration/test_query_agent.py)、[scope](../../tests/unit/test_scope.py)、[query spec completion](../../tests/integration/test_query_spec_completion.py)。

## I10：展示流、持久状态和执行所有权分开

模型 delta 与阶段事件用于展示；已完成结果来自持久化回执。断流应续读同一个 run 的事件或状态。当前 API 的活跃 runs/live_tasks 及 Pi 回调查找仍依赖进程内存，不能从事件已入 PG 推断支持任意多副本接管。

入口：[api](../../src/interview_intelligence/api.py)、[journal](../../src/interview_intelligence/agent/journal.py)、[query-stream](../../src/interview_intelligence/web/assets/query-stream.js)。验证：[query streaming](../../tests/unit/test_query_streaming.py)、[query-stream](../../tests/web/test_query_stream.cjs)。

## I11：用户使用、复习和质量评测各有口径

候选召回、核验通过、检索返回和题库浏览分别记录，返回不等于复习。零使用次数表示所选观测期未记录，不能单独推断检索质量。`usage_purpose=evaluation` 排除个人使用汇总，但不使反馈/复习等其他写入自动无副作用；评测仍需相应账号或数据库隔离。

入口：[usage](../../src/interview_intelligence/analytics/usage.py)、[review](../../src/interview_intelligence/review/service.py)、[评测手册](../../eval/README.md)。验证：[question usage](../../tests/integration/test_question_usage.py)、[PostgreSQL usage](../../tests/integration/test_question_usage_postgres.py)。

## I12：模型与质量结论保留真实边界

预算包含调用、期限和 token 预留；未知 usage 不能记作零消耗，共享模型锁只能约束使用同一锁的调用方。版本登记区分 prompt 文件、provider 实现和实际响应模型身份。新增候选通过离线检查或已见集局部改善，不等于默认已切换、已部署或正式质量门禁通过。

入口：[RequestLimits](../../src/interview_intelligence/providers/runtime.py)、[ModelCallGate](../../src/interview_intelligence/providers/gate.py)、[配置登记](runtime-config.md)、[状态摘要](../status/current.md)。验证：[budget](../../tests/unit/test_budget.py)、[model call gate](../../tests/unit/test_model_call_gate.py)、[release gate](../../tests/unit/test_release_gate.py)。

## I13：来源展示与异步页面不能改变事实

来源原文按文本转义展示，原帖链接只允许 HTTP(S)；本地文件访问遵守语料目录边界。搜索、详情、来源等异步响应按请求序号检查，过期响应不能覆盖新结果；健康状态还需按语料/索引版本合并。

入口：[app.js](../../src/interview_intelligence/web/assets/app.js) 的 `escapeHTML/safeLink`、`searchSequence/detailSequence/sourceSequence`，以及 [core.js](../../src/interview_intelligence/web/assets/core.js) 的 `selectHealthSnapshot`。验证线索：[Web 路径与纯文本](../../tests/integration/test_web.py) 的 `test_local_source_is_plain_text_and_cannot_escape_corpus`、[健康状态与会话边界](../../tests/web/test_core.cjs)。这些测试不代表所有异步页面场景已有完整覆盖。
