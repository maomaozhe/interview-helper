---
id: PIT-004
status: current
resolution: mitigated
resolution_scope: generation-grounding-and-identity-contracts
scope: historical-report-and-workspace
verified_at: 2026-10-15
runtime_verified: false
tags: [extraction, truncation, grounding, schema, identity, bounded-repair]
sources:
  - path: md/issue.md
  - path: docs/verification/2026-10-01-import-rag-smoke.md
  - path: docs/verification/2026-10-09-qwen-tenants.md
  - path: docs/verification/2026-10-15-source-optimization.md
  - path: src/interview_intelligence/extraction/provider.py
    symbols: [OpenAICompatibleExtractor.extract, OpenAICompatibleExtractor._validate_quote_grounding]
  - path: src/interview_intelligence/extraction/validation.py
    symbols: [validate_extraction]
  - path: src/interview_intelligence/dedup/provider.py
    symbols: [OpenAICompatibleJudge.judge_many]
  - path: src/interview_intelligence/agent/task_annotation.py
    symbols: [model_labels, resilient_model_labels, annotate_tasks]
  - path: src/interview_intelligence/providers/qwen_identity.py
    symbols: [QwenIdentityClient]
tests:
  - tests/unit/test_extraction_contract.py
  - tests/unit/test_extraction_quote_feedback.py
  - tests/unit/test_extraction_source_coverage_v6.py
  - tests/unit/test_batch_judge.py
  - tests/integration/test_task_annotation.py
  - tests/unit/test_annotation_recovery.py
  - tests/unit/test_qwen_identity.py
---

# PIT-004：完整输出、逐字证据与记录身份各有一道关

[返回索引](README.md) · [入库流程](../architecture/ingestion-flow.md)

## 30 秒回忆

- 模型返回 JSON，不代表响应完整；quote 能定位，也不代表标签或问题划分正确。
- 本专题保留三个不同机制：生成未完整、证据或结构无效、让模型复写宿主身份造成映射错误，不能用一种重试解释全部。
- 有限修复、完整草稿反馈和短 ID 协议已落地；上游偶发截断及抽取语义完整性仍未解决，因此状态为局部缓解。

## 合并记录与原始证据

原始失败、逐次尝试和私人来源名保留在[问题日志](../../md/issue.md)；下表按原编号或时间反查，不复制源名、题句及运行 ID。

| 原记录 | 纳入本篇的内容 |
| --- | --- |
| #3「模型超时及输出截断」；#51「新来源抽取发生读取超时，有限重试中」 | 完成信号与读取失败边界 |
| #6「分类不在固定 taxonomy 中」；#11「每次失败仅记录异常类型，诊断信息不足」 | 合法结构与可用于修复的错误信息 |
| #16「模型把原文中的错别字‘修正’进了 raw_quote」；#40「同一句原文重复出现时抽取未指定出现位置」 | 逐字引用、重复位置，不允许任意匹配 |
| #46「小篇幅来源连续截断及诊断缓存恢复」；2026-10-02「抽取参数诊断的结果边界」 | 同配置偶发成功和参数组合诊断的因果限制 |
| #5「去重候选 ID 遗漏或改写」；Q03「回填让模型复写 UUID 与原文，增加输出成本并触发严格校验失败」 | 两种任务中的宿主身份映射协议 |
| 2026-10-02 19:55；2026-10-03 01:55、02:55 的结构失败记录 | 未知追问 ID 与重复问题 ID，按错误机制聚类 |
| [2026-10-09 Qwen 验收](../verification/2026-10-09-qwen-tenants.md)的首次宽范围重排失败 | JSON 合法但候选身份不完整，是同类协议问题在另一个 provider 边界的复发 |
| 自动单次模型失败 / 文件错误历史中的截断、超时、引用与结构错误 | 保留全部原记录，不把重试行或终态行再算成独立事故 |
| [2026-10-15 来源优化](../verification/2026-10-15-source-optimization.md)的抽取段 | 完整草稿修复及 v6 语义质量的后续证据 |

