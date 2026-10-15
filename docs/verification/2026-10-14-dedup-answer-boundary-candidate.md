# 去重回答边界候选（2026-10-14）

本次新增 `dedup_judge_v2` 与可选 SAME 复核，供真实面经对照采集。生产仍默认 v1，未调整去重可信度阈值、候选数量或线上归并结果。

来源基线是 `data/gold/source-reviewed-dedup-20261014-v2` 的 206 对真实原文：43 SAME、112 RELATED、51 DIFFERENT。此前冻结后的 r293 canonical 审计发现 2 对非 SAME 被归一起、17 对 SAME 被拆开；那是历史归并结构审计，不能视为当前模型 Judge 的准确率，也不能直接归因于候选召回。

候选按主体、操作、完整回答范围、作答产物、关键技术条件、故障范围、指代与条件完整性进行比较。明确要求代码/SQL和口述方案不能只作为展示差异；宽泛故障与指定对象、阶段或数量的故障不能凭共同技术名合并。一方提到如下要求或给定代码但未提供内容时不猜测条件。普通完整泛问、同义描述、可辨错别字仍允许 SAME，不把所有近似表达降为 RELATED。规则不包含 gold 题对、题 ID 或 canonical 真值。

开启复核后只对首阶段 SAME 增加一次边界复核：八项均通过保留原 SAME；存在实际边界差异才降为 RELATED。单题与 batch 两条接口均使用此机制，并保留 batch 候选 ID 完整性检查。默认 v1 的原有复核指令与五项 schema 保持原样。

实际 resolver 还修复了一个独立确定性缺陷：显式 `confidence=0.0` 原来被 `or 1.0` 改成 1.0，现在保留零值，只有 `None` 采用既有默认 1.0。没有增加新的合并阈值。

ProviderCollector 的实验参数为：

```python
ProviderCollector(section, dataset, settings, gate, budget, *,
                  extraction_prompt_version=None,
                  dedup_prompt_version=None,
                  dedup_verify_equivalence=None)
```

`dedup_prompt_version` 接受 `dedup_judge_v1` / `dedup_judge_v2`。`dedup_verify_equivalence=None` 继承运行时设置；True/False 可显式启用/禁用复核。新 CLI 参数只适用于 provider dedup，错误任务在读取数据与连接模型前被拒绝。

在共享 Linux gate 及匹配快照的隔离评测环境，可用现有采集器分别运行：

```sh
python -m eval.collect --dataset data/gold/source-reviewed-dedup-20261014-v2 \
  --section dedup --split test --review-policy delegated_agent --adapter provider \
  --dedup-prompt-version dedup_judge_v1 --no-dedup-verify-equivalence \
  --max-calls 650 --max-tokens 1000000 --output data/reports/dedup-baseline-predictions.jsonl

python -m eval.collect --dataset data/gold/source-reviewed-dedup-20261014-v2 \
  --section dedup --split test --review-policy delegated_agent --adapter provider \
  --dedup-prompt-version dedup_judge_v2 --no-dedup-verify-equivalence \
  --max-calls 650 --max-tokens 1000000 --output data/reports/dedup-v2-prompt-predictions.jsonl

python -m eval.collect --dataset data/gold/source-reviewed-dedup-20261014-v2 \
  --section dedup --split test --review-policy delegated_agent --adapter provider \
  --dedup-prompt-version dedup_judge_v2 --dedup-verify-equivalence \
  --max-calls 1300 --max-tokens 2000000 --output data/reports/dedup-v2-guard-predictions.jsonl
```

这三个命令只是采集接口示例，本工作未执行外部模型调用。实际迭代应统一预算并由运行者控制共享 Linux gate，不能绕过 gate 在 Windows 另起调用。采集器保持现有最多三次重试规则，失败及重试均计入预算。

候选首阶段版本为 `dedup_judge_v9_answer_boundary`；开启复核为 `dedup_judge_v9_verified_answer_boundary`。每行和 collection 元数据保存实际 provider 指纹，包括请求模型别名、主提示词 SHA-256、是否复核、复核提示词 SHA-256 和 schema。每次调用另记实际响应 `model_revision`、实际发送系统提示词指纹、阶段和 schema，区分首判与复核；不能因请求别名相同宣称模型权重相同。

验证结果：36 项去重候选、既有 provider、候选排序及 shadow 单元测试通过；12 项评测采集器及 canonicalization 集成测试通过。覆盖逐轴拒绝、真正 SAME 保留、非 SAME 不增加复核、batch 覆盖、实际模型身份/提示词指纹、CLI 消融选项、配置继承，以及 SQLite + exact rank + batch Judge + guard 的真实 resolver 路径保留零置信度。测试均为无外部模型的控制流和契约验证，不能证明提示词语义质量提升。

限制：resolver 只给 Judge 提供问题文本和候选 canonical_text。如果抽取或归并已经删除关键条件、剩余文本又没有可观察的缺项提示，提示词无法恢复丢掉的原文。此次未全库回填或替换候选，也未据 gold 直接修改线上归并。真实 206 对结果、SAME precision/recall、两类错误与成本变化应等实际采集后评估，旧来源开发回归成绩不能声称新来源泛化。
