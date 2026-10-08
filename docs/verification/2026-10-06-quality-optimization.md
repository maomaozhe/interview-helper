# 2026-10-06 质量优化验证

本轮修复了抽取和任务分类的执行缺陷，补充检索判断池与去重完整链路。完整项目质量门禁仍为 **BLOCKED**，Fast Decision 继续关闭。原有失败样本、未通过实验和旧版报告全部保留，未降低抽取、去重、检索或查询门槛。新增任务分类门槛在来源留出预测前封存。

所有实验沿用 corpus / index / annotation **293 / 293 / 30**，as_of 为 2026-10-05。Gold 由 Agent 按原文审核，`human_verified=false`；不能称为大规模人工 Gold。实验输出未覆盖已发布题库或任务标签。历史报告见 [初轮验证](2026-10-06-resume-quality.md)。

## 抽取：恢复整篇正文，保留语义失败

`extract_question_v4` 在 schema 中使用完整闭集 `topic_id`，由主机还原两级主题；显式关闭本轮 DeepSeek 抽取模型的思考模式，避免隐藏推理占满输出预算。正文覆盖、推广汇编资格和独立任务边界进入提示词。标题问题被拒绝，发布契约校验进入同一三次有界重试。AI 知识类型沿用既有域规则，5 次主机修正保存原模型类型；其他具体任务类型不被改为 AI。

同一 32 篇、511 题开发回归的实测如下，两版模型均为 `deepseek-v4-1-flash-260910`：

| 指标 | v3，关闭思考 | v4，闭集主题、关闭思考 |
|---|---:|---:|
| 校验失败文档 | 5 / 32 | 0 / 32 |
| TP / FP / FN | 363 / 18 / 148 | 460 / 29 / 51 |
| Precision / Recall | 95.28% / 71.04% | 94.07% / 90.02% |
| F1 | 81.39% | 92.00% |
| 排除负文档 | 2 / 2 | 2 / 2 |
| 实际模型调用 | 48 | 32 |
| 文档耗时 P95 | 34.24 秒 | 10.19 秒 |

原整篇漏抽来源 `236846725417` 已恢复全部 **19 道**正文问题；两个汇编负例均返回 COMPILATION、零问题。原先只抽标题的结果未被作为正确答案保留。v4 的 32 篇都一次完成，但仍有 **51 个 FN**：多题合并和真实漏题继续计入分母。人工代理对齐只接受同一来源、同一提问的一对一对应，不将一条合并预测拆成多个 TP。

语义字段仍阻断：公司 **25/31，80.65%**，轮次 **75%**，匹配题目的 L1 / L2 **78.26% / 71.09%**，空值字段误填 **1 次**；追问为 0 TP / 2 FP / 3 FN。闭集保证类别合法，并不保证分类正确。另核对两版共同匹配的 **363 题**，L1 正确从 **331 降至 281**，L2 从 **288 降至 257**，题型从 **271 降至 254**，存在真实语义回归，因此新版保持实验配置，不默认启用。整体改动同时包含 prompt、类型政策和输出配置，不能单独归因于闭集 schema。这是已见开发回归，一次顺序运行不证明普遍降延迟。初轮已有输出由其他模型产生，不能当作本次 prompt 单因素基线。

复核入口：[v4 全部预测](../../data/reports/quality-optimization-20261006/extraction.v4-closed-topic-full.jsonl)、[独立对齐裁决](../../data/reports/quality-optimization-20261006/extraction-v4-alignment/alignment.decisions.json)、[版本差异](../../data/reports/quality-optimization-20261006/extraction.v3-disabled-to-v4.diff.json)、[共同匹配题字段核对](../../data/reports/quality-optimization-20261006/extraction.common-matched-attributes.json)。先前抽取适配器错误导致的中止、思考模式截断、非法主题失败和连接失败探针也保存在同一目录。

## 任务分类：误报下降，工程精度仍不合格

将“现有机制 / 项目怎么工作”与“构造具体组件 / API”分开，作答形式独立判断。`task_context_v1` 从不可变原文 span 编译有界前后文和代码环节标题；校验来源 SHA、revision 与逐字引用，避免将已归一文本中的推断再次用作证据。v4 和 v6 都使用同一上下文和批次协议。

另冻结 **45 个未见来源的 200 条**参考标签，排除旧参考集来源、相同归一文本和 canonical。未见指相对旧诊断集的来源不相交；仍来自同一语料和业务，并非跨领域或自然流量泛化。

| 数据集 | v4 联合正确率 | v6 联合正确率 | v4 工程 FP | v6 工程 FP | v6 严格工程 Precision |
|---|---:|---:|---:|---:|---:|
| 已见 211 条回归 | 134/211，63.51% | 205/211，97.16% | 72 | 3 | 14/17，82.35% |
| 200 条来源留出首次测试 | 121/200，60.50% | 192/200，96.00% | 74 | 3 | 3/6，50.00% |

