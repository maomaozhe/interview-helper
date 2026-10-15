---
id: PIT-006
status: current
resolution: open
resolution_scope: source-eligibility-and-task-semantic-quality
scope: historical-report-and-workspace
verified_at: 2026-10-15
runtime_verified: false
tags: [source-eligibility, classification, false-reject, annotation-policy, semantic-quality]
sources:
  - path: md/issue.md
  - path: docs/verification/2026-10-01-import-rag-smoke.md
  - path: docs/verification/2026-10-15-source-optimization.md
  - path: docs/annotation-guide.md
  - path: docs/decisions/2026-10-05-query-state-and-annotation-policy.md
  - path: src/interview_intelligence/contracts/__init__.py
    symbols: [ExtractedQuestion, ExtractionResult]
  - path: src/interview_intelligence/config.py
    symbols: [discover_corpus_documents, resolve_corpus_document, Settings]
  - path: src/interview_intelligence/ingestion/pipeline.py
    symbols: [IngestService._materialize, IngestService.ingest_file]
  - path: src/interview_intelligence/agent/task_annotation.py
    symbols: [annotate_tasks]
tests:
  - tests/unit/test_config.py
  - tests/unit/test_extraction_contract.py
  - tests/integration/test_ingestion_lifecycle.py
  - tests/integration/test_task_annotation.py
  - tests/unit/test_task_annotation_v8.py
  - tests/unit/test_task_context.py
---

# PIT-006：引用和枚举合法，仍可能把错误内容变成事实

[返回索引](README.md) · [标注指南](../annotation-guide.md)

## 30 秒回忆

- 真实提问必须有具体面试发生证据；有元数据不保证真实，缺公司日期也不意味着没有真实场次。
- 主题、实际任务、作答形式和编码焦点不同；不能看到 AI 或旧 ALGORITHM 标签就替代来源判断。
- 局部资格修正已有报告，但误拒、任务政策冲突和跨集质量仍开放；结构零错误不能关闭这些语义问题。

## 合并记录与原始证据

原始问句、来源名、调用和回执保留在[问题日志](../../md/issue.md)，本篇只保留可复用机制。

| 原编号 / 时间 | 原问题及归档位置 |
| --- | --- |
| #10 | 「问题记录会污染面经总数」：扫描边界问题，是确定性入口保护，不能泛化为模型误判 |
| #13、#25 | 「跨多次面试的经验汇总被算成具体场次」「两条早期排除记录缺少具体原因」 |
| #18、#31 | 「‘反问’环节标题被抽取为面试官问题」「混合项目帖中的真实提问因元数据缺失被排除」 |
| #33、#41 | 「AI 领域被误当成提问类型」「Schema 合法仍可能违背实际任务类型」 |
| 2026-10-02 15:30、17:51、20:17；2026-10-03 00:30、02:30、02:55、15:17、16:30、17:04、20:43、23:55 | 原文有真实问题却返回无场次/无有效问题；同类误拒按机制归档，不复制私人标题 |
| 2026-10-03 21:10；2026-10-04 02:44 | 用户选择跳过反复失败来源，以及后来终点的剩余未导入范围 |
| Q02「‘手撕代码’与‘算法题’混淆，旧标签不能直接用于新需求」 | 新任务维度的建模动机；查询侧使用问题另见查询专题 |
| [2026-10-15 来源优化](../verification/2026-10-15-source-optimization.md)的分类段 | 新集改善、旧集退步与政策冲突的后续证据 |

这里按资格和标签的语义政策组织材料，仍保留“扫描入口、场次/问题资格、任务分类”三种不同机制；它们不是一个正则或提示词能全部修复的同一缺陷。
输出截断和 ID/quote 结构错误见 [PIT-004](2026-10-15-extraction-output-contract.md)；来源分类正确而展示过期见 [PIT-007](2026-10-01-canonical-display-drift.md)。

## 已证实原因与仍未知的解释

