---
id: module-map
status: current
scope: workspace
verified_at: 2026-10-25
sources:
  - path: src/interview_intelligence/mcp_server.py
    symbols: [KnowledgeClient, create_server]
  - path: src/interview_intelligence/agent/goal_contract.py
    symbols: [UserGoal, bind_goal, completion_for, restore_completion, evidence_by_ordinal]
  - path: src/interview_intelligence/agent/evidence_projection.py
    symbols: [project_row, project_meta]
  - path: src/interview_intelligence/agent/tool_definitions.py
  - path: scripts/sync-tool-contract.py
    symbols: [generated_section, generated_evidence_section]
  - path: src/interview_intelligence/api.py
    symbols: [create_app]
  - path: src/interview_intelligence/api_errors.py
    symbols: [register_error_handlers]
  - path: src/interview_intelligence/worker.py
    symbols: [build_worker_services, process_queued_run]
  - path: src/interview_intelligence/agent/query_service.py
    symbols: [QueryService]
  - path: src/interview_intelligence/relevance_scope.py
    symbols: [compile_relevance_scope, validate_relevance_scope]
  - path: src/interview_intelligence/agent/search_pagination.py
    symbols: [store_candidates, candidate_page]
  - path: src/interview_intelligence/agent/complete_workset.py
    symbols: [create, validate, apply_batch, page]
  - path: src/interview_intelligence/search/workset.py
    symbols: [validate_engineering_authorization, validate_manifest]
  - path: src/interview_intelligence/search/elasticsearch.py
    symbols: [ElasticsearchRetriever.retrieve]
  - path: src/interview_intelligence/search/service.py
    symbols: [search_questions, engineering_target_eligibility, validate_retrieval_rank_audit, target_catalog_contract, validate_catalog_selection_audit]
  - path: src/interview_intelligence/search/catalog_selector.py
    symbols: [CatalogSoftSelector.prepare, CatalogSoftSelector.select]
  - path: src/interview_intelligence/search/business_scope.py
    symbols: [business_proof_assessment, proof_query]
  - path: src/interview_intelligence/search/referents.py
    symbols: [build_reference_frame, build_source_item_frame, validated_reference_view, validate_reference_claim]
  - path: src/interview_intelligence/web/assets/app.js
    symbols: [semanticLibraryRequest, search, renderAssistant]
  - path: src/interview_intelligence/agent/collection_selection.py
    symbols: [CollectionSelection.resolve, CollectionSelection.prepare]
  - path: src/interview_intelligence/analytics/engineering_categories.py
    symbols: [engineering_collection_categories]
  - path: src/interview_intelligence/agent/query_response.py
    symbols: [build_query_response]
  - path: src/interview_intelligence/analytics/ranking.py
    symbols: [score_components, sql_score_components, importance_score, gap_score, sql_gap_score]
  - path: services/pi-agent/runtime.mjs
  - path: .github/workflows/evaluation.yml
  - path: eval/suite.py
    symbols: [run_suite]
  - path: eval/release.py
    symbols: [verify_report, check_bundle]
  - path: eval/query_results.py
    symbols: [build_query_results, score_query_results, source_references_for_case]
  - path: eval/answer_quality.py
    symbols: [validate_answer_rubric, score_answer]
  - path: eval/answer_reviews.py
    symbols: [load_answer_reviews]
  - path: src/interview_intelligence/analytics/semantic_audit.py
    symbols: [build_amendment, project_amendment]
  - path: src/interview_intelligence/analytics/semantic_scope.py
    symbols: [load_semantic_scope, semantic_scope_version]
  - path: src/interview_intelligence/analytics/listing.py
    symbols: [list_questions]
  - path: src/interview_intelligence/analytics/detail.py
    symbols: [get_question_detail, list_occurrences]
  - path: src/interview_intelligence/analytics/semantic_scope_import.py
    symbols: [build_scope_import_artifact, validate_scope_import_artifact, import_semantic_scope]
  - path: scripts/import-semantic-scope.py
    symbols: [main]
  - path: src/interview_intelligence/search/scenario_scope.py
    symbols: [scenario_proof_assessment]
  - path: src/interview_intelligence/search/reranker.py
    symbols: [LLMReranker.repair_primary_evidence, LLMReranker.repair_scope_evidence]
  - path: eval/latency.py
    symbols: [compare_latency]
