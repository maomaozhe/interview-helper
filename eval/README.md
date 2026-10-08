# 评测操作手册

搭建规范见 [spec](../docs/plans/2026-10-05-evaluation-system-spec.md)，标注口径见 [指南](../docs/annotation-guide.md)。所有命令在仓库根目录运行；Windows 可将 `python` 换成 `.venv\Scripts\python.exe`，或用 `uv run --locked --extra dev python`。

## 1 先验证框架

```powershell
python -m pytest -q
python -m eval.demo --output data/reports/evaluation-demo-new
```

打开输出的 `index.html`。六个模块报告包含刻意制造的漏题、弃权和错误参数，供检查评分与坏案例定位。所有数据为 SYNTHETIC，门槛为 NOT_ELIGIBLE。输出目录必须不存在；历史结果不会覆盖。CI 运行离线测试及演示，无需 API 凭据或线上服务。

## 2 导出真实快照及待标注工作包

在已有开发栈的 Linux 容器中运行，复用数据库、数据挂载及模型锁：

```bash
docker compose run --rm --no-deps \
  -e PYTHONPATH=/workspace/src:/workspace \
  -v "$PWD:/workspace:ro" api \
  python -m eval.snapshot --output /app/data/reports/evaluation-snapshot-new --as-of 2026-10-05
```

回到本地仓库：

```powershell
python -m eval.prepare dataset --snapshot data/reports/evaluation-snapshot-new --output data/reports/evaluation-workbench-new
python -m eval.prepare feedback --directory data/feedback --output data/reports/evaluation-workbench-new/feedback-candidates.jsonl
```

快照包含生效原文、问题/场次/追问、已发布分类、canonical及物理索引；facts内容有独立校验hash。它用于复核和版本绑定，不是可恢复整套数据库的备份。重复导出会生成新目录；旧物理索引若已删除，应重新准备可用环境与数据版本。

工作包默认抽样30篇文档、200条分类候选、至多150对去重候选、6条检索种子、17个Query场景和约12个SQL开发场景。每条均为 dev、human_verified=false。数量依实际语料调整：这些是候选规模，不能保证30篇都是正例，也没有满足50条独立检索/路由test规模。`proposals/`单独保存机器分类；SQL期望来自独立facts频率oracle，但沿用尚未人工核验的生产分类与归并。

缺少数据库快照时可先准备原文抽取包：

```powershell
python -m eval.prepare dataset --corpus-root md --output data/reports/extraction-annotation-new
```

保留原文快照。先独立标标签，再按来源/转载、题目对/问题身份、模板和完整对话划分dev/test。填写review并冻结新版本后复制到 `data/gold/<version>`；个人反馈及调参案例只进入dev。不要整体将机器工作包改成test。

## 3 采集原始预测

| adapter | section | 执行内容 | 模型调用 |
|---|---|---|---|
| sql | sql | 真实SQL排序、聚合及完整分页 | 0 |
| published | extraction / task_labels | 审计快照中的已有输出 | 0；历史抽取费用未重算 |
| provider | extraction / dedup / task_labels | 调用现有生产适配器，不发布语料或分类 | 显式预算 |
| retrieval | retrieval | 四路检索，复用向量、物理索引和Hybrid Top50 | 显式预算 |
| http | routing | 真实Pi QueryAgent、会话、工具及评测用户状态 | 显式预算 |

SQL完整采集示例（Linux容器）：

```bash
docker compose run --rm --no-deps \
  -e PYTHONPATH=/workspace/src:/workspace -v "$PWD:/workspace:ro" api \
  python -m eval.collect --dataset /app/data/reports/evaluation-workbench-new \
  --section sql --adapter sql --output /app/data/reports/evaluation-workbench-new/sql.predictions.jsonl
```

审计已有机器分类示例（本地，不调用模型）：

```powershell
python -m eval.collect --dataset data/reports/evaluation-workbench-new --section task_labels --adapter published --snapshot-export data/reports/evaluation-snapshot-new --output data/reports/evaluation-workbench-new/task_labels.predictions.jsonl
```