| 机制 | 已有证据 | 未获支持的推论 |
| --- | --- | --- |
| 扫描入口污染 | `md/issue.md` 被通用 Markdown 扫描纳入 | 所有 Markdown 都是业务语料，或排除一个文件等于完整准入治理 |
| 汇总误计场次 | 跨次经验题集被虚构成具体面试；缺少逐题场次归属 | 标题带“一面”或问题像面试题就能证明实际发生 |
| 混合帖误排除 | 模型把公司/日期等元数据齐全当作必要条件 | 元数据 null 就应丢弃原文明确回忆的实际提问 |
| 环节标题误收 | quote 逐字匹配，但只是候选人反问环节，无具体问题 | 逐字证据自动证明 `INTERVIEW_QUESTION` 的资格 |
| 任务标签错误 | AI 领域覆盖了设计/故障等真实任务；合法枚举未违反 schema | 合法类型值即正确，或 schema 能自动理解任务政策 |
| 来源误拒 | 人工核对有题，抽取结果却没有有效问题 | 已确定仅由 OCR、emoji 编号或短文造成 |

日志曾怀疑 OCR 与 emoji 编号，之后标准数字编号短文也误拒；这削弱了单一格式解释，未构成完整根因实验。
某些条目的“重试通常可解”是当时经验判断；后续多次失败和跳过记录说明不能用它保证恢复。

## 实际处理时间线与失败尝试

| 阶段 | 实际做法与结果 | 不能越过的边界 |
| --- | --- | --- |
| 2026-10-01 #10 | 先复现清单多算操作文档，再统一发现/显式导入入口排除保留文件 | 只覆盖明确规则，不能自动识别所有未来操作文档 |
| 2026-10-01 #13 | 正常抽取重跑汇总来源，确认 `COMPILATION/EXCLUDED`、零场次后原子发布 | 排除的是频率事实资格，不是判断内容没有学习价值 |
| 2026-10-01 #16 → #18 | 引用修好后仍误收环节标题；发布前回滚，补共享资格契约后重跑 | 前一步成功不是整篇正确；不手工删缓存伪造模型产物 |
| 2026-10-01 #31/#25 | 新提示词重跑混合来源及缺理由的排除记录；真实片段纳入、元数据留空、汇总仍排除并补理由 | 不为补理由把汇总题混入频率，也不把作者拟讲内容当发生事实 |
| 2026-10-01 #33 | 仅枚举约束不足，补任务优先级；正常抽取、归并、发布和索引后八项来源对照通过 | 局部来源修正不能证明所有旧标签正确 |
| 2026-10-01 #41 | 又发现合法枚举内的故障任务误标，先复现独立断言再排队重处理 | 202/QUEUED 不是修复完成；开发对照不是冻结金标 |
| 2026-10-02～03 | 多个“无有效问题”误拒重处理，有些恢复、有些持续失败；后来用户要求停止反复重试 | 不能把同一文件不同尝试相加为新样本，不能无限重排 |
| 2026-10-04 Q02 | 增加 occurrence 层 `response_form / coding_focus`，保留旧题型和 canonical 身份 | 不从旧 ALGORITHM 批量推导工程/算法新标签 |
| 2026-10-15 | v8/v9 在新来源集改善，但旧回归仍失败；继续全文审核政策差异 | 不因一个新集较高分就更换默认或修改旧 gold |

历史 10-03 跳过清单曾为 11 篇，10-04 终点仍有 6 篇未导入；这是不同时间范围，不相加或覆盖原失败。
“所有来源已注册”和“队列清零”也不等于每篇成功形成有效事实；剩余跳过必须独立报告。
当时许可的有限重处理和跳过策略是该次运行处置，不自动成为所有未来任务的重试授权。

## 当前已有防复发措施

