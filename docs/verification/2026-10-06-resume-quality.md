# 项目实验、交付与简历证据

记录日期：2026-10-06（Asia/Shanghai）；实验事实快照 `as_of=2026-10-05`。简历草稿见 [项目经历](../resume-project-experience.md)。

当前本地查询、统计、检索和复习功能已部署，真实实验可支持检索排序改善、查询回归改善和候选检索提速。**完整质量发布门禁为 BLOCKED**，阻断项为 extraction、task_labels、dedup、retrieval。不能将模块回归通过或工程测试通过写成完整 V1 质量通过。

## 固定事实和审核来源

- PostgreSQL / Elasticsearch / task annotation revisions 为 **293 / 293 / 30**，开发任务分类策略 `KNOWN`；生产配置默认 `VERIFIED`。
- 172 篇有效来源、185 场面试、2,771 次提问、2,452 道发布标准题。ANN 候选池包含所有 2,668 条活动向量，发布索引头 2,452 条，增量尾 216 条；两种数量口径不同。
- 查询、去重和检索参考集绑定不可变事实快照；`facts_sha256=0b8fd1b94127c90f0c0c463295c1dc8045184d73eb8475bb91f6eca88c67501a`。各冻结目录有 `freeze.json`、逐文件 hash、review provenance 和原文 span。
- 用户授权 Agent 代做标注与决策；审核数据使用 `delegated_agent`，所有整行人工标记仍为 false。抽取/分类最新参考集有 3 个用户字段裁决，单字段答复不等同整行人工审核。
- 查询、去重、抽取/分类数据已暴露给诊断过程，当前结果作为固定回归或既有输出审计。Fast Decision 的 60 条校准与 50 条独立测试按场景组分离；都是自编业务请求，不能称为采集的真实用户流量。

## 六层质量结果

入口与文件 hash 由 [证据登记](2026-10-06-evidence.json) 保存；发布配置见 [bundle](2026-10-06-quality-bundle.json)，实际拒绝结果见 [gate](2026-10-06-quality-gate.json)。

| 模块 | 数据与方式 | 核心结果 | 门禁 |
|---|---|---|---|
| 抽取 | Agent Gold v5，32 篇 / 511 题；审计既有 r293 输出 | TP 492、FP 21、FN 19；F1 **96.09%**；公司 **25/31**；L1 **86.79%**、L2 **78.46%** | BELOW_GATE：公司与主题字段不足 |
| 任务分类 | 同批 Gold，211 条：随机 200 + 边界 11；审计既有输出 | 形式 **97.16%**、焦点 **75.83%**、联合 **159/211=75.36%**；随机 **148/200**、边界 **11/11** | NOT_RUN：门槛未配置，不能视为通过 |
| Dedup Judge | 204 对来源题和 hard negatives；同一修正 Gold v6 | 默认 v6 SAME P/R **89.29% / 96.90%**；可选 v8 **91.60% / 93.02%**，TP 120 / FP 11 / FN 9 | BELOW_GATE：默认版本 Precision < 95%；v8 也不足且未启用 |
| Retrieval | 55 查询：50 正例 + 5 负例；四路真实消融 | Hybrid + Rerank Recall@10 **83.03%**、NDCG@10 **0.9250**；负例空结果 **5/5** | BELOW_GATE：Recall < 85% |
| Routing / trajectory | 同一 Gold v5 的 50 场景；62 次非重放请求及一次幂等重放 | v4 **42/50** → v6 **49/50**；工具选择 **100%**；参数 **99.6%**；未知断言 0、越权写 0 | PASSED：固定查询回归 |
| Exact SQL | 20 组完整范围与全分页；独立导出事实 oracle | 排序、计数、覆盖及唯一性全部通过；模型调用 **0** | PASSED：当前数据库范围 |

抽取与任务分类这轮没有新增模型推理，报告的毫秒是读取既有输出的本地耗时，不能当作模型推理延迟。公司错误中包括别名归一化差异，需与雇主识别错误分开分析。整篇漏抽和 false positive 都保留在分母中。

