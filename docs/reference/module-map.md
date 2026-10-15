---
id: module-map
status: current
scope: workspace
verified_at: 2026-10-16
sources:
  - path: src/interview_intelligence/agent/tool_definitions.py
  - path: scripts/sync-tool-contract.py
    symbols: [generated_section]
  - path: src/interview_intelligence/api.py
    symbols: [create_app]
  - path: src/interview_intelligence/api_errors.py
    symbols: [register_error_handlers]
  - path: src/interview_intelligence/worker.py
    symbols: [build_worker_services, process_queued_run]
  - path: src/interview_intelligence/agent/query_service.py
    symbols: [QueryService]
  - path: src/interview_intelligence/agent/query_response.py
    symbols: [build_query_response]
  - path: src/interview_intelligence/analytics/ranking.py
    symbols: [score_components, sql_score_components, importance_score, gap_score, sql_gap_score]
  - path: services/pi-agent/runtime.mjs
  - path: .github/workflows/evaluation.yml
tests:
  - tests/integration/test_api.py
  - tests/integration/test_query_agent.py
  - tests/node/pi-runtime.test.mjs
---

# 模块与验证导航

静态工具声明统一维护在 [tool_definitions.py](../../src/interview_intelligence/agent/tool_definitions.py)，通过 [生成脚本](../../scripts/sync-tool-contract.py) 更新 Pi runtime 标记区；[Python 一致性](../../tests/unit/test_tool_contract.py) 和 [实际 Pi 菜单快照](../../tests/node/tool-contract.test.mjs) 守护漂移。主机业务校验与动态 ToolPolicy 仍独立执行。

按任务定位代码和守护测试。本页列出入口，不复制实现细节；跨模块流程见 [系统](../architecture/system.md)、[入库](../architecture/ingestion-flow.md) 和 [查询](../architecture/query-flow.md)。返回 [知识库首页](../README.md)。

## 入口与装配

| 入口 | 职责与关键符号 | 修改时联查 |
| --- | --- | --- |
| [api.py](../../src/interview_intelligence/api.py) | `create_app` 装配数据库、模型/provider、用户服务、HTTP；内部 `execute_query` 调度查询 | Settings、QueryService、Pi 回调、tenant、journal |
| [api_errors.py](../../src/interview_intelligence/api_errors.py) | `register_error_handlers` 注册验证、领域和提供方错误的 HTTP 映射 | API 注入 request_id；账号/管理 422 脱敏与状态码/可重试语义由端点集成测试守护 |
| [worker.py](../../src/interview_intelligence/worker.py) | `build_worker_services`、`process_queued_run`、`recover_orphaned_runs` | 入库、预算/模型锁、索引、批次恢复 |
| [cli.py](../../src/interview_intelligence/cli.py) | Typer `ii` 命令，经服务接口/共享能力执行 | API 契约、配置与不同查询路径 |
| [web.py](../../src/interview_intelligence/web.py) | `register_web`：静态工作台及原文、反馈等接口 | tenant 用户范围、快照路径、反馈回执 |
| [Pi runtime](../../services/pi-agent/runtime.mjs) / [server](../../services/pi-agent/server.mjs) | Node 编排循环、工具 schema 与主机回调 | Python QuerySpec/TOOL_ACTIONS、Harness、ModelGateway |

注意 `web.py` 是服务端代码，`web/` 是浏览器资源；`api.py` 既是组合入口，也包含运行调度和业务端点，不能按文件名假设它只有薄路由。

## 领域模块

