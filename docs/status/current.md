---
id: current-project-status
status: current
scope: workspace-with-historical-deployment-references
verified_at: 2026-10-16
runtime_verified: false
sources:
  - path: src/interview_intelligence/agent/tool_definitions.py
  - path: scripts/sync-tool-contract.py
    symbols: [generated_section]
  - path: src/interview_intelligence/config.py
    symbols: [Settings]
  - path: compose.yaml
  - path: src/interview_intelligence/agent/query_service.py
    symbols: [QueryService, QueryService._requested_pipeline]
  - path: src/interview_intelligence/worker.py
    symbols: [build_worker_services]
  - path: src/interview_intelligence/agent/task_annotation.py
    symbols: [annotate_tasks]
  - path: src/interview_intelligence/dedup/provider.py
    symbols: [OpenAICompatibleJudge]
  - path: docs/verification/2026-10-14-query-timeout-frequency.md
  - path: docs/verification/2026-10-15-source-optimization.md
  - path: docs/verification/2026-10-15-project-consolidation.md
---

# 当前工作区状态与证据入口

工程收口：静态工具声明统一由 Python 生成 Pi 数据；SEARCH 的 requested_pipeline 已独立解析，执行时不再重建完整规划上下文。验证与结构审视见 [10-16 收口记录](../verification/2026-10-16-tool-contract.md)。后续优先统一证据投影，再梳理兼容检索入口与执行生命周期。

**核心功能已有实现，完整 V1 语义质量门禁仍未通过，当前服务器版本尚未重新核验。** 本页是项目现状的唯一汇总入口；核对范围为 2026-10-15 收口分支源码。下列部署和质量结论来自对应日期报告；本轮工程验证另见 [收口验收](../verification/2026-10-15-project-consolidation.md)。

