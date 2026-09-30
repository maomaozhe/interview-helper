# Interview Intelligence V1 Implementation Plan

**Goal:** 将现有 Markdown 面经转换为可靠的结构化 corpus，并完成统计、检索、单 Agent 工具编排、复习回流和真实评测。

**Architecture:** 模块化单体，API/CLI/worker 共用领域服务。PostgreSQL 保存事实与任务，Elasticsearch 保存可重建检索索引，模型仅用于结构化抽取、语义判断与请求编排。

**Tech Stack:** Python 3.12、FastAPI、Pydantic 2、SQLAlchemy 2、PostgreSQL 16、Elasticsearch 8.19、pytest、Typer、Docker Compose；patch 版本与镜像在 Task 1 验证后锁定。

**执行依据：** [Implementation Spec](../implementation-spec-v1.md) 是行为契约；[原始需求](../requirements/interview-intelligence-v1-source.md) 保留产品意图。当前代码和未完成项以 [实施状态](../implementation-status.md) 为准。本计划列出目标与验收，不表示所有任务已经通过。

执行时可使用 `superpowers:executing-plans` 逐项落实。用户现已要求实施；人工 gold 与真实模型评测仍是发布门槛，不能因代码骨架完成而跳过。

## Task 1 锁定契约与开发环境

创建：`pyproject.toml`、`compose.yaml`、`.env.example`、`src/interview_intelligence/config.py`、`src/interview_intelligence/contracts/`、`config/models.yaml`、`config/retrieval.yaml`、`config/scoring.yaml`。

1. 定义 FilterSpec、SourceSpan、来源引用、工具输入输出、结构化错误和版本 manifest；导出 JSON Schema。
2. 记录模型能力预检：JSON Schema、embedding 维度与前缀、rerank 返回语义、usage/价格来源。未提供模型配置时显式未就绪。
3. 创建最小 API/worker/CLI 入口，锁定依赖与容器版本；Compose 只包含四个必要服务。
4. 写配置/契约测试：非法枚举、维度不符、缺预算/凭据、越界路径必须产生明确定义错误。
5. 验证并保存环境清单。尚无真实凭据时可以继续纯契约工作，但不能跳过最终真实模型预检。

验证：`uv run pytest tests/contract/test_schemas.py tests/unit/test_config.py -q`；`docker compose config --quiet`。预期契约拒绝不合法值且 Compose 配置可解析。

出口：所有公共字段及默认值与 spec 一致，后续模块可共用，不在各模块复制一套参数。

## Task 2 数据库 Schema 与版本快照

创建：`migrations/`、`src/interview_intelligence/domain/models.py`、`src/interview_intelligence/repository/`、`tests/integration/test_schema_constraints.py`、`tests/integration/test_corpus_snapshots.py`。

1. 先定义数据库不变量的失败测试：同 build 内题序冲突、无效 FK、重复 review 幂等键、互斥关系端点。
2. 建立源文档/revision/build/场次/occurrence/canonical/assignment/关系/算法/review/run/task/cache/index-sync 实体。
3. 实现 corpus revision manifest、active views 和只读事务，使历史版本可导出重放。
4. 将数据库唯一约束、事务冲突与业务错误码连接，不用“先查询后插入”替代唯一约束。
5. 验证空库 migration、回退/升级和持久化重启。

验证：`uv run alembic upgrade head`；`uv run pytest tests/integration/test_schema_constraints.py tests/integration/test_corpus_snapshots.py -q`。

出口：可以直接用 fixture 插入可追溯的多场次数据；不依赖 LLM 就能验证关系和计数。

## Task 3 Taxonomy 与 Gold Set

创建：`config/taxonomy/v1.yaml`、`config/aliases/`、`docs/annotation-guide.md`、`data/gold/v1/manifest.json`、`eval/common/`、四类 eval 入口。

1. 原样落实 spec 的 L1/L2 和 question_type；写入公司/岗位 alias 的明确证据与粒度规则。
2. 定义 source span、原子问题、场次、观察追问、三类去重标签和检索相关等级的标注指南。
3. 从完整目录筛选真实面经及 hard negatives；人工标注并裁决，不把模型初稿当 gold。
4. 冻结 test：至少 30 篇真实文档、150 对题目、50 条检索 query、50 条路由 query；另备 disjoint dev。
5. 写数据集校验器：hash、ID、引用、标签、dev/test 泄漏和最小规模。

验证：`uv run python -m eval.validate_gold --dataset data/gold/v1`。

出口：必须人工核验的数据明确列为未完成直至真实完成；无 gold 不进入宣称质量通过的阶段。

## Task 4 文档发现 快照与任务恢复