tests:
  - tests/unit/test_query_result_multiturn.py
  - tests/unit/test_answer_quality.py
  - tests/integration/test_evaluation_goal_quality.py
  - tests/unit/test_mcp_server.py
  - tests/integration/test_mcp_api.py
  - tests/integration/test_search_request_limits.py
  - tests/node/compact-query-wire.test.mjs
  - tests/unit/test_compact_query_wire.py
  - tests/unit/test_tool_budget_policy.py
  - tests/integration/test_goal_completion.py
  - tests/integration/test_goal_completion_api.py
  - tests/node/goal-completion-runtime.test.mjs
  - tests/integration/test_api.py
  - tests/integration/test_query_agent.py
  - tests/integration/test_engineering_scope_listing.py
  - tests/integration/test_complete_engineering_query.py
  - tests/integration/test_clarified_business_scope.py
  - tests/unit/test_relevance_scope.py
  - tests/unit/test_business_scope_retrieval.py
  - tests/unit/test_catalog_selector.py
  - tests/integration/test_complete_catalog_retrieval.py
  - tests/integration/test_business_target_eligibility.py
  - tests/unit/test_business_scope_verification.py
  - tests/unit/test_business_scope_evidence_projection.py
  - tests/node/business-scope-pending-render.test.mjs
  - tests/integration/test_semantic_scope_import.py
  - tests/unit/test_engineering_evidence_recovery.py
  - tests/integration/test_detail_invalid_source.py
  - tests/unit/test_pending_evidence_projection.py
  - tests/node/pending-evidence-projection.test.mjs
  - tests/node/engineering-pending-render.test.mjs
  - tests/node/pi-runtime.test.mjs
---

# 模块与验证导航

证据投影的字段和限额统一在 [evidence_projection.py](../../src/interview_intelligence/agent/evidence_projection.py)；ContextCompiler 保留委托入口，Pi 使用同一脚本生成声明。host / pi_legacy 的字段、空值、分页与原生字符切片差异显式保留；[主机输出](../../tests/unit/test_evidence_projection.py)和 [Pi 实际摘要](../../tests/node/evidence-projection.test.mjs)固定原有口径，主机 evidence 数组优先、缺失/非数组才有界回退。

工程审核状态与确认/待核验次数在 host、Pi 和会话摘要中保留，避免后续回答把 PENDING 当确认；摘要仍不传未决来源全文或签名 cursor。详情的坏 span 另列 `source_issues` 与当前原问，`source_evidence_verified=false`，有效来源和次数不丢失；页面分组、题目行及详情都展示异常原因，不生成伪引用按钮。对应[投影守护](../../tests/unit/test_pending_evidence_projection.py)、[异常详情](../../tests/integration/test_detail_invalid_source.py)和[页面渲染](../../tests/node/engineering-pending-render.test.mjs)。

工作区新增的澄清范围 `relevance_scope` 与语料归属 `semantic_scope` 分开：前者核对用户本次业务目标、明确排除和独立要求，AI 默认不排除；其 CONFIRMED/PENDING 不替代工程归属审核。普通候选快照最多50题；原话明确全量工程范围后细化业务目标则保存完整候选工集，每次在至多20题窗口内按实际剩余预算核验连续前缀，两者均可签名恢复。10-16 已发布的320道 SQL 工程全集独立保留。当前源码候选为 v42/scope v25/candidate source v4，线上仍为 v15/scope v6；v41 真实首组23题未通过，原gold不变，新实现仍待工程、真实模型和部署验收。

