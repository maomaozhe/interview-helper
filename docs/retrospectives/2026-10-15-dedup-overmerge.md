---
id: PIT-003
status: current
resolution: open
resolution_scope: semantic-dedup-quality
scope: historical-report-and-workspace
verified_at: 2026-10-15
runtime_verified: false
tags: [dedup, overmerge, precision-recall, source-evidence, evaluation]
sources:
  - path: docs/verification/2026-10-14-dedup-answer-boundary-candidate.md
  - path: docs/verification/2026-10-15-dedup-source-context-candidate.md
  - path: docs/verification/2026-10-15-source-optimization.md
  - path: src/interview_intelligence/dedup/provider.py
    symbols: [OpenAICompatibleJudge.judge, OpenAICompatibleJudge.guard_same, OpenAICompatibleJudge.judge_many]
  - path: src/interview_intelligence/dedup/context.py
    symbols: [compile_source_evidence, SourceQuestionEvidence]
  - path: src/interview_intelligence/dedup/service.py
    symbols: [DedupService.resolve]
  - path: src/interview_intelligence/worker.py
    symbols: [build_worker_services]
  - path: eval/collect.py
    symbols: [ProviderCollector]
tests:
  - tests/unit/test_dedup_answer_boundaries.py
  - tests/unit/test_dedup_source_context.py
  - tests/unit/test_batch_judge.py
  - tests/integration/test_canonicalization.py
---

# PIT-003：去重误合并与复核误伤

[返回踩坑回顾索引](README.md) · [当前入库流程](../architecture/ingestion-flow.md)

## 30 秒回忆

- 踩坑：共同技术名、答案交集或包含关系，不代表两个完整任务可以归为同一道题。
- 失败尝试：给 `SAME` 加复核虽能拦住误并，也会撤回真实同义；只看精确率会漏掉召回损失。
- 当前结论：v3 联合候选在已见参考集改善，但 SAME 精确率仍未达 95%；默认入口保持 v1，问题开放。

## 症状与测量对象

表现有两面：不同对象、作答产物或条件的任务被判 `SAME`；真正同义因过严边界判断被降为 `RELATED`。
SAME precision 衡量“判为可合并的题对中有多少真同义”，recall 衡量“真实同义中找回多少”。
历史 canonical 结构审计、候选召回与逐对 Judge 评分是不同测量，不能从最终误并数量直接反推哪一层出错。

## 已知原因与尚未证明的部分

已识别的判断边界包括：任务主体、操作、完整回答范围、口述或代码产物、技术条件、故障范围、指代是否完整。
原文中的项目实践事实与一般用途、复合任务与其中子问，也不能只因回答部分重叠就合并。
[默认 resolver](../../src/interview_intelligence/dedup/service.py) 给 Judge 的是规范题文本和候选标准问法；上游丢失的条件无法靠更强提示词凭空恢复。
但每个错例究竟来自抽取、候选选择、首判还是复核，需要逐阶段证据；不能统一归因“模型不够强”或“召回不够好”。

## 尝试过程：v2 guard 的代价与 v3 联合干预

[v2 候选](../verification/2026-10-14-dedup-answer-boundary-candidate.md) 增加回答边界规则，并可只对首判 `SAME` 做一次八维复核；没有新增合并阈值。
后续真实采集中发生 55 次复核，其中 11 次降级：4 次拦住误并，7 次错误撤回真同义。
这是 guard 相对 v2 首判的局部影响，不能拿它直接解释 v1 与 v2+guard 的 FP 差值。
控制流、schema 和契约测试通过，也没有证明该提示词的语义质量更好。

[v3 候选](../verification/2026-10-15-dedup-source-context-candidate.md) 同时改了完整任务范围规则、输入证据和复核策略：