| 模块 | 负责什么 / 首看符号 | 依赖与边界 |
| --- | --- | --- |
| [domain/models](../../src/interview_intelligence/domain/models.py) | ORM、事实版本、会话、任务和回执实体 | schema 变化联查 [migrations](../../migrations/versions/)；预留字段不代表执行机制已实现 |
| [contracts](../../src/interview_intelligence/contracts/__init__.py) | FilterSpec、SourceSpan、抽取/复习数据契约 | SQL、provider、API 使用同一领域语义 |
| [ingestion/pipeline](../../src/interview_intelligence/ingestion/pipeline.py) | `IngestService.ingest_file` / `_materialize` | 快照→抽取缓存→归并→发布事务 |
| [extraction/provider](../../src/interview_intelligence/extraction/provider.py) / [validation](../../src/interview_intelligence/extraction/validation.py) | 结构化抽取、重试反馈与原文校验 | 不完整输出和不匹配引文不能进入发布 |
| [dedup/service](../../src/interview_intelligence/dedup/service.py) / [provider](../../src/interview_intelligence/dedup/provider.py) | `DedupService.resolve`、候选、SAME/RELATED/DIFFERENT 判断 | [source context](../../src/interview_intelligence/dedup/context.py) 是可选能力，核对实际调用入口 |
| [repository/corpus](../../src/interview_intelligence/repository/corpus.py) | `active_occurrences`、`publish_build`、展示分类刷新 | 有效事实与发布事务的统一口径 |
| [analytics/listing](../../src/interview_intelligence/analytics/listing.py) / [counts](../../src/interview_intelligence/analytics/counts.py) / [stats](../../src/interview_intelligence/analytics/stats.py) | 完整范围列表、计数、分组、排名和分页 | scope/签名/用户版本；stats 与 listing 共用下方排名政策 |
| [analytics/ranking](../../src/interview_intelligence/analytics/ranking.py) | importance/gap 权重、分段、版本，以及 Python / SQL 归一化适配 | 变更需保留完整范围与排序/游标语义；[固定值和零分母回归](../../tests/integration/test_ranking.py) |
| [analytics/semantic_scope](../../src/interview_intelligence/analytics/semantic_scope.py) | `load_semantic_scope` 验证完整 ALGORITHM_PROBLEM 范围 | 审计证据缺失或过期时报不完整，不能用 SEARCH 候选替代 |
| [search/service](../../src/interview_intelligence/search/service.py) | `eligible_canonical_ids`、`search_questions` | SQL 限定范围，检索后用 SQL 补事实 |
| [search/elasticsearch](../../src/interview_intelligence/search/elasticsearch.py) | 召回、融合、候选、`rebuild_index` | ES 物理索引/alias、embedding cache、语料版本 |
| [search/reranker](../../src/interview_intelligence/search/reranker.py) | `LLMReranker` 及范围复核 | [citations](../../src/interview_intelligence/search/citations.py)、[design_scope](../../src/interview_intelligence/search/design_scope.py)、[retry](../../src/interview_intelligence/search/retry.py) |
| [agent/query_service](../../src/interview_intelligence/agent/query_service.py) | `_begin` 幂等与状态；`_execute` 工具执行；`finish` 提交 | 契约、权限、scope、SQL/检索/复习及 journal；结果呈现委托下方纯函数 |
| [agent/query_response](../../src/interview_intelligence/agent/query_response.py) | `build_query_response` 组装答案文案、facts 与工具回执 | 不更新会话或数据库；[输入/引用守护](../../tests/unit/test_query_response.py)及各查询入口集成测试 |
| [agent/harness](../../src/interview_intelligence/agent/harness.py) / [query_contract](../../src/interview_intelligence/agent/query_contract.py) | `ContextCompiler`、`ToolPolicy`、`QuerySpec`、`TOOL_ACTIONS` | Pi schema 是一层投影，主机仍独立校验 |
| [agent/journal](../../src/interview_intelligence/agent/journal.py) | `QueryJournal` 持久化 turn、工具回执和事件 | 活跃执行对象另在进程内 |
| [agent/task_annotation](../../src/interview_intelligence/agent/task_annotation.py) / [annotation_review](../../src/interview_intelligence/agent/annotation_review.py) | 分类生成、核验草稿和发布 | prompt 选择、证据、annotation revision；与检索重排不同 |
| [review/service](../../src/interview_intelligence/review/service.py) / [analytics/usage](../../src/interview_intelligence/analytics/usage.py) | 复习写入与使用观测 | 返回/浏览不会自动改变掌握状态，evaluation 用途单独处理 |
| [preferences](../../src/interview_intelligence/agent/preferences.py) / [feedback_memory](../../src/interview_intelligence/agent/feedback_memory.py) | 用户偏好、显式启用的查询纠正 | 原反馈文件、PG 偏好和会话状态分开 |
| [providers](../../src/interview_intelligence/providers/) | CallBudget、ModelCallGate、RequestLimits、模型身份与传输 | 调用次数、期限、token 预留/结算和共享文件锁 |
| [access](../../src/interview_intelligence/access.py) / [tenant](../../src/interview_intelligence/tenant.py) / [tenant_retriever](../../src/interview_intelligence/tenant_retriever.py) | 访问策略、账号会话、个人模型/额度和服务作用域 | 用户状态隔离不等于每用户独立语料库 |
| [observability](../../src/interview_intelligence/observability.py) | 管理员运行观测与调用汇总 | 以实际保存字段为限，不从计费字段推断完整语义轨迹 |