创建：`src/interview_intelligence/ingestion/{discovery,identity,snapshot,pipeline}.py`、`src/interview_intelligence/worker/`、`tests/integration/test_ingestion_lifecycle.py`。

1. 用当前真实文件编写多轮/未知轮次/相对日期/复制路径等 fixtures，保留原始字节与 hash。
2. 实现 source identity、alias、不可变快照、StageArtifact 与 per-run task。
3. 实现单 corpus 写租约、任务状态机、超时接管、幂等请求和失败重试。
4. 用 extraction stub 验证 staging→原子发布→index outbox；失败时保持旧 active build。
5. 覆盖文件变更、回退、缺失文件、同来源重复复制与有冲突来源的待复核。

验证：`uv run pytest tests/integration/test_ingestion_lifecycle.py -q`。

出口：stub 条件下重复运行零重复事实；崩溃恢复不会发布半份文档。

## Task 5 抽取 分类和归一

创建：`src/interview_intelligence/extraction/`、`src/interview_intelligence/normalization/`、`src/interview_intelligence/taxonomy/`、`src/interview_intelligence/providers/`、`prompts/extract_question_v1.md`、`prompts/classify_question_v1.md`。

1. 先实现 provider stub、结构化校验及错误注入，覆盖无效 JSON、span 不匹配、超时、429、错误维度、输出截断。
2. 实现真实 provider adapter 和每 attempt ModelCall 日志，确保敏感内容不默认打印。
3. 实现场次分割、atomic extraction、严格原文 span、非面试提问排除和长文完整段落批处理。
4. 归一公司/岗位/轮次/日期，实施固定 taxonomy；保存可独立复用的抽取与分类产物。
5. 在 dev 迭代后跑冻结 extraction test，保存完整误差与质量报告。

验证：`uv run pytest tests/unit/test_extraction_contract.py tests/integration/test_provider_failures.py -q`；`uv run python -m eval.extraction --dataset data/gold/v1 --split test`。

出口：P/R/F1、公司/轮次/topic 指标达到 spec；未达标不能用 UI 工作绕过质量问题。

## Task 6 Canonical 去重 关系与算法

创建：`src/interview_intelligence/dedup/`、`src/interview_intelligence/domain/relations.py`、`src/interview_intelligence/domain/algorithms.py`、`prompts/dedup_judge_v1.md`、`tests/integration/test_canonicalization.py`。

1. 建立以模型版本与 text hash 为键的向量缓存，候选库纳入当前 run 新 canonical。
2. 实现 Top10 candidates + SAME/RELATED/DIFFERENT judge，明确多 SAME 冲突的保守处理。
3. 实现观察边 occurrence 端点约束、证据校验；推断边独立存储与展示。
4. 实现 AlgorithmMatch 的显式/核验/候选状态，未知题号保持 null。
5. 实现最小管理员重分配及审计、redirect、索引同步；个人状态遇到拆分不得复制。
6. 跑 pair 与端到端评测，以及纯 threshold 消融；SAME Precision 优先。

验证：`uv run pytest tests/integration/test_canonicalization.py -q`；`uv run python -m eval.dedup --dataset data/gold/v1 --split test --ablation`。

出口：SAME P≥0.95、R≥0.85，错误归并有可复现 bad case；Occurrence 保留完整。

## Task 7 SQL Analytics 与 M1

创建：`src/interview_intelligence/analytics/{filters,stats,scoring}.py`、`src/interview_intelligence/api/questions.py`、`src/interview_intelligence/api/sources.py`、`tests/integration/test_analytics.py`。

1. 用手工可算 fixture 测试计数、JOIN、unknown 桶、公司/岗位/轮次相关性、日期边界和月末。
2. 实现统一 FilterSpec、四类聚合、趋势、完整来源遍历和 detail。
3. 实现 importance_v1 的完整集合分母、确定阈值与零分母处理；预留 gap 的用户状态端口。
4. 跑至少 100 篇不同真实来源，扫描剩余输入，保留排除/失败/待复核原因。
5. 对完整输入运行两次，导出第二次零新增 Interview/Occurrence/模型调用的报告。

验证：`uv run pytest tests/integration/test_analytics.py tests/e2e/test_ingest_idempotency.py -q`；`uv run ii ingest --root ./md` 连续两次；`uv run ii stats --topic Redis --group-by question`。

出口 M1：真实 corpus、SQL、追溯与重试可用，抽取/去重门槛满足。成功来源不足 100 时如实报告，不用汇总帖凑数。

## Task 8 检索与 M2

创建：`src/interview_intelligence/retrieval/{base,elasticsearch,bm25,dense,hybrid,rerank,indexer}.py`、`tests/integration/test_search_filters.py`、`tests/integration/test_index_recovery.py`。