静态工具声明统一维护在 [tool_definitions.py](../../src/interview_intelligence/agent/tool_definitions.py)，通过 [生成脚本](../../scripts/sync-tool-contract.py) 更新 Pi runtime 标记区；[Python 一致性](../../tests/unit/test_tool_contract.py) 和 [实际 Pi 菜单快照](../../tests/node/tool-contract.test.mjs) 守护漂移。主机业务校验与动态 ToolPolicy 仍独立执行。

现代Pi传输 `query_step_v1` 复用同一完整schema及当前动作能力，减少FilterSpec重复；输入版本由API显式选择，领域工具及host权限继续单独执行。排查慢规划时先看ModelCall的TTFT/queue/interval/provider和实际 `model_request`，不能仅由输入字节量推断上游等待原因。

按任务选一个小节，找到代码与测试即可转源码。跨模块关系不清时再读 [系统](../architecture/system.md)、[入库](../architecture/ingestion-flow.md) 或 [查询](../architecture/query-flow.md)。返回 [知识库首页](../README.md)。

## 入口与装配

| 改动 | 代码入口与边界 | 首选测试 |
| --- | --- | --- |
| HTTP 装配、错误映射 | [api.py](../../src/interview_intelligence/api.py)：`create_app` / `execute_query`；[api_errors.py](../../src/interview_intelligence/api_errors.py)：`register_error_handlers`。联查 Settings、QueryService、tenant、journal；保留 request_id、脱敏与重试语义 | [API](../../tests/integration/test_api.py)；账号/管理端点加对应领域测试 |
| CLI 入口 | [cli.py](../../src/interview_intelligence/cli.py) 的 Typer `ii` 命令；核对实际调用服务及配置 | [CLI](../../tests/integration/test_cli.py)，并选对应领域测试 |
| Web 服务端接口 | [web.py](../../src/interview_intelligence/web.py) 的 `register_web`：静态工作台、原文与反馈；联查用户范围、快照路径和回执 | [Web](../../tests/integration/test_web.py) |

`web.py` 是服务端，`web/` 是浏览器资源；`api.py` 也承担运行调度。worker 见入库，Pi 见 Agent。

## 领域模块

实体变更联查 [models](../../src/interview_intelligence/domain/models.py)、[contracts](../../src/interview_intelligence/contracts/__init__.py)、[migrations](../../migrations/versions/) 及受影响任务的测试。预留字段不代表机制已实现；FilterSpec、SourceSpan 在各入口保持一致。

<a id="ingestion"></a>

### 入库、抽取与去重

| 改动 | 代码入口与边界 | 首选测试 |
| --- | --- | --- |
| 抽取、引文、缓存 | [extraction/provider](../../src/interview_intelligence/extraction/provider.py)、[validation](../../src/interview_intelligence/extraction/validation.py)；不完整输出和不匹配引文不能发布 | [contract](../../tests/unit/test_extraction_contract.py)、[quote feedback](../../tests/unit/test_extraction_quote_feedback.py)、[stage cache](../../tests/integration/test_extraction_stage_cache.py) |
| 入库、发布、恢复 | [pipeline](../../src/interview_intelligence/ingestion/pipeline.py)：`IngestService.ingest_file` / `_materialize`；[worker](../../src/interview_intelligence/worker.py)：`build_worker_services` / `process_queued_run` / `recover_orphaned_runs`；[corpus](../../src/interview_intelligence/repository/corpus.py)：`active_occurrences` / `publish_build`。联查发布事务、批次身份、索引 | [lifecycle](../../tests/integration/test_ingestion_lifecycle.py)、[worker](../../tests/integration/test_worker.py)、[snapshots](../../tests/integration/test_corpus_snapshots.py)、[metadata publication](../../tests/integration/test_canonical_metadata_publication.py) |
| 归并、来源上下文 | [dedup/service](../../src/interview_intelligence/dedup/service.py) 的 `DedupService.resolve`、[provider](../../src/interview_intelligence/dedup/provider.py)、[context](../../src/interview_intelligence/dedup/context.py)；SAME/RELATED/DIFFERENT 与可选上下文装配 | [provider](../../tests/unit/test_model_dedup_provider.py)、[source context](../../tests/unit/test_dedup_source_context.py)、[answer boundaries](../../tests/unit/test_dedup_answer_boundaries.py)、[canonicalization](../../tests/integration/test_canonicalization.py) |
| 任务分类、核验发布 | [task_annotation](../../src/interview_intelligence/agent/task_annotation.py)、[annotation_review](../../src/interview_intelligence/agent/annotation_review.py)；联查 prompt、证据和 annotation revision | [task annotation](../../tests/integration/test_task_annotation.py) |