两组 v6 的严格工程 Recall 均为 100%，已知标签覆盖分别为 99.53% / 98.48%；失败、UNKNOWN 和全部原始样本仍计入联合正确率。工程 Precision 没有达到预先固定的 **90%**，不能因总体 96% 放行。来源留出只有 **3 条工程正例**，其中工程代码只有 2 条，不能将这个子集的全对扩展为工程筛选可靠。

MIXED 纳入工程 / 算法业务筛选的辅助指标另行计算：211 条回归的工程筛选 Precision 为 88.24%，仍不足 90%。这个指标没有替换严格类别门禁。批次恢复只针对格式、ID 和引用校验失败，网络 / 预算失败不拆批；逐次计费，整批通过校验才写标签。四组正式对照均未触发拆批恢复，不能把成绩提升归因于这一恢复机制。新的模型标签未发布成 VERIFIED，也未批量覆盖旧分类。

复核入口：[回归差异](../../data/reports/quality-optimization-20261006/regression.task-context.diff.json)、[留出差异](../../data/reports/quality-optimization-20261006/holdout.task-context.diff.json)、[冻结留出集](../../data/gold/task-generalization-agent-20261006-v2/manifest.json)。首次结果保留；后续针对这 200 条修复后的重测只能称为已见回归。

## 检索：完整裁决四路 Top 10，更新结论

在已有 55 查询和同一四路预测上新增 **828 条** query / canonical 相关性判断：556 条不相关、249 条邻近或较宽相关、23 条直接相关。审核依据包含全部来源变体和歧义上下文，共绑定 141 个不可变来源。原有 relevance 不改写。四路 Top 10 共出现 517 / 550 / 550 / 292 条结果，未裁决项均为 0；更深排序和池外相关项仍未穷尽。

| 路径 | 扩充池后的 Recall@10 | NDCG@10 |
|---|---:|---:|
| BM25 | 48.33% | 0.6013 |
| Dense | 69.04% | 0.8522 |
| RRF Hybrid | 65.49% | 0.7733 |
| Hybrid + Rerank | 60.29% | 0.8488 |

四路都未达到 85% Recall 门槛。当前池中 Rerank 的输出截断 / 过滤损失了较宽相关项，不能继续将旧池的“Rerank NDCG 0.925、较 Hybrid 提升 18.9%”作为当前质量成果。原实验继续留证；新旧 relevance 分母不同，不能把重新评分差额当作同 Gold 上的模型退化。新增判断属于看过排序后的诊断审核，不是未见查询测试。

复核入口：[新增判断](../../data/gold/project-quality-agent-20261006-v7/pool_reviews.jsonl)、[Top 10 判断覆盖](../../data/reports/quality-optimization-20261006/retrieval.top10.coverage.json)。重评分没有新增模型调用。下一轮需向池外补查相关项，检查候选召回、重排过滤和 hard negatives；不能通过降低发布 Recall 门槛解决。

## 去重：新输入向量至 resolver 的完整路径

使用预测前封存的 **20 个已审核开发题对：12 SAME / 5 RELATED / 3 DIFFERENT**。每条重新生成 incoming embedding，在实际 2,668 条活动候选中运行精确和强制 HNSW + 精确增量尾，两条路径各调用真实批量 Judge 与生产 resolver。为避免题对 ID 混淆及代表文本偏差，将 occurrence ID 映射到当前 canonical，在回滚事务中用已审核右侧原文代表目标；两端 canonical 不同时临时移出左侧。目标原文没有缓存向量时另准备共享向量，11 次调用单独计数。

| 指标 | 精确 | HNSW + 精确增量尾 |
|---|---:|---:|
| 执行失败 | 0/20 | 0/20 |
| 指定 SAME 目标召回 | 12/12 | 12/12 |
| 指定 SAME 目标被 Judge 判 SAME | 10/12 | 10/12 |
| 合并到指定目标 | 8/12 | 8/12 |
| 多个 SAME 导致 NEW / 待审核 | 2/20 | 2/20 |
| incoming 至回滚完成 P95 | 11.969 秒 | 11.678 秒 |

总计 **71 次**真实模型调用：20 次 incoming embedding、11 次目标向量准备、40 次批量 Judge，已知用量 62,047 tokens。完整路径耗时包含 incoming embedding、数据库读取、候选缓存、搜索、Judge、flush 与回滚；不含单列的目标准备和冷 head 初始化。路径顺序固定为精确再 ANN，这 20 条不能证明整个导入链路的普遍提速。

候选召回之后仍有 Judge 漏判和多 SAME 歧义，说明原 60 条缓存 ANN 探针的 100% 近邻一致性不能代表去重成功。其他候选题对未逐条审核，整体 SAME Precision / semantic success 保留为 null。204 对默认 Judge 参考集仍为 **89.29% Precision <95%**，质量阻断没有被这个开发链路替代。

所有事务回滚，活动候选文本 hash 前后一致；canonical 总数 2,668、embedding 总数 3,852 均未改变，发布新增为 0。这是替换目标代表文本的 shadow replay，不是未经改动的生产候选池或自然新输入测试。最初两个预检中止均保留，未删除题对或修改类别。

