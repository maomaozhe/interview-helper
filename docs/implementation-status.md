# Implementation Status

本页记录当前代码与 V1 发布门槛的差距。规范仍以 [Implementation Spec](implementation-spec-v1.md) 为准，原始需求保持在 [requirements](requirements/interview-intelligence-v1-source.md)。

| 任务 | 已实现 | 尚未通过的出口 |
|---|---|---|
| 1 契约与环境 | Pydantic 请求/输出契约、Compose、依赖锁文件；真实模型 Schema、1024 维向量、去重和重排预检；跨进程串行门 | 完整运行中的 usage 对账与配额行为 |
| 2 数据库 | SQLAlchemy 模型、Alembic 迁移、当前构建、corpus revision 与发布行锁 | 完整并发/恢复验证、PostgreSQL 负载回归 |
| 3 Taxonomy 与 Gold | 固定 taxonomy、标注指南、30 篇候选清单、gold 校验器 | 30/150/50/50 人工标注及冻结 |
| 4 导入与任务 | 来源识别、hash 快照、原子发布、后台批次、失败路径重试、单 worker 数据库锁和断点续跑；完成调用立即记录，恢复预算；已验证抽取产物独立持久缓存 | 长文档语义切段、其余阶段缓存、全目录处理结论及更完整崩溃注入回归 |
| 5 抽取 | 严格 schema 模型适配、原文 span 校验、元信息归一、完整流式接收和中断拒绝；两篇真实文档试运行 | 30 篇抽取质量评测 |
| 6 去重与关系 | 向量候选、三分类判断、观察追问、显式算法题号 | 150 对 dedup 评测、人工修正流程、复杂题号核验 |
| 7 SQL 统计 | 同 occurrence 筛选、频率、时间、importance/gap、详情来源 | 100+ 真实来源的 M1 门槛与完整真实回归 |
| 8 检索 | ES 建索引、BM25/Dense/Hybrid/LLM rerank、SQL 补事实 | 冻结 50 查询四路消融、完整索引故障及版本竞态回归 |
| 9 Agent | 受限路由、工具轨迹、事实模板、API | 50 查询 routing 评测、复杂问句和一致性恢复 |
| 10 Review | 事务性复习、幂等、未匹配记录与解析、API | 更强并发压力测试与自然语言候选澄清体验 |
| 11 CLI 与发布 | 本地导入/进度/失败重试/统计/检索/来源/问答/复习 CLI、README、API/容器烟测、评测脚手架 | 四个真实 demo、完整看板、所有硬门槛均达成 |

**模型配置：** 按用户明确选择，使用火山方舟 Coding Plan `/api/coding/v3`，`ark-code-latest` 与 `doubao-embedding-vision`。完整串行预检已验证 Schema 抽取、1024 维向量、双题与批量候选判断和重排，五次调用记录 3,469 tokens；报告在 `data/reports/model-preflight.json`。API 与 worker 的模型请求共用串行锁，请求结束后默认间隔 2 秒。向量使用对称语义相似度指令 v2；一次判断最多 10 个候选，并记录实际模型与用量。真实语料导入与质量评测的状态见后续运行记录。

**已核验的工程证据（2026-09-30）：** PostgreSQL 16.9 的 Alembic 升级成功；Elasticsearch 8.19.0 的真实建索引和 BM25/Dense/Hybrid 查询成功；重新构建的 API/worker 容器启动成功。实际 HTTP 请求验证 `/api/health`、`/api/topics`、统计、快照原文和问答。安装包已包含 taxonomy 和提示词资源。自动化测试为 124 通过、1 跳过；启用独立的真实 ES 测试后该项通过。人工 gold 仍为 `draft`，因此所有发布质量指标均为“未评测”。

**真实试运行：** 得物一面回忆帖发布 1 场面试、8 次提问；Agent 高频题汇总被排除。corpus revision 与索引 revision 均为 2。BM25、Dense、Hybrid、Hybrid + Rerank 均通过真实模型/ES 查询，没有降级；快照来源读取和统计 Agent 已核验。重复导入这两篇为 2 次跳过、0 次新增模型调用。PostgreSQL 下用隔离的验收用户验证了复习事务和幂等，未改变本地用户的复习记录。运行报告保存在被忽略的 `data/reports/`。早期批次保留了超时失败历史，因此其最终状态为 PARTIAL_FAILURE；新的幂等复跑批次为 SUCCEEDED。

**完整目录：** 190 篇文件已加入后台串行导入队列。每篇成功发布后同步索引，完整导入结果尚未出炉；不能据此宣称 100 个真实来源或 M1 已达标。本机接口为 `http://127.0.0.1:8000/docs`；GitHub `main` 保存实施代码，密钥和运行语料不提交。

**串行处理修复（2026-10-01）：** 去重 v3 将候选 UUID 映射为请求内短 ID，并在每次 JSON Schema 中约束候选枚举和精确数量；仍严格拒绝遗漏、重复、未知候选，按 ID 映射回原候选，校验失败的重试增加明确纠正说明。真实 10 候选调用一次通过，SAME / RELATED / DIFFERENT 验证符合预期，记录在 `data/reports/bound-candidates-smoke.json`。这只是功能烟测，不替代冻结 gold 的去重质量评测。

抽取产物现在在整篇发布事务前独立写入 `StageArtifact` 和 `data/processed/extract/`，后续去重失败或 worker 重启可复用。缓存包含来源版本、模型、提示词、schema、有效抽取参数和分类表内容；judge 变化不使抽取缓存失效。读写检查 hash，复用时重新检查结构、来源 span 和分类，损坏或缺失产物重新抽取。无有效提问、重复场次 ID、反向追问等无效结果在缓存前拒绝。历史失败篇此前没有产物，其首次重试仍需抽取；向量和去重判断的跨失败持久缓存仍未实现。最终完整测试为 162 通过、1 跳过（真实 ES 项需单独启用），串行门保持生效。