<a id="query"></a>

### 统计、检索与范围

| 改动 | 代码入口与边界 | 首选测试 |
| --- | --- | --- |
| SQL 筛选、计数、分页 | [listing](../../src/interview_intelligence/analytics/listing.py)、[counts](../../src/interview_intelligence/analytics/counts.py)、[stats](../../src/interview_intelligence/analytics/stats.py)；完整事实范围、同 occurrence 条件、签名及用户版本 | [analytics](../../tests/integration/test_analytics.py)、[filter scope](../../tests/unit/test_query_filter_contract.py)、[count scope](../../tests/integration/test_count_review_scope.py) |
| 排名、完整语义集合 | [ranking](../../src/interview_intelligence/analytics/ranking.py) 共用 importance/gap 政策；[semantic_scope](../../src/interview_intelligence/analytics/semantic_scope.py) 分开维护 `ALGORITHM_PROBLEM` / `ENGINEERING_DESIGN` 的来源审核与版本，联查 [listing](../../src/interview_intelligence/analytics/listing.py) 的签名分页。工程将已确认与待核验行一并展示，`meta.semantic_counts` 分开计数，未决不计已确认频次；算法保持完整审核要求。不能用 SEARCH50 候选或旧题型标签替代全集 | [固定值与零分母](../../tests/integration/test_ranking.py)、[算法 scope](../../tests/integration/test_semantic_scope_listing.py)、[工程 scope](../../tests/integration/test_engineering_scope_listing.py)、[工程主机链路](../../tests/integration/test_complete_engineering_query.py) |
| 大集合浏览、分类与分页 | [collection_selection](../../src/interview_intelligence/agent/collection_selection.py) 在实际可见题数超过阈值后先报数量，保存原范围与用户全量/分类选择；[engineering_categories](../../src/interview_intelligence/analytics/engineering_categories.py) 按 occurrence 既有 L1 对 canonical 互斥分桶，分类不明及待核验单列；[listing](../../src/interview_intelligence/analytics/listing.py) 在原 Top N/复习集合之后筛类别并签名分页。分类不代替题意审核 | [主机选择](../../tests/integration/test_collection_selection.py)、[状态与范围](../../tests/integration/test_collection_selection_state.py)、[分类 SQL](../../tests/integration/test_collection_category_listing.py)、[菜单与续页](../../tests/node/collection-selection-render.test.mjs) |
| 来源审核导入、历史与回滚 | [semantic_scope_import](../../src/interview_intelligence/analytics/semantic_scope_import.py)、[import-semantic-scope.py](../../scripts/import-semantic-scope.py)：精确逐问集合、原始/解码 hash、span、版本和审核者绑定；默认 dry-run，显式 apply 的原子导入与幂等、旧证据历史及 PREPARED/提交回执。工程允许有合法引用的 UNRESOLVED 导入并保持 incomplete，算法仍需完整二态。[semantic_audit](../../src/interview_intelligence/analytics/semantic_audit.py) 保留原有内存库更正入口 | [完整审核导入](../../tests/integration/test_semantic_scope_import.py)、[旧审核更正](../../tests/integration/test_semantic_scope_amendment.py) |
| 召回、索引与候选 | [search/service](../../src/interview_intelligence/search/service.py) 的资格、软先验与 SQL 标题hash契约；[elasticsearch](../../src/interview_intelligence/search/elasticsearch.py) 的同PIT完整目录枚举及投影版本核对；[catalog_selector](../../src/interview_intelligence/search/catalog_selector.py) 复用现有模型profile。仅明确 BUSINESS+CANDIDATES 的重排路径，以完整软先验目录标题提名最多40新增，替代第二目标embedding，保留原全库资格宽10/总50。目录不截标题或取前N；未选不是负例，标题不是核验证据，旧审核负例可走宽支路，不加type/topic/AI硬筛。目录/预算/调用异常显式fallback且不追加隐藏embedding；后续不足保留Pending。初次私有 `retrieval_rank_audit` 仍仅代表有界ES排名；`catalog_selection_audit` 绑定目录与prepared请求hash、软提名和预算回执，stage entered与provider admitted分开；均不进模型或后页，不能当业务全集 | [ES retriever](../../tests/unit/test_elasticsearch_retriever.py)、[完整目录及来源/预算回退](../../tests/integration/test_complete_catalog_retrieval.py)、[共享预算筛选](../../tests/unit/test_catalog_selector.py)、[业务目标召回](../../tests/unit/test_business_scope_retrieval.py)、[同来源先验与回退](../../tests/integration/test_business_target_eligibility.py)、[frequency ranking](../../tests/integration/test_search_frequency_ranking.py) |
| 相关性与证据核验 | [reranker](../../src/interview_intelligence/search/reranker.py) 的 `LLMReranker`；v42/scope v25 将同一 host 范围送入主排/复核，联查[业务范围证据](../../src/interview_intelligence/search/business_scope.py) 的 `business_scope_v7_source_activity_reference`。业务活动证据单列；当前任务/建设/领域仍绑定原问，[referents](../../src/interview_intelligence/search/referents.py) 单独校验连续前件和编号条目要求及scope引用声明，来源合法不等于语义正确。首次独立判定不携带主排标签；原主排诊断保留，修复联合重审矛盾字段。正、负、证据不足三态分别处理，未决保留 PENDING，补核验按实际剩余预算公平取子集。[design_scope](../../src/interview_intelligence/search/design_scope.py) 的独立要求由明确授权决定 | [业务活动边界](../../tests/unit/test_business_scope_verification_v42.py)、[指代与来源消费](../../tests/unit/test_business_scope_reference_v42.py)、[条目来源](../../tests/unit/test_source_item_frames.py)、[reranker](../../tests/unit/test_reranker.py)、[用户设计范围](../../tests/unit/test_design_scope_task_intent.py)、[有界补核验](../../tests/unit/test_scope_evidence_repair.py)、[主排证据恢复](../../tests/unit/test_engineering_evidence_recovery.py) |
| 澄清范围、候选分页与重查 | [relevance_scope](../../src/interview_intelligence/relevance_scope.py) 编译/重算原话授权，host [query_scope](../../src/interview_intelligence/agent/query_scope.py) 保留目标与控制词边界；[search_pagination](../../src/interview_intelligence/agent/search_pagination.py) 持久化至多50个确认/待核验候选，签名绑定会话/用户/来源run/scope/result/版本。续页不重新核验，重查保留范围，新目标清旧快照；旧宽集合菜单不能覆盖任何受约束scope | [授权与否定](../../tests/unit/test_relevance_scope.py)、[真实多轮措辞与恢复](../../tests/integration/test_clarified_business_scope.py)、[有界投影](../../tests/unit/test_business_scope_evidence_projection.py) |
| 全量工程细化业务后的完整候选 | [complete_workset](../../src/interview_intelligence/agent/complete_workset.py) 在核验前签名持久化全部资格 ID、进度及逐批回执；[search/workset](../../src/interview_intelligence/search/workset.py) 绑定原始工程全量授权、源/版本、两提示词 hash 及自适应前缀政策；[search/service](../../src/interview_intelligence/search/service.py) 提供至多20题窗口，[LLMReranker.select_workset_prefix](../../src/interview_intelligence/search/reranker.py) 用完整主排和必要独立复核的实际 prepared 上界选择前缀，目录只调顺序。来源加载仅用同条件工程合格 occurrence；多来源排除须完整负证，异常保留 PENDING；未处理尾部留在原工集。NEXT 需要处理新批次时消耗新一轮正常预算，缓存页/GET/刷新零模型；全量选择和重查保留范围 | [真实四轮与恢复](../../tests/integration/test_complete_business_workset.py)、[容量边界](../../tests/unit/test_workset_prefix_capacity.py)、[完整集合与每题结论](../../tests/integration/test_engineering_candidate_workset.py)、[签名分页](../../tests/unit/test_complete_workset_pagination.py)、[投影](../../tests/unit/test_candidate_workset_projection.py)、[页面进度](../../tests/node/candidate-workset-render.test.mjs) |