`no eligible questions` 经原文确认有题的语义误拒，见 [PIT-006](2026-10-15-source-eligibility-policy.md)；缓存和恢复见 [PIT-005](2026-10-15-ingestion-cache-recovery.md)。
#5 的早期内部 provider 版本名，不能与后来 `dedup_judge_v3.md` 来源上下文候选混为同一发布。

## 触发、已证实原因与未知

| 机制 | 已证实 | 不能据此推断 |
| --- | --- | --- |
| 未完整生成 | 收到 `finish_reason=length`、流未完整结束或读取超时 | 输入一定过长、一定频控、所有模型请求都不可用 |
| 引用定位失败 | 原始 quote 被纠错；重复 quote 缺少唯一出现位置 | 找到任意相似文本就能证明来源位置 |
| 输出结构失败 | taxonomy 组合非法、local ID 重复、追问指向未知 ID | 改成合法枚举就证明语义分类正确 |
| 身份协议失败 | 长 UUID 回填遗漏/改写，或分类同时要求模型重述原文 | 提高输出容量就能保证候选与输入一一对应 |

短文也发生过三次截断；一次同配置重试成功没有解释上游为何截断。
某次 `ReadTimeout` 的 usage 为空，只能保留未知，不能凭经过时长计算实际账单 tokens。
日志中关于推理耗时、短文/OCR 等线索应与已证实错误分开，不能提升为所有失败的统一根因。

## 实际处理时间线与走过的弯路

| 时间 / 阶段 | 实际尝试和结果 | 留下的判断 |
| --- | --- | --- |
| 2026-10-01 早期 | 完整接收流、检查结束原因，保留读取超时与失败记录 | 半份或截断输出不发布；流式也不是不会失败 |
| 2026-10-01 #16 → #18 | 反馈逐字引用错误后，下一次 quote 修好，却又把环节标题当提问 | 一道校验通过不能替代后面的语义资格判断 |
| 2026-10-01 #40 | 重复引文未给位置被拒；反馈后下一次抽取成功进入归并 | 不能任取第一个位置；抽取成功不等于整篇已发布 |
| 2026-10-02 #46 | 短文连续三次截断；共享调用门内一次同配置诊断得到完整且可定位的结果 | 当次恢复成立，上游机制仍未知；缓存恢复另见 PIT-005 |
| 2026-10-02 参数诊断 | 单独提高 `max_tokens` 仍超时；提高上限并关闭 thinking 的组合诊断才通过引用校验 | 两项同时变动，不能证明哪项有效，更不等于逐题完整或已发布 |
| 2026-10-04 Q03 | 回填改为批内序号与压缩标签，由宿主恢复 ID 和原文，验证完整批次后提交 | 让模型做分类，确定性记录关联由宿主负责 |
| 2026-10-09 Qwen | 50 候选只返回 39 个唯一 ID，11 个重复/11 个遗漏；原 r14 拒绝且重试耗尽预算，保存复现后改系统 SDK 身份协议 | 已排除输出截断等当次嫌疑；合法 JSON 不能替代候选集合完整性 |
| 2026-10-15 第一次修复 | 汇总全部 quote 定位错误，目标失败来源仍三次失败 | “错误说得更多”仍不足以稳定修复完整输出 |
| 2026-10-15 第二次修复 | 保留上一完整草稿、反馈所有真实定位、禁止有效 index 退回空值，两次调用后通过 | 修复对象和已正确状态必须一起保留，来源仍是唯一事实依据 |

没有采用模糊匹配放行、改写不可变原文、任意补 ID，或放宽失败上限来制造成功。
记录“未采用”与上表真实失败尝试分开，不能把推测过的方案写成已经试错。

## 当前已有的防复发措施