provider/retrieval 必须在Linux容器复用 `/app/runtime/model-call.lock`；Windows不能用另一种文件锁代替。沿用上面的容器前缀，例：

```bash
python -m eval.collect --dataset /app/data/reports/evaluation-workbench-new \
  --section retrieval --adapter retrieval --max-calls 20 --max-tokens 1000000 \
  --output /app/data/reports/evaluation-workbench-new/retrieval.predictions.jsonl
```

预算只是本次采集的上限，不能保证全部样本完成。所有超时、预算拒绝和模型失败保留对应行。未知usage保留保守预算占用；直接provider和reranker文本输出限制为最多16384 token，HTTP调用沿用服务端预算。provider可同样运行extraction/dedup/task_labels；抽取同时应用生产证据校验和提问资格筛选，保存原始结构。检索输出四路rankings和带文本的candidate_pool供人工标注；仍需池外补查，不能把候选池当全部相关题。检索报告的分路延迟仅计该阶段实测耗时，共享embedding的耗时和调用在顶层记录一次，不能忽略它来比较完整请求成本。

独立gold尚未完成时 `--split dev` 允许先采集。正式采集使用 `--split test`，会先验证人工gold资格。

## 4 隔离QueryAgent评测

在Linux工作目录运行（每次换新的eval用户ID）：

```bash
export EVAL_USER_ID=eval-my-unique-run
docker compose -p interview-evaluation -f compose.evaluation.yaml up -d
```

Pi sidecar回调专用评测API，数据库沿用开发快照，个人状态归属该评测用户。`/api/evaluation/context`只在此专用服务暴露评测账号、版本、事件、状态和真实模型usage。采集前要求账号没有历史会话及复习状态。API绑定本机18002端口；真实用户API不可作为此adapter的目标。

```powershell
python -m eval.collect --dataset data/reports/evaluation-workbench-new --section routing --adapter http --base-url http://127.0.0.1:18002 --max-calls 100 --max-tokens 3000000 --allow-writes --output data/reports/evaluation-workbench-new/routing.predictions.jsonl
```

每轮预留服务端最大调用/token预算，执行后以新增telemetry结算；最后一轮重放相同request_id，检查没有重复事件和新增调用。写场景只有显式 `--allow-writes` 才执行；默认会保留 `EVALUATION_WRITES_DISABLED` 失败行。完成后停止临时服务：

```bash
docker compose -p interview-evaluation -f compose.evaluation.yaml down
```

当前HTTP评测要求as_of等于服务当天日期。历史日期的SQL可直接运行；历史自然语言排序需另行实现服务日期注入后再测，不能伪造版本信息。

## 5 评分、对齐与比较

先完成相应标签。以下SQL开发样本已有独立期望，可直接评分：

```powershell
python -m eval.run --dataset data/reports/evaluation-workbench-new --section sql --split dev --predictions data/reports/evaluation-workbench-new/sql.predictions.jsonl
```

抽取正例在有预测问题时另需一对一人工alignment：

```powershell
python -m eval.prepare alignments --predictions data/reports/evaluation-workbench-new/extraction.predictions.jsonl --output data/reports/evaluation-workbench-new/alignments.jsonl
python -m eval.run --dataset data/reports/evaluation-workbench-new --section extraction --split dev --predictions data/reports/evaluation-workbench-new/extraction.predictions.jsonl --alignments data/reports/evaluation-workbench-new/alignments.jsonl
```

prepare只生成空对齐模板，人工核对后填写pairs/sessions及review；不能自动填写human_verified。其prediction_sha256绑定结果内容，预测改动后需重新裁决。负例错误保留、正例漏题和失败都进入FP/FN。

正式检查及评分：

```powershell
python -m eval.validate_gold --dataset data/gold/v2 --section retrieval
python -m eval.run --dataset data/gold/v2 --section retrieval --predictions data/reports/retrieval-test.predictions.jsonl --default-pipeline HYBRID_RERANK
python -m eval.compare eval/sql/results/old-run eval/sql/results/new-run --output data/reports/sql-comparison.json
```