<a id="agent"></a>

### Agent、会话与个人状态

外部 agent 的只读 MCP 入口见 [接入说明](../mcp.md)：[mcp_server](../../src/interview_intelligence/mcp_server.py) 通过固定 HTTP 后端复用 legacy REST；[协议测试](../../tests/unit/test_mcp_server.py)、[真实 API 契约](../../tests/integration/test_mcp_api.py)及[公开检索预算](../../tests/integration/test_search_request_limits.py)分别验证。它不等价现代 QueryService 的范围继承与完整工集，不开放复习写入。

目标与完成契约由 [goal_contract](../../src/interview_intelligence/agent/goal_contract.py) 单独维护，`final`不再决定综合目标的终态；主机、Pi和恢复读取同一持久completion。现代自然语言v16先声明目标，typed/旧协议保留兼容。多轮/恢复/权限及预算闭环见[host回归](../../tests/integration/test_goal_completion.py)、[实际API](../../tests/integration/test_goal_completion_api.py)、[Pi多轮](../../tests/node/goal-completion-runtime.test.mjs)。回答覆盖字段与复习状态分别有[覆盖投影](../../tests/unit/test_answer_coverage_evidence.py)和[状态投影](../../tests/unit/test_review_state_evidence.py)，包含零值、截断和未知；这些工程断言不证明模型语义已通过。

