# Implementation Status

本页记录当前代码与 V1 发布门槛的差距。规范仍以 [Implementation Spec](implementation-spec-v1.md) 为准，原始需求保持在 [requirements](requirements/interview-intelligence-v1-source.md)。

| 任务 | 已实现 | 尚未通过的出口 |
|---|---|---|
| 1 契约与环境 | Pydantic 请求/输出契约、Compose、依赖锁文件；真实模型 Schema、1024 维向量、去重和重排预检；跨进程串行门 | 完整运行中的 usage 对账与配额行为 |
| 2 数据库 | SQLAlchemy 模型、Alembic 迁移、当前构建、corpus revision 与发布行锁 | 完整并发/恢复验证、PostgreSQL 负载回归 |
| 3 Taxonomy 与 Gold | 固定 taxonomy、标注指南、30 篇候选清单、gold 校验器 | 30/150/50/50 人工标注及冻结 |
| 4 导入与任务 | 来源识别、hash 快照、原子发布、后台批次、失败路径重试、单 worker 数据库锁和断点续跑；完成调用立即记录，恢复预算 | 长文档语义切段、分阶段缓存、全目录处理结论及更完整崩溃注入回归 |
| 5 抽取 | 严格 schema 模型适配、原文 span 校验、元信息归一、完整流式接收和中断拒绝 | 两篇真实文档试运行、30 篇抽取质量评测 |
| 6 去重与关系 | 向量候选、三分类判断、观察追问、显式算法题号 | 150 对 dedup 评测、人工修正流程、复杂题号核验 |
| 7 SQL 统计 | 同 occurrence 筛选、频率、时间、importance/gap、详情来源 | 100+ 真实来源的 M1 门槛与完整真实回归 |
| 8 检索 | ES 建索引、BM25/Dense/Hybrid/LLM rerank、SQL 补事实 | 冻结 50 查询四路消融、完整索引故障及版本竞态回归 |
| 9 Agent | 受限路由、工具轨迹、事实模板、API | 50 查询 routing 评测、复杂问句和一致性恢复 |
| 10 Review | 事务性复习、幂等、未匹配记录与解析、API | 更强并发压力测试与自然语言候选澄清体验 |
| 11 CLI 与发布 | 本地导入/进度/失败重试/统计/检索/来源/问答/复习 CLI、README、API/容器烟测、评测脚手架 | 四个真实 demo、完整看板、所有硬门槛均达成 |

**模型配置：** 按用户明确选择，使用火山方舟 Coding Plan `/api/coding/v3`，`ark-code-latest` 与 `doubao-embedding-vision`。完整串行预检已验证 Schema 抽取、1024 维向量、双题与批量候选判断和重排，五次调用记录 3,469 tokens；报告在 `data/reports/model-preflight.json`。API 与 worker 的模型请求共用串行锁，请求结束后默认间隔 2 秒。向量使用对称语义相似度指令 v2；一次判断最多 10 个候选，并记录实际模型与用量。真实语料导入与质量评测的状态见后续运行记录。

**已核验的工程证据（2026-09-30）：** PostgreSQL 16.9 的 Alembic 升级成功；Elasticsearch 8.19.0 的真实建索引和 BM25/Dense/Hybrid 查询成功；重新构建的 API/worker 容器启动成功。实际 HTTP 请求验证 `/api/health`、`/api/topics`、空库统计，以及未配置模型时的导入拒绝。安装包已包含 taxonomy 和提示词资源。自动化测试为 121 通过、1 跳过；启用独立的真实 ES 测试后该项通过。测试语料仍为合成 fixture。人工 gold 仍为 `draft`，因此所有发布质量指标均为“未评测”。
