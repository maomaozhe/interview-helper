# 评测操作手册

搭建规范见 [spec](../docs/plans/2026-10-05-evaluation-system-spec.md)，标注口径见 [指南](../docs/annotation-guide.md)。所有命令在仓库根目录运行；Windows 可将 `python` 换成 `.venv\Scripts\python.exe`，或用 `uv run --locked --extra dev python`。

## 入口分类：通用评测、候选实验与历史复现

本页是评测与实验的统一入口；完整应用运行使用 [普通 Compose](../docs/development.md#推荐完整运行普通-compose)，当前能力、部署和质量结论见 [状态页](../docs/status/current.md)，默认版本见 [运行配置](../docs/reference/runtime-config.md)。评测脚本、候选提示词和历史配方继续保留，存在于目录中不表示已启用或适合直接运行。

| 入口 | 身份与用途 | 从哪里开始 |
| --- | --- | --- |
| `python -m eval` | suite 统一预检、运行/恢复、比较与严格报告检查；旧模块命令继续可用 | 先看第 1 节；需要准备数据或真实采集再看第 2–5 节 |
| `compose.evaluation.yaml` | 独立评测 API / Pi，不替代完整运行；沿用指定开发快照、隔离账号及共享 Linux 模型锁 | [第 4 节](#4-隔离queryagent评测)；真实调用遵守预算与写入授权 |
| `evals/agent_gold/` | 独立来源审核、裁决与冻结配方；带日期的文件绑定特定来源/数据版本 | [第 6 节](#6-已完成的独立标注与审核)、[来源参考集](#9-2026-10-14-真实面经来源参考集)、[历史查询范围](#10-历史用户查询与真实来源范围-gold)；旧批次配方见 [agent_gold](../evals/agent_gold/README.md) |
| `evals/project_quality/` | 候选对照、封存运行、性能诊断和历史回放；没有独立运行默认的承诺 | 第 7–10 节；`source_iteration.py` 等来源候选结果见 [10-15 报告](../docs/verification/2026-10-15-source-optimization.md)，具体参数以封存协议为准 |
| `scripts/evaluate_*.py` 与旧提示词 | 对应专项的采集/回放工具，保留旧输入、调用与失败证据 | 从相应日期 [验收记录](../docs/verification/) 或 [复盘索引](../docs/retrospectives/README.md) 找原协议；不要凭文件名或最高版本号推定当前默认 |

第 1–5 节说明通用流程；第 6–10 节保留有日期和数据身份的批次配方。示例中的旧日期、快照和 gold 不是新一轮自动选择；复现须匹配原数据与协议，新实验须先冻结自己的输入、版本、预算和验收条件。私有 `data/gold/`、`data/reports/` 不随仓库分发，缺少原始材料时不能声称复现历史分数。

## 1 先验证框架

日常从这个入口开始，不需要先读后面的历史配方。首次用合成数据跑通整个流程，无需私有语料、数据库或模型凭据：

```powershell
python -m eval demo --output .tmp/evaluation-demo
```

打开 `.tmp/evaluation-demo/run/index.html`。六模块走同一 suite 编排；抽取漏题、分类弃权和错误参数是刻意设置的失败。流程完成不等于这些失败消失：合成报告的门槛保持 `NOT_ELIGIBLE`。输出目录必须不存在。

生成的 `.tmp/evaluation-demo/suite.json` 是完整配置示例。新实验复制配置后显式选择自己的输入和版本；路径相对 suite 文件，而不是运行命令的目录。候选 `candidate.id/components`、每模块的 dataset、split、review_policy、threshold_version、default_pipeline 都必须填写，不能凭最高版本号自动选择。每模块使用已有 `predictions` 或 `collect: {adapter, options}` 二选一；抽取另填审核过的 alignments。`components` 按 section 声明版本，可用 `files: {文件路径: SHA256}` 核对实际文件；声明本身不证明服务器运行了它。

| 要做什么 | 命令 |
| --- | --- |
| 检查环境、数据、版本和预算，不访问服务 | `python -m eval preflight <suite.json>` |
| 新建一次运行 | `python -m eval run <suite.json> --output <新目录>` |
| 继续同一次运行 | `python -m eval resume <suite.json> --output <原目录>` |
| 比较两次 suite 或两个模块报告 | `python -m eval compare <旧目录> <新目录> --output <比较.json>` |
| 拆分已有真实请求的阶段耗时与代价 | `python -m eval profile-latency <旧predictions.jsonl> <新predictions.jsonl> --output <耗时对照.json>`；仅复算原遥测，不重跑模型，不叠加 tool/decision 与其内部模型耗时 |
| 严格复算一个模块报告 | `python -m eval check <模块报告目录>` |
| 检查六模块与指定回归门禁 | `python -m eval check <运行目录>/release-bundle.json --root <运行目录>` |
| 从独立原文审核冻结查题对照集 | `python -m eval freeze-queries --snapshot <快照目录> --reviews <审核JSON...> --recipe <规则JSON> --authorization <授权JSON> --output <新gold目录>` |

suite 的 `release.modules` 可指定 `{section: module_id}`，`release.required_regressions` 指定 `{回归名: module_id}`；这些模块均须实际运行，不因主评测通过就跳过旧回归。六模块不完整、数据未达资格或门槛未过都会阻断发布。旧 bundle 使用 `python -m eval check <旧bundle.json> --root . --legacy` 明确作历史核查；缺少封存输入或版本证据的旧报告不会自动升级为严格验收。

恢复会检查输入、候选、评分代码、已完成报告及会话/轮次账本指纹，保持原预算及未知 usage 占用；完成项不会重跑。缺少新账本指纹的旧未完成 HTTP 检查点明确拒绝自动续跑，已完成回执仍可核查。无法确认结果的在途模型/HTTP 请求会停下并保留证据，不自动重复扣费或写入。纯本地 SQL/已发布结果的只读未完成项可以重新读取；HTTP 已产生复习写入的恢复仍需单独核对。直接采集入口也支持 `python -m eval.collect ... --resume`，复用相同 output 和参数。

新抽取采集须预先填写 `alignments` 的目标路径，可以暂不存在。采集后若缺对齐，运行停在 `NEEDS_INPUT`，模块保存 `alignments.draft.jsonl`；核对后把批准的对齐写到原目标路径，再用同一 suite 续跑，其他模块不必等待。评分成功后绑定对齐 hash；修改已验收对齐须另建运行。

预检 `PASS` 只证明本地静态条件满足。需要服务的采集还必须显式 `--allow-live`，模型 adapter 在 `collect.options` 填写正数 max_calls/max_tokens；HTTP 另指定 base_url、expected_actor（`eval-` 账号），实际采集核对服务快照、账号与共享锁。provider/retrieval 仍须 Linux 共享模型锁。真实预算和业务口径在开展该轮前确定；离线演示没有真实模型调用。

严格发布还要求候选的 `runtime_fingerprint` 匹配实际采集运行物，模型/提示词声明与调用观测一致；声明 model_revision 时也须一致。本地采集使用源码指纹，远端不能拿本地指纹代替。

HTTP 可通过独立部署证明绑定 API/Pi 的运行文件、资源、依赖、PID/启动身份，以及 API/worker 共用锁的路径、设备和 inode。先用 [身份探针](runtime_identity.py) 独立测量，再给 `eval.serve --deployment-evidence <证明.json>`；Pi 的身份端点须显式启用且通过内部认证。候选预先固定 `deployment_proof_sha256` 与运行指纹；collector 同样传 `--deployment-evidence`，每轮前后及恢复时核对。证据一致才记 `REMOTE_SOURCE_BOUND`，否则保持未核验并阻断严格发布。该证明核验运行物，不认证模型权重、标注真实性或整体质量。

HTTP 整轮预算使用服务回报的 `request_max_model_calls/request_max_tokens`（当前默认 24 / 256000），与 `query_planning_max_calls`（默认 8）分开；成功终态还须持久尝试数与唯一调用账本相符，才能按完整遥测结算。失败终态或日志不全保留整轮未知扣计并停止，不能按空日志退成零。共享锁默认支持容器路径；SSH 自定义位置须有进程环境和同文件身份的独立证明，不能只指定任意路径。多租户评测使用新 `eval-` 账号和 `--http-cookie-file <私密JSON>`，文件只含 `ii_tenant_session`，Linux 权限为 0600；报告仅保存文件 hash。保留实际账号的提供方选择及全局 embedding 链，不关闭多租户来替代真实路径。

小批采集可用 `--stop-after-samples N` 在完整场景后暂停，不生成最终预测/receipt；随后同一输入、output、预算和证明加 `--resume` 继续。暂停参数不改变身份；失败的完成项也不重放。[真实服务 pilot](pilot.py) 从独立快照及来源审核冻结 8 场景/12 轮，`assess` 同时检查结果/频次/来源/指代，技术答案另绑定独立审核，缺审核保持 UNKNOWN；该 dev 小集没有正式发布资格。

新报告封存 gold、预测、alignment、评分协议和候选身份，严格检查重新计算指标、逐样本断言和门槛。比较会展示缺失指标与断言变化，并标明协议变化，不能把换数据/门槛当作模型变好。首页分别显示执行、质量和发布检查状态；真实用户任务质量仍标为未建立，需用原始问法、实际结果与独立预期补充回归。详细实现及本批验证见 [可用性记录](../docs/plans/2026-10-15-evaluation-readiness.md)。

`python -m pytest -q` 是完整工作区回归。部分来源复现测试需要私有快照/gold：`test_user_query_source_gold.py`、`test_user_query_source_revision_20261015.py` 和 `test_user_query_checkpoint.py` 在必要输入缺失时明确 skip；输入存在时仍检查 JSON、hash 和语义，坏数据会失败，不会被跳过。使用 `python -m pytest -q -rs` 查看原因，报告须列出跳过项和未覆盖范围；新 clone 运行结束不代表复现了历史实验，skip 和合成检查通过都不等于真实质量通过。

真实质量实验需要自己的语料、模型服务和冻结参考集。私有原文与历史 `data/gold/`、`data/reports/` 不随仓库分发，不为让测试通过复制到公开仓库。

当前五问回归使用固定[原问与验收规则](scenarios/user_scope_five.json)、[参考版本/hash](scenarios/user_scope_five.reference.json)，正确题单在私有 `data/gold/user-scope-five-20261016-v2/EXPECTED-ANSWERS.md`。原文审核、边界、反例和历史分歧保留；这是已见来源的开发集。`query_result_reference` 让正常 routing 评分同时核对实际题目/来源、强正例数量、工程对象族、已知正例覆盖；未审项为 UNKNOWN，有限面板不认证全库召回。完整语义 SQL 声称已返回全部时，漏掉已知正例不能通过。

复算原服务五问：`python -m eval run eval/scenarios/user_scope_five.baseline.json --output <新目录>`。它读取字节不变的旧预测重新评分，原 v1 采集回执另存来源证据，不冒充针对 v2 采集；缺私有材料会阻断。候选使用相同 gold/评分器，以实际新采集结果比较，不把离线资格恢复伪装为新命中。SQL 完整字符 span 须唯一对应冻结原题；SEARCH 须核对实际 occurrence、hash 和定位，弱引用或歧义失败。

### 多轮题单与最终回答

单轮 `source_query_results_v1` 继续按原契约评分。新的多轮参考使用 `source_query_results_v2`：`query_result_reference.queries[case_id].turns` 与场景 `turns` 等长，每个来源参考绑定当轮原始 `message`，独立审核的完整意图另存 `review_query`；后轮不能借用首轮标签。新多轮场景还检查会话身份连续、版本逐轮递增，显式重放保持原版本；缺证据为 UNKNOWN，换会话或版本倒退失败。`freeze-queries` 的 recipe 支持 `cases[].turns`，每轮的 `query` 选择独立来源审核，`message` 保留用户原话，并填写该轮的 `minimum_positive/expected_plan` 等条件。分页也需为对应轮准备参考，不会把跨页并集充作当页命中。

同一次请求先取证再回答时，来源参考可指定正整数 `tool_ordinal`，从**当轮持久完成回执**取原始题单；数组位置不是 ordinal。缺回执保持 UNKNOWN，失败、跨 run 或矛盾回执不能通过。没有题单要求的轮次须显式 `source_results_required: false` 并使用 null 来源参考；若实际执行 LIST/NEXT/SEARCH/DETAILS，仍会失败，不能隐藏中间查题结果。COUNT/STATS 的事实可用该轮普通 assertions 检查。

需要评测最终回答的轮次增加 `answer_rubric`，例如：

```json
{
  "schema": "query_answer_v1",
  "required_basis": "CORPUS",
  "criteria": [
    {"id": "goal", "description": "直接回答当前原问的数量和判断，引用当轮统计。"},
    {"id": "scope", "description": "区分筛选范围、检索候选和全库覆盖，保留未决限制。"}
  ]
}
```

rubric 在采集前作为 gold 冻结；技术解释应另写具体正确性标准。可加 `assertions` 检查实际 observation 的确定性字段。工程检查要求非空 ANSWER、成功终态、主机完成契约及当前 run 的持久最终 ANSWER 一致，引用 ordinal 只能来自本轮已完成读工具；合法的“上轮列题、本轮解释这些题”可使用本轮 ANSWER 重新加载的有来源详情。上述检查不替代技术正确性和目标达成审核。

独立回答审核放在外部 JSON，不能写回预测或由被测模型自报成功。根对象为 `schema: query_answer_reviews_v1`，包含 `dataset_sha256`（`digest(load_dataset(dataset))`）、`predictions_sha256`（预测文件原始字节 hash）及 `reviews` 数组。每条记录格式为：

```json
{
  "case_id": "conversation-1",
  "turn_index": 1,
  "answer_sha256": "<answer_text 的 UTF-8 字节 SHA256>",
  "rubric_sha256": "<digest(answer_rubric)>",
  "reviewer_kind": "agent",
  "reviewer": "独立审核者",
  "reviewed_at": "2026-10-25T00:00:00+08:00",
  "independent": true,
  "assessments": {
    "goal": {"passed": true, "evidence_quote": "回答中的逐字片段", "reason": "与冻结参考核对的理由"},
    "scope": {"passed": null, "evidence_quote": "", "reason": "当前证据尚不足以判断"}
  }
}
```

`turn_index` 从 0 开始。审核绑定完整数据集、原始预测、指定轮次、逐字回答和 rubric；正向判断必须提供回答中的非空逐字片段，负向或未决判断仍须写理由。缺审核或缺 criterion 保持 UNKNOWN，明确否定为 FAIL；失效 hash、重复/越界轮次、非独立审核和虚构引用会拒绝评分。审核者身份是来源声明，hash 绑定不认证其真实性。

suite 的 routing 模块可填写 `answer_reviews: "独立审核.json"`；模块入口也支持 `python -m eval.run --dataset <gold> --section routing --predictions <预测.jsonl> --answer-reviews <审核.json> --split dev`。先采集时可不带审核，报告会保留 UNKNOWN；审核完成后用 stored predictions 和新输出目录重新评分。携带审核的 suite 只接受已有预测，不能给正在采集的请求预填答案审核。已完成报告/检查点不会被补审核静默改写。

报告输出逐轮来源检查与 `answer_quality` 的工程/语义分项，审核文件随报告封存并参与严格复算和恢复指纹；篡改审核即使重写外层 hash 也须重新通过输入绑定及评分。比较还核对每轮的审核覆盖与审核主体类型，补审核不计作模型效果提升。当前评分协议为 `evaluation_scoring_v2`，旧报告须显式历史核查或从原输入新建重评分，不覆盖历史结果。有限已审核题单覆盖仍不是全库召回，未声明 rubric 的旧场景不因此获得回答正确性认证。

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

HTTP adapter 及会话质量、工程设计、历史重检脚本默认发送 `X-Usage-Purpose: evaluation`。启用使用统计的新服务保存评测事件，但不计入个人候选、返回和浏览次数；旧服务可忽略该附加请求头。自行编写探针时也要标记此请求头，或显式传 `usage_purpose=evaluation`，并避免与请求体/查询参数中的用途冲突。这个标记只控制使用统计；复习、反馈等写入仍需隔离评测账号或数据库，不能据此放宽下面的 `--allow-writes` 要求。

```powershell
python -m eval.collect --dataset data/reports/evaluation-workbench-new --section routing --adapter http --base-url http://127.0.0.1:18002 --max-calls 100 --max-tokens 3000000 --allow-writes --output data/reports/evaluation-workbench-new/routing.predictions.jsonl
```

每轮预留服务端最大调用/token预算，执行后以新增telemetry结算；最后一轮重放相同request_id，检查没有重复事件和新增调用。写场景只有显式 `--allow-writes` 才执行；默认会保留 `EVALUATION_WRITES_DISABLED` 失败行。完成后停止临时服务：

```bash
docker compose -p interview-evaluation -f compose.evaluation.yaml down
```

HTTP 评测要求数据集 `as_of` 与服务报告的日期一致。未冻结的评测服务使用当天日期；历史回放通过 `eval.serve --as-of YYYY-MM-DD` 或第 10 节的封存入口固定查询及响应日期，再核对 `/api/evaluation/context` 的快照。普通服务仍使用当天日期，不能通过改写回执冒充历史环境。

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
python -m eval compare eval/sql/results/old-run eval/sql/results/new-run --output data/reports/sql-comparison.json
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

本节保留 2026-10-06 的候选配方，报告为 [当次质量修整](../docs/verification/2026-10-06-quality-refinement.md)，不是今天的默认版本登记。`run_refinement` 使用封存协议校验 Prompt hash，分类及查询均走共享 Linux 锁；查询要求新的 eval actor，其 conversation、state、event 全部为 0，评测时钟固定为 2026-10-05。原 200 条来源留出本轮已归为回归；新 200 条工程挑战只有题目不相交，来源仍共享，不能声称新来源泛化。

候选 v5 抽取可用 `--extraction-thinking-mode disabled` 明确覆盖 Settings。未启用该覆盖的中断采集另存 ABORTED，完成部分保留，未知在途调用不计成 0。规范化回放与模型重新采集分开登记。

`query-generalization-reserved-agent-20261006-v3` 原始首次评分为 46/50。两处 round 使用显示文本而非契约枚举，修订到独立 v5 后，同一预测重评分为 48/50；这是标注修订，零新增模型调用，不是模型提升。`reference_contract=typed_query_plan_v1` 可验证部分 expected_plan、显式清除字段及枚举；旧未声明版本继续按历史协议重放，不能回写原始评分。新备用 50 场景 / 95 轮还没有模型预测。

当前 [v4 bundle](../docs/verification/2026-10-06-quality-bundle-v4.json) 使用原 quality_gate_v3 门槛，旧 211 条、已见 200 条分类、已见查询及备用查询原始首测一起参与验收，退出码仍为 1。存储预测可以重评分；已注册阶段、已有 Gold 输出目录、用过的 actor 均拒绝覆盖。

## 9 2026-10-14 真实面经来源参考集

用户再次授权 Agent 自行阅读真实面经审核。最终版本为 `source-reviewed-extraction-20261014-v3`（30正/1负、538题及任务标签）、`source-reviewed-dedup-20261014-v2`（206对）和 `source-reviewed-retrieval-20261014-v2`（59正查询）。来源绑定历史r293快照；全部human_verified=false，不代表当前线上或未见来源泛化。[报告与阅读入口](../docs/verification/2026-10-14-source-gold.md)保留具体缺陷、首次冻结、后续裁决和已有输出审计。

新数据声明 `source_evidence_contract=raw_source_spans_v1`。严格校验逐字原问、字符/行跨度、去重实际输入及检索映射证据；复制的 `source_facts_path` 另校验真实canonical完整occurrence集合。检索只完成锚点审核，`retrieval_evidence_scope=anchor_only`、`release_eligible=false`，评分和发布汇总强制NOT_ELIGIBLE。锚点指标另命名，不能当全库Recall或负查询认证。

```powershell
python -m evals.agent_gold.audit_sources --snapshot data/reports/resume-quality-snapshot-20261005-v1 --dataset data/gold/source-reviewed-extraction-20261014-v3 --dataset data/gold/source-reviewed-dedup-20261014-v2 --dataset data/gold/source-reviewed-retrieval-20261014-v2 --output data/reports/source-gold-recheck-new
```

每次使用新的输出目录。命令逐模块验证冻结gold，将归档字节与真实快照核对，生成可搜索原问/审核理由/来源全文的HTML。它不生成标签、不调用模型、不发布分类；真实gold与来源沿用私有忽略目录。

## 10 历史用户查询与真实来源范围 Gold

本节明确复现 **v1 历史首测**，下面涉及 gold 的命令都显式指定 v1。当前 `user_query_alignment` 的新运行和 `user_query_viewer` 省略数据集时默认 v2；重评分/续跑会读取已有协议。因此不能省略 `--dataset` 来复现 v1，也不能将 v2 当作 v1 原首测。新实验须独立选择、冻结并记录数据版本。

`data/gold/user-query-source-reviewed-20261015-v1` 保存6条历史用户消息中的真实问法及标明派生关系的变体，共24个场景、34轮。重点检查宽工程系统设计、排行榜示例与明确限定、澄清/改方向/翻页、Redis场景、Agent应用、字节算法全量与频次；系统设计范围独立于 `coding_focus=ENGINEERING`。

主范围逐条筛查r293的2771次真实提问，保存21个明确正例、2728个负例和22个未裁边界，并单列2道未被抽取的原文设计题。全部184份原文归档，不声称全部篇章均全文审阅。其他范围为明确列出的来源面板，不能据此认证全库Recall。Gold、指南、历史原句、全部canonical variants与真实source spans均受冻结清单约束，审核标记始终为Agent。

```powershell
$env:PYTHONPATH='src;.'
$env:PYTHONIOENCODING='utf-8'
.venv/Scripts/python.exe -m evals.agent_gold.user_query_source_20261015 --dataset data/gold/user-query-source-reviewed-20261015-v1
.venv/Scripts/python.exe -m evals.project_quality.user_query_alignment prepare --dataset data/gold/user-query-source-reviewed-20261015-v1 --output data/reports/user-query-replay-new
```

`prepare` 创建新API/Pi源码封存、独立collector/validator/scorer封存、随机eval账号和 `compose.sealed.json`；不启动服务或调用模型。在WSL仓库目录将 `EVAL_USER_ID` 设为新 `runtime-seal.json` 的actor，再启动隔离服务：

```bash
docker compose -p user-query-replay -f compose.evaluation.yaml \
  -f data/reports/user-query-replay-new/compose.sealed.json up -d
```

然后从Windows采集；每次使用新账号与新输出目录，快照必须与gold一致。模型调用发生在复用共享Linux锁的隔离服务中，collector不把预期标签或参考题单发送给模型。

```powershell
.venv/Scripts/python.exe -u -m evals.project_quality.user_query_alignment collect --dataset data/gold/user-query-source-reviewed-20261015-v1 --runtime-root data/reports/user-query-replay-new --output data/reports/user-query-replay-new/run --max-calls 160 --max-tokens 5000000
.venv/Scripts/python.exe -m evals.project_quality.user_query_alignment score --dataset data/gold/user-query-source-reviewed-20261015-v1 --output data/reports/user-query-replay-new/run
```

结果保留逐轮计划、实际来源witness、调用、失败与预算，分别检查正题覆盖、对象family、误加硬筛、公司、NEXT会话与去重、频次顺序。原文证据不足或面板未审的返回保留待审，不能自动算负例；普通page可以独立对齐，而全量/COUNT的完整性认证必须另外满足。`ANSWER`保护用例只验证回答/找题意图，不认证技术答案质量。输出 `alignment.md/json`，不替代完整V1发布门禁。完成后用同一compose参数执行 `down` 停止本次隔离服务。

可离线生成带范围、审核角色和原文证据的浏览页。原站URL只读取已验证归档的显式链接字段，未联网验证当前可达性：

```powershell
.venv/Scripts/python.exe -m evals.agent_gold.user_query_viewer --dataset data/gold/user-query-source-reviewed-20261015-v1 --output data/reports/user-query-browser-new
```

中断结果不得冒充完整首测。`collect --resume-from <旧run>` 仅恢复已完整成功的case前缀，要求同gold、API源码、账号、服务地址和预算；实际服务器的请求、原问、会话、成功状态和调用必须仍与保存证据一致。恢复保留未知usage的整轮预留，不重放已成功case。旧目录和旧evaluator保持原样；若修复采集代码，应新建目录复制同一API runtime/`runtime-seal.json`，再用 `seal-evaluator --runtime-root <新目录>` 封存新的collector。已有会话数量必须与checkpoint严格相等，额外或在途会话不能自动跳过。

来源真实性、范围接受、待审边界和全量认证分别记录。若旧封存scorer的报告字段把这些含义混在一起，可对同一完整预测做独立报告补充，不重写原始评分：

```powershell
.venv/Scripts/python.exe -m evals.project_quality.user_query_alignment audit-reporting --dataset data/gold/user-query-source-reviewed-20261015-v1 --source-run data/reports/user-query-replay-new/run --output data/reports/user-query-reporting-new
```

补充审核强制所有逐轮checks及aligned与原始评分完全相同，仅澄清来源和认证字段；不调用模型、不将后见补审回填为冻结gold答案，也不宣称模型提升。