| 改动 | 代码入口与边界 | 首选测试 |
| --- | --- | --- |
| 工具契约、Pi 编排 | [harness](../../src/interview_intelligence/agent/harness.py) 的 `ContextCompiler` / `ToolPolicy`、[query_contract](../../src/interview_intelligence/agent/query_contract.py) 的 `QuerySpec` / `TOOL_ACTIONS`；[Pi runtime](../../services/pi-agent/runtime.mjs) / [server](../../services/pi-agent/server.mjs) 的 schema 与主机回调，联查 ModelGateway；主机独立校验 | [harness](../../tests/unit/test_harness.py)、[query agent](../../tests/integration/test_query_agent.py)、[Pi runtime](../../tests/node/pi-runtime.test.mjs) |
| 执行、多轮、重查 | [query_service](../../src/interview_intelligence/agent/query_service.py)：`_begin` / `_execute` / `finish`；[journal](../../src/interview_intelligence/agent/journal.py) 保存回执，执行对象在进程内。[preferences](../../src/interview_intelligence/agent/preferences.py)、[feedback_memory](../../src/interview_intelligence/agent/feedback_memory.py) 与会话状态分开 | [conversation focus](../../tests/integration/test_conversation_focus.py)、[requery](../../tests/integration/test_query_requery.py)；流式恢复见前端路线 |
| 原始用户范围 | [query_scope](../../src/interview_intelligence/agent/query_scope.py) 的 `guard_plan` / `user_messages`；[relevance_scope](../../src/interview_intelligence/relevance_scope.py) 的原话ordinal/quote授权及hash身份。完整工程 LIST/COUNT/STATS 与普通 SEARCH 分开；候选选择不新增传统/非AI/独立条件，行为限制不误变领域排除，模型改写不升级硬约束 | [范围回归](../../tests/integration/test_query_user_scope_guard.py)、[工程完整查询](../../tests/integration/test_complete_engineering_query.py)、[范围授权](../../tests/unit/test_relevance_scope.py) |
| 结果呈现 | [query_response](../../src/interview_intelligence/agent/query_response.py) 的 `build_query_response` 组装文案、facts 与工具回执；[completeness](../../src/interview_intelligence/search/completeness.py) 区分候选核验、证据异常与全库覆盖。不更新会话或数据库 | [输入/引用守护](../../tests/unit/test_query_response.py)、[部分核验回执](../../tests/integration/test_query_partial_rerank.py)、[证据异常完整链路](../../tests/integration/test_query_evidence_failures.py)，并核对受影响查询入口 |
| 复习、使用记录 | [review/service](../../src/interview_intelligence/review/service.py)、[analytics/usage](../../src/interview_intelligence/analytics/usage.py)；返回/浏览不改变掌握状态，evaluation 用途单独处理 | [review](../../tests/integration/test_review.py)、[usage](../../tests/integration/test_question_usage.py) |