## 前端修改路线

| 改动 | 浏览器文件 | 联查服务端 |
| --- | --- | --- |
| 会话提交、页面状态 | [app.js](../../src/interview_intelligence/web/assets/app.js)、[core.js](../../src/interview_intelligence/web/assets/core.js) | 消息端点、QueryService 返回契约 |
| SSE、超时、轮询、取消和恢复 | [query-stream.js](../../src/interview_intelligence/web/assets/query-stream.js)、[answer-stream.js](../../src/interview_intelligence/web/assets/answer-stream.js) | journal、events/status/cancel、最终完成回执 |
| Markdown 内容 | [markdown.js](../../src/interview_intelligence/web/assets/markdown.js) | 来源链接、回答依据和渲染安全 |
| 管理观测与账号 | [admin.js](../../src/interview_intelligence/web/assets/admin.js)、[agent-observability.js](../../src/interview_intelligence/web/assets/agent-observability.js)、[tenant.js](../../src/interview_intelligence/web/assets/tenant.js) | access、observability、tenant |

## 按变更选择验证

下列是测试入口，不是本页更新时的通过清单。先执行相关范围，只有新风险或失败需要时才扩大。

| 变更范围 | 首选测试文件 |
| --- | --- |
| 抽取/证据 | [extraction contract](../../tests/unit/test_extraction_contract.py)、[quote feedback](../../tests/unit/test_extraction_quote_feedback.py)、[stage cache](../../tests/integration/test_extraction_stage_cache.py) |
| 入库/发布 | [ingestion lifecycle](../../tests/integration/test_ingestion_lifecycle.py)、[worker](../../tests/integration/test_worker.py)、[corpus snapshots](../../tests/integration/test_corpus_snapshots.py)、[metadata publication](../../tests/integration/test_canonical_metadata_publication.py) |
| 去重 | [provider](../../tests/unit/test_model_dedup_provider.py)、[source context](../../tests/unit/test_dedup_source_context.py)、[answer boundaries](../../tests/unit/test_dedup_answer_boundaries.py)、[canonicalization](../../tests/integration/test_canonicalization.py) |
| 完整统计/分页 | [analytics](../../tests/integration/test_analytics.py)、[filter scope](../../tests/unit/test_query_filter_contract.py)、[count scope](../../tests/integration/test_count_review_scope.py)、[semantic scope](../../tests/integration/test_semantic_scope_listing.py) |
| 召回/核验/排名 | [ES retriever](../../tests/unit/test_elasticsearch_retriever.py)、[reranker](../../tests/unit/test_reranker.py)、[citations](../../tests/unit/test_canonical_citations.py)、[frequency ranking](../../tests/integration/test_search_frequency_ranking.py) |
| Agent 契约/多轮 | [harness](../../tests/unit/test_harness.py)、[query agent](../../tests/integration/test_query_agent.py)、[conversation focus](../../tests/integration/test_conversation_focus.py)、[requery](../../tests/integration/test_query_requery.py)、[Pi runtime](../../tests/node/pi-runtime.test.mjs) |
| 流式/恢复 | [query streaming](../../tests/unit/test_query_streaming.py)、[answer streaming](../../tests/integration/test_answer_streaming.py)、[query-stream](../../tests/web/test_query_stream.cjs)、[answer-stream](../../tests/web/test_answer_stream.cjs) |
| 复习/使用/账号 | [review](../../tests/integration/test_review.py)、[usage](../../tests/integration/test_question_usage.py)、[tenants](../../tests/integration/test_tenants.py)、[access](../../tests/integration/test_access.py) |
| 模型预算/配置 | [config](../../tests/unit/test_config.py)、[call gate](../../tests/unit/test_model_call_gate.py)、[query limits](../../tests/unit/test_query_gate_limits.py)、[packaged resources](../../tests/unit/test_packaged_resources.py) |
| 质量框架 | [evaluation system](../../tests/unit/test_evaluation_system.py)、[release gate](../../tests/unit/test_release_gate.py)、[collectors](../../tests/integration/test_evaluation_collectors.py)、[source gold](../../tests/unit/test_source_gold.py) |