## Retrieval：可重复的质量改善

| 路径 | Recall@10 | NDCG@10 |
|---|---:|---:|
| BM25 | 62.78% | 0.6184 |
| Dense | 81.25% | 0.8831 |
| RRF Hybrid | 74.65% | 0.7781 |
| Hybrid + Rerank | 83.03% | 0.9250 |

相对 Hybrid，NDCG 绝对增加 **0.14695**、相对增加 **18.88%**；Recall 增加 **8.38 个百分点**。Dense 强于裸 Hybrid，不能声称 RRF 在所有场景胜出。Rerank 也超过 Dense，但质量差距小于相对 Hybrid 的差距。

同一 query embedding 在顶层只记录一次，四路共享索引、筛选范围和事实版本。Rerank 对 Top 50 候选严格验证完整 ID 集及相关性枚举，Ark 输出关闭 reasoning、限制 token。原采集因 embedding 将未知输出按大额预算预留，13 条被预算拒绝；修复向量调用的输出 token 口径后，只重跑同一冻结集中的失败条目，原失败记录保留。全实验账本有 **111 次实际调用尝试**；最终 55 条预测观测合计 **110 次模型调用**。

这是恢复后的两个串行批次。Rerank 阶段 P95 **12.24 秒**，包含 query embedding 的完整四路采集 P95 **15.14 秒**；不能把两个批次的延迟称为单次连续负载实验，也不能用该结果声称 Rerank 降延迟。未配置货币单价，`estimated_cost=null`，不是零成本。

relevance 标注池不完备，未审核结果按零分处理；报告记录 Rerank 排名中的未审核项 150 次。当前 50 个正例集中在指定主题族，泛化范围有限；扩池后必须冻结新版本，不能把不同 Gold 的分数差当成模型提升。

## Harness：实现边界和回归

Context Compiler 固定投影可信 QueryState、最近 2 条消息（各最多 800 字符）及最近 8 个工具摘要，序列化主体限制 24,000 UTF-8 bytes。保留当前页最多 100 个完整题目 ID；签名游标由主机保存，模型只看到是否有下一页。若可信状态本身超限则明确失败，不截断授权或筛选事实。

动态 Tool Policy 根据状态开放列表、搜索、统计、详情、翻页和写工具；主机在实际调用时重新核验。写入要求当前请求明确授权和当前展示范围，引文、否定或过去消息不能授予权限。工具的权威完整结果由主机返回，模型仅见有限预览；事务性 action ID 与请求幂等阻止重放重复写入。

同 Gold 的 v4 → v6 回归为 **84% → 98%**；不是 Compiler 单独消融。修复涉及 prompt 的任务焦点 / 作答形式区分，以及要求模型返回工具调用的协议。仍有一条“我想练工程代码实现，请给我题目清单”遗漏 `response_form=CODE`。查询成功依据规划、工具、状态、写入事件及 SQL 结果断言；SQL oracle 使用当前机器标签，因此 98% 不能证明分类本身正确。

v6 观测 70 次模型调用、已知输入 / 输出 tokens **432,130 / 16,205**，P50 **6.50 秒**、P95 **19.07 秒**。v4 P95 约 **13.03 秒**。本轮没有查询延迟或 token 节省的正面结论。

## Dedup ANN：性能证据与 Judge 分开

三个路径使用同一组 2,668 条已缓存向量、seed `20261005`、60 条查询向量：

| 候选路径 | P50 | P95 |
|---|---:|---:|
| Python 全量精确扫描 | 259.96 ms | 330.11 ms |
| NumPy 向量化精确扫描 | 56.52 ms | 223.41 ms |
| HNSW 头 + 精确增量尾 + 精确排序 | 39.72 ms | 114.17 ms |

ANN 相对 Python 候选扫描 P95 减少 **65.42%**，相对向量化路径减少约 **48.90%**；相对完整精确 Top 10 的候选 Recall **1.0**。包含 ANN HTTP 与每轮内存计算，所有路径都不含前置批量数据库向量读取；索引头冷刷新另测 **457.20 ms**。仅一个真实语料、单 worker、热缓存，未测万级规模、新原文 paraphrase 或完整导入链路。