1. 逐端绑定原问、原始字节 hash 与 spans；来源证据编译器校验逐字内容和解码后的字符位置。
2. 邻近原文标为 `reference_only`，仅帮助解析本题对象、指代或作答形式，不能把邻题任务拼进本题。
3. 显式采用 v3 + 独立来源上下文、不启用 guard；记录首判、复核和最终结果，避免只凭最终标签猜测过程。

因此后续改善属于联合干预，不能单独归功于 v3 提示词，也尚不能断言每项改动的独立贡献。
v3 候选说明写作时尚无真实模型结果；下表来自之后的[质量优化报告](../verification/2026-10-15-source-optimization.md)，不改写前一报告的时间边界。

## 历史结果与适用条件

同一冻结参考集共 206 对：43 SAME、112 RELATED、51 DIFFERENT；来源上下文核验覆盖 45 个旧源文件。
结果绑定历史 r293 / i293 / a30，审核为 Agent，`human_verified=false`；来源已见，不能作为未见来源泛化或当前线上质量证明。

| 历史候选 | SAME P | SAME R | SAME F1 | FP / FN |
| --- | ---: | ---: | ---: | ---: |
| v1 | 80.77% | 97.67% | 88.42% | 10 / 1 |
| v2 + guard | 81.82% | 83.72% | 82.76% | 8 / 7 |
| v3 + 独立来源上下文，无 guard | 91.30% | 97.67% | 94.38% | 4 / 1 |

v2+guard 的精确率略升，但召回和 F1 下降；v3 恢复召回并减少误并，仍有 4 个误并、1 个漏并，精确率未达 95% 门槛。
v3 完成全部样本且无最终样本失败；一次请求失败后重试成功，共 207 次调用。534,781 是预算扣计 tokens，含未知 usage 预留，不能当账单。
请求模型别名相同不能证明上游权重不变；比较还需实际响应模型身份、prompt / guard 指纹、配置与数据封存记录。

## 当前边界与防复发

静态核对：[worker 装配](../../src/interview_intelligence/worker.py) 未传去重 prompt 路径；[provider 默认](../../src/interview_intelligence/dedup/provider.py) 仍是 v1，[Settings](../../src/interview_intelligence/config.py) 的 guard 默认值为 false。
v3 与上下文是显式实验能力；默认 `IngestService → DedupService.resolve` 仍只传文本。本次未核验部署环境，也未宣称历史归并已被修复。

- 改归并策略时同时比较 SAME P/R、FP/FN 和成本，保留失败与真实同义回归，不只统计拦住多少误并。
- 做来源上下文、提示词、guard 的消融，并扩展未见来源；不修改旧 gold 或拼接最好结果宣称整体通过。
- 每端证据独立认证与绑定，禁止借另一端、邻题或参考答案补齐本题条件；缺证据就保留不确定性。
- 保留首判、guard、最终标签及真实模型身份；provider/collector 可记录详细 trace，不等于 worker 数据库已持久化全部字段。
- 发布前同时过质量门与默认装配检查；局部指标改善不足以证明默认切换或线上 canonical 回填的正确性。

## 测试入口与两道自测

契约导航：[回答边界与默认兼容](../../tests/unit/test_dedup_answer_boundaries.py)、[来源认证及隔离](../../tests/unit/test_dedup_source_context.py)、[批量候选完整性](../../tests/unit/test_batch_judge.py)、[实际 resolver 归并](../../tests/integration/test_canonicalization.py)。
本页仅回读历史报告并静态核对源码，未运行这些测试或外部模型；契约通过也不能替代语义评测。

1. guard 拦住 4 个误并，为什么仍不能说 v2+guard 更好？
2. v3 的 SAME F1 达到 94.38%，为什么默认仍不应直接切换？

<details><summary>查看答案</summary>

1. 还误伤 7 个真同义，召回和 F1 下降；同时要区分 guard 对首判的影响与整套候选相对 v1 的对照。
2. P 未达门槛、参考为已见集且非人审、联合干预尚无消融，默认生产链也未接入来源证据。

</details>
