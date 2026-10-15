---
id: PIT-017
status: current
resolution: mitigated
resolution_scope: evaluation-reference-provenance-and-comparison
scope: historical-report-and-workspace
verified_at: 2026-10-15
runtime_verified: false
tags: [evaluation, gold, provenance, metrics, regression]
sources:
  - path: md/issue.md
  - path: docs/verification/2026-10-06-quality-optimization.md
  - path: docs/verification/2026-10-06-quality-refinement.md
  - path: docs/verification/2026-10-14-source-gold.md
  - path: eval/validate_gold.py
    symbols: [validate_gold]
  - path: eval/release.py
    symbols: [check_bundle]
tests:
  - tests/unit/test_gold_validator.py
  - tests/unit/test_release_gate.py
  - tests/unit/test_source_gold.py
---

# PIT-017：参考标注和比较口径改变，分数不再回答同一个问题

[返回踩坑索引](README.md)

## 30 秒回忆

一次失败可能来自模型，也可能来自错误参考、检查锚点或测量口径。
修正参考、扩充判断池、换样本和修复后重测都要留下独立版本；原预测、原分数与真实失败保留。
现有保护缓解了证据混用风险，完整语义质量及独立人工真值仍未因此通过。

## 合并的原记录

- 原问题 #36：检查器漏掉原文空格，实际已发布事实正确；修检查锚点后只读核验，没有重调模型。
- 原问题 #37：截图与详情读取跨了两次发布，检查器错误要求版本相同；需分别保留两个版本及历史归并证据。
- 10-06 连续两轮质量记录：扩充 relevance 判断池、首测/回归、非法参考枚举的重评分。
- [10-14 来源审核](../verification/2026-10-14-source-gold.md)：无法绑定的真实原问和粒度错误不能强行映射到已有 canonical。

这些问题共同需要“参考、预测和证据版本绑定”；具体检查器误写、数据自然演进、标注政策冲突仍是不同子原因，不合成一次模型故障。

## 真实失败、处理与取舍

| 事件 | 实际问题与无效前提 | 有证据的处理 |
| --- | --- | --- |
| #36 来源检查失败 | 检查器把原题中的空格省掉；不能据检查退出码断言入库失败 | 读取不可变来源及 occurrence，修锚点并保留原失败报告，只读重核 |
| #37 版本不一致 | 后台已发布新版本；旧截图与新详情不是同一时间快照 | 保留原查询身份/版本与新读取版本，验证历史 assignment 和原 span |
| relevance 池不足 | 旧排序分数基于不完整裁决，不可继续宣称旧池改进代表当前成绩 | 新增裁决、同预测重评分，保留原 relevance 与旧报告 |
| Query 首次备用集失败 | 部分期望用中文轮次，而契约要求 FIRST/SECOND | 按原始请求及既有契约另建参考版本，原始分数不改写 |
| source-only 或合并粒度不一致 | 真实问题不存在可比较 occurrence，或一个预测合并多问 | 明确不可比较/缺失/粒度错误，拒绝强映射或一预测多计 TP |

10-06 原备用集首次 46/50；修订两处错误参考后，对同一预测重评分为 48/50，新增调用为 0。
这是参考纠正，不是模型从 92% 提升到 96%；该测试观察过后，后续修复重测只能称为已见回归。

扩充检索池后四路排序的 Recall/NDCG 结论改变，但新旧判断分母不同，不能把差额直接解释为同条件模型退化。
新增原文参考的 80.44% 抽取 F1 与旧 32 篇 92.09% 的样本/粒度也不同，不能直接作退化比较。

## 尚未解决与验证范围

`mitigated` 指参考契约、来源绑定和版本留证有了保护，不表示 Gold 永远正确或全部数据独立。
Agent 审核保留 `human_verified=false`；看过输出后修参考需说明时点，不能改名为首次独立测试。
没有穷尽池外相关项时不宣称全库 Recall；框架合成报告不当作真实业务质量。
旧完整 V1 阻断仍按原门禁保存，本页没有复跑质量实验或更新生产标签。

## 防复发与排查顺序

1. 先保存输入、参考、预测、评分器和协议版本/hash，分清是执行失败、业务失败还是验证器失败。
2. 对照原请求、不可变来源和既有契约，不能按预测结果反向设计期待值。
3. 原参考有误时另存新版本，明确理由与时点；同预测重评分和重新调用分别记账。
4. 对比前检查相同样本、真值、模型/输入条件与计时口径；不满足就分别报告。
5. 保留未审核、不确定和无法映射项，检查分母是否悄悄消失。

| 防复发措施 | 已有或待补 |
| --- | --- |
| 参考枚举/必填/未知字段、来源证据校验 | 已有：[validate_gold](../../eval/validate_gold.py) 及来源契约 |
| bundle 版本、阈值和产物身份检查 | 已有：[release.check_bundle](../../eval/release.py) |
| 扩池、未见来源、人审与政策冲突裁定 | 仍需后续证据，机器校验不能代替语义裁决 |

守护入口：[gold validator](../../tests/unit/test_gold_validator.py)、[release gate](../../tests/unit/test_release_gate.py)、[source gold](../../tests/unit/test_source_gold.py)。本次未执行。

## 回顾自测

1. 修正参考后，同一预测从 46/50 变成 48/50，应该怎样描述？
2. 一个长预测覆盖两道独立原问，能否算两个 TP？

<details><summary>查看答案</summary>

1. 保留首测原分数，另报参考修订后的重评分、修订原因及相同预测身份，不称模型提升或新首测。
2. 严格一对一对齐不能这样计分；按协议保留合并粒度错误和未匹配问题。

</details>
