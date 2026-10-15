---
id: project-knowledge-index
status: current
scope: workspace
verified_at: 2026-10-15
sources:
  - path: src/interview_intelligence/api.py
    symbols: [create_app]
  - path: src/interview_intelligence/worker.py
    symbols: [process_queued_run]
---

# 项目知识库

先看 [当前状态](status/current.md) 的一张表，区分能力、默认版本、部署证据、质量结论与下一步；本页只负责按任务导航。产品介绍见 [项目 README](../README.md)，AI 开发约定见 [AGENTS.md](../AGENTS.md)。

当前知识页依据 2026-10-15 工作区静态核对，包含未提交实现。`verified_at` 是核对日期，不代表测试通过或线上已部署。

完整运行统一从 [普通 Compose](development.md#推荐完整运行普通-compose) 开始，Windows 经 WSL 启动。界面示例、模块直启、隔离评测和 SSH / Qwen 专项部署的用途见 [其他入口的用途](development.md#其他入口的用途)；候选实验和历史复现从 [评测手册](../eval/README.md#入口分类通用评测候选实验与历史复现) 进入。

## 日常开发入口

| 要解决的问题 | 阅读入口 |
| --- | --- |
| 整个项目怎样组成、数据是什么 | [系统架构](architecture/system.md) → [数据模型](architecture/data-model.md) |
| 原文怎样进入题库，失败怎样恢复 | [入库数据流](architecture/ingestion-flow.md) |
| 问答、统计、检索、会话与流式怎样工作 | [查询数据流](architecture/query-flow.md) |
| 应该改哪里、用什么验证 | [模块与测试导航](reference/module-map.md) · [业务不变量](reference/invariants.md) |
| 配置怎样生效，哪个模型和提示词是默认 | [运行配置](reference/runtime-config.md) |
| 哪些已实现、哪些仍有缺口 | [状态摘要](status/current.md) |
| 如何调查、记录、决策和交接 | [Agent 工作方式](reference/agent-workflow.md) |
| 以前踩过什么坑，哪些尚未解决 | [21 个根因专题](retrospectives/README.md)；追原始编号时再读 [来源映射](retrospectives/SOURCE-MAP.md) |

## 运行与专项资料

| 任务 | 入口 |
| --- | --- |
| 推荐完整运行与本地开发 | [普通 Compose / Windows WSL](development.md#推荐完整运行普通-compose) |
| 已有远端环境维护、可选模型和账号配置 | [SSH](deployment-ssh.md) · [Qwen 专项](deployment-qwen.md) · [账号与个人模型](tenants.md) |
| 通用评测、候选实验与历史复现 | [评测入口分类](../eval/README.md#入口分类通用评测候选实验与历史复现) · [标注指南](annotation-guide.md) |
| 产品目标与验收契约 | [V1 规范](implementation-spec-v1.md) · [原始需求](requirements/interview-intelligence-v1-source.md) |
| 展示项目 | [简历与面试说明](resume-project-experience.md) · [截图出处](images/README.md) |

<details>
<summary>按需查阅：专项设计、未完成提案与历史证据</summary>

- 专项契约：[Pi 查询层](plans/2026-10-04-pi-agent-spec.md)、[评测体系](plans/2026-10-05-evaluation-system-spec.md)、[对话检索](plans/2026-10-08-conversation-retrieval-spec.md)、[检索修复](plans/2026-10-08-retrieval-fixes-spec.md)、[访问管理](plans/2026-10-09-access-panel-spec.md)、[使用统计](plans/2026-10-09-question-usage-spec.md)；其他边界补充见 [plans 目录](plans/)。
- 尚未闭环：[MCP 接入调研](plans/2026-10-08-mcp-integration-research.md) 未实施；[业务设计修复](plans/2026-10-12-business-design-spec.md) 引用的最终验收文件缺失，不能认定已按计划发布。
- 长期取舍见 [decisions](decisions/)；具体日期的环境、输入、结果见 [verification](verification/)，入口按主题由复盘来源映射提供。
- 早期 [实施流水](implementation-status.md)、[故障摘记](bad-cases.md)、[查询 Agent 原始复盘](retrospectives/2026-10-04-query-agent.md) 保留独有证据，均不作为今天状态的入口。
- `plans/` 中的 [System One 实测](plans/2026-10-04-open-system-one-deployment.md) 和 [10-05 验收](plans/2026-10-05-query-spec-verification.md) 实际含原始报告，保留旧路径供证据引用。

</details>

## 维护方式

一个主题维护一份当前说明。变更服务边界更新 system；实体身份更新 data-model；导入/问答更新对应 flow；配置默认值更新 runtime-config；路径或测试变化更新 module-map；稳定约束更新 invariants；能力和质量结论变化更新 current。只改受影响页，保留必要的交叉链接。

- 当前知识页保留 `id/status/scope/verified_at/sources`，来源写仓库相对路径和关键符号，测试路径可放 `tests`。核对正文后才改日期。
- 当前状态只汇总到 `status/current.md`；其他页面链接该表，不再追加另一份状态流水。当前实现查源码，目标行为查有效规范，部署与实验结论查对应环境的证据；冲突写明范围，不用其中一种替代另一种。
- 工作记录优先追加已有任务；新计划只用于独立目标或需要接续的工作。完成后将有效规则并回当前页；纯步骤清单和重复总结删除，独有决策及失败证据保留。
- 同根因更新已有复盘。最小格式在 [复盘索引](retrospectives/README.md#新增与维护)，不为小改动机械增加计划、复盘或“文档完成报告”。
- 历史日志、验收结果和未解决问题不因过期而删除。清理前查入链和代码读取路径；保留证据内容，必要时仅修导航链接。提示词和被程序读取/哈希的规范不能当普通重复文档清理。
- 提交前核对链接、来源路径、符号、图和改动范围；仅文档改动不需要运行整套业务测试。文件 hash 只能证明内容身份，不能代替语义核查。

本次清理已将重复建设方案、初始施工清单、意图概述和演示入口收敛到上表；原始问题日志、验收事实及 21 个复盘专题继续保留。