候选实现固定物理索引 / PIT，检查文本 hash 和 embedding version；未入索引、变化、当前事务及缺失项走精确尾扫描，候选合并后二次精确排序。索引故障回退精确路径。`auto` 的 ANN 阈值为 10,000，当前 2,668 条默认走向量化精确；ANN 是显式离线探针验证，不能写成当前所有线上查询已启用。

Judge v8 是对 v6 首判 SAME 的可选范围审核，204 次首判加 140 次真实复核，共 **344 次调用**。同一 Gold 上 P 提高、R 下降，仍未过 95% 精度门槛；`DEDUP_VERIFY_EQUIVALENCE=false`，实验分支未默认启用。该报告没有候选 SAME 召回或完整 dedup 端到端召回。

## Fast Decision：独立校准的否定结论

Laya 中文 20 条开发探针完整关键字段正确 **1/20**。Qwen3-1.7B 私有部署固定官方 model revision，有限类型接口校验输出范围；60 校准 / 50 独立测试直接编写期望 QuerySpec，与 decoder 实现分开，文件先封存后推理。当前 host decision version 为 `jev_query_v5_state_bound_paging`，协议 `typed_json_v1`。

| 数据 | 完整类型化决策正确 | Provider 失败 | 路由 P95 | 实际调用 |
|---|---:|---:|---:|---:|
| 独立校准 60 条 | 18/60（30%） | 23 | 5.51 s | 60 |
| 独立测试 50 条 | 10/50（20%） | 16 | 5.46 s | 50 |

失败包含原生 JSON 或有限选项校验拒绝，保留在整体分母。置信度 0.99 接收的 20 条校准预测有 12 条错误；0.995 接收 3 条、错 2 条，更高阈值覆盖率为零。没有阈值同时满足至少 20 条接受与零观察错误，因此 artifact 状态 **NOT_ELIGIBLE**，正式 Jev 关闭；零接受时 precision 是 null，不写 100%。该准入规则是观察性门槛，不是未来零错误保证。

calibration artifact 绑定模型 revision、provider、decision contract hash、服务代码 hash、协议与 grammar hash；服务自身报告“已校准”不能代替主机审核。测试集不用于重新拟合阈值。60 + 50 次调用已知 tokens 分别 **48,574 / 40,454**。这些是含网络和共享串行门的 router 阶段耗时，完整查询对照另记，不宣称当前有大模型调用节省。

### 完整查询链路对照

另从已见回归集固定 10 个单轮场景，覆盖任务类别、重要度 / 短板排序、未支持公司、主题 Top N、统计及缺少分页状态。先运行关闭快模型的 Pi 基线，再在全新隔离账号启用快速模型调用与准入失败回退；两轮共 20 次真实查询，无传输失败。该集是小样本诊断，未混入 50 条独立测试，也不作为发布集。

| 完整查询 | 场景成功 | Frontier 调用 | 小模型调用 | 查询 P95 |
|---|---:|---:|---:|---:|
| 直接 Pi | 9/10 | 10 | 0 | 7.48 s |
| Qwen 决策 + Pi 回退 | 9/10 | 10 | 10 | 10.10 s |

6 条因缺少合格校准回退，4 条原生输出 / 选项校验被 provider 拒绝后回退；接管率 0，Frontier 调用节省 **0**。配对查询耗时的中位增量 **3.59 秒**，总模型调用由 10 增至 20；同一个工程代码筛选失败保留在两轮分母。由此决定正式入口继续关闭快速模型。P95 是每轮 10 个场景的 nearest-rank（即该轮最大值）；顺序运行而非随机交错，不能当生产容量或普遍延迟收益。原始请求、模型 telemetry、逐例对照与 hash 见登记中的 `fast-e2e.summary.v1.json`。