Python 用 `uv run --locked --extra dev python -m pytest -q <文件...>`；Node 用 `node --test <文件...>`。构建条件与完整工程回归见 [AGENTS](../../AGENTS.md) 和 [CI](../../.github/workflows/evaluation.yml)。PostgreSQL/ES 活体测试需按文件要求显式准备环境，不能把跳过当作通过。

## 评测与实验的分工

| 位置 | 适用任务 | 入口 |
| --- | --- | --- |
| `eval/` | 可复用的快照、采集、gold校验、评分、比较与门禁 | [手册](../../eval/README.md)、[collect](../../eval/collect.py)、[run](../../eval/run.py)、[release](../../eval/release.py) |
| `evals/agent_gold/` | 来源审阅、独立标注、对齐和裁决配方 | [README](../../evals/agent_gold/README.md) |
| `evals/project_quality/` | 绑定特定数据、协议或候选的实验编排 | [run_refinement](../../evals/project_quality/run_refinement.py)、[source_iteration](../../evals/project_quality/source_iteration.py) |
| `scripts/evaluate_*` | 服务验收、具体场景和历史回放 | [conversation](../../scripts/evaluate_conversation_quality.py)、[historical requery](../../scripts/evaluate_historical_requery.py) |
| `services/jev-gateway/`、`services/qwen-openai/` | 可选模型/决策服务的部署与验证 | [Jev/Qwen说明](../../services/jev-gateway/README-qwen.md)、[Qwen部署](../deployment-qwen.md) |

日期实验脚本可能绑定本地数据和历史服务。先读协议与参数；不因位于 `evals/` 就假定是离线无副作用的测试。迁移这些入口前检查 Python 导入、脚本调用和文档引用。

## 已知维护热点

- API 与 QueryService 职责较集中；定位时按装配、生命周期、scope、工具执行、呈现划分阅读。
- 工具声明由 Python 单源生成 Pi 数据；证据投影仍有两侧口径，下一步围绕 host 投影核对和收敛。
- legacy `/api/questions/search`、`AgentService` 和现代查询链路的 fallback 不完全相同；接口相似不代表结果语义相同。
- 通用 context/schema 有跨层依赖；重构前检查调用关系，不把静态双向包依赖直接判作运行循环导入故障。

本轮已统一排名政策，拆出 API 错误映射与 QueryService 结果呈现，详见 [收口验收](../verification/2026-10-15-project-consolidation.md)。上述执行、状态和跨语言契约边界仍需逐项治理。