<a id="runtime"></a>

### 账号、模型与观测

| 改动 | 代码入口与边界 | 首选测试 |
| --- | --- | --- |
| 模型配置、预算与锁 | [providers](../../src/interview_intelligence/providers/) 的 CallBudget、ModelCallGate、RequestLimits；联查 [运行配置](runtime-config.md)，保留调用、期限、token 预留/结算和共享锁口径 | [config](../../tests/unit/test_config.py)、[call gate](../../tests/unit/test_model_call_gate.py)、[query limits](../../tests/unit/test_query_gate_limits.py)、[预算装配与租约](../../tests/integration/test_query_budget_config.py)、[packaged resources](../../tests/unit/test_packaged_resources.py) |
| 账号、访问与个人模型 | [access](../../src/interview_intelligence/access.py)、[tenant](../../src/interview_intelligence/tenant.py)、[tenant_retriever](../../src/interview_intelligence/tenant_retriever.py)；用户状态隔离不等于独立语料库 | [access](../../tests/integration/test_access.py)、[tenants](../../tests/integration/test_tenants.py) |
| 管理员运行观测 | [observability](../../src/interview_intelligence/observability.py)；仅按实际保存字段汇总，不从计费字段推断完整语义轨迹 | [agent observability](../../tests/integration/test_agent_observability.py) |

<a id="frontend"></a>

## 前端修改路线

| 改动 | 代码入口与联查范围 | 首选测试 |
| --- | --- | --- |
| 会话提交、页面状态 | [app.js](../../src/interview_intelligence/web/assets/app.js)、[core.js](../../src/interview_intelligence/web/assets/core.js)；消息端点与 QueryService 返回契约。候选页刷新按meta恢复，下一页/控件重查用同会话NL及来源run；semantic cursor不能送SQL，router关闭明确失败。业务范围PENDING徽标/原因与工程归属审核分开 | [core](../../tests/web/test_core.cjs)、[候选渲染/恢复/真实回调](../../tests/node/business-scope-pending-render.test.mjs) |
| SSE、超时、轮询、取消、恢复 | [query-stream.js](../../src/interview_intelligence/web/assets/query-stream.js)、[answer-stream.js](../../src/interview_intelligence/web/assets/answer-stream.js)；journal、events/status/cancel 与完成回执 | [query-stream](../../tests/web/test_query_stream.cjs)、[answer-stream](../../tests/web/test_answer_stream.cjs)；服务端变更加 [query streaming](../../tests/unit/test_query_streaming.py)、[answer streaming](../../tests/integration/test_answer_streaming.py) |
| Markdown、来源展示 | [markdown.js](../../src/interview_intelligence/web/assets/markdown.js)；来源链接、回答依据和渲染安全 | [markdown](../../tests/web/test_markdown.cjs)、[Web](../../tests/integration/test_web.py) |
| 管理观测、账号页面 | [admin.js](../../src/interview_intelligence/web/assets/admin.js)、[agent-observability.js](../../src/interview_intelligence/web/assets/agent-observability.js)、[tenant.js](../../src/interview_intelligence/web/assets/tenant.js)；access、observability、tenant | [observability](../../tests/web/test_agent_observability.cjs)、[tenant panel](../../tests/node/tenant-panel.test.mjs)；服务端见账号与观测 |