重新采集须生成新的输出路径、两个全新 `eval-` 前缀账号。通过 `QUALITY_USER_ID` 和 `QUALITY_JEV_ENABLED=false/true` 依次启动 `compose.quality.yaml`，只向专用评测端口发请求；不得使用真实用户 API 采集带状态的对照。集可用 `python -m evals.project_quality.build_fallback_gold --parent data/gold/project-quality-agent-20261005-v5 --output data/gold/fast-fallback-e2e-rebuild-new` 再生成，`eval.collect --split dev --review-policy delegated_agent --adapter http --max-calls 60 --max-tokens 2000000` 采集，再分别 `eval.run` 和 `eval.compare`。

## 工程交付和可复现入口

完整工程测试上一轮 **359 passed / 1 skipped**；真实 ES 默认测试未启用，另有 60 次真实 ANN 索引探针。固定 Pi runtime 7 项、前端 9 项通过。工程测试与质量评测分别报告。

2026-10-06 本地 API、worker、Pi 镜像构建并重建成功，API / PostgreSQL / Elasticsearch / Pi 健康；开启 Context Compiler 和 Dynamic Tools，Fast Decision 及可选 dedup guard 关闭。没有重导语料或发布新分类，r293 / annotation 30 保持。构建时 WSL 默认 bridge DNS 失败，使用仅针对 build 的 `build.network=host` override 成功，已保留为可选 [构建配置](../../compose.build-wsl.yaml)；未改 Docker 全局 DNS。Dockerfile 将依赖锁文件与 pyproject 分层，避免 prompt 打包调整使依赖安装缓存失效。部署收据的 8 个打包文件 hash 已与工作区逐一核对一致。

```powershell
$env:PYTHONPATH = "$PWD\src;$PWD"
# 质量门禁预期返回 BLOCKED，进程退出码 1。
.venv/Scripts/python.exe -m eval.release --bundle docs/verification/2026-10-06-quality-bundle.json --output docs/verification/2026-10-06-quality-gate.json
# 只使用校准预测拟合阈值；test 仅报告，不改变已封存 artifact。
.venv/Scripts/python.exe -m evals.project_quality.calibrate_fast --dataset data/gold/fast-decision-agent-20261006-v2 --calibration data/reports/resume-quality-workbench-20261005-v1/qwen.calibration60.v5.json --test data/reports/resume-quality-workbench-20261005-v1/qwen.test50.v5.json --artifact data/reports/resume-quality-workbench-20261005-v1/qwen.calibration.v5.json --report data/reports/resume-quality-workbench-20261005-v1/qwen.validation.v5.json
```

六层采集、Gold 校验与评分命令见 [评测手册](../../eval/README.md)。真实模型调用必须从 Linux 容器加入 `/app/runtime/model-call.lock`，使用显式调用 / token 上限，实验串行运行；Windows 源码测试不发起并行真实模型调用。原始报告在被忽略的 `data/reports/`，本文件、登记 JSON 与 Gold 留在版本控制范围。

当前 GitHub CI 验证工程测试与合成评测框架；真实质量检查是上述本地 `eval.release` 命令，已实际返回退出码 1。它尚未接入自动部署工作流；本轮是开发服务更新，不是已获准通过质量门槛的 V1 正式发布。

## 当前不足和历史问答记录

已有历史执行与问答可读，曾只读核对 54 条执行记录、23 个会话，记录 H01 概念扩展与偏题、H02 历史浏览入口缺失、H03 反馈与原问答关联不足。见 [issue](../../md/issue.md) 及 `data/feedback/`。54 条中包含结构化操作与验收，不能当成纯用户问答准确率；目前未实现完整历史浏览 UI。

后续优先修整篇漏抽、推广汇编排除、工程实现分类误报，再扩充检索判断池和去重完整链路；为查询保留新场景做泛化检验。Fast Decision 保持关闭，直至新的模型 / 训练策略在独立校准及测试上产生有效接管覆盖，并通过真实完整链路对照。完整项目质量门禁的阻断是实验结论，不能通过降低门槛或删失败样本消除。