- [语料入口](../../src/interview_intelligence/config.py) 对根 `issue.md` 做共享发现和显式导入拒绝，API、清单与导入复用该边界。
- [ExtractionResult](../../src/interview_intelligence/contracts/__init__.py) 要求排除理由；`ExtractedQuestion` 拒绝裸反问标题并约束部分 AI 知识类型政策。
- [IngestService](../../src/interview_intelligence/ingestion/pipeline.py) 只物化纳入构建的有效提问；`UNKNOWN` 或无有效问题的混合构建保留复核，不切旧 active build。
- [标注指南](../annotation-guide.md) 区分事件资格、原子问题、主题、任务、作答形式与焦点；未知元数据保持 null。
- [独立任务标注](../../src/interview_intelligence/agent/task_annotation.py) 绑定 occurrence 与证据，低置信度/未知值保留 UNKNOWN，机器明确分类为 NEEDS_REVIEW。
- [分类政策决策](../decisions/2026-10-05-query-state-and-annotation-policy.md) 和 Settings 默认 `VERIFIED`；开发 `KNOWN` 必须显式使用并提示未核验。

上述确定性规则只能约束已表达的契约，不能普遍识别所有合乎 schema 的错误语义；也没有自动把机器结果变成人审。

## 最新历史证据与待补措施

[来源优化报告](../verification/2026-10-15-source-optimization.md)绑定旧 r293 / i293 / a30，Agent 审核、`human_verified=false`，来源已见。
同一 538 条任务中，v7/v8/v9 联合正确率为 96.28% / 97.96% / 98.14%；v9 strict ENGINEERING P/R 均为 94.12%。
但 v9 在旧 211、已见来源 200、工程挑战 200 的 strict ENGINEERING 精确率分别只有 57.89%、21.43%、40.74%。
新集模块过门不能覆盖旧回归；业务工程筛选包含 MIXED 的口径也不能替代 strict ENGINEERING 门槛。
独立复核旧 42 个工程错例，区分 24 个真实误判、16 个政策冲突、1 个上下文缺失、1 个未遵守用户裁定；不是把全部旧错例改标签来提高分数。
因此 `resolution=open`；默认任务回填版本仍由装配参数决定，当前 `annotate_tasks` 默认 v4，v8/v9 只是可显式选择的候选。

待补：先裁定政策冲突并保留双方证据，再增加未见来源、短文/OCR/编号格式和混合帖覆盖；用固定一对一对齐区分误拒、漏题和拆并错误。
参考标注修订、已见回归与不同分母的比较边界见 [PIT-017](2026-10-06-evaluation-reference-drift.md)，不得用重写参考消除真实退步。
回补旧失败来源应有独立输入清单、预算、失败保留和语义审核，不能把重试成功率当成抽取召回率。
再次排查时先问“真实发生了什么任务”，再查字段合法性、场次归属与标签政策，最后检查展示；不要反过来从旧标签推断原文。

## 测试入口与自测

| 契约 | 测试入口 |
| --- | --- |
| 保留文件和显式入口 | [test_config.py](../../tests/unit/test_config.py) |
| 标题、排除理由和有限类型约束 | [test_extraction_contract.py](../../tests/unit/test_extraction_contract.py) |
| 未知重抽保留旧发布 | [test_ingestion_lifecycle.py](../../tests/integration/test_ingestion_lifecycle.py) |
| 标签上下文、候选默认隔离与证据 | [test_task_context.py](../../tests/unit/test_task_context.py)、[test_task_annotation_v8.py](../../tests/unit/test_task_annotation_v8.py)、[test_task_annotation.py](../../tests/integration/test_task_annotation.py) |

本次未运行测试、模型或线上核验；历史结构/契约通过与语义质量是否通过分别陈述。

1. 一个问句有逐字 span、类型也在枚举内，为什么还可能不能计入频率？要点：它可能是标题、作者拟讲内容或没有具体场次的汇总。
2. 新集标签更准，为什么不直接替换默认？要点：要核对同一政策、旧回归、未裁边界和未见来源；不能换统计口径或改旧 gold 消除退步。