1. 实现 canonical 粒度文档、中文分词与严格 SQL eligibility；验证公司/轮次不能跨 occurrence 拼接。
2. 实现 BM25、Dense、RRF、同候选 Rerank，所有版本/分数可追踪。
3. 实现按 revision 顺序的索引任务、fencing、删除、全量重建和 alias 切换。
4. 验证索引落后/模型失败的显式状态与交互降级，evaluation 不静默降级。
5. 在 dev 决定默认方案后，完整运行四路冻结 test 并保存消融表。

验证：`uv run pytest tests/integration/test_search_filters.py tests/integration/test_index_recovery.py -q`；`uv run python -m eval.retrieval --dataset data/gold/v1 --split test --pipelines bm25,dense,hybrid,hybrid_rerank`。

出口 M2：四路真实结果存在，默认方案 Recall@10≥0.85，无过滤/来源错误；Hybrid 不胜出则如实选用和解释最佳方案。

## Task 9 Agent Tools 与 Routing

创建：`src/interview_intelligence/agent/tools/`、`src/interview_intelligence/agent/routing/`、`src/interview_intelligence/agent/orchestrator.py`、`prompts/router_v1.md`、`prompts/compose_answer_v1.md`、`tests/contract/test_agent_tools.py`。

1. 将领域服务包装成六个固定工具；个人状态服务先使用明确 stub，禁止把 stub 结果作为真实 demo。
2. 实现五类 route、参数校验、默认日期解释、工具预算和错误传递。
3. 实现多工具版本一致性与事实引用渲染，统计问题不能用 semantic search 代替。
4. 用计划 fixture 验证 A/B/C 与写意图边界，歧义返回澄清。
5. 路由评测可先使用稳定工具 fixtures；端到端 Task Success 待真实 Review 接入后再运行。

验证：`uv run pytest tests/contract/test_agent_tools.py tests/unit/test_router.py -q`；`uv run python -m eval.routing --dataset data/gold/v1 --split test --mode routing`。

出口：工具选择准确率≥90%，有参数与任务失败分类，所有工具执行受白名单约束。

## Task 10 Review 闭环与 M3

创建：`src/interview_intelligence/review/{service,resolution,repository}.py`、`src/interview_intelligence/api/review.py`、`tests/integration/test_review.py`、`tests/e2e/test_user_scenarios.py`。

1. 先测试状态缺省、四态转换、score/note 语义、重复幂等键、版本冲突和批量回滚。
2. 实现 ReviewEvent/State/UserRevision 的事务写入，接入真实工具。
3. 实现题意匹配、未匹配事件保存、澄清与后续绑定，不产生 corpus occurrence。
4. 实现 gap_v1 全题集排序，以及状态与 canonical 纠错的兼容行为。
5. 跑 Q1–Q8、A/B/C/D 与真实 routing task-success 评测。

验证：`uv run pytest tests/integration/test_review.py tests/e2e/test_user_scenarios.py -q`；`uv run python -m eval.routing --dataset data/gold/v1 --split test --mode end-to-end`。

出口 M3：Agent+真实 review 闭环完成，零未授权写入和零无法追溯事实。

## Task 11 CLI 文档与最终发布核验

创建：`src/interview_intelligence/cli/`、`scripts/build_report.py`、`README.md`、`docs/demo.md`、`docs/bad-cases.md`。

1. CLI 支持 ingest/status/retry-failed、stats、search、detail/source、review、chat、评测与纠错入口。
2. 从真实数据库和评测产物生成看板，解释 occurrence/标准题/场次等计数差异。
3. README 按 spec 的工程问题逐项解释，记录技术取舍、运行方法、真实 benchmark 和局限。
4. 在干净环境执行启动/迁移/导入/搜索/复习/恢复流程，保存四个场景 demo。
5. 运行最终受影响的完整测试与四类真实评测，核查质量门槛和 artefact manifest；未通过项留清单，不能宣布 V1 完成。

验证：`docker compose up -d --build`；`uv run pytest -q`；`uv run python -m eval.run_all --dataset data/gold/v1 --split test`；`uv run python scripts/build_report.py --output eval/reports/release-v1`。

出口 M4：spec 的 Definition of Done 全部有证据。没有真实实验支撑的性能提升数字不能出现在 README 或简历。

## 每个任务的执行纪律

每次先选择本任务的行为验收，再写能暴露该行为的测试和最小实现，完成验证后更新结果；不写只复述实现细节的测试。模型质量必须在独立 gold 上评估，mock 通过不能替代真实评测。

如果目录尚未初始化 Git，后续正式实现时再建立版本管理；当前规范任务不需要自行初始化仓库。实施中的提交按可审阅的小功能组织。任务之间共享契约变更先更新 spec，后改调用方，防止多个模块各自解释同一字段。
