---
id: business-invariants
status: current
scope: workspace
verified_at: 2026-10-19
sources:
  - path: src/interview_intelligence/agent/goal_contract.py
    symbols: [bind_goal, completion_for, restore_completion]
  - path: src/interview_intelligence/repository/corpus.py
    symbols: [active_occurrences, publish_build]
  - path: src/interview_intelligence/search/service.py
    symbols: [eligible_canonical_ids, search_questions]
  - path: src/interview_intelligence/search/reranker.py
    symbols: [LLMReranker._adjudicate_clarified, LLMReranker._clarified_result, LLMReranker.select_workset_prefix]
  - path: src/interview_intelligence/search/workset.py
    symbols: [independent_negative_adjudication_valid, validate_manifest]
  - path: src/interview_intelligence/agent/complete_workset.py
    symbols: [start_batch, apply_batch]
  - path: src/interview_intelligence/search/referents.py
    symbols: [validated_reference_view, validate_reference_claim]
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
  - tests/node/compact-query-wire.test.mjs
  - tests/unit/test_compact_query_wire.py
  - tests/unit/test_tool_budget_policy.py
  - tests/integration/test_goal_completion.py
  - tests/integration/test_goal_completion_api.py
  - tests/integration/test_corpus_snapshots.py
  - tests/integration/test_query_agent.py
  - tests/integration/test_search_frequency_ranking.py
  - tests/unit/test_business_scope_verification_v41.py
  - tests/unit/test_business_scope_verification_v42.py
  - tests/unit/test_business_scope_reference_v42.py
  - tests/unit/test_source_item_frames.py
  - tests/unit/test_workset_prefix_capacity.py
  - tests/unit/test_complete_workset_pagination.py
  - tests/integration/test_complete_workset_recovery.py
  - tests/integration/test_engineering_candidate_workset.py
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

澄清范围核验可使用单独来源绑定的连续前件或编号条目要求，但不得改写当前原问。当前任务、建设要求和领域证据仍须来自当前原问；引用前件或条目要求作单位、目标、活动和查询关系证明前，须验证来源及 frame 声明，歧义保留待核验。主机校验来源身份与结构不等于证明模型的语义归属判断。业务正类须有实际所问的业务活动证据，不能只以技术指标或背景使用者代替。

澄清范围的首轮判负不能直接成为最终排除，所有硬排都需当前同源独立复核回执；工作集消费层不能只信旧的已核验布尔标记。复核未执行、失败或预算不足保留待核验，有效混合边界未决不被反复补问强制二分。负判恢复为正类还须重新证明同源查询相关性，用户额外条件与领域授权保持。

入口：[snapshot](../../src/interview_intelligence/ingestion/snapshot.py)、[extraction validation](../../src/interview_intelligence/extraction/validation.py)、[dedup context](../../src/interview_intelligence/dedup/context.py)、[citations](../../src/interview_intelligence/search/citations.py)。验证：[extraction contract](../../tests/unit/test_extraction_contract.py)、[source context](../../tests/unit/test_dedup_source_context.py)、[canonical citations](../../tests/unit/test_canonical_citations.py)。

## I04：缓存、构建、发布和索引就绪是不同状态

抽取缓存独立提交用于失败复用；单文档事实发布在 PG 事务中切换 active build 和 revision。ES 在事务之外构建及切换 alias，不具备 PG/ES 跨系统原子事务。保持入口的语料/索引版本检查，不能把旧索引包装成新语料查询。

入口：[ExtractionStageCache](../../src/interview_intelligence/ingestion/extraction_cache.py)、[publish_build](../../src/interview_intelligence/repository/corpus.py)、[rebuild_index](../../src/interview_intelligence/search/elasticsearch.py)。验证：[stage cache](../../tests/integration/test_extraction_stage_cache.py)、[ingestion lifecycle](../../tests/integration/test_ingestion_lifecycle.py)、[worker](../../tests/integration/test_worker.py)。

## I05：完整 SQL 范围与 SEARCH 候选池分开

LIST/COUNT/STATS 的 SQL 操作面向完整资格范围；SEARCH 先由 SQL 选 eligible canonical IDs，再在检索候选中核验。每题 occurrence_count 来自 SQL，不代表候选池包含全库所有相关题。普通 SEARCH 频次排序覆盖本次最多 50 个候选，不能称为全库最高频榜；确认与待核验项可按同一签名候选快照分页。

原话明确全量工程集合后再细化业务目标，可保存完整工程资格候选工集，按正常单轮预算分批核验。每次提供至多20题窗口，必须用完整主排和必要独立复核的 prepared 上界选择能容纳的来源就绪前缀；消费层只接受窗口的连续实际处理前缀，尾部保持未处理。提供窗口与实际处理前缀分别签名记账，未知结果意图不能自动重呼；第一道也无法容纳时零模型保留待核验并推进。排序不能删除未提名题；候选总数、业务确认、待核验、排除与未处理数必须分开。待核验和未处理不能算作负例；多来源标准题排除须覆盖所有合格原问的有效负证。缓存续页和刷新不调用模型，处理新批的 NEXT 是新一轮核验，范围及全量选择继续保留。

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

复习写入目标绑定请求开始页；本轮明确“列出目标题并标记”的复合授权可绑定首个合法结果页，一经绑定不再扩大，空结果也不会授权后续其他集合。后续读工具不能通过换页扩大授权。user_goal只说明回答目的，不授予筛选、身份或写权限。目标、单次执行和整轮完成分开：综合目标的工具读取不能因模型final=true直接提交SUCCEEDED；host completion控制继续/回答完成/澄清/写提交，恢复读取同一持久状态。预算耗尽保留partial，不能假称已完成回答。

读取准入同时考虑工具基础模型成本及后续回答规划；工具菜单与host执行检查一致，归一化为SEARCH的计划不能从低成本动作绕过。综合目标的工具provider调用不能花掉最后一次回答预留；零模型SQL读取也须留下一次回答规划。未绑定目标不阻断单规划直接结果，声明综合目标后立即按预留检查。该保护不证明token或期限必有足够余量。

压缩模型传输不授予新领域工具：`query_step`按host当前action能力唯一分派，不能直接作为host工具调用；host仍校验原QuerySpec与范围。完整QuerySpec中的中性默认字段可被新线协议接受，不复制旧菜单字段白名单造成自相矛盾；动作授权、必填及合法值约束不因此放宽。流式正文仅在可信wire选择且显式action=ANSWER时投影，临时delta不代表回答已完成。

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
