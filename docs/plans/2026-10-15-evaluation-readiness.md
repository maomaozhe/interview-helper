# 评测第一批可用性建设

本记录合并目标、决策与验证结果，不再另建同批验收总结。当前操作入口仍为 `eval/README.md`。

## 20 组真实多轮诊断（2026-10-26，实施前）

- 目标：按用户要求完成 20 组多轮案例的真实服务、真实模型采集，分别定位召回、语义核验和任务完成问题，优先修复影响案例最多的根因，同步代码和受影响文档。
- 事实与边界：当前默认 query_agent_v16、rerank_v42_source_activity_reference；服务预算为规划 8、共享模型 24、工具 8、256000 token、600 秒。已有离线回归不能代表真实语义质量。本批使用当前私有语料设计开发案例，由执行 Agent 在预测前冻结来源、关系事实与逐轮验收；不标记人工审核、未见集或正式发布通过。保留已有私有 `md/issue.md` 改动。
- 区分检查：候选池未包含独立正例才归召回；进入候选后被错误排除或放入负例归核验；取证正确但范围继承、分页、指代、计数单位、解释或用户目标不完整归任务完成。缺少可定位证据时保留 UNKNOWN，不从最终空题单反推召回失败。一个案例可有次级原因，主因按最早阻断用户目标的阶段判定。
- 方案与预算：先冻结 20 组每组至少两轮及结果 oracle、来源证据、回答 rubric，再在独立 eval 账号和 API/Pi 上采集，复用 Linux `/app/runtime/model-call.lock`。基线最多 300 次模型调用、4000000 token；修复后复测另最多 300 次、4000000 token，总上限 600 次、8000000 token。每轮沿用服务上限，未知用量保守占用，失败留证后再决策，不盲目重放在途请求；不改金标或放宽门槛求通过。
- 验收：保留 20 组逐轮原始预测、持久回执、版本指纹、预算与分阶段归因；对影响最大的共性根因补有意义的回归测试，并用同一冻结输入复测。原文、账号凭据、数据库及原始轨迹留在私有 `data/reports/`，公开文档只写汇总、限制和证据入口。真实调用、命令、错误、结果和未覆盖范围在本节续记。
- 中途事实与候选：前 11 组已完成，基础 SQL 六组通过；澄清后的明确查询仍被追问、两家公司比较只查一家、清单加总量只给清单，均属目标承接/过早终止。Redis 的 10 个正例均已召回，返回正确但 50 候选中 32 个核验未决，不能由此声称召回或全库语义通过。先准备目标判定候选：明确 DIRECT_RESULT 仅适用于单范围、单一结果，去掉 COUNT 工具“一次即可”对多目标的误导，澄清选项不成为用户必须选择的分类；保留 v16，新增版本，不用案例关键词硬路由。隔离基线使用冻结代码，不受候选编辑影响；全部 20 组后再核对优先级及修复效果。
- 评测输入限制：采集前发现 c10 的综合回答 recipe 未声明内部 LIST 的来源参考，严格评分会额外产生 `source_results.not_requested`；c16 的语义算法目标也可合法走经来源审核的 SQL 集合。这两项在对应预测产生前已写入私有 `precollection-diagnostic-addendum.json`，保留原金标与严格失败，另按已冻结关系 oracle 检查用户目标，不能将 recipe 的工具约束问题冒充产品缺陷。
- 候选首轮离线检查：目标/多轮集成 39 passed；规划相关 62 passed / 1 failed，唯一失败为新提示词超过原 14000-byte 限额。先保留 `.tmp/multiturn20-20261026/planning.xml`，再压缩重复表述，不提高限额或放宽目标/证据检查；候选尚未真实验证或部署。基线原始轨迹及预算独立保留。
- 候选后续检查：第二次仍超限 60 bytes（62 passed / 1 failed），记录 `planning-2.xml` 后继续压缩重复文字；最终正文 13987 bytes，原限额内，规划相关 63 passed。Node 首轮 41 passed / 1 failed / 1 Linux 身份检查 skipped，失败为 COUNT 说明文案变化使历史菜单 hash 归一化失配；历史 fixture 不改，只显式归一化本次文案差异并继续比较完整参数/约束。最终回归结果后续补充。
- 基线后续诊断：线程池范围解释正确，但附带建议“下一页”，持久状态却无 `list_request`/候选页游标；将其另列回答能力承诺警告，不把已满足的范围 rubric 改判失败。候选尚无模型调用，保留初稿指纹与 wheel 后补“不承诺无游标下一页”，重新冻结启动身份。离线全回归为 3362 passed / 3 skipped（483.64 秒），Node 修正后 42 passed / 1 Linux 身份检查 skipped；正式真实候选复测仍待基线 20 组全部结束。
- 基线完成：20 组 / 41 轮全部取得持久 SUCCEEDED 回执，106 次实际模型调用、941007 token、复习写入 0，API/Pi 文件与进程及共享 Linux 锁绑定为 REMOTE_SOURCE_BOUND，语料 r400/i400/a30、KNOWN。独立任务复核为 17 完成 / 3 未完成；原严格评分保留 7 PASS / 10 FAIL / 3 UNKNOWN，不把有限面板与路径差异洗成通过。剩余召回缺口和核验未决另列，不将该开发诊断当整体发布门禁。初次 CLI 封存未传入采集时的 candidate 绑定，严格复算报 COLLECTION_CANDIDATE_MISMATCH；保留原报告及错误后，改用 `run_section(candidate=采集原绑定)` 重新封存，未关闭严格验证或重跑模型。
- 候选复测选择：使用完全相同的原始 manifest 和逐例 hash，定向重跑 c07/c09/c10（三组明确失败）及 c12/c13/c17/c19/c20（多次检索、综合解释、范围边界、换主题和意图细化），共 8 组 / 16 轮；其余 12 组不推断为修复后通过。仍受独立的 300 次 / 4000000 token 上限，选择、回执与预算持久保留。
- v17复测中新发现：三组主要失败已恢复；c13技术建议本身合理，但把题干未明确提出的“验证效果”归为原题共同要求，未满足本例明确的事实/建议区分。保留该失败和独立审核，不把正确常识当作原文证据。准备保留v17、增加v18，明确归纳的步骤/方案/效果验证属于模型建议；最终默认以完成相关复测的候选为准。c12首轮仍缺积压题覆盖且已明确说明，保留覆盖警告，不声称召回/核验已经修复。后续真实调用共用原复测总预算，不能因换候选重置上限。
- 最终真实结果：v17的8组/16轮为独立任务复核7完成/1未完成，59次调用、554735 token；v18对原c07/c09/c10/c13/c17五组/10轮复测，5组完成，27次调用、279699 token。v18原题事实仅概括定位/分析/优化，效果验证明确置于模型建议；范围说明未再承诺无游标下一页。v18原严格分数仍为1 PASS/4 FAIL，保留动作路径/有限面板断言结果，不将任务复核当严格门禁通过。所有阶段使用同一原始manifest与逐例hash，累计192次调用、1775441 token、67轮，复测两批合计86次/834434 token，未重置300次/400万token的复测总额。
- 归因与剩余项：明确阻断优先在目标承接/多目标完成；召回与选择仍有Agent已知正例漏进池、MQ积压正例进池未展示等缺口。基线15次SEARCH中6次核验PARTIAL、合计150个候选未决（按调用计，不是去重题数）；这些限制未改金标、未放宽证据或阈值，仍不构成全库召回、精度或完整V1质量通过。新版本仅对5组确认，不外推未复测案例。逐例、原始严格分数、独立审核及预算见[本机私有报告](../../data/reports/multiturn20-20261026/REPORT.md)。
- 最终工程验证：`.venv\Scripts\python.exe -B -m pytest -q -rs --junitxml=.tmp/multiturn20-20261026/full-offline-v18.xml` 为3364 passed/3 skipped，354.71秒；跳过为 `test_access_postgres.py::test_postgres_cross_instance_quota_admission_is_atomic`、`test_elasticsearch_live.py::test_live_es_rebuild_and_all_four_stage_shapes`、`test_question_usage_postgres.py::test_postgres_usage_migration_and_concurrent_aggregation` 的专用服务前提。`node --test tests/node/tool-contract.test.mjs tests/node/compact-query-wire.test.mjs tests/node/goal-completion-runtime.test.mjs tests/node/pi-runtime.test.mjs tests/node/runtime-identity.test.mjs` 为42 passed/1 Windows下Linux身份检查skipped；实际Linux运行身份另行核对。v18首次提示词超原限额27 bytes，留证后压缩为13999 bytes；定向104项通过，原14000限额未改。五页文档结构检查与生成声明检查通过，正文和diff另行核对；未做浏览器/SSE或SSH服务发布验收。
- 本机启用：离线复用缓存wheel和现有依赖，`--network=none`构建API/Pi镜像，无新增依赖下载；部署前确认无活跃run，停止三个隔离评测项目。API/worker/Pi运行文件分别与最终受测候选一致，共用同一Linux模型锁；Windows健康回执为query_agent_v18、数据库/索引ready、首页HTTP200、保活任务Running，预算仍8规划/24共享/8工具/256000 token/600秒。原始语料、凭据、回执及已有 `md/issue.md` 改动留在私有工作区，公开同步仅本次代码、提示词、测试和受影响文档。

## 多轮结果与最终回答评测（2026-10-25，实施前）

- 目标：按用户要求补齐逐轮实际题单、来源及最终回答的独立验收，接入现有 suite、报告封存和严格复算；本轮只做离线工程验证，真实模型和服务调用预算为 0。
- 事实：`score_query` 当前逐轮检查工具和断言，但来源题单只检查 `observed[0]`；`source_query_results_v1` 仅接受单轮。技术答案审核仅存在于专项 pilot，通用报告没有绑定独立回答审核。2026-10-24 定向 333 项通过及六模块演示只证明原范围框架可用。
- 区分检查：第二轮换范围、错误来源、缺失后续轮、分页和同请求取证后 ANSWER 必须分别被检查；主机 COMPLETE 或模型自报成功不能替代用户目标达成与技术正确性。
- 方案：保留单轮 v1，增加按原始 message 绑定的 v2 逐轮来源参考及可选持久工具 ordinal；每轮回答可声明独立 rubric，确定性完成/引用检查与独立语义审核分开。审核以外部文件绑定完整数据集、原预测字节、回答和 rubric 的 hash；缺审核保留 UNKNOWN，不改预测、金标或旧门槛。
- 验收：后轮错误不能被首轮正确掩盖；最终回答空缺、伪完成、跨轮借证、错误回答、失效/非独立审核均不能通过；报告封存后可脱离原输入复算，篡改审核及恢复时变更输入会被拒绝；单轮兼容及相关评测回归通过。命令、首错、剩余限制和最终结果在本节续记。
- 中间验证与首错：本地实际 host/collector 的八项离线集成通过；首组题单/回答/报告 152 项通过，XML 位于 `.tmp/evaluation-multiturn-20261025/`。回答持久回执收紧时出现两项 fixture/期望未同步失败，先留证后补真实最终回执和合法跨轮解释预期，未放宽生产校验。独立对抗检查另外复现布尔 ordinal、内外 run 身份矛盾、持久完成字段缺失及三个空容器导致的 AttributeError；分别补严格类型/身份一致性、缺证据 UNKNOWN 和安全容器检查，再做统一回归。只补审核的前后报告按审核覆盖变化标为不可作质量增益比较。以上真实模型/服务调用均为 0，旧金标和门槛未修改。
- 最终结果：`source_query_results_v2` 支持逐轮换范围、持久工具序号、会话身份和版本连续性；`answer_rubric/query_answer_reviews_v1` 将持久最终回答、引用和独立语义判断接入统一评分。合法跨轮题目解释保留，本轮引用 ordinal 不借前轮；审核按原始预测字节及数据集/轮次/回答/rubric 绑定，随报告封存、复算和恢复检查。评分协议显式升级 `evaluation_scoring_v2`，不覆盖历史报告。独立复核的合法、缺证、伪编号及篡改路径符合预期；内部回答 helper 的 failed 标志由正式入口统一校验，不构成额外整体质量认证。
- 最终验证：`.venv\Scripts\python.exe -B -m pytest -q -rs tests/unit/test_runtime_identity.py tests/unit/test_evaluation_http_contract.py tests/unit/test_collection_resume.py tests/unit/test_evaluation_integrity.py tests/unit/test_evaluation_preflight.py tests/unit/test_evaluation_suite.py tests/unit/test_service_pilot.py tests/unit/test_evaluation_system.py tests/unit/test_release_gate.py tests/integration/test_evaluation_collectors.py tests/unit/test_query_result_reference.py tests/unit/test_eval_latency.py tests/unit/test_eval_metrics.py tests/unit/test_query_result_multiturn.py tests/unit/test_answer_quality.py tests/integration/test_evaluation_goal_quality.py --junitxml=.tmp/evaluation-multiturn-20261025/regression-1.xml`：509 passed，0 skipped，57.83 秒。包含进程内实际 API→collector→多轮评分→审核→封存/复算/恢复/比较，模型调用为 0。
- 入口验证：`python -m eval demo --output .tmp/evaluation-multiturn-20261025/demo` 六模块 COMPLETED/VERIFIED，保留 NOT_ELIGIBLE；`python -m eval run eval/scenarios/user_scope_five.baseline.json --output .tmp/evaluation-multiturn-20261025/legacy-replay` 成功重评分旧 v13/v15 预测，仍为 5 FAIL，不冒充当前 v16 实测。四页文档结构检查通过，静态检查不证明正文语义或浏览器效果。机器记录见 `.tmp/evaluation-multiturn-20261025/verification.json`。
- 未覆盖与下一步：未运行整个脏工作区回归、真实模型/外部服务、浏览器/SSE 或正式发布。新场景须先冻结逐轮来源参考和回答 rubric，再采集并由独立审核者按实际输出补审核；缺审核继续 UNKNOWN，开发集不自动获得发布资格。本轮没有接入 Harness²、修改业务默认、部署或替换旧参考答案。