默认 `eval/<section>/results/<unique-run>/` 保存HTML/Markdown摘要、坏案例、逐样本CSV/JSONL、原始预测、配置hash和门槛；采集receipt存在时一并校验与复制。检索额外保存四路消融表。UNKNOWN不能成为任务成功；不同数据hash、snapshot或split不可直接比较。embedding阈值baseline可按相同id/result.label格式导入评分，目前没有候选召回或baseline实验时标明NOT_RUN。

原V1空gold仍应被正式校验拒绝。dev正常出报告且CLI退出0；正式门槛未达到、未配置或输入无效退出1。发布门槛仅针对已测模块，不能把一个模块PASSED视作整体V1发布通过。

## 6 已完成的独立标注与审核

按用户明确授权，已冻结 `data/gold/evaluation-agent-reviewed-20261005-v5`：30篇正例、2篇排除负例、511道问题、211条任务分类。标注者为 Agent，`human_verified` 均为 false；用户裁定的三个字段另存人工记录。LRU已确认手写，题型按既有口径标为ALGORITHM，待审核项为0；该文档的19道漏题仍计FN。

[报告入口](../data/reports/agent-reviewed-20261005-v5/summary.md) 汇总分母、门槛、缺陷及复跑命令；[审核材料](../data/reports/agent-reviewed-20261005-v5/REVIEW.md) 保留原文与已确认答复。完整独立标注配方、冻结和裁决步骤见 [agent_gold 操作说明](../evals/agent_gold/README.md)。

```powershell
python -m eval.validate_gold --dataset data/gold/evaluation-agent-reviewed-20261005-v5 --section extraction --review-policy delegated_agent
python -m eval.validate_gold --dataset data/gold/evaluation-agent-reviewed-20261005-v5 --section task_labels --review-policy delegated_agent
python -m eval.run --dataset data/gold/evaluation-agent-reviewed-20261005-v5 --section extraction --review-policy delegated_agent --predictions data/reports/agent-reviewed-20261005-v5/extraction.predictions.jsonl --alignments data/reports/agent-reviewed-20261005-v5/alignments.jsonl --output-root data/reports/agent-review-rescore-new
python -m eval.run --dataset data/gold/evaluation-agent-reviewed-20261005-v5 --section task_labels --review-policy delegated_agent --predictions data/reports/agent-reviewed-20261005-v5/task_labels.predictions.jsonl --output-root data/reports/agent-review-rescore-new
```

上述是 corpus/index 293、annotation 30 的已有输出审计。预测由 `published` adapter 在封存后导出，本轮没有新增模型调用；报告中的毫秒耗时是本地读取时间，不能当作模型推理延迟。更换成 `provider` 后应重新采集并审核 alignment，不能沿用绑定旧预测 hash 的对齐。

首次原文标注先于预测比较；v2根据原文复核拆分与岗位口径，v3加入用户字段裁决，v4仅完善过程说明，v5加入LRU手写裁决并清空待审项。该数据已用于诊断，后续调参应另留未见来源测泛化。

历史 `quality_gate_v2` 的任务分类门槛未设定，故其 `gates.json` 为 NOT_RUN；抽取为 BELOW_GATE。CLI 返回1表示门槛未通过或尚未配置，不表示没有生成评分报告。

## 7 分类泛化、闭集抽取与完整去重链路

本轮协议为 [quality_gate_v3](../evals/project_quality/quality_gate_v3.json)，在新来源分类预测前封存。保留 v2 其他模块全部门槛；另要求分类联合正确率 ≥90%、已知标签覆盖 ≥95%、严格 ENGINEERING Precision ≥90% / Recall ≥85%。MIXED 纳入业务筛选的辅助指标独立报告，不能替换严格类别门禁。模块缺测或不达标继续阻断整体发布。

