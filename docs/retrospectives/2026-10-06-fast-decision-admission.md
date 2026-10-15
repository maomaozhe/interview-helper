---
id: PIT-016
status: current
resolution: mitigated
resolution_scope: fast-decision-admission-and-fallback
scope: historical-report-and-workspace
verified_at: 2026-10-15
runtime_verified: false
tags: [model-routing, calibration, latency, evaluation, fallback]
sources:
  - path: md/issue.md
  - path: docs/retrospectives/2026-10-04-query-agent.md
  - path: docs/verification/2026-10-06-resume-quality.md
  - path: src/interview_intelligence/agent/jev.py
    symbols: [JevPlanner.payload, JevPlanner.plan]
  - path: evals/project_quality/calibrate_fast.py
    symbols: [fit, operating_point]
tests:
  - tests/unit/test_choice_constraints.py
  - tests/unit/test_jev_query.py
  - tests/unit/test_fast_calibration.py
---

# PIT-016：小模型很快，但未形成有效的查询接管

[返回踩坑索引](README.md) · [历史完整测评](../verification/2026-10-06-resume-quality.md#fast-decision独立校准的否定结论)

## 30 秒回忆

接口 ready、推理毫秒级和置信度高，都不能证明它适合接管业务。
先后发现输入选项在预算中丢失、中文完整计划正确率不足、高置信错误和端到端额外成本。
有效止损是保持准入与回退，正式快路径关闭；获得可靠接管和调用收益仍未解决。

## 合并的原记录与不同失效机制

- `md/issue.md` Q06：公司选项超过 Laya 问题头预算，候选丢失；这是输入完整性故障。
- 10-06“六层评测缺口与快速路由取舍”：输入协议修复后仍有业务决策错误；这是模型能力与域校准问题。
- [既有查询复盘](2026-10-04-query-agent.md) 第 3 节：推理时间和完整用户请求耗时不同。

这些是同一接管决策上的连续阻断，不能把输入修复说成业务能力已修好，也不能把回退成功说成小模型接管成功。

## 失败尝试与处理过程

| 尝试 | 实际观察 | 决策 |
| --- | --- | --- |
| 直接给 Laya 完整公司目录 | 选项预算导致截断/合并，曾出现 422 | 检查选项总数、去重数与截断；新公司交回生成式规划 |
| 修复协议后测中文业务计划 | 20 条开发探针只有 1 条完整关键字段正确 | 保留接口，不能依据毫秒推理时间开放接管 |
| 换私有 Qwen 并做独立校准 | 60 校准/50 测试；高置信仍有错误 | 阈值只能由校准集决定，测试只作报告 |
| 调高置信阈值 | 0.99 接收 20 条错 12 条；0.995 接收 3 条错 2 条，更高阈值无覆盖 | 没有满足既定覆盖/观察错误要求的操作点，NOT_ELIGIBLE |
| 真实完整链路先小模型、再 Pi 回退 | 接管率 0，Frontier 调用未减少，额外调用增加 | 正式快速决策继续关闭，保留失败及成本证据 |

准入规则是历史观察门槛，不保证未来零错误。零接受时 precision 为 null，不能写成 100%。

## 历史验证和边界

10 场景链路对照中，两条路径都成功 9/10；Pi 基线调用 10 次，加小模型的路径另有 10 次小模型调用，Frontier 节省为 0，配对耗时中位增量约 3.59 秒。
这组对照顺序运行、规模小，不是生产 SLA 或普遍性能结论；校准/测试是自编业务场景，不是自然用户流量。
Laya 和 Qwen 是不同模型及协议阶段，不能用一方延迟与另一方正确率拼成收益。

本页 `mitigated` 指不合格接管已被准入机制隔离；高质量且有收益的快路径仍待解决。
当前默认开关及选择机制见 [配置登记](../reference/runtime-config.md)，实际线上状态本次未复核。

## 防复发与再排查顺序

1. 检查模型实际收到的候选与截断标记，再看预测；输入不完整的运行单独记录。
2. 衡量完整计划和业务断言，不能只看单字段正确率、格式合法或 confidence。
3. 在独立校准集选阈值，冻结后测试；报告接管覆盖率和错误，不能只报接受子集的漂亮结果。
4. 计入小模型、回退模型、网络/排队/共享锁及工具开销，做同场景配对比较。
5. 确认“接管了多少、少调了几次大模型、全链路是否更好”，再考虑改默认。

| 防复发措施 | 当前情况 |
| --- | --- |
| 输入选项与截断、置信和域校准检查 | 已有：`JevPlanner.plan` 及对应契约测试 |
| 校准集与测试集分开、无合格阈值不准入 | 已有：[calibrate_fast](../../evals/project_quality/calibrate_fast.py) |
| 新模型/训练策略与新独立测试、真实混合负载收益 | 待补证据，不能沿用旧成绩默认放行 |

测试入口：[有限选项](../../tests/unit/test_choice_constraints.py)、[Jev 查询](../../tests/unit/test_jev_query.py)、[校准](../../tests/unit/test_fast_calibration.py)。本次未执行。

## 回顾自测

1. 提高阈值到零覆盖，为什么不能说达到 100% precision？
2. 用户请求最终成功，怎样分辨是小模型接管还是 Pi 兜底？

<details><summary>查看答案</summary>

1. 没有接受样本，precision 不可定义；还必须报告接管覆盖率。
2. 查看实际决策/回退原因与每条路径调用记录，完整成功率不等于接管率或节省调用。

</details>