- 首轮检查（2026-10-25 10:03，Windows／本工作区）：`.venv\Scripts\python.exe -m pytest -q tests/unit/test_query_result_multiturn.py tests/unit/test_query_result_reference.py` 为 57 passed／1 failed；新多轮用例均通过，旧 `test_report_archives_source_reference_and_strictly_recomputes_it` 的封存后重算返回 `SCORER_VERSION_MISMATCH`。首错报告保留在本机 pytest-845 的该用例目录；当时其他子任务并发修改评分源码，严格身份校验拒绝变化符合保护契约，未修改断言或放宽校验。模型／服务调用 0、共享数据写入 0；不单独重试旧报告，待源码稳定后由根线程统一回归。

## 目标与已知事实

- 用户要求统一入口、环境预检、断点续跑、版本绑定和报告门禁先达到可用，以离线验证验收；历史数据、门槛和失败证据不变。
- 六模块采集与评分已存在，相关离线测试上一轮为 116 passed；直接运行 `eval.demo` 曾因未加载 `src` 失败，显式 PYTHONPATH 后成功。证据在本地 `.tmp/evaluation-readiness-20261015-hap0l_ny/assessment.json`。
- 通用采集只有 partial，没有完整续跑契约；预算保存在内存。门禁校验部分输入 hash，但未从原始输入重算指标，也未统一绑定候选身份。
- 历史质量 bundle 的 BLOCKED 是实际质量失败，不能靠换门槛解决。用户反馈实际问答偏离预期，具体例子稍后补充，暂不能判断根因或宣称语义达标。

## 选定方案与边界

- 增加 `python -m eval` 与显式 suite 配置，统一预检、运行、恢复、比较、门禁；从仓库运行时加载本仓库 src。旧模块入口保留。
- 预检不调用模型；冻结数据、评分协议、候选身份、预算和采集方式。缺少必要版本或真实调用授权时失败并指明缺项。
- 采集前持久化调用状态和保守预算，完成行原子落盘；恢复校验输入、身份和已完成结果，无法判明的在途调用不自动重放。
- 新报告封存输入、评分协议和候选版本，严格门禁从输入重算；历史报告继续支持明确标记的历史校验，不能冒充新正式验收。
- 报告区分流程验证、组件评测与真实用户任务覆盖。真实案例稍后进入 dev 回归，不自动成为独立 test 或修改预期。
- 代价：新增少量编排与校验代码；对不完整旧证据采用明确限制而非补造身份。本批不调用真实模型、不部署、不更改业务标签与门槛。

## 验收与检查点

- 无私有语料、数据库和模型凭据也可执行完整合成离线流程；合成结果保持 NOT_ELIGIBLE。
- 错误环境/配置在采集前失败；恢复不重复完成项，预算不清零，在途不确定状态安全拒绝，输入或版本变更拒绝续跑。
- 篡改预测、alignment、指标、逐样本结果、门槛/评分协议或混合候选被门禁检出；比较不把协议变化或缺失指标当作质量提升。
- 补充针对性单元/集成测试，记录命令、实际结果和未覆盖范围；更新已有操作手册。

当前检查点：第一批实现及离线验收完成。实际用户案例待用户补充；真实服务身份和模型质量留待授权预算后的验收。

### 实现中的离线检查

- 首次并行测试命令选入尚未写完的 `test_evaluation_preflight.py`，pytest 未收集测试（exit 1）；随后只检查已完成的 suite 测试。
- `python -m pytest -q tests/unit/test_evaluation_suite.py` 首轮 4 passed / 1 failed：负例已正确判定任务失败，但测试错误假定断言 ID 没有 turn 前缀；同时 Windows 子进程输出按系统编码而父进程按 UTF-8 解码，出现读取线程警告。先核查实际断言 ID，再修正测试与 CLI 输出编码，不改评分条件。此次仅合成数据、模型调用 0。
- 修正后复测遇到另一并行编辑中的 collector 缩进错误（4 failed / 1 passed），交由文件所有者完成编辑后统一集成验证；stored predictions 的 suite 改为仅在需要采集时导入 collector，减少离线依赖。此轮不代表最终验证结果。
- 集成初轮 183 passed；随后复核补充恢复报告路径 resolve、输入 hash 直接核对、Windows 文件名冲突及“采集完成等待独立对齐”的边界，相关 81 项通过。
- 审查确认模型 usage 的负整数可能降低预算，且声明的 model_revision 未参与原有别名匹配。选择保守保留非法 usage 的预算占用、拒绝非法调用数/估算，并逐项比对已声明的模型 revision；不放宽门槛。预算定向 120 passed（含既有采集/评分测试）；最终集成结果待下面补充。

## 最终结果与验证范围

- 已实现 `python -m eval` 的 demo/preflight/run/resume/compare/check；旧 `eval.*` 入口也加载本仓库 src。suite 明确输入、候选、协议和回归项，禁止静默遗漏及 Windows 目录名冲突。
- 采集预算与在途状态先原子落盘，完成行持久化后才进入下一项；完成输出可离线补写。无法确认的调用不自动重放。新抽取缺独立对齐时进入 NEEDS_INPUT，补齐原目标路径后续跑；通过后锁定对齐 hash。
- 报告封存必要输入、候选、评分协议及所有产物，严格检查重新计算；恢复另核对报告与本次输入。比较显示协议/缺失指标/断言变化。实际模型 revision 有声明时逐项核验，尚未发生调用的采集前阶段允许待核验。
- 历史数据、默认版本和 v2/v3 门槛未改。HTTP 远端源码身份目前不具备认证证据，严格发布仍阻断；本地 source 指纹不能替代它。