`task-generalization-agent-20261006-v2` 是 45 个未见来源中的 200 条，排除旧参考集来源和相同题目 / canonical；不是跨领域或自然流量测试。`compare_label_context` 先固定 v4/v6 与同一原文上下文协议，再依次采集两版在回归 / 留出来源上的预测。格式失败的批次不会被删掉；有界小批恢复只处理格式／ID／引用校验失败，网络或预算失败不触发拆批。所有调用计费，整批验证成功才保存标签。

抽取可指定 `--extraction-prompt-version extract_question_v4`，通过闭集 `topic_id` 还原两级主题；`EXTRACTION_THINKING_MODE=disabled` 是显式实验配置。默认仍为 v2 / auto。完整发布校验进入同一有界重试，标题不能作为唯一问题。改变 schema / prompt / thinking / 类型政策会改变阶段缓存身份。原文、Gold 和各版失败预测均保留；新版通过结构校验不代表主题或元信息正确。

完整去重链路是开发诊断，沿用已审核题对，新增输入 embedding，走真实数据库候选、强制 HNSW + 精确增量尾、批量 Judge 和 resolver。题对 ID 是 occurrence ID，必须经冻结事实解析到 canonical ID。为了让实际 Judge 看到同一已审核右侧文本，在事务中替换目标代表文本；若两端 canonical 不同，临时移出左侧 canonical。目标向量必要时预先计算，耗时和调用单独计数。两条路径全部回滚，最后核对候选文本 hash、记录数和原快照。

```bash
python -m evals.project_quality.benchmark_dedup_chain prepare-source-pairs \
  --parent-protocol data/reports/quality-optimization-20261006/dedup.full-chain.protocol.json \
  --snapshot data/reports/resume-quality-snapshot-20261005-v1 \
  --protocol data/reports/my-new-run/dedup.source-pair.protocol.json
python -m evals.project_quality.benchmark_dedup_chain collect \
  --protocol /app/data/reports/my-new-run/dedup.source-pair.protocol.json \
  --output /app/data/reports/my-new-run/dedup.chain.jsonl --max-calls 120 --max-tokens 2000000
```

第一条可离线运行；第二条必须使用共享 Linux 模型锁的源码容器。这是目标代表文本经过替换的 shadow replay，不是未经改动的生产候选池。只审核了指定题对，其他候选未知，因此整体 SAME Precision / semantic success 保留为 null；20 条诊断也不能替代 150+ 对质量门禁。

## 8 第二轮候选、查询参考契约与原始首测

最新入口为 [质量修整报告](../docs/verification/2026-10-06-quality-refinement.md)。`run_refinement` 使用封存协议校验 Prompt hash，分类及查询均走共享 Linux 锁；查询要求新的 eval actor，其 conversation、state、event 全部为 0，评测时钟固定为 2026-10-05。原 200 条来源留出本轮已归为回归；新 200 条工程挑战只有题目不相交，来源仍共享，不能声称新来源泛化。

候选 v5 抽取可用 `--extraction-thinking-mode disabled` 明确覆盖 Settings。未启用该覆盖的中断采集另存 ABORTED，完成部分保留，未知在途调用不计成 0。规范化回放与模型重新采集分开登记。

`query-generalization-reserved-agent-20261006-v3` 原始首次评分为 46/50。两处 round 使用显示文本而非契约枚举，修订到独立 v5 后，同一预测重评分为 48/50；这是标注修订，零新增模型调用，不是模型提升。`reference_contract=typed_query_plan_v1` 可验证部分 expected_plan、显式清除字段及枚举；旧未声明版本继续按历史协议重放，不能回写原始评分。新备用 50 场景 / 95 轮还没有模型预测。

当前 [v4 bundle](../docs/verification/2026-10-06-quality-bundle-v4.json) 使用原 quality_gate_v3 门槛，旧 211 条、已见 200 条分类、已见查询及备用查询原始首测一起参与验收，退出码仍为 1。存储预测可以重评分；已注册阶段、已有 Gold 输出目录、用过的 actor 均拒绝覆盖。