复核入口：[冻结完整链路协议](../../data/reports/quality-optimization-20261006/dedup.full-chain.source-pair-v2.protocol.json)、[全部路径结果](../../data/reports/quality-optimization-20261006/dedup.full-chain.source-pair-v2.jsonl)、[调用、回滚与计时凭据](../../data/reports/quality-optimization-20261006/dedup.full-chain.source-pair-v2.collection.json)。

## 查询：新组合首测 86%，继续保留未运行场景

预测前封存新的 **50 场景、70 轮**，覆盖交叉筛选、UI 条件与默认页大小、字段清除 / 继承、分页边界和多个序号指代。专用账号初始会话、复习状态、业务事件均为 0；结束时 50 个会话、复习状态和业务事件仍为 0。Fast Decision 关闭，真实 Pi 路径共 **88 次**模型调用、578,182 个已知 tokens。

严格 Task Success 为 **43/50，86%**，低于保持不变的 **90%** 门槛；工具选择 **97.14%**，参数字段 **97.77%**，完整计划 **92.86%**，UNKNOWN 断言与越权写入均为 **0**。50 个场景全部执行完，执行状态成功不能替代业务断言。场景整体 P95 为 **20.134 秒**，含多轮，不是单次路由或单次模型 P95。

全部 7 个失败保留：2 个列表请求被路由为 SEARCH / STATS；2 个口述范围场景擅自添加 `coding_focus=NONE`，并在取消回答形式后继续继承；3 个场景只因语言参数 `Java` 与冻结期望 `JAVA` 的严格表示不一致而失败，返回 ID 本身符合期望。独立事实 oracle 在使用实际计划的 NONE 筛选后与 SQL 返回一致，计数偏差来自规划器收窄范围，不是更改 Gold 以匹配结果。

旧 50 场景回归 49/50、98% 与本次 86% 使用不同数据，不能直接算模型退化幅度，也不能只报旧回归成绩。新组合仍是同一产品契约和语料上的自编场景，不是未见真实用户流量。分类质量另外测试，SQL 结果正确不表示其机器标签正确。

本次结果已观察，下一轮针对它修复后的重测归为已见回归。另冻结 **50 个新组合、62 轮**备用集，目前没有采集预测、模型调用为 0；它根据本轮缺口设计，不宣称语义家族或真实流量独立性。最初 20 条备用草稿未达到既有 50 条最低规模，被正式校验拒绝，失败版本保留；后续版本增加 30 个作答形式 / 任务焦点独立组合，未降低最低规模。

复核入口：[首测协议](../../data/reports/quality-optimization-20261006/query.generalization.protocol.json)、[所有预测](../../data/reports/quality-optimization-20261006/query.generalization.jsonl)、[7 个失败诊断](../../data/reports/quality-optimization-20261006/query.first-run.diagnostics.json)、[未运行备用集](../../data/gold/query-generalization-reserved-agent-20261006-v3/manifest.json)。

## 发布门禁、工程验证与下一轮

[v3 bundle](2026-10-06-quality-bundle-v3.json) / [可执行结果](2026-10-06-quality-gate-v3.json) 为 **BLOCKED**：抽取、任务分类、去重、检索、新查询及必须通过的 211 条分类回归均阻断；完整 SQL 20 组通过。任务分类门槛在留出预测前设定，其他模块门槛与 v2 完全相同。发布检查校验 Gold / 预测 hash、快照和门槛版本；新来源通过也不能绕开声明为必过的旧回归。历史 v2 文件和全部失败仍保留。

本轮 Python 全量 **380 通过、1 跳过**，跳过项为需单独启用的真实 ES 测试；真实 ES 已用于本轮候选与完整链路实验。Pi / 前端未改动，本轮沿用此前 16 项通过记录。API / worker / Pi 镜像已重新构建，仅部署通用校验、恢复和配置支持；抽取 v4、任务分类 v6 + source context 没有被设为默认，旧题库和任务标签未重新发布。没有启用快速决策或可选 dedup guard。

下一轮先处理抽取主题与题型混淆、元信息别名 / 空值、独立问题边界；扩充工程正例及相似机制负例，重新冻结来源留出；修复查询的额外焦点、动作选择和参数规范化，先跑原 50 回归，再一次性使用备用新组合。检索继续补池外相关项并检查重排过滤，去重继续审核完整路径中的其他候选。Fast Decision 只有新模型 / 训练策略在独立校准和测试取得有效覆盖、并通过真实链路对照后才重新考虑启用。

简历措辞已更新至 [项目稿](../resume-project-experience.md)，不使用旧判断池的 Rerank 提升作为当前主成果。版本、原始路径和 hash 登记见 [本轮机器证据](2026-10-06-quality-optimization.evidence.json)。

复核门禁，预期退出码为 1：

```powershell
python -m eval.release --bundle docs/verification/2026-10-06-quality-bundle-v3.json --output data/reports/quality-gate-v3-recheck.json
```