Windows、Python 3.12.1 实际执行：

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/test_evaluation_suite.py tests/unit/test_evaluation_preflight.py tests/unit/test_evaluation_integrity.py tests/unit/test_collection_resume.py tests/unit/test_evaluation_system.py tests/unit/test_eval_metrics.py tests/unit/test_gold_validator.py tests/unit/test_delegated_gold.py tests/unit/test_release_gate.py tests/unit/test_source_gold.py tests/unit/test_label_gate_version.py tests/integration/test_evaluation_collectors.py tests/unit/test_docs_check.py
.venv\Scripts\python.exe scripts/check-docs.py
git diff --check
```

结果：249 passed、0 skipped；1 条既有 Starlette/httpx 弃用警告。文档默认 14 页静态检查与 diff 检查通过；不是 Mermaid 渲染或全文语义认证。

真实 CLI 冒烟另执行 8 条命令：demo、preflight、resume、第二次 run、compare、单报告 check、合成 bundle 严格 check、历史 v4 bundle `--legacy` check。输出在本地 `.tmp/evaluation-batch1-final-scfdvrb8/`：`commands.json` 保存完整命令/退出码，`smoke-summary.json` 保存结论，`demo/run/index.html` 为报告入口。六模块 COMPLETED、证据 VERIFIED、比较可比；合成质量 NOT_ELIGIBLE、发布 BLOCKED。历史 v4 仍为抽取/分类/去重/检索及两项分类回归共六项阻断。

本批未运行真实模型、数据库/ES/Pi 活体或部署验收，模型调用 0；未执行整库工程回归。离线负例证明“工具和参数正确但返回结果不符预期”会失败，仍不证明当前真实问答达标。下一步以用户稍后提供的原问/实际结果/独立预期补 dev 回归，遇到业务裁决或真实模型预算再由用户确定。

## 下一步：真实服务与真实问答（待裁定和预算，10-15）

本节是下一批执行方案。本轮仅核查代码、装配和历史汇总，不代表已经核验今天的运行服务；实际偏差案例后补，不阻塞准备。

### 推进顺序与开跑条件

1. **零模型版本盘点**：以用户实际使用的环境/账号模型选择为基线，只读获取实际启动路径、安装包/镜像、API/Pi/资源 hash、配置摘要、数据/索引/标签 revision、物理索引/向量模型/维度及共享锁。配置只记录白名单和指纹，不导出凭据或私有原文。worker 只登记当前事实的生产来源，本批不重新导入。
2. **绑定真实运行物**：冻结基线；如比较工作区候选，另冻结候选和隔离实例。API 绑定安装/挂载内容，Pi 绑定实际 runtime 与上游产物，不靠 HEAD、固定 pi_version 或请求模型别名推断。用独立部署测量对照服务回执，采集前/每轮/恢复时检查，保留最初及结束证据。
3. **先导 8 场景 / 12 轮**：先执行其中 3 场景 / 4 轮，确认模型链、预算、来源与持久化回执后继续。覆盖精确列表/统计、语义找题、技术回答、澄清纠正、分页指代及无结果；账号状态从声明的空状态开始，复习写入首批关闭。每条先冻结预期和来源，再运行。
4. **扩展既有诊断集**：现有 v1/v2 均为 24 场景 / 34 轮，选择与裁定口径一致的冻结版本，记录来源已见和修订关系。旧 run-v2 是 13 场景 / 20 轮的 partial，不能当作全批验收；本轮应完整运行并单独记录。后续用户偏差进入 dev，正式 test 另准备足量独立来源与未见场景。
5. **出能解释失败的报告**：分别判目标/范围、结果内容、回答语义、展示来源、完整性、多轮终态；同时报告首个有效反馈与最终持久完成耗时、调用和未知 usage。正确工具不免除结果及答案审核，部分可用不计完整成功。浏览器登录/SSE/断流恢复另做少量产品验收，不能用 HTTP collector 冒充已覆盖页面链路。

真实调用前须先补三处技术适配，均由实现方处理，不交用户裁定技术细节：

- [serve.py](../../eval/serve.py) 当前无 API/Pi 实际源码身份；[checkpoint.py](../../eval/checkpoint.py) 将 HTTP 固定标为 `REMOTE_SOURCE_NOT_VERIFIED`，[release.py](../../eval/release.py) 严格阻断。须建立部署测量→服务身份→请求回执→报告门禁的核对链，仅填自报 fingerprint 不足以放行。
- `serve.context.query_max_model_calls` 返回规划上限（默认 4），[collect.py](../../eval/collect.py) 却用于整轮预留；[RequestLimits](../../src/interview_intelligence/providers/runtime.py) 整轮为 6，包含向量/重排/复核。须区分并使用真实整轮上限，增加跨阶段预算离线验证后开跑。
- 共享锁判定目前固定 `/app/runtime/model-call.lock`，SSH 运行入口使用部署目录下 `state/run/model-call.lock`。须核实同一文件/挂载并支持对应布局，不放宽为任意路径。旧部署验证脚本含真实问答，整包导出脚本还包含敏感配置，不能直接充当零模型版本探针。

### 用户集中裁定的业务项

| 待定项 | 建议方案与边界 |
| --- | --- |
| 新系统/新应用设计是否纳入 ENGINEERING | 明确交付新能力且有可检查功能行为时纳入；纯架构介绍、原理解释、已有项目复述不纳入。口头任务的 response_form 仍为 VERBAL。16 项旧/新政策冲突主要据此逐项复核，不能整体重贴标签。 |
| 系统设计找题的弱证据补充区 | 确定正例进主结果；标题过简、题干残缺、局部组件等边界另列可参考/待确认，是否默认展示补充区由用户定；不混入确定总数。来源事实不足继续核查，不交用户猜事实。 |
| 普通找题的最低可用标准 | 保留“完成 / 部分可用 / 失败”，部分不计完整成功；先沿用现有宽设计开发案例的至少 10 个确定正例、4 类对象、无明确负例、来源支持等场景标准，其余按意图逐条冻结。该标准不外推所有查询。 |
| 参考回答的验收目标 | 要点覆盖、关键事实正确、假设明确、结论可执行；代码/SQL 加可运行性与边界检验。允许多种正确表达，严重事实错误或关键缺项不能通过；题干引用不认证答案正确。 |
| 体验阈值 | 先分统计/找题/技术回答报告真实首反馈和完成耗时，再冻结 SLO；180 秒是执行超时上限，不是体验达标线。首批不凭未校准延迟阈值改旧门槛。 |

旧样本的 C++ 模板/BOSS/LRU 裁定继续有效；工程 filter 含 MIXED、strict ENGINEERING 另算、SEARCH 非全集、UNKNOWN 非负例、来源与授权不变量均已定，不重复询问。已有 Agent 标注授权继续使用，独立审核不标为 human_verified。

### 预算一次说明

先确认目标环境和实际账号模型链，再选择单版本诊断或 A/B；查询、回答、embedding、重排及产品内复核共用整轮限额。以下按当前默认 **每个新请求轮最多 6 次 / 96,000 tokens** 计算，修好预留契约并核对实际环境后才可使用；是保守硬上限，不是账单或预计消耗。

| 批次 | 调用上限 | token 上限 |
| --- | ---: | ---: |
| 先导 8 场景 / 12 轮，单版本一次（推荐先批这一档） | 72 | 1,152,000 |
| 既有完整 24 场景 / 34 轮，单版本一次 | 204 | 3,264,000 |
| 同一 34 轮，两版本各一次；替代上一行 | 408 | 6,528,000 |
| 小集 12 轮共 3 次稳定性；首轮已跑则只追加两次 | 216（追加 144） | 3,456,000（追加 2,304,000） |

先导之后再做单版本完整基线合计 276 次 / 4,416,000 tokens；先导之后做双版本完整比较合计 480 次 / 7,680,000 tokens。稳定性是另选项目，不能把上表各行全当作默认授权。先导中的前 4 轮包含在 12 轮总额内。

首批建议不比较多个供应商、不重跑抽取/分类/去重、不另开 API 裁判（额度 0），由已授权的独立 Agent 审核并注明主体。若要求额外 API 裁判或多模型比较，必须在同一预算决定中另列对象、次数和 token 上限，不能藏在服务调用中。金额上限须根据实际账号计费/套餐核算；仓库 pricing 未验证，不能把 token 预留换算成可信人民币报价。

所有重试及证据补核验计入同一额度，不自动扩容；身份/快照/锁不符、telemetry 不可核对、未知在途、剩余额度不足完整一轮时停止。连续两次提供方失败或超时先定位；语义错误保留，不边跑边改预期。历史旧版本的 2 个完整语义搜索样本约 105 秒/轮，只说明体验需要实测，不是当前版本性能承诺。

核查依据：上述源码、[运行配置](../reference/runtime-config.md)、[10-15 来源评测](../verification/2026-10-15-source-optimization.md)、[标注指南](../annotation-guide.md)及其明确链接的审核摘要；未连接服务、调用项目模型或改变业务/门槛。

## 第二批执行授权与检查点

用户已同意上一节建议的业务口径，授权补齐三处技术适配、只读核验当前实际服务，并运行单版本 8 场景 / 12 轮真实问答。**本批总预算最多 72 次项目模型调用、1,152,000 tokens，不自动追加；重试和复核包含在内。** 偏差案例后补；独立 Agent 审核沿用原授权，额外 API 裁判为 0。

选定方案：先封存当前服务身份，以它为唯一待测基线；评测适配以独立隔离入口接入同一运行包和模型链，新增评测代码不替换生产业务实现。API/Pi 的运行文件、资源与独立部署测量一致，锁文件必须与部署调用方相同，整轮预算先预留后调用。先独立冻结 8 场景 / 12 轮及答案审核要点，再完成 4 轮先导并核对，之后按同一预算继续。

验收：三处适配离线测试通过；真实身份、数据版本、模型链、共享锁有可复核证据；12 轮逐项核对结果/来源/答案/终态；所有失败与未知 usage 保留，报告执行完整性、业务成功、耗时及预算，不用通过流程替代效果。发现阻断先保存证据再处理；不额外重跑已完成请求。

当前检查点：三处适配、SSH 服务版本核验、冻结的 8 场景 / 12 轮采集、独立业务审核、数据库对账和最终报告封存均已完成。执行失败 0；业务为 6 通过 / 1 失败 / 1 部分可用。实际扣计 15 次 / 186,658 tokens，预算未追加，已完成请求未重跑。

- 实际服务为 r295/i295/a31、query v13（SHA `ed14907f245f758b7eeb704dad9bde68671f438593f3c6c70f5e3128e49ff159`）、rerank v15；不是工作区 r31。最近活跃个人账号使用 Ark 查询/重排，embedding 为原全局 Doubao 1024；系统 Qwen inactive。隔离实例复制相同业务包和 Pi runtime/依赖，新增评测账号保留多租户模型选择，仅 Pi wrapper 增加认证身份探针。
- API/Pi 独立进程测量与实际运行观察吻合，API/worker 同锁的 device/inode 匹配；整体调用 6、token 96000 与规划 4 分开。Linux Pi 4052 个运行文件测量约 1.38 秒。独立证明与候选固定 hash 在本地 `.tmp/evaluation-live-20261015-oh5gveb9/` 和 SSH 独立运行目录保存。
- 独立事实统计：字节 545 canonical / 565 occurrences，腾讯 228 / 238；Redis 源审核 43 窗口（21正/11负/11边界），gold/oracle 冻结先于本轮预测。Gold 为 seen-source dev、Agent 审核、人审 false、不可用于发布。
- 实际离线集成命令：`.venv\Scripts\python.exe -m pytest -q tests/unit/test_runtime_identity.py tests/unit/test_evaluation_http_contract.py tests/unit/test_collection_resume.py tests/unit/test_evaluation_integrity.py tests/unit/test_evaluation_preflight.py tests/unit/test_evaluation_suite.py tests/unit/test_service_pilot.py tests/unit/test_evaluation_system.py tests/unit/test_release_gate.py tests/integration/test_evaluation_collectors.py`：240 passed，1条既有弃用警告。独立 Node 身份测试 2 passed/1 Windows 条件跳过；Linux 实际测量补足平台核对，未据此声称整库回归。
- 开跑前失败：首次 SSH `python -c` 受 PowerShell 引号影响，改用 stdin；隔离 API 配置复制把 CIDR 列表写为 Python repr，保存错误后改为 JSON 并只重启隔离 API；登录首次缺少实际 UI 要求的 X-Tenant-Action，保存 403 后补齐该头。均发生在问答调用前、模型调用 0。记录与原始错误位于本地 attempts/start-failure/binding 日志；生产服务未重启。

### 最终核对与离线收尾（10-16）

原输入、预算和检查点续跑完成 8 场景 / 12 轮。数据库逐条对账的 40 项检查通过：8 会话、12 个 `AgentTurn` 均持久 `SUCCEEDED`；15 个 `ModelCall` 的 ID、归属和 usage 与原始预测及检查点一致，操作为 12 QUERY_PLAN / 1 EMBEDDING / 1 RERANK / 1 RERANK_VERIFY。已知输入 178,985、输出 7,673，扣计 186,658 tokens；未知 usage、重试、复习状态/事件、未结预算均为 0。生产 API/Pi 文件及进程、worker 进程、r295/i295/a31 与共享锁前后未变。仅两条隔离 unit 已停止，生产继续运行，原始数据与账号证据保留。

结果核对已经发现此前“路由正确”无法解释的偏差，不做模型重跑或修改金标：

- p07 澄清首轮正确；第二轮用户只限定字节和频次，实际却额外加入 `ALGORITHM_PROBLEM`，返回算法子集（3、2、1、1、1），与独立全范围前 5（3、3、3、3、2）不符，明确 FAIL。
- p03 返回 3 条处理方案、2 条纯概念，且首位是概念区别；规划把处理场景扩展为区别/原理。6 处来源 hash、位置和身份及第二轮指代正确；冻结审核仍为 UNKNOWN，看到预测后的补审另记部分可用，不补贴正例标签。
- 其余 6 场景结果符合独立预期。p05 实际回答 3 / 2 / 1，经独立 SQL 复算并审核文字和无伪造来源；列表、分页、零结果、轮次统计及改公司均与独立关系事实一致。

12 轮采集完成 P50 9.45 秒，nearest-rank P95 / 最慢 88.77 秒（Redis 找题），技术回答 18.00 秒；此计时包含前后身份/状态探针。没有浏览器首反馈/页面 SSE 验收，不能据此设定或声称通过体验 SLO。模型 revision 回报 `auto`，证据绑定实际调用别名与运行文件，不认证供应商不可变权重。

严格报告已初次离线复算为 VERIFIED / BOUND_REMOTE_SOURCE，作用域仅为独立测量的隔离运行进程。dev 金标明确无发布资格；路由任务成功 87.5% 亦低于既有 90% 线，加入独立来源/答案审核后仅 6/8 场景完整符合，不能宣称本批或完整 V1 质量通过。

收尾审计发现三个离线可复现边界，修法先记录再实施：失败终态本身不能证明 ModelCall 日志完整，日志缺失时不得把预留退成零，应保留整轮未知扣计；异常负 token 不得被效率指标当作已知 usage；恢复仅比较会话数量不足，应绑定不含正文的会话/turn 账本指纹。代价是未知失败更保守占用预算、缺新指纹的旧未完成检查点不能静默续跑；已完成旧回执仍可核查。本批是独占评测账号且全量遥测逐条对账，15 次 / 186,658 的实际结果不受影响。修正仅做离线反例回归，最终评分器重新封存与复算，不再启动真实问答。

收尾修正已完成：成功回执也须持久 `model_attempts` 与完整唯一调用日志数一致；失败或不一致保留整轮未知预算并阻断续跑。12 轮原始回执的尝试数已另行离线逐条核对（11 轮各 1、SEARCH 4），与数据库账本一致。会话/turn 指纹不含正文；旧缺指纹检查点有明确拒绝及完成回执兼容测试。非法 usage、合法 0 与 embedding 空输出均有反例验证。本批真实采集工具单独冻结保存，这些收尾边界没有额外启动真实服务验证。

最终在上述 Python 命令增加 `tests/unit/test_eval_metrics.py tests/unit/test_budget.py` 后统一执行：**290 passed / 0 skipped**，39.26 秒，1 条既有 Starlette/httpx 弃用告警。根线程另执行 Node 身份测试 2 passed / 1 Windows `/proc` 条件跳过，`node --check services/pi-agent/server.mjs` 与 `identity.mjs` 通过。未执行整库回归、Pi 构建或浏览器/SSE 验收。

私有结果入口：[先导核对报告](../../data/reports/service-pilot-20261015-oh5gveb9/REPORT.md)。同目录封存冻结金标、事实来源、原始回执、独立审核、40 项终态对账、隔离停止证明、采集工具和最终离线评分器；详细命令/结果/跳过项见 `evidence/validation.json`。独立审核另核对了 31 处来源及 19 个原文件；审核脚本初次将 NULL 直接与 UNKNOWN 比较的诊断及修正依据保留，未改金标。

最终 CLI `python -m eval check <封存模块目录>` 为 VERIFIED / BOUND_REMOTE_SOURCE；`python data/reports/service-pilot-20261015-oh5gveb9/recheck.py` 离线复查本包 307 个文件 hash、答案审核、原始评分与运行绑定全部通过，网络/模型调用 0。原始输入与 6 PASS / 1 UNKNOWN / 1 FAIL 不变，质量仍 NOT_ELIGIBLE。下一步优先修复两类范围漂移并沿原问/独立预期做 dev 回归；新的真实重跑、扩展集或 A/B 另列预算，当前剩余额度不自动使用。

## 用户追加的五类查题（10-16）

用户明确要求增加「agent工程设计题」「字节的算法题」「工程场景设计题」「redis场景题」「mq场景题目」。本批只测试这五个原问，各一个全新会话、一次请求；不替用户追加限定词、变体或重试，不修改生产业务以求通过。目的为检查自然短问的意图、显式范围、结果内容和来源，不再只看工具选择。

预算沿用用户已确定的同版本总硬上限，不提高授权额度：上一批已耗 15 次 / 186,658 tokens，本批单独限 **30 次 / 480,000 tokens**（5轮 × 6 / 96,000）；两批最大合计 45 次 / 666,658，低于原 72 次 / 1,152,000。这五问来自本次明确追加，不自动消费其他余额；额外 API 裁判为 0。版本若变化先保存事实并区分结果，不混成原版本延续。

已知：上一批 Redis 混入概念题、澄清后无依据添加算法范围；当前工作区有另批候选未部署，不能拿工作区结果冒充当前服务。待验证：五个短问会如何规划、是否正确区分工程任务/原理介绍/项目复述、算法实题/概念问答、场景/机制，以及来源是否真正支持命中。

选定方案：先零模型复核当前 SSH 运行物与快照；独立检查原始事实和来源，冻结每问的正例、反例、边界及审核标准；再以同业务包、真实个人模型链的新隔离账号运行，使用上一批已离线验证的完整性守卫、共享锁和持久预算。每问完成后只读核验，语义失败保留，不补跑。最终逐题审目标/硬筛选/相关性/来源/完整性和延迟，账本/版本前后对账；报告仍为 seen-source dev，不替代正式发布质量。

当前工作目录：`.tmp/evaluation-scope-20261016-566081d5/`，授权和累计上限保存在其中 `authorization.json`。五个原问已逐字发送，各一个新会话，无人工过滤或页数覆盖、无变体或重跑；采集、逐条内容审核、前后对账及报告封存均已完成。

- 零模型前后核验确认生产 API/Pi/worker、业务文件和模型 profile 均未变，沿用 r295/i295/a31、query v13 / rerank v15 / scope v6。只将新批 `as_of` 固定为 2026-10-16；旧 facts 保留 10-15 日期及原 hash，复用依据是版本和文件相同，不伪装新导出。
- 采集前冻结三份独立源审核：Agent 81 条（28正/24负/29边界）；字节算法16条、工程21条；Redis104条、MQ52条。Gold hash `be0e53ec926726b50e9b401f019f9363202723ada6cad6b3f78487addb283131`。工程沿10确定正例/4类对象；其余每问的5条强相关目标是本批覆盖标准，不是声称用户原问指定数量。有限面板不充当全库召回分母。
- 真实返回依次 6 / 28 / 9 / 20 / 8，共71个结果。实际15调用（6 QUERY_PLAN / 3 EMBEDDING / 3 RERANK / 3 RERANK_VERIFY）、输入121,715 / 输出11,790，共133,505 tokens；加上一批累计30调用 / 320,163 tokens。无重试、未知 usage、复习写入或在途预算；两条隔离 unit 已停止，生产未动。
- 独立终态对账47项通过：5会话/5轮、调用ID和usage、持久attempts、最终会话/turn账本、数据/索引/锁及运行文件进程均一致。72个展示来源见证、50个原文件的身份/hash/span/行号检查通过；有真实引用不代表语义范围正确。
- 逐轮服务回报 `total_ms` 为 Agent106.26秒、字节算法6.77秒、工程82.52秒、Redis14.89秒、MQ45.60秒；采集含身份/状态探针约多0.6秒，页面/SSE首反馈未测。不与前批不同查询组合的中位数直接作版本性能比较。
- 准备失败保留：首次 gold 构建因独立审核的“未看预测”字段在嵌套元数据而非统一顶层出现 KeyError，发生在建 gold/调用前；读取器按明确schema取对应字段并核对事实hash后通过，未放宽独立性要求或改标签。原错与修法在 `attempts.json`；模型调用0。

最终独立内容结论为 **1 PASS / 1 FAIL / 3 PARTIAL**：字节算法28条的任务/同occurrence公司/来源通过，但不认证全库无遗漏；Agent补审3正/3边界，首条与预审保守标签的分歧单列，冻结标签不改；工程8正/1边界但模型主动排除Agent/AI应用、限定传统后端，意图范围FAIL。Redis16正/4边界，MQ6正/2边界。

Redis与MQ都把“场景”硬映射旧 `SCENARIO` 类型。按全部active occurrence核对同canonical的替代见证，冻结面板中的Redis55个强正canonical有34个不可能满足 Redis+SCENARIO，MQ34个有27个不可能满足 中间件+SCENARIO；不是全库召回率。Redis实际前20与该狭窄标签集合30题/37次中的频次排序一致，说明SQL按标签执行正确不等于语义范围正确。工程规划同样明确无依据排除AI；只能指出冻结正例未返回及规划范围错误，不臆测各例在哪个检索阶段丢失。

私有入口：[五问核对报告](../../data/reports/user-scope-five-20261016-566081d5/REPORT.md)。`python data/reports/user-scope-five-20261016-566081d5/recheck.py` 已执行通过：273文件hash、5原问、72来源见证、累计额度、47项终态对账及严格报告运行绑定 VERIFIED / BOUND_REMOTE_SOURCE，复核网络/模型调用0。独立补审绑定完整预测、前两行prefix未变，原gold不动。原始响应、预审/补审、范围可达性证据及少量canonical展示细节备注均保留。

本轮只增加私有评测材料与本页/状态页结论，没有修改业务或评测实现、默认配置、历史门槛；未重复上批290项工程回归。已执行新gold静态验证、私有脚本语法检查、真实5问、来源/数据库/预算/版本复核及报告严格复算，文档静态引用检查通过。下一步优先修“模型无依据添加范围”和“用旧题型标签代替场景语义”，保留五原问为dev回归；本批不追加真实重跑或问法。

## 五问固定对照与范围修复（10-16）

用户进一步要求先独立生成五问的正确查题答案，再优化问题、固定对照和评测集。选定方案：以封存的 r295 原始事实与原文为依据，独立复核正例/负例/边界，先冻结新参考版本（保留原预审与争议），再改实际查询契约；不把生产返回内容直接当正确答案，不把有限审核面板当全库全集。重点核查短问被无依据限定传统后端、场景被硬映射旧题型，以及显式筛选和继承范围的区别。

本批先完成零项目模型调用的来源审核、可复用评测入口、范围修复和离线前后对照。验收包含五个原问不变、来源 hash/span 可核对、固定参考先于候选、明确反例不混入、显式 UI/用户约束仍有效、算法 SQL 路径不退化。主要代价是自然场景改走语义检索后不再具有旧标签 SQL 全集含义；离线可达性与计划修复不能冒充真实模型命中率提升。已有未提交业务与工具契约改动保留，不部署生产。真实候选只在版本、轮次与限额具体确定后另行确认，原总预算不扩容。

离线检查点：新增来源结果契约初次定向测试 23 通过 / 1 失败。失败是测试把 `verify_report` 返回当成含 `metrics` 的对象（实际接口只返回核验元数据/配置），评分与来源归档已通过；保存此事实后按实际接口读取封存 `metrics.json` 核对，不调整业务标签或通过门槛。环境为工作区 Python 3.12，项目模型调用/预算消耗 0。

v14 首次默认装配检查为 57 通过 / 3 失败：两项指出提示词 14,865 bytes 超过原 14,000 bytes 多步预算约束；一项仍期望宽工程示例必设 SYSTEM_DESIGN 软偏好，而新示例允许跨 AI/传统工程、偏好为空。保留长度限制并精简新增文字，旧偏好断言改为核对宽范围示例的真实契约；不放宽预算。主机首次测试 74 通过 / 8 失败及分层原因另存私有 `scope-attempts.json`，包含一个主题标签被误认为题型标签的真实缺陷及旧模型改写断言，修复后再重跑。

独立复核在冻结前指出来源评分器的真实缺陷：`revision_id + quote` 缺少 occurrence/hash/定位时，可把同版本同文案的两个提问同时匹配到正负例，错误借入正例。选择对本次新来源契约要求 occurrence、原文件hash及明确字符/行定位，缺失或歧义按无效来源失败；既有不启用该契约的数据不受影响。增加缺字段和重复文案反例，不用弱引用凑通过；修复前未产生正式新报告，模型调用0。另将报告测试从 legacy 分支改为实际 strict 重算，并将原审阅文件的路径/hash/独立性声明纳入报告封存。

扩大离线回归初次为 345 通过 / 3 失败：澄清后的短答是否保留原找题目标需核对；一个无旧原话的测试仍把模型凭空恢复的秒杀系统当权威；另一个反馈测试用无检索器的 LIST 假装设计检索，受新真实路由影响。先保留失败，分别修主机和更新不合新契约的 fixture，不能统一删断言。旧预测用新gold直接封存被严格门禁拒绝 `COLLECTION_DATASET_MISMATCH`（符合预期保护）；后续明确采用存储预测重新评分，原采集回执另作来源证据保存，不伪造它针对新gold采集。以上均离线，模型调用0。

存储预测初次新评分暴露 SQL 列表与 SEARCH 的来源结构不同：SQL 只有 revision + 完整字符span的 `source_summary`，没有 SEARCH 的 occurrence/hash；直接强制后一结构使字节28条和Redis20条全部误判无效。选择显式支持两种证据：SEARCH仍强制完整身份/hash/定位；SQL仅允许完整字符span与冻结事实精确、唯一对应，独立原文件hash照常校验，解析出的身份标明来自冻结事实，弱引文/歧义仍失败。另发现API在主机将 final=false 计划改为终态澄清后仍按旧plan继续规划，改为检查实际run终态，追加零后续规划反例。

内容复算还指出评分缺项：字节 SQL 声称完整算法范围、首末同页共28题，仍漏掉新参考中的JSON路径求值正例；只有最低5题断言会误判PASS。新增通用检查：当完整语义SQL回执明确零offset、无TopN/后页且已展示其宣称全部结果时，必须包含已审相关正例；不能借有限参考认证全集，也不能容忍已知反例。SEARCH仍只报告面板正例覆盖，不强求单页覆盖全库。冻结题单/门槛不改，两个版本使用同一评分器。JSON题旧REJECT/新正例的业务理由继续保留，不在此运行写共享数据库。

用户随后明确批准隔离候选真实实测一次：query v14 / 工作区 rerank v31 对五原问各一个新会话一次，**本批最多30调用/480,000 tokens**；连同此前30调用/320,163 tokens，最大累计60/800,163，不提升原72/1,152,000上限。无额外API裁判、不部署、不语义补跑。参考v2已先行冻结：`data/gold/user-scope-five-20261016-v2`，manifest SHA `39abd02284f5c0451d8524a388f674dafabf7b19a3636a5d4cbbd7ce721dc5c1`；597条逐问/原题审核，强正canonical依次76/29/85/63/36，已见来源开发用途。新隔离run为 `evaluation-candidate-five-20261016-b8dc8e3b`，使用新账号、同个人Ark模型链和Linux共享锁，候选与生产差异按实际hash列明，生产r295/i295/a31事实不写入。真实采集前保存最终工程检查和身份/预算预检，结束逐项核对来源、模型账本、终态与生产未变并停止隔离unit。

候选采集中独立审核再发现评分器例外未限定路线：完整span的SQL summary fallback也可用于SEARCH，使后者绕过完整occurrence/hash。保存复现后将例外限定 `meta.route=SQL` 且实际计划为 LIST/NEXT，新增SEARCH与伪造SQL-route反例。此修复只改最终离线评分器，固定题单及隔离业务候选不变；新旧预测统一用修正后的评分器重算，原采集工具另行封存，不能把本地评分器改动当成候选模型质量提升。

最终检查点：五原问候选各实测一次完成，实际 query v14 / rerank v31 / scope verify v15，r295/i295/a31 未变；19 次调用 / 193,702 tokens（5 QUERY_PLAN、4 EMBEDDING、4 RERANK、4 RERANK_VERIFY、2 EVIDENCE_REPAIR），累计49次/513,865，未超授权。5轮持久SUCCEEDED，47项终态/账本/版本/共享锁及生产未变对账通过，无未知usage、未结预算或复习写入；只停止本批隔离实例。运行绑定严格 VERIFIED / BOUND_REMOTE_SOURCE，只覆盖实际隔离进程；生产未部署。供应商revision=auto，不能据此认证不可变权重。

同v2金标及最终评分器重算前后预测：强正canonical命中依次3→6/76、28→28/29、7→10/85、11→13/63、6→13/36；分母仅为已审有限面板，不是全库召回。候选0 PASS / 2 UNKNOWN / 3 FAIL：Redis仍混3条概念题，MQ混1条原理题；Agent有13个证据异常候选、11个已召回固定正例被筛掉；宽工程另有15个已进入筛选记录的固定正例未返回。字节28条及顺序与基线相同，JSON路径题仍被旧a31审计REJECT，保留新正例/旧拒绝理由且未写共享库。事后审核Agent7正4边界、工程11正2边界，冻结分数不改；4/5轮total_ms更慢，未冻结SLO，不能宣称效率或语义质量通过。候选还包含此前未部署的重排器/工具契约，不能把变化全归因v14。

已执行相关Python工程回归375 passed/0 skipped、收尾评分器76 passed（有重叠）、Node18 passed/0 skipped，7页文档静态检查通过。原五问封存包273文件/72来源见证重新核验通过；本次92结果/102来源见证及发布文本核对通过，私有入口为[固定参考与候选核对报告](../../data/reports/user-scope-five-v2-20261016/REPORT.md)，同包保留最终离线评分器、真实采集工具、固定题单、三份严格报告、逐题补审与零网络复查入口。未执行整库回归、Linux Pi构建、浏览器/SSE、稳定性或额外候选。下一步补场景任务证据契约、分层修Agent/宽工程误拒并对齐算法审计口径；不直接禁KNOWLEDGE、不恢复硬SCENARIO筛选、不因补审回填金标，本批不再追加真实调用。

## 五问剩余质量与耗时修复（10-16，离线批）

目标与授权：按用户新要求，优先修 Redis/MQ 概念误收、Agent/工程误拒及字节算法漏题，并查清耗时增长；固定 v2 gold、历史预测/回执/门槛全部不改，只做离线对照。当前已消耗49次/513,865 tokens，本批项目模型与真实服务调用预算为0；再次实测须先给出候选版本及新增预算，不自动使用余额或部署。

事实与假设：上批来源/运行/预算核对已通过；四个场景负例在 query_task=OTHER 下获 KNOWLEDGE 且二次核验CONFIRMED，缺场景任务证据约束。设计误拒同时涉及独立系统偏好、构造任务识别和证据结构，不能统一放宽。JSON路径题被a31审计REJECT，查询SQL执行本身未漏，须核对算法审计口径及完整发布路径。4/5问变慢与Redis由SQL转SEARCH、重排/核验/修复串行调用有关，各阶段占比及输入膨胀仍需原始遥测复算，不能预先认定唯一原因。

方案与区分检查：三条质量链路分别从实际主机与提示契约修复，避免硬编码五问/私有ID和用关键词直接贴金标；算法修复保留旧审计并走可验证的本地候选审阅/投影，不静默改共享事实。耗时先用已封存逐调用计时和token独立拆分，优先缩减重复证据/无用工作，保留来源与安全检查。用冻结原文、真实历史模型输出重放和必要的合成反例分别说明主机可确定修正及仍需模型重跑的部分。

验收：v2所有冻结文件hash不变；四条明确场景反例被新契约拒绝且正例不被粗暴禁用；设计组件/机制与明确构造任务有相应通过/反例测试、证据缺失仍拒绝；JSON路径正例经版本化审计流程在隔离事实库可达且不影响同occurrence公司条件/全集语义；延迟与调用账本复算一致、优化只报告确定性输入/计算变化，不冒充真实加速。按受影响范围运行离线测试及最终独立核对；真实语义结果维持“未实测”。

最终离线核对：候选已封存为 query-v14-rerank-v32-scope-v16-9c70f3a4dfbc，177份本地运行源码/资源指纹9c70f3a4dfbcb5f6e7ee1665a9df6ae29d033cbcb02bc8b7328b88c797e7a6ba，来源视图v2。585项相关Python回归通过、0跳过，执行前后代码/测试hash相同；独立审查的7项反例/兼容检查通过。完整来源场景重放40条为5 REJECT/35 UNKNOWN（4原负例、1原边界），26原正例未被概念守卫误拒；设计51旧证明重现，4处主机解除误拒、8旧model否定保留，原问优先令12旧canonical独有证明转证据不足，并未贴语义负例。独立审查曾发现标准题补造任务、介绍既有机制被当应用任务、截断前缀被当全题，先保存反例再分别修复并最终回归，未靠修改金标或源码预算求通过。

算法更正物仅本地内存库，旧565条来源/决定校验，仅1条重审，annotation31→32，SQL/count/三页分页28题31次→29题32次，固定参考29正例均可达；原服务仍a31/28，缺安全隔离事实发布/运行绑定适配。增时139.88秒中provider请求墙钟多131.49秒、间隔多7.98秒、queue0、其余差0.41秒；MQ旧候选9→50，Agent/工程重证明与修复增加成本。同历史集合重建输入主排bytes少2.3%–2.7%，设计复核少10.1%/12.4%，场景复核反而多6.3%/4.0%，不声称真实加速。主排也统一预留完整messages+schema；原96k无法承担的未知usage重试明确拒绝。

本批真实模型/服务调用0、共享DB/ES写入0、生产未部署，累计仍49次/513,865 tokens。报告及离线复查入口见[本批封存报告](../../data/reports/user-scope-five-v3-offline-20261016/REPORT.md)。下一批仅建议四个语义搜索原问一次，各按6次/96k预留，最多24次/384,000 tokens，累计73次/897,865；比原72调用线多1次，须另行明确授权才可启动，不自动使用余额、追加或部署。算法更正进入真实API前先完成隔离事实发布与新身份绑定；不为已知a31结果再重跑算法规划。完整五问候选一次上界为30次/480,000、累计79/993,865，亦未授权。未执行整库回归、Linux Pi/Node构建、浏览器/SSE、真实延迟或稳定性；原参考、原始预测及门槛保留。

### 算法范围修订方案（离线实施前）

根因已核：10-14 `data/reports/byte-algorithm-all-20261014/finalize_audit.py` 的人工边界覆盖将 JSON 路径求值直接归为工程组件并 REJECT；当前 `load_semantic_scope` 忠实执行了完整565条审计，SQL没有漏行。独立v2原文审核按给定结构/路径的解析遍历任务判正，旧理由及固定参考均保留。旧专项发布脚本还恢复build、修改标题并安排索引，不能用它只修一条语义裁定。

选定方案：新增通用、零模型的来源审核修订物与本地投影入口。审核者明确填写实际计算/操作目标及当前题连续证据，不能因组件名称、SQL语言或旧标签直接接受/拒绝；证据不足仍留待审阅。候选绑定原完整范围hash、语料/注解版本、每个输入和原始文件hash，修订同时保留旧决定及审核人/规则版本；规则版本为 `algorithm_task_review_v2`，兼容的持久证据格式仍为 `algorithm_problem_scope_v1`，不把未逐题重审的旧负例伪称为v2审核。投影只接受隔离内存SQLite，不提供共享数据库连接/发布命令，不在查询中加入私有ID或短语规则。

代价与验收：这是可复核的数据裁定候选，不是自动语义分类器或线上修复；全范围仍需有效正负审计。用封存r295事实和原文验证565条来源/旧审计，在本地事务只追加审计历史并更新目标决定，保持其他事实/标签不变，经实际SQL列表/计数/分页确认29个已知正例及同occurrence公司筛选。错误来源、范围漂移、旧负例证据失效、重复投影和持久化回执失败须拒绝或回滚；固定gold与历史回执hash不得改变。本批模型/服务调用0，证据保存于新 `data/reports/user-scope-five-v3-offline-20261016/algorithm/`。

### 场景任务证据契约方案（离线，readability）

四个已封存误收原题分别只问概念区别、含义、高并发下原理及延迟消息内部实现；“场景”字样、故障名或KNOWLEDGE标签都不能替代实际任务。选择在原问明确要场景题时启用独立场景核验：同时绑定当前题的给定情境/异常/约束和所需诊断、处置、设计或取舍任务，引用仅来自本题或已验证原问，禁止借邻题。纯概念问有可复核的拒绝原因；明确处理、诊断及方案正例保持，缺新证据字段按未完成核验，不伪造历史模型响应。

实现放scenario_scope及reranker装配，和设计核验共用本题片段ID证据机制；宽/窄设计由原query决定，算法/设计/场景提示按原query及已核查任务类型注入，保留二次核验与候选池。候选版本拟rerank v32 / scope v16，旧版本字符串/历史产物保留。验收用四反例、泛化合成对照、来源串用/字段缺失反例和历史输出离线重放，固定v2文件不变，模型/服务调用0；效率只报告确切输入变化，不冒充真实加速。

### 设计任务主机修复方案（alignment，10-16）

已核对 PIT-002、当前 design_scope/citations 差异及封存 s01/s03 审计：宽工程请求被统一套用独立系统范围；两个带明确构造对象的口语指令因目标名不在有限词表而误报 NO_CONSTRUCTION_DIRECTIVE。选定按原用户请求区分宽工程交付与显式独立系统范围，组件/机制/工作流可在宽范围下凭当前题建设证据成立；保留无效/邻题/跨 occurrence 证据拒绝，旧模型 keep=false 或缺证据不由主机自动翻案。构造识别仅修指令语法与证据单位，不新增私有 ID 或五问白名单；裸“怎么实现”仍须有同题交付对象证据。

接口采用 design_scope_policy(query)、design_scope_required(query)、design_scope_instructions(query)，assessment 增加可选 query 关键字；旧未传 query 的调用保持独立范围兼容。新增 COMPONENT_OR_WORKFLOW 证明类型；调用方及主排提示由另一 agent 统一装配。主要代价是宽/窄契约与模型证明类型需要协同演进，历史拒绝只能部分确定性重放，不能宣称真实命中率提升。验收：通用口语构造/组件正反例、显式独立限制、历史项目/纯原理/编码与缺失证据反例通过；固定 gold 与历史包 hash 不变；真实来源/历史输出重放单列主机确定修正和仍需模型重跑，证据存新离线包 design/，项目模型/服务调用0。

设计任务主机完成检查点（alignment）：按原query区分宽工程与显式独立范围，新增COMPONENT_OR_WORKFLOW证明，口语构造目标不依赖对象名称词表；同题连续范围ID补跨标点目标/动作，限制来源、长度及编号题边界。新增防护：COUPLED目标与双约束必须同一原问共现；纯原理/项目复述不因如何实现而升级，用AI coding作工具不等于现场编码。实际装配stub、设计、引用与repair定向回归136 passed/0 skipped，项目模型/服务调用0。私有新包 `data/reports/user-scope-five-v3-offline-20261016/design/` 保存命令、合成反例测试入口、真实来源及历史输出重放：51条旧原因全部复现，新主机确定性解除4处，8条模型keep=false不回填，14条坏证明仍失败。固定gold所有file_hash未变；7个新证明仅接口fixture，不是新预测。未验真实模型选择、候选召回、答案、延迟或部署；版本建议由主线程统一v32/v16装配。


### 场景任务证据契约离线收口（readability）

实现完成：原query决定场景/设计/算法契约；场景正例要求同一原问情境与实际任务证据，概念解释有明确拒绝依据，缺字段/串用证据保持UNKNOWN。设计接入宽/独立原话policy与同题连续范围；主排抽取完整请求，使输入检查、完整预留、retry共用UTF-8上界，scope按需注入任务规则，候选池和二次核验保留。候选版本rerank_v32_source_task_contract/scope_verify_v16_source_task_contract；旧v31/v15行为仍以封存源码复现，未部署。

实际执行8个相关unit文件351项通过（scenario专属25项）；初轮5项和扩大回归3项旧预算fixture失败已先保存，随后按完整预留更新测试条件，未改变6calls/96k上限。历史40条展示结果来源核对31文件/49 occurrence-span；原4条误收均可确定拒绝，额外1条旧BOUNDARY拒绝单列，26旧POSITIVE无概念误拒，其余35条旧输出缺新字段仍UNKNOWN。证据见`data/reports/user-scope-five-v3-offline-20261016/scenario/`，gold/历史预测不改，模型及服务调用0。真实语义保留率、召回和延迟未验证，不自动重跑。

场景收口补充独立审阅：`介绍消息队列如何保证消息不丢失的机制`修前被动作词误PASS，先保存反例，再增加既有机制解释框架识别；同题后续真实构造/故障诊断仍保留。新增6个反例/混合题守护，8个相关unit文件最终357项通过（scenario31），历史重放结果不变；证据同目录，模型/服务0。


设计原问优先补强（alignment，实施前）：独立审阅发现标准题“设计一个通知系统。”与唯一VERIFIED原问“通知系统的原理是什么？”可经完整stub rerank误得COMPLETE；已保存修前输入、完整audit及源码hash到design/failure-canonical-only-before.json，并保留此前重放replay-results.before-original-priority.json，项目模型/服务调用0。选择将有VERIFIED原问时的正向设计证明限制到该原问：标准题片段仅在同一原问逐字支持时可用；同义标准题可选原问自己的片段/连续范围；COUPLED目标和双约束必须同一原问共现。没有已核验原问时保留canonical兼容路径。代价是旧标准题独有/改写引文会转证据不足，不能计为恢复；验收补完整rerank反例及同义原问正例、重跑定向测试和51条历史证明，明确重放变化，不改金标或旧输出。

场景截断边界补充：生产VERIFIED原问仅是100–200byte片段，不能充当完整负证据。concept硬拒改为要求original_question_complete=True；False/缺标志及模型CONCEPT负标签均保持UNKNOWN，前缀内真实任务允许正证明。来源prompt提示截断边界。34项scenario专属通过；私有replay显式使用完整raw_question complete=True，不把全文审核结论说成历史截断输入可原样修复。

设计原问优先补强结果（alignment）：canonical-only construction 完整stub反例现为PARTIAL且construction_evidence无依据，原问自身片段/连续范围及canonical逐字被原问支持的正例保留。定向4文件145 passed/0 skipped；首次144pass/1fail为旧反例更早被建设证据拒绝的原因断言，已在validation保存后只更新该断言，拒绝/PARTIAL不变。51条旧原因全部复现，4处确定性解除、8条keep=false仍不变；原问优先使12条旧canonical改写证明转证据不足，总无效从14升26，另10条原已无效的理由提前，不能当语义负例或冒充新预测。修前/后完整audit、修前重放与22条理由差异均在design/新包保存；固定gold/hash与历史预测未变，模型/服务0。当前仅验证主机证据契约，不验证新模型采证、真实召回/质量/延迟或部署。

## 四问封存候选真实核验（10-16，执行前）

用户明确批准“按这个候选实测 Redis、MQ、Agent、工程四问，各一次。总调用上限不限制”。执行封存 query v14 / rerank v32 / scope v16，源码指纹9c70f3a4dfbcb5f6e7ee1665a9df6ae29d033cbcb02bc8b7328b88c797e7a6ba；累计调用次数上限撤销，本批仍保留384,000 tokens、每问6次/96k/180秒保护及原累计1,152,000 tokens。此前49次/513,865 tokens；四轮自然预留最多24次，采集器此值仅限制四轮请求，不是恢复累计调用门槛。不新增问法、API裁判、语义重跑或部署。

本批run为evaluation-candidate-four-20261016-25386aa3。执行前从固定v2金标仅选s04/s05/s01/s03四个原问，保留原题、全部标签与门槛，封存选择视图及父级来源hash，不修改原金标/旧包。隔离API/Pi采用177份封存源码/资源及原部署依赖，新隔离账号、同个人Ark链/embedding/共享Linux锁；重新核验实际生产版本与r295/i295/a31，算法本地a32更正不发布。本批按原始实际响应核对概念误收、固定正例被拒、来源与独立事后审阅；对比v31同四问的固定参考分数、调用及分阶段耗时，有限面板与单次观测不冒充全集召回或稳定性。

验收：四轮请求各一次、持久回执/账本/计量一致，异常或未知usage先保存并停止；无自动追加，预算、候选hash、模型链、共享锁、来源及生产未变逐项复核，结束关闭本批隔离unit。真实质量如实记录失败/未知，固定金标不因结果调整。新报告目录data/reports/user-scope-four-v4-live-20261016，原离线和历史包全部保留。

四问真实批失败检查点：Redis、MQ两轮SUCCEEDED，实际8调用/79,617 tokens；Agent在180秒持久FAILED，error=QUERY_DEADLINE_EXCEEDED，EVIDENCE_REPAIR失败usage=null，已保存5条该轮调用（4成功39,252 tokens+1未知），持久model_attempts=null，不能声称attempt/账本完整。原checkpoint已保存3行、inflight=null、uncertain=true，collector自动停止；本段按14调用槽/175,617 tokens保守计费（Agent整轮6/96k），原uncertain及原始失败不清除、不重跑Agent。实际持久13调用/其中12成功已知118,869 tokens与保守额度分列。

只读复核终态稳定、FAILED调用已落账且API/Pi已释放整个provider期间的共享gate锁，说明本地执行退出；不能据此认证供应商远端停止或恢复未知usage。仍以96k完整预留覆盖Agent。工程问尚未提交，继续履行用户已授权的第4原问：新空actor、同177份封存业务，原隔离unit留证后关闭、另起工程隔离进程并按真实新PID重新测量proof（eval context要求actor等于启动local_user_id，不能伪复用旧proof）。仅s03原v2不变选择视图777bf3bb83c7c69dbf88bb0d318aa1ab0caaa10f385da56e2927406cd8f21e56，6调用/96k一次；合计最坏271,617<384,000 tokens，累计最坏785,482<1,152,000。不修改业务源码、不放宽单轮或原金标、不清uncertain、不重提任何已请求问题。

最终证据须分开原3行checkpoint失败段与独立工程collection；四问组合只作stored_predictions同金标离线重算，不能伪造原四问完整collection或整批HTTP通过。工程段的完整HTTP报告独立绑定新进程；原段失败/未知usage、保守预算与来源审查均保留。事后同快照source_context确定性重建仅用于诊断，不冒充实际传模请求捕获。

最终实测与核对：四原问均仅一次，3轮SUCCEEDED/Agent1轮FAILED，未重跑或增加变体/裁判。候选仍同177份封存v14/v32/v16，两个隔离进程proof分别4c726586…与9e2bdc4f…（文件runtime指纹相同，真实PID/proof不同）；生产仍r295/i295/a31及原进程/业务包，0复习写入，两段unit均已停止。原段28项失败收尾与工程47项终态检查通过，原Agent末次usage及attempt完整性仍未知，不因检查通过改记SUCCEEDED。

固定同金标/同评分器重算为2 UNKNOWN/2 FAIL：Redis7返=5P2B（固定正命中13→5/63）、MQ3返=3P（13→3/36，低于5门槛）、工程20返=15P1B4U（10→15/85，7对象族）；Agent约180秒超时，不能用0结果计语义退步。工程事后19P1B独立保留、不回填冻结标签。三个成功问题耗时79.188→99.190、93.809→103.084、99.795→110.530秒，总增40.012秒，provider请求墙钟增39.792秒，queue0、interval少949ms；单次/auto revision不能认证稳定性或供应商根因。

重建来源视图与独立原文审核定位：Redis/MQ4旧概念负canonical被主机拦截，但大量正例因canonical引文不被实际原问支持、任务剪尾或情境/动作漏识未返。工程11个池内固定正未返分6scope拒绝/4正常分页/1低分；MCP原问任务尾部被裁掉。原问正例不能自动救回当前片段证据。Agent主排约99.449秒、复核后仍启动可选repair并耗尽期限，下一步修原问证据分配/可合法引用选项、主机场景识别和repair余时调度；FAILED回执attempt=null导致评测无法正式完整收尾的路径也待补。

预算：实际记录18条调用（17条已知164,434 tokens+1未知），本批保守扣19调用槽/221,182 tokens，累计实际67条、保守68槽/735,047 tokens；调用槽不是实际调用数，累计调用上限撤销、tokens仍低于本批384,000及总1,152,000。组合输入逐字保留原3行checkpoint与独立工程最终原始行；工程初步prefix为不同JSON序列化，只能说对象相等。原uncertain不清、原四问无完整collection，两份stored报告严格内容核验通过但不声明部署；工程单问HTTP报告独立strict VERIFIED/BOUND_REMOTE_SOURCE，质量均NOT_ELIGIBLE。

结果入口为[四问实测报告](../../data/reports/user-scope-four-v4-live-20261016/REPORT.md)，完整原始来源、两个运行段、两份最终补审、预算和门禁均在同包。离线首轮复查已通过1563份文件、30条返回/32来源见证、固定金标190文件不变；最终封存复查结果保存在同包recheck-result.json，命令为`.venv\Scripts\python.exe -B -X utf8 data/reports/user-scope-four-v4-live-20261016/recheck.py`。3页文档静态检查和git diff --check通过（仅既有CRLF提示）；本批未改业务源码，未重复585项工程回归、Pi/Node构建、浏览器/SSE或稳定性测试。未知usage、语义UNKNOWN及失败均保留，不称候选已达可用或发布门禁。

## 原问裁剪、引用衔接与可选修复期限（10-16，离线实施前）

用户要求继续修：优先解决原题裁剪/证据引用衔接造成的漏题和Agent修复阶段超时；固定v2金标及所有门槛不变，先离线对照，需再次实测时再列清候选版本/预算。本批真实模型/服务调用预算0，不部署，不继续消费余额。此前实际持久67调用、保守735,047 tokens，1条usage未知及原uncertain继续保留。

事实：封存v14/v32/v16四问各一次为Redis/MQ/工程成功、Agent180秒失败。Redis固定正13→5、MQ13→3；大量合法原问与canonical改写不一致、原题尾部任务被500byte分配中的前文挤掉、片段切分只给对象/背景导致证明失败；工程MCP题的任务尾部被裁掉。Agent首轮复核为部分状态（仍有证据异常，已通过题目数不可确认），余时约14秒仍启动可选修复，触发整轮deadline；不能通过提高期限、放宽来源或改金标解决。

选定方向：在原总文本/调用/token预算内优先完整原问，再分配上下文；将模型可选证据与主机实际允许的同原问证据对齐，补一般化情境/动作与连续证据单元，仍拒绝借canonical或邻题造任务。可选repair另设受父期限限制的时间准入/返回预留，余时不足直接保留已核验的partial，取消与父deadline仍传播。先区分具体行为与兼容代价再实施，不为凑通过修改旧响应、gold或历史产物。

验收：原v2全部文件/历史v4及v3包hash不变；同真实候选集合与来源范围重建前后视图，保留UTF8字节上界/完整标志/同occurrence硬条件；旧模型响应重放与新增合法选项fixture分别统计，不把新选项存在当模型已恢复。场景概念负例继续拒绝，缺来源仍UNKNOWN，显式独立设计限制保留；用可控时钟/假客户端验证余时不足0新增call、子期限失败保留初始partial、取消/父deadline不被吞、未知usage保守扣账不变。按变更范围跑离线集成及独立反例核验，最后封存候选源码/报告/可离线复查入口；真实质量与延迟仍未测。新产物data/reports/user-scope-five-v5-offline-20261016，优先更新现有入口结论，不新增公共阅读入口。


### 原问引文衔接与场景任务漏识（readability，v5离线实施前）

目标：修复v4来源复审确认的合法引文选项与任务片段缺口，固定gold、门槛、历史包和真实输出不改，模型/服务预算0。当前43个场景scope异常中同时有canonical改写引用、错误选片段、截断和词面漏识；不能统一把UNKNOWN改成通过，亦不能把原问的正标签借给另一occurrence。

选择方案：在citations统一构造来源优先的任务证据候选；有VERIFIED原问时仅把原问片段及被同一原问逐字支持的canonical片段作为正向任务选项，canonical仍可供对象/主题对照。提供有长度/连续性/同occurrence/编号题边界保护的连续证据单元，design/scenario共享解析；不模糊修复历史错误引文、不从邻题拼接。scenario在证据已落原问后，补通用动作语法与实际状态表达识别，保留机制解释/裸名词/不完整原问拒绝负断言等反例。来源视图v3分配由另一agent负责，本实现不改evidence.py；rerank v33/scope v17版本由主线程统一。

验收和证据：先保留v4历史响应逐字重放的结果，再单列合法备选片段的纯函数fixture，证明选项可用不等于模型会选对；扩大同题跨标点、canonical补任务、跨occurrence/邻题、截断、纯机制和真实故障处理成对反例。新增证据只写data/reports/user-scope-five-v5-offline-20261016/evidence/，定向测试命令/失败/修复结果分别记录，真实语义命中率与延迟仍未测。不抬预算、不改阈值或私有ID/题文白名单。


## 原题上下文预算修正（10-16，离线执行前）

- 目标与授权：修复原题被邻近上下文挤占预算导致任务尾不可见；本轮模型、HTTP、真实数据库调用及持久写入均为 0，固定 v2 gold、历史预测与门槛不改。
- 已核事实：v4 成功池 Redis/MQ/工程共 150 个 canonical、167 条匹配 occurrence，各 canonical 的至多两条原题合计均不超过 500 UTF-8 bytes；v2 仅 156 条完整。MCP 原题 362 bytes，v2 原题视图仅 198 bytes，before/after 占 171/48 bytes，建设任务在被裁尾部。Agent 失败段没有完整来源池，不能纳入此次 150 题重放。
- 方案及兼容：source_context v3 保持字段、匹配筛选、至多两条 occurrence、500 bytes 总上限及 hash/span 校验。先按公平份额给原题分配预算，短题余量让给长题；剩余预算才分给邻近上下文。超限仍只保留连续前缀、complete=false；不挑语义关键词、不拼接首尾、不把邻题当原题。代价是邻近上下文更短，长题仍可能截断。
- 验收：覆盖原题任务尾、两 occurrence 共享、超过 500、UTF-8 边界、筛选、坏 hash/span、邻题隔离；用冻结事实和源码在本地重建 v2/v3 的 150 候选上下文，校验旧视图逐项一致与源 hash/span，保存脚本、分布、差异及未覆盖项到 data/reports/user-scope-five-v5-offline-20261016/context/。只证明模型输入可见性，不宣称新的语义通过、预测恢复或线上改善。

可选修复时间策略细化（root，实施前）：选固定准入至少20秒执行余量+3秒返回预留，修复协作式子期限最多30秒且不晚于父deadline-3；原Agent剩约14秒将0新增repair。子ContextVar只增加时间限制，共用原RequestLimits计数/预留/取消，覆盖gate等待/间隔及SDK流可观察点；成功/异常均还原上下文。捕获子超时后再查父取消/硬deadline，父错继续抛；mandatory排序/复核不接受软失败。同步SDK/httpx read timeout按单次I/O计算，持续不完整SSE或末块阻塞可能越过协作式子期限，本批不承诺严格30秒墙钟、不引入后台继续调用伪造返回；这一运输层绝对截止限制明确保留。跳过准入可确定避免本次历史末次repair，但原失败无完整candidate明细，不能据此认证当时能返几条或新语义通过。修前14秒仍进入repair的假provider复现已保存validation/repair-before.json，真实模型/服务0。

宽工程代码形式细化：固定参考接受有真实系统/服务/组件交付目标的代码形式题；因此仅在ENGINEERING_DELIVERY且原问自身明确构造工程对象时允许进入scope。纯算法/数据结构求解、只有写代码、原理/项目复述仍不满足；独立系统/架构范围继续排除现场编码。主排通过专用helper只放行进入scope，不能直接算设计相关；历史裸“怎么处理”仍缺目标，不借全文自动补过。

原题预算修正结果：`candidate_source_context_v3` 已落地；原题先公平分配，短题余量可给长题；邻文只取剩余且保持旧字段上限，避免短题无故扩张相邻问题。实际定向测试18通过；冻结150候选/167 occurrence的v2视图逐项复现，v3完整原题156→167，MCP 198/362→362/362 bytes，固定v2的190文件hash不变。来源153个去重occurrence的hash/span均校验。总文本44768→44989 bytes，仍逐canonical≤500；不等于token实测。首版副作用和一次apply_patch工具写失败均在新context包留证，已修正。Agent失败段无完整池，未纳入；超500、模型实际证据选择、真实语义质量与延迟仍未验证。模型/HTTP/真实DB调用0，测试只用内存SQLite。命令、复现脚本及全部输入输出绑定见新包`context/verification.json`与`comparison.json`，原始预测、gold和门槛未改。

工程 CODE_TASK 主排序适配的补充验证（10-16，测试执行前）：仅新增独立 reranker stub 测试，不改生产源码；固定响应验证宽工程且当前 VERIFIED 原问有系统/服务交付才能进入 scope，独立/架构、纯算法、邻题及 canonical 补任务不可借用；入选仍需主分数阈值、模型 scope keep 与主机证据同时通过。复用当前 helper 接口并保存新 context 包验证回执；模型、HTTP、真实数据库调用0。

工程 CODE_TASK 补充验证结果：新独立`tests/unit/test_reranker_engineering_code.py`18通过（完整固定响应rerank链）。宽ENGINEERING_DELIVERY+同VERIFIED原问构造交付且grade2/3进入scope后才收；独立/架构、纯算法、系统内链表、原理、canonical/邻文补任务拒绝；scope keep=false、unit不符、借邻文证据和grade1不能被primary特例绕过。XML、命令及实际源码hash已保存新包context/engineering-code-verification.json；本测试子任务未改生产源码、共享tests、gold或门槛，0模型/HTTP/真实DB。


原问引文衔接结果（readability）：citations统一来源优先任务选项，design/scenario共享同题连续范围，跨occurrence、邻题、编号新题与canonical补任务仍拒绝；宽工程代码形式只在本题有工程构造对象时进入scope，独立/架构限制保留。通用场景动作/状态及有给定假设的后果或取舍核验已补，纯机制解释、无条件概念询问与截断原题的负向断言仍受保护。5份定向测试198通过、0跳过；失败环境、首轮错误及修正后日志都保存在新包evidence/。

封存v32来源重建与87份历史scope响应逐项复现旧主机判断。保持旧响应和上下文时，Redis grounded 7→10、MQ 3→5、工程26→30；v3上下文配同旧响应数量相同。MQ新增2份包含1固定正和1边界，不能统称质量正例恢复。旧未grounded中另有Redis12/MQ20/工程6份存在合法原问备选证据，仅为纯函数fixture，不代表新模型已选择、最终返回或评分通过；主排来源收紧也可能拒绝旧canonical任务引文，不能用scope单层数字推断端到端效果。失败Agent无完整池，未补造。

完整messages/schema按同50题主排池和历史scope池做静态重建：输入UTF8 bytes的primary/scope增量分别为Redis +325/+847、MQ +144/+793、工程 +696/+2923；各请求仍另预留6144输出，整轮96k上限未变。两次保守上界之和可能超过整轮额度，依赖已知usage归还；未知usage仍需按原规则拒绝后续。此为输入体积与预留核验，不是token或延迟实测。命令、源码/输入hash、重放及限制见新包evidence/validation.json、source-units-replay.json、source-request-bytes.json；本批模型/服务调用0，固定gold、历史响应和门槛未改。


首轮离线检查点（root，已由后续修正替代）：封存177份候选v14/v33/v17/source_context_v3，源码指纹b310a2512c5caeae377b42e94df0ac53cb9a68161cf8a8f28e11901c1c9d7cc6；仅7份生产源码变化。QueryService实际repair控制器合成集成覆盖余时跳过、子期限失败保留已验证partial、空partial仍503及父到期失败；同步SDK阻塞读仍无严格墙钟中断，不能以此推断上次Agent可返回数量。共享gate的锁文件PermissionError退避也改为分片检查期限，未把此Windows边界称为生产根因。

首轮相关离线回归719通过/0跳过（完整命令、源码/测试hash及此前失败见新包validation/python-final-5.json）；之前一次命令含不存在的测试路径、若干旧版本断言，以及旧测试把91秒迟到primary记为成功日志，均先留原输出再按版本/实际新契约修正，未放宽门槛；遗漏版本断言及一次运行中测试文件变化的回执保留但不作最终验收。23项冻结源码重放初核通过，涵盖190份金标、1572份原实测证据、原问视图/87证明/请求字节/调度/4负例锚点。最终统一封存及独立核对入口见[本轮离线报告](../../data/reports/user-scope-five-v5-offline-20261016/REPORT.md)。本批模型/服务0，不消费预算。后续四原问各一次提案上限24调用/384000tokens，累计保守1119047<1152000，余32953；仅提案、未实测或部署，不自动重跑/追加，固定金标和历史未知保留。

工程CODE完整原问语境反例回归（10-16）：先封存`context/engineering-code-explanation.before.json`失败：canonical“设计一个通知服务”、raw“代码题：介绍设计一个通知服务的原理。”、primary/scope均选q0，实际1返回且进入scope。等待helper补完整原问语境后，新独立测试22通过（原18保持，新增宽/独立纯原理拒绝及带/不带代码题前缀的解释后真实新交付正例4）；纯原理PRIMARY拒绝且0scope，真实混合交付仍通过。前后固定响应、源码hash、XML和命令见新context/engineering-code-explanation.verification.json。此测试子任务未改生产代码/gold/历史预测，模型/服务调用0；根线程需重新冻结候选，不将旧冻结包视为已修。


第二次离线检查点（root，已由后续通用语境修正替代）：首轮719通过后发现标准题逐字短片段可剥去原问解释框架的真实误放行，完整固定响应反例为代码题介绍设计通知服务原理，错误PRIMARY→SCOPE→返回1。先保留函数与完整链失败、环境路径错误和b310候选全177源码/旧报告，再仅让CODE准入同时检查所选证据与同条完整原问建设语境；补6个函数对照/4个完整链对照，原理与项目拒绝、解释后真实建设保留。最终相关33文件729通过/0跳过，测试与源码hash前后一致；新177源指纹d49b41857b50571f7977baa7a4fda8879fc990bb0ee569040642e788f9f2abea，旧尝试在新包attempts/b310a2512c5c中保留。新源23项重放初核通过；87证明计数、请求字节及4概念负例/5事件结果不变。最终命令/回执改为validation/python-final-6.json；金标/门槛/历史失败与未知未动，真实模型/服务0。

非CODE通用原问语境补充验证（10-16，执行前）：独立终核发现DESIGN_TASK截取纯解释原问中的canonical建设子串，NEW_OR_REBUILT_SYSTEM及COMPONENT_OR_WORKFLOW均可错误放行。before由独立审阅agent保留；本子任务仅将stub候选类型参数化并追加4条完整链（两unit纯解释拒绝、真实混合新建/工作流正例），原22语义不变；等待通用assessment源码稳定后再执行，固定gold/门槛不变，0模型/服务。


通用设计原问语境修正（实施前）：独立终核确认非CODE也可把介绍原理中的建设短语截为canonical，绕过NEW/COMPONENT；本地COUPLED同根因也复现。仅修design_scope及专属单测：以原问offset定位所选goal实际覆盖的最小子句窗，尾标点不跨入下一句，不允许后半另一目标建设救回前半解释。resolver保留不入schema的来源/offset元数据；同词重复与多occurrence仍按实际引用核对。NEW/COMPONENT要求本窗真实交付任务，COUPLED保留非解释的可观察交付要求并保持两约束同原问。混合原理后真建设/组件任务需保留，截断末句不完整则证据不足、不判整题负例。旧d49由根线程归档，完成后统一回归/refreeze；本步骤0真实calls，不改gold/阈值或其他业务模块。


通用设计证明语境修正（实施前）：独立核对确认非CODE的DESIGN_TASK仍能用canonical逐字短片段剥去原问解释框架，在NEW/COMPONENT两unit误返；d49完整失败与BLOCKED审核及177源码均保留于新包，0模型/服务。选择将目标所在原问子句语境门槛统一到design proof，不能借另子句另一目标或另一occurrence补任务；按所选goal实际覆盖的连续原句定位，保留跨标点真实任务、解释后真实新建/流程交付及COUPLED既有可观察交付要求和同题双约束。来源片段元数据仅在host解析保存，不加模型schema。截断而缺任务语境保持证据不足，不强判全题负；新旧unit与完整链成对反例先验证，再统一封存新源/全回归/独立终核，不复用旧729作为新代码通过。


通用设计语境修正定向结果：选中goal按原问offset覆盖的子句核验，末标点不跨到后一任务；明确source/range引用保留不入schema的PrivateAttr来源和位置并重新比对原文，canonical/literal仍需真实匹配原问且不假称某个source位置。NEW/COMPONENT解释片段不能借另一目标或另一occurrence任务放行；COUPLED保留本窗可观察要求及两约束同原问。截断末句框架不明时记证据不足，已完整前置任务句仍可评价。新增17参数例后5份定向221通过；首轮扩展“如何优化”误开独立范围的1失败及修正日志留在evidence/general-frame-*，原一般机制/项目反例保留。0真实calls，fixed gold/阈值未改；独立完整链及总回归/refreeze由根线程统一，未用此次单测宣称实测质量或刷新旧评分。

非CODE通用原问语境完整链结果：独立文件26通过（原22+新增4），NEW_OR_REBUILT_SYSTEM与COMPONENT_OR_WORKFLOW对纯解释原问均在scope以ORIGINAL_DELIVERY_TASK_NOT_ESTABLISHED拒绝；primary与模型keep=true保留真实输出，没有回填成功。混合解释后新建服务/实现审批工作流仍CONFIRMED。新context/design-task-original-context.verification.json绑定独立before SHA6c7cf273…与本次源码/测试hash；原22+d49回执已归档。当前验证是新guard工作区，根线程需再次冻结；本子任务未改生产源码/gold/门槛，0模型/服务。


本轮最终收口（root）：通用目标子句/来源offset守护完成，独立29项边界复核通过；去掉已切分子句内100/20字的解释识别盲区，229项函数组及冻结源码26完整链通过。最终33份相关测试文件758通过/0跳过（validation/python-final-8.json，经final-regression.json绑定），源码/测试前后hash一致；177源指纹e21faecf11ac8256de4fa5a7f09dee17ebdea4aa320941c7acc43d96f2175526，query v14/rerank v33/scope v17/context v3，两个旧候选及失败均保留。三个池为150条候选记录/167条原问视图（去重153 occurrence），不作全库数量；完整原问156→167。87历史scope证明原响应重放7→10/3→5/26→31，新增10、失去0；合法备选12/20/7只作fixture，MQ边界不提升为正例，4旧概念负例/5事件仍拒绝。工程scope输入相对旧v32增加2412字节（比d49少511），其余增量不变，不声称token/延迟已改善。

冻结源码重放、190金标/1572旧实测文件、独立终核及总封存以[最终报告](../../data/reports/user-scope-five-v5-offline-20261016/REPORT.md)及同包recheck-result.json为准。758是相关离线回归而非全仓/真实语义通过；本轮项目模型/服务0，生产未部署，parent硬期限与未知usage仍保留。后续四原问各一次候选为e21faecf，最多24调用/384000tokens，累计保守1119047、原上限1152000，余32953；提案未执行，不自动追加/重跑。

## e21faecf 四原问实测（10-16，执行前）

用户明确授权 e21faecf 候选对 Redis、MQ、Agent、工程四原问各一次，预算不限额外累计上限，不自动追加或重跑。仅启动隔离 API/Pi 和全新 eval 账号，封存 177 源码的 query v14/rerank v33/scope v17/context v3，不部署生产；单轮既有 6 调用/96000 tokens/180 秒保护不变，四问派生上限 24/384000。历史保守计费 68 调用槽/735047 tokens（实际已记录67次，有1次usage未知）保留，不把保守数当账单。失败先留终态、日志、预算与环境，不重提已经提交的问题；若collector因未知usage中止，只允许在独立新段继续尚未提交的原问，重新核验来源和版本。

区分检查：先核对封存 e21 源码、固定v2金标190文件和旧v4实测1572文件；实测前后测量生产及隔离进程/代码、r295/i295/a31快照、模型配置和同一Linux模型锁物理身份。与v32使用完全相同四问金标视图及原门槛，核对返回题逐条原文/span、固定标签、池内漏题原因、旧概念负例、Agent修复准入/终态和各阶段耗时；UNKNOWN和边界不回填正例。新回执与独立复核仅写 data/reports/user-scope-four-v6-live-20261016；未获得的失败池不补造，不用离线758通过宣称真实质量通过。当前准备阶段模型调用0；一次PowerShell只读命令误用了花括号路径展开，解析失败、未触发远端动作，已改为逐文件读取。

首问失败留证（继续其余未提交问之前）：隔离 d304ebe6 的 Redis 仅提交一次，87.2秒终态FAILED/QUERY_RELEVANCE_UNAVAILABLE；规划与embedding共12854已知tokens，第三条RERANK在70.2秒记录APIError、usage未知、retry_count=0。没有进入scope/repair，不能解释为本次证据裁剪或修复超时；原始提供方细节未持久化，过滤日志无细节，不推断具体根因。checkpoint含1行、inflight=null、uncertain=true，按整轮6槽/96000保守扣计；不解锁或重跑该检查点、不重问Redis。先核对并关停本段，再给MQ/Agent/工程每个尚未提交原问单独空账号/进程/证明和单问6/96k额度，总体仍仅四原问一次，无组合HTTP回执。

本地准备失败同时保留：第一次单问视图校验缺frozen/business导入路径，0远端动作，半成视图转存attempts/remaining-preparation-import；另一次PowerShell内联Python引号解析失败，未执行Python/网络，改用保存脚本。原数据与候选代码未改，后续包在服务端复制已核177源映射，仅上传新选择元数据和授权，避免重复传原文；新单问版本绑定重新测量，不沿用旧PID证明。

e21实测收口：四原问均仅一次，无追加/重问/模型裁判。Redis87.2秒及MQ55.5秒在主排APIError失败，各3call/1unknown，整轮各6槽/96k保守计；工程180秒在主排到期，3call/1unknown，累计门禁queue133586ms、interval5917ms、provider39258ms，其中主排queue107345ms，不能归因repair或假定持锁者。Agent90.692秒成功返回13，4call/36917tokens全known；固定7P/1B/5未标注、评分UNKNOWN，scope13入13过，无repair，不证明23/30时间策略实战覆盖。Agent50池52原问均完整，17固定P中漏10=9主排非逐字规范化任务引用+1局部细节/低分；事后12P/1项目复述误收只单列审核，固定标签不动。其余三问无完整候选审计，不以0命中或未返回负例宣称语义改善。

各段运行前后177源映射、API/Pi/依赖、模型配置、r295/i295/a31、同Linux锁64514/2246625和空账号独立绑定，终态数据库/调用账本/原问对应核对后均已关停，生产API/Pi/worker PID及代码未变。本批持久13call，10known75472tokens、3unknown，保守22槽/324917tokens；累计持久80call、保守90槽/1059964tokens、4unknown保留，known753771仅下界。用户额外累计cap为null，单问6/96k/180与四问派生24/384000保护不变；不把保守预算当账单。

同ac6四问视图/原v2金标与同评分器的stored对照重算为v32的2UNKNOWN/2FAIL→v33的1UNKNOWN/3FAIL；运行失败不作语义零基线，四问没有双成功的耗时对照。Agent单段另以完整真实HTTP回执和新PID证明严格验证，组合不伪造HTTPreceipt。最终离线命令为`.venv\Scripts\python.exe -B data/reports/user-scope-four-v6-live-20261016/run_offline.py scoring-final-correct-path analyze.py --segment-report data/reports/user-scope-four-v6-live-20261016/segments/s01`和同入口`assemble-final assemble_results.py`，均已成功；本轮未重跑758/全仓业务测试。前置离线整理错误（异构terminal无顶层actor、依赖输入未生成、Windows原子替换WinError32、段路径根目录误用）都0模型/远端，原输出/半成报告留于validation和attempts，不改候选/金标/阈值求通过。最终文档结构检查、独立终审和封存复查入口见[本批报告](../../data/reports/user-scope-four-v6-live-20261016/REPORT.md)及同包recheck-result.json；下一步补主排错误/持锁诊断，再修原问证据选择和项目复述边界，本批不再实测。