完整运行只推荐 [普通 Compose](../development.md#推荐完整运行普通-compose)：API + Pi + worker + PostgreSQL + Elasticsearch，Windows 经 WSL 启动。表中默认指未显式覆盖的该运行方式；配置来源、具体提示词身份及其他入口差异以 [运行配置](../reference/runtime-config.md) 为准。

## 能力、运行版本与验收状态

| 能力与已实现范围 | 默认版本 / 启用状态 | 已知部署证据 | 质量结论与限制 | 下一步 |
| --- | --- | --- | --- | --- |
| 原文入库：快照、抽取校验/缓存、单文档发布、索引重建 | 抽取 `extract_question_v2`；worker 按 Settings 装配 | [10-09 本地服务](../verification/2026-10-09-local-deployment.md)，仅证明当次环境 | [10-15 来源评测](../verification/2026-10-15-source-optimization.md)：v6 定向修复已验证，但全量召回、独立问题拆分和轮次仍有缺口，未启用或部署 v6 | 对齐 API 入队固定 v2 与 worker 实际版本；明确批次/文档 run 父子关系；再评估逐文档全量索引与长事务 |
| 语义归并：候选检索、Judge、canonical 归并 | `dedup_judge_v1`；可选复核默认关闭 | 候选 v3 [明确未部署](../verification/2026-10-15-source-optimization.md)；今天的生产 resolver 待核验 | 同一 206 对实验中 v3 SAME precision 91.30%，低于 95% 门槛，默认保持；来源上下文候选由实验入口显式使用 | 解决剩余误并/漏并；明确 `needs_review` 在 materialize 未消费时的复核状态契约，以及事务内模型调用边界 |
| 任务分类与核验：独立标注、草稿及发布 | 分类 CLI 默认 `task_annotation_v4`；查询标签政策 `VERIFIED` | [10-09 本地验收](../verification/2026-10-09-local-deployment.md)；候选 v9 未部署 | v9 新参考集改善，旧回归仍阻断；新旧政策冲突和未裁来源继续留证 | 先裁清政策冲突并验证新旧参考集，再扩展未见来源；不据新集分数替换默认 |
| SQL 统计与完整算法范围：有效范围 LIST / COUNT / STATS、签名分页 | SQL 事实口径；`ALGORITHM_PROBLEM` 需绑定完整语义证据 | [10-14 算法全集](../verification/2026-10-14-byte-algorithm-all.md)，仅覆盖当次证据、SQL 与部署范围 | SQL 全范围正确不证明上游分类/归并正确；缺失或过期审核时拒绝完整算法结论 | 排名政策已共用；继续守护 occurrence 同范围筛选、版本、分页及固定值回归 |
| 语义检索：BM25 / Dense / Hybrid / 相关性核验 | API / CLI 默认 `HYBRID_RERANK`；工作区 rerank v28、scope v13 | [10-14 频次修复](../verification/2026-10-14-query-timeout-frequency.md)；10-12 业务设计最终验收缺失，不能由工作区 v28 推定生产版本 | 漏召回、核验损失及延迟仍在；SEARCH 候选频次排序不是全库榜，也没有后续分页 | 按真实来源范围验证召回与误报；补齐业务设计发布/验收证据，另采样完成事件延迟原因 |
| 查询 Agent、流式与恢复：澄清、工具、回答、重查、预算权限、持久化事件/SSE/轮询/取消 | Compose router / Pi 默认开启，`query_agent_v13`；Jev 默认关闭 | [10-14 管理观测](../verification/2026-10-14-agent-observability.md)、[频次与超时修复](../verification/2026-10-14-query-timeout-frequency.md) | 当次专项通过不等于完整 V1；活跃 runs / live_tasks 仍在 API 进程内，Pi 回调依赖其所有权，尚无多实例接管证明 | 已拆出错误映射/结果呈现；静态工具声明与 pipeline 解析已收敛，继续统一证据投影并拆分生命周期，跨进程接管另需协议与验证 |
| 用户功能：账号、个人模型、偏好、纠正记忆、复习、使用记录 | 多账号默认关闭，需显式开启；纠正记忆按规范化查询匹配 | [10-09 账号与模型](../verification/2026-10-09-qwen-tenants.md)、[使用与复习](../verification/2026-10-09-question-usage.md) | 账号状态隔离不等于独立题库；个人模型不切换共享向量或导入模型；使用次数不证明检索质量 | 按实际部署核对账号/模型配置与运行版本，保持授权、隔离和幂等 |
| 评测：六层采集、gold 校验、评分、比较、发布门禁 | [评测手册](../../eval/README.md) 为入口；隔离评测显式运行，标签政策为 `KNOWN` | [10-15 来源质量报告](../verification/2026-10-15-source-optimization.md)，绑定历史 r293 / i293 / a30，非最新生产库 | 完整 V1 旧六项阻断保留；Agent 审核、已见回归、合成工程验证、正式质量结论分别记录 | 完成未裁边界与新旧回归后扩展未见来源；保留失败和预算，不降低门槛或拼接局部结果 |

源码和测试入口见 [模块导航](../reference/module-map.md)，业务边界见 [不变量](../reference/invariants.md)。本表的“已实现”不替代功能验收；表内历史报告中的测试数也不是本轮重新执行的结果。

## 工程收口与后续边界

现有工作已按主题提交；SQL / Python 排名共用政策，API 错误映射与 QueryService 结果呈现各自完成首轮职责拆分。提交与验证见 [收口验收](../verification/2026-10-15-project-consolidation.md)，模块边界见 [导航](../reference/module-map.md)。后续继续按一个职责拆分、验证：工具契约与 pipeline 解析已收敛，再统一证据投影并处理运行生命周期；分布式接管、入库索引策略和语义门禁仍独立开放。

如需回答“现在服务器用什么”，仍须新的环境配置摘要、运行身份和版本/哈希证据。`verified_at` 仅表示工作区静态核对日期；当前源码、默认配置、历史发布和真实质量不能互相替代。

候选版本与历史复现统一从 [评测入口分类](../../eval/README.md#入口分类通用评测候选实验与历史复现) 查找；版本文件更高、已被打包或已有测试，都不表示已启用。旧候选报告中的“尚无结果”属于当时状态，v3 的真实结果现已由 10-15 报告补充。

另有 [MCP 接入提案](../plans/2026-10-08-mcp-integration-research.md) 尚未实施；[10-12 业务设计修复](../plans/2026-10-12-business-design-spec.md) 引用的最终验收文件在工作区缺失，相关代码不能补足发布和验收证据。历史问题与失败继续保留在 [复盘索引](../retrospectives/README.md)；返回 [知识库首页](../README.md)。
