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
  - path: docs/reference/module-map.md
  - path: docs/reference/agent-workflow.md
---

# 项目知识库

按本次任务选下面一行，只读相关小节。**找到契约、代码入口和首选测试后，转源码核查；跨模块行为不清时再补流程。** 产品介绍见 [README](../README.md)，AI 开发硬约束见 [AGENTS](../AGENTS.md)。

## 日常开发入口

| 任务 | 先看这里 | 需要理解跨模块行为时 |
| --- | --- | --- |
| 理解项目 | [组件与职责](architecture/system.md#组件与职责) | [核心实体](architecture/data-model.md#核心实体与身份) |
| 改抽取、去重、导入 | [代码与测试](reference/module-map.md#ingestion) | [入库主链](architecture/ingestion-flow.md#主链与职责) |
| 改统计、筛选、检索 | [代码与测试](reference/module-map.md#query) | [工具与事实范围](architecture/query-flow.md#工具与事实范围) |
| 改对话、回答、复习 | [代码与测试](reference/module-map.md#agent) | [范围继承](architecture/query-flow.md#scope-来源与继承) |
| 改页面、流式、恢复 | [前端与测试](reference/module-map.md#frontend) | [SSE 与完成回执](architecture/query-flow.md#sse反馈与记忆) |
| 改配置、模型、账号 | [配置入口](reference/runtime-config.md#先确定配置入口) · [默认版本](reference/runtime-config.md#提示词选择与版本身份) | [代码与测试](reference/module-map.md#runtime) |
| 排查故障 | [按症状找 PIT](retrospectives/README.md#按症状快速查找) | 对应专题的排查步骤与原始证据 |
| 判断现状与缺口 | [当前状态](status/current.md) | 表内各日期的部署与质量证据 |

业务约束按涉及范围查 [不变量 I01–I13](reference/invariants.md)；调查、记录和交接方法见 [Agent 工作方式](reference/agent-workflow.md)。这两页用作查阅，无需每次通读。

## 运行与专项资料

| 任务 | 入口 |
| --- | --- |
| 启动完整服务 | [普通 Compose](development.md#推荐完整运行普通-compose)，Windows 用 [WSL](development.md#windows--wsl)；演示和模块调试见 [其他入口](development.md#其他入口的用途) |
| 已有远端环境维护、可选模型和账号配置 | [SSH](deployment-ssh.md) · [Qwen 专项](deployment-qwen.md) · [账号与个人模型](tenants.md) |
| 通用评测、候选实验与历史复现 | [评测入口分类](../eval/README.md#入口分类通用评测候选实验与历史复现) · [标注指南](annotation-guide.md) |
| 产品目标与验收契约 | [V1 规范](implementation-spec-v1.md) · [原始需求](requirements/interview-intelligence-v1-source.md) |
| 展示项目 | [简历与面试说明](resume-project-experience.md) · [截图出处](images/README.md) |

<details>
<summary>按需查阅：专项设计、未完成提案与历史证据</summary>

- [plans](plans/) 同时保留专项契约、未实施提案及少量原始验收；先核对页内日期和适用范围，实施/发布结论查 [当前状态](status/current.md#按问题查看证据)。
- 长期取舍在 [decisions](decisions/)，日期验收在 [verification](verification/)；追原问题、历史报告及旧编号用 [来源映射](retrospectives/SOURCE-MAP.md)。历史文件保留原路径。
- 早期 [实施流水](implementation-status.md)、[故障摘记](bad-cases.md)、[查询 Agent 复盘](retrospectives/2026-10-04-query-agent.md) 保留独有证据，按原日期理解。

</details>

## 维护方式

只更新受影响页：服务边界 → system；实体身份 → data-model；导入/问答 → 对应 flow；默认配置 → runtime-config；代码/测试入口 → module-map；稳定约束 → invariants；能力与质量结论 → current。一个主题保留一份当前说明，其余用链接。

- 当前知识页保留 `id/status/scope/verified_at/sources`，来源写仓库相对路径和关键符号，测试路径可放 `tests`。核对正文后才改日期。
- 当前实现以源码与装配为准，目标行为查有效规范，部署/质量查对应环境证据；冲突写明范围。`verified_at` 是源码或来源核对日期，不代表部署验收。
- 记录归并和清理边界见 [工作方式](reference/agent-workflow.md#将有复用价值的结果沉淀)；复盘格式见 [新增与维护](retrospectives/README.md#新增与维护)。文档检查命令统一见 [验证入口](../AGENTS.md#验证入口)。