- [抽取 provider](../../src/interview_intelligence/extraction/provider.py) 检查截断/流结束、集中 quote 错误和完整待修草稿；修复保持原有有限尝试预算。
- [共享抽取校验](../../src/interview_intelligence/extraction/validation.py) 检查 revision、原文切片、taxonomy、local ID 和追问端点；缓存命中也要复验。
- [批量 Judge](../../src/interview_intelligence/dedup/provider.py) 把真实 ID 映射为请求内短 ID，约束候选枚举与数量，并验证完整集合。
- [任务回填](../../src/interview_intelligence/agent/task_annotation.py) 使用 `i/f/c/p`；宿主关联原始 occurrence，保存 `quote_origin=host_original`、输入 hash 和 producer 版本。
- 回填格式错误可做有界修复，当前还可在适用失败下拆小批恢复；最终仍要求完整结果，不能提交半批或绕过预算。
- [系统 Qwen SDK 边界](../../src/interview_intelligence/providers/qwen_identity.py) 将候选 ID 设为对象必填键，以显式 rank 还原原列表；个人接口不变，r14 的集合/证据校验、Top50 和六次预算未放宽。
- 修复反馈身份纳入抽取缓存配置；旧行为产物不能仅凭模型别名相同冒充新修复结果。

这些措施证明输入输出和来源绑定受控，`host_original` 不能证明分类正确，覆盖账本也不能证明独立问题没有合并或漏抽。

## 结果、适用范围与待补措施

[来源优化报告](../verification/2026-10-15-source-optimization.md)绑定旧 r293 / i293 / a30，Agent 审核且 `human_verified=false`，来源已见。
同一 31 篇 / 538 问严格对齐中，v5 为 TP374/FP70/FN164、F1 76.17%；v6 为 TP358/FP41/FN180、F1 76.41%。
F1 略升掩盖了召回下降和整篇失败从 1 增至 4；候选未切为默认，不能用结构覆盖规则声称全面改善。
四篇旧 v6 失败源定向修复均通过结构校验，但仍 TP79/FP13/FN26、F1 80.20%；独立题合并与轮次遗漏仍在。
这是预声明失败子集的结果，不能拼接旧成功输出充当一次完整全量复测，也不能证明未见来源泛化。

待补：按结束信号、quote、结构、资格误拒分开统计；做参数消融和未见来源逐题对齐；保留上游模型实际身份与全部失败。
再次发生时先看实际响应与 finish reason，再看校验阶段、完整草稿和 ID 集合；不要先用更大超时掩盖数据契约错误。
真正闭环须同时满足完整性、合法绑定和目标语义质量；本篇 `mitigated` 不等于已解决所有抽取失败。

## 测试入口与自测

| 契约 | 测试入口 |
| --- | --- |
| 完整响应、位置和分类合法性 | [test_extraction_contract.py](../../tests/unit/test_extraction_contract.py) |
| 全量 quote 反馈和正确位置保留 | [test_extraction_quote_feedback.py](../../tests/unit/test_extraction_quote_feedback.py) |
| v6 覆盖、预算及明确完成信号 | [test_extraction_source_coverage_v6.py](../../tests/unit/test_extraction_source_coverage_v6.py) |
| 批量身份与原文宿主关联 | [test_batch_judge.py](../../tests/unit/test_batch_judge.py)、[test_task_annotation.py](../../tests/integration/test_task_annotation.py)、[test_annotation_recovery.py](../../tests/unit/test_annotation_recovery.py) |
| Qwen 身份映射、取消/期限与原宿主规则保留 | [test_qwen_identity.py](../../tests/unit/test_qwen_identity.py) |

本次仅核对日志、验收报告和源码，未运行上述测试或模型；历史测试数字不作为本次通过证明。

1. 一篇短文三次截断后，同配置一次成功，能关闭哪个问题、不能关闭哪个问题？要点：该次完整响应可用；上游原因和后续稳定性未知。
2. 宿主恢复原始 ID/quote 后，为什么仍需语义审核？要点：身份和引文不再由模型猜写，但资格、标签、原子问题划分仍可能错。