## 按变更选择验证

测试入口已放在各任务行；它们不是本页更新时的通过清单。先执行相关范围，只有新风险或失败需要时才扩大。

Python 用 `uv run --locked --extra dev python -m pytest -q <文件...>`；Node 用 `node --test <文件...>`。构建条件与完整工程回归见 [AGENTS](../../AGENTS.md) 和 [CI](../../.github/workflows/evaluation.yml)。PostgreSQL/ES 活体测试需按文件要求显式准备环境，不能把跳过当作通过。

## 评测与实验的分工

| 位置 | 适用任务 | 入口 |
| --- | --- | --- |
| `eval/` | suite 预检/运行/恢复、采集、逐轮题单、最终回答、比较与门禁 | [手册](../../eval/README.md#多轮题单与最终回答) 的 `python -m eval`；编排见 [suite](../../eval/suite.py)，首选 [suite 测试](../../tests/unit/test_evaluation_suite.py)；逐轮来源见 [query_results](../../eval/query_results.py)、[多轮测试](../../tests/unit/test_query_result_multiturn.py)，最终回答见 [answer_quality](../../eval/answer_quality.py)、[真实主机离线集成](../../tests/integration/test_evaluation_goal_quality.py)；独立审核随报告封存并参与复算、恢复与比较覆盖检查。评分及门禁见 [evaluation system](../../tests/unit/test_evaluation_system.py)、[release gate](../../tests/unit/test_release_gate.py)，采集见 [collectors](../../tests/integration/test_evaluation_collectors.py) |
| `evals/agent_gold/` | 来源审阅、独立标注、对齐和裁决配方 | [README](../../evals/agent_gold/README.md) |
| [evals/project_quality/](../../evals/project_quality/) | 绑定数据与候选的实验编排 | `run_refinement.py`、`source_iteration.py` |
| `scripts/evaluate_*` | 场景验收、历史回放 | 按 [评测入口分类](../../eval/README.md#入口分类通用评测候选实验与历史复现) 选择脚本 |
| `services/jev-gateway/`、`services/qwen-openai/` | 可选模型/决策服务 | [Jev/Qwen说明](../../services/jev-gateway/README-qwen.md)、[Qwen部署](../deployment-qwen.md) |

日期脚本可能绑定本地数据和历史服务，先读协议与参数；`evals/` 不保证离线无副作用。迁移前检查导入、调用和文档引用。

## 已知维护热点

- API 与 QueryService 职责较集中；定位时按装配、生命周期、scope、工具执行、呈现划分阅读。
- 工具及证据字段/限额由 Python 单源生成 Pi 声明；证据保留 host / pi_legacy 兼容口径，主机 evidence 优先与有界回退由固定摘要守护。
- legacy `/api/questions/search`、`AgentService` 和现代查询链路的 fallback 不完全相同；接口相似不代表结果语义相同。
- 通用 context/schema 有跨层依赖；重构前检查调用关系，不把静态双向包依赖直接判作运行循环导入故障。

当前进展统一见 [状态摘要](../status/current.md)。
