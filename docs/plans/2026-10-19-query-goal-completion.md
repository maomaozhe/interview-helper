# 查询目标、工具取证与回答完成分离

## 2026-10-25：复杂查询预算调整

- 目标/授权：用户要求增加复杂任务预算、同步代码和文档，并提交推送全部现有代码/文档改动；排除 `.env`、`md/issue.md`、原始语料、数据库、快照及私有回执。
- 已知事实：当前规划默认4次、共享模型调用6次、工具8次、96,000 tokens、180秒；浏览器另有210秒固定等待，评测上下文从 `RequestLimits` 的历史默认推导共享上限。只提高规划配置不能解除其他瓶颈。
- 方案：规划默认8次，共享调用增加独立配置 `QUERY_MAX_TOTAL_MODEL_CALLS=24`，整轮256,000 tokens/600秒；工具上限保持8，每步一个工具。API、直接构造QueryService、Compose与配置示例使用同一默认；Pi继续消费host剩余预算。前端使用持久run的时限并保留30秒传输余量，评测回报实际服务预算。历史提示词与旧实验预算不改写。
- 区分检查/验收：配置上下界与低预算覆盖、超过4轮的Pi规划、共享调用独立耗尽、回答预留、长时限SSE恢复、评测预算回报；执行相关测试后做完整离线回归、Node/Web及文档检查。重建本地服务复用既有依赖并只读核对生效配置；不发真实模型评测请求，不部署SSH环境。
- 实现结果：Settings与QueryService共享预算默认；新增共享调用配置并贯通API、公开检索、Compose、健康接口和评测上下文；Pi规划上界与计时同步。前端读取持久租约并保留传输余量，MCP/HTTP评测等待也同步。执行内只解析一次当前检索偏好，结束后清理，不跨工具缓存旧偏好。
- 首次完整离线回归：`python -m pytest -q`，3341通过、8失败、11个fixture错误、3跳过。保留原日志；旧固定评测预算显式绑定原4/6/96k/180秒，旧零规划fixture指定v15，现代模型模拟补必填目标，资源默认断言改为已决策v16。cookie诊断证明测试同时发送有效原cookie及新增无域cookie，修复为覆盖同一域/路径；认证实现和原有拒绝断言不变。动态预算校验多次读取偏好已在执行边界收敛，不放宽来源/权限/语义判断。
- 最终Python复验：`.venv\Scripts\python.exe -m pytest -q tests/unit tests/contract --tb=short`，2343通过（111.52秒）；`.venv\Scripts\python.exe -m pytest -q tests/integration --tb=short`，1017通过、3跳过（318.78秒）。合计3360通过，跳过项为两个PostgreSQL活体验证及一个Elasticsearch活体验证，未把跳过当作通过。
- Node/Web：以`rg --files tests/node tests/web`生成测试文件列表后执行`node --test --test-reporter=spec @nodeSuiteFiles`，Windows169通过、1个Linux进程身份测试跳过。WSL没有独立npm，改用锁文件hash一致的现有Pi镜像在Linux中执行`node scripts/build-pi.mjs`，再运行`node --test --test-reporter=spec tests/node/pi-runtime.test.mjs tests/node/goal-completion-runtime.test.mjs tests/node/runtime-identity.test.mjs`，32通过（含该Linux检查，组间不累加）。首次容器构建继承node用户导致COPY目录写入EPERM，构建阶段切root、运行仍node后通过；失败日志保留。
- 其他验证：`npm run check`、`python scripts/check-docs.py`、`git diff --cached --check`通过；`python -m eval demo --output data/reports/query-budget-20261025/evaluation-demo`离线演示通过，仅证明工程流程。原模型金标、候选范围和14,000字节提示词限制未放宽；本次无真实模型质量评测或SSH部署。
- 本机运行：更新现有镜像、仅修改私有`.env`的四个查询预算项，再执行`.\scripts\start-local.ps1 -Distribution Ubuntu-22.04 -WaitTimeoutSeconds 180`成功。Windows首页HTTP200，数据库/索引ready，Pi健康、worker运行、保活Running；`/api/health`实际回报8次规划、24次共享调用、8次工具、256000 tokens、600秒。切换前无RUNNING/QUEUED查询和导入。既有运行依赖全部复用；原始证据和失败日志仅留在被忽略的`data/reports/query-budget-20261025/`。
- 同步范围：按用户确认同步全部现有代码/文档；`.env`、`md/`、`data/`及私有回执不进入本次提交。提交前扫描未发现凭据或私有路径；推送结果及commit身份以Git和本次任务回执为准。

## 目标与边界

用户已授权通用修复和多轮工具调用。将用户目标、单次工具动作、整轮完成条件分别表达；不依赖特定数量或句式补丁。保留既有范围、权限、请求幂等、共享预算和历史提示词，不部署正式环境。初始实现仅离线检查；用户随后追加授权固定两轮对话的隔离真实模型回归，预算与验收见下文。

## 事实与待区分点

正式故障回执证明 SEARCH(final=true) 后直接输出固定模板，无 ANSWER；候选核验完整不代表语料覆盖完整。完整私有证据见本地 `data/reports/xiaohongshu-count-audit-20261019/diagnosis.md`。当前工作区存在大量既有未提交改动，改动前目标文件已保存私有基线。

现有事实/范围/权限校验保留；新增目标不授予筛选或复习写权限。目标选择属于模型语义质量，host只能校验来源、状态及证据结构，不能声称工程断言证明任意自然语言理解正确。

## 方案与验收

- 独立 UserGoal（objective、DIRECT_RESULT/SYNTHESIS），由 host 绑定当前请求、来源与run；后续工具不能降级或改写目标。旧协议保留兼容路径，新提示词采用新目标契约。
- 每次工具回执保存 host completion，`final`只作提议。综合目标读工具后必须继续，ANSWER/CLARIFY/已提交写入分别记录回答完成、等待澄清、写入提交。finish及恢复统一依赖该持久状态。
- 回答可引用当前run的工具ordinal及有界统计/覆盖证据，支持零结果，不借虚构题ID绕过证据门。完整性字段在host和Pi摘要中保留，历史会话不自动充当新鲜聚合事实。
- 多轮仍受单轮工具8次、配置规划次数、共享调用/token/截止预算约束；提前耗尽保留partial，不能保存SUCCEEDED。复习写入的授权目标绑定请求起始页，后续检索不能扩大。
- 定向回归覆盖数量核查、分析/比较/建议、多轮读取、候选不完整/零结果、直接列表/计数、结构化入口、澄清/写入、恢复、预算失败及范围/权限不扩张。模拟计划/工程测试与真实模型质量分别报告。

## 进度与结果

2026-10-19：已完成正式只读诊断和源码核对；开始实现。证据投影、Pi与prompt、主机完成契约分工并行。执行命令、失败与最终结果在本节继续追加；本次不宣称已部署或真实语义质量通过。

首轮主机定向检查：`pytest -q tests/integration/test_intent_answers.py tests/unit/test_answer_context.py tests/unit/test_query_intents.py tests/unit/test_tool_contract.py tests/unit/test_query_response.py tests/unit/test_harness.py`，99通过、2失败。失败为旧默认v15断言及冻结schema未扣除本次新增goal/evidence字段；保留旧fixture，新增字段单独断言后继续比较全部旧字段。无真实模型调用，未更改参考语义。

默认版本决策：新v16要求显式user_goal，默认切换有本任务的目标/完成契约变更依据；历史prompt保留。旧typed adapter无goal保留legacy兼容，不假称其目标已由新协议识别。现代自然语言入口先声明目标，停止走未声明目标的集合/Jev直达捷径；结构化UI保持SQL直达。

第二轮：扩展至query agent/requery/streaming，共127通过、2失败；二者是旧Pi callback和JSON流fixture未提供v16必填goal，补齐模拟模型的显式目标后重测。未放宽正式模型边界。多轮错误恢复另外发现工具ordinal可能有空洞，已按持久真实ordinal映射，避免用完成列表下标代替回执身份。

第三轮扩大检查：240通过、10失败，均为旧工程全集/分类选择测试使用NoPlanner却隐式采用新默认v16。将这些明确验证零规划旧捷径的fixture显式绑定v15，保留原业务断言；v16另测“先声明goal，再走主机审核SQL或综合回答”。新多轮主机专项15通过，另一次相关89通过（范围重叠不相加）。新prompt压缩至13,596字节，保留既有14,000字节单文件预算，不抬请求预算。

新增API/权限/预算回归首轮76通过、1失败：第8次工具在执行前已经预占ordinal，第二次动态权限检查误把该预占位置当作耗尽，返回NOT_ALLOWED。修复为二次校验计入当前已预占槽，保持最多8次而不提高上限；第9次仍预算失败，持久partial无completed事件。

用户追加真实回归授权：候选冻结和离线定向检查完成后，用原话“小红书的算法题”→“小红书只有四道算法题吗？”在隔离账号、隔离候选服务执行真实Pi与模型。保留正式模型配置、共享Linux锁、每请求6calls/96k/180秒、规划配置4；两轮单次场景上界12calls/192k。若真实失败，先封存原始回执和预算，不覆盖原结果或放宽判断/覆盖要求。最多预留一次修复后同场景回归，总上界24calls/384k，未知usage保守扣计；不执行其他模型场景、不部署正式环境。

工程最终定向检查：Python核心269项、证据/保护154项、独立schema/长链边界6项，互不重复共429通过；Pi真实运行时（模拟上游响应）59通过；无跳过，既有Starlette/httpx弃用提示保留。Node语法、静态声明同步及受影响6页文档结构检查通过。精确命令及XML在私有[工程检查记录](../../data/reports/query-goal-completion-20261019/ENGINEERING-CHECKS.md)。未运行完整脏工作区回归，未修改/重建vendor。复习状态摘要在Python/Pi一致：最多5条明细，但status_counts覆盖本次合法请求ID；非法数单列，summary_complete=false时不把未知当UNSEEN。

追加旧写入/恢复保护组首轮45通过、1失败：旧有效复合请求“列出三题并标记为已掌握”不引用起始页，统一冻结空起始页会误拒。修复为由本轮明确复合指令（不是模型goal）授权首个合法结果页，一旦绑定即冻结；当前页指代仍冻结起始页，重查仍只读。补新结果首屏之后换页、空结果后换集合及恢复边界检查。此修正不改只读小红书语义范围，候选冻结需同步该授权增量后执行真实模型。

该权限修正后两组定向各50通过（重叠不相加），包括3项新增首屏/空集合/恢复保护。新提示词同步准确授权说明后为13,662字节；未抬任何预算。正式基线只读预检152文件SHA一致，r295/i295/a32、rerank15/scope6及共享锁身份已核；真实回归候选只叠加本次完成契约，未带入工作区v42检索实验。

真实回归attempt1未通过，失败后已封存原始两轮HTTP、持久状态、模型输入输出和预算（私有 `data/reports/query-goal-completion-20261019/attempt1-final-evidence/`）。第一轮成功；第二轮已正确绑定SYNTHESIS，首次SEARCH的4题/50候选/80资格及非全库覆盖证据、NEEDS_RESPONSE均正确，但模型再次SEARCH，入第6次embedding后rerank被共享预算拒绝，最终503/FAILED，无ANSWER。两轮真实进入provider共10次，另2条零token门前拒绝不能当provider调用；可观察token为83,595，仍按未知usage保守扣10calls/131,072tokens，不退费。采集脚本把含拒绝的日志条数误当provider次数而额外断言失败；业务失败已独立保存，未重放请求。

据此修复通用能力准入：工具菜单及执行校验同时考虑下一次规划、搜索基础模型成本和SYNTHESIS回答预留；最后一次规划只开放终态动作，SQL多轮读取仍可在预算内进行。已归一化为SEARCH的LIST不能绕过成本检查，未处理workset翻页按高成本处理。工具阶段provider门另保护1次回答调用，防可选核验吞掉预留；不提高6calls/96k/180秒及规划4次。调用预留不保证token/期限内一定回答，原硬限仍有效。prompt通用规则更新后13,979字节，仍低于14,000。

attempt1临时API/Pi已停止，clone按owner、marker及零连接检查后删除；正式152文件、服务PID/InvocationID、r295/i295/a32、共享锁及ES alias前后均不变。唯一修复后回归需从原dump恢复新clone，使用新账号、会话、request，避免embedding缓存改变调用成本；保留总预算24calls/384k，尚不宣称真实语义通过。

修复后离线核心扩展组295通过，最终预算边界补到31项（含DENSE、planning=1直接数量、已签名workset缓存翻页与新批次）；预算/分页/恢复合组76通过，Pi完整60通过，语法/声明同步/6页文档检查通过。各命令有重叠，不累加虚报数量。候选06基于正式原包仅叠加本任务12文件增量，142旧文件保持，SHA `3939e5fdddb2e716138c42dcb202deb9626a3ffd81ab0d8a45d7c7a1c159a2a8`；候选自身62项Python、10项Pi离线检查通过。工作区未发布workset预算分支不进入该正式基线候选。

attempt2从首次保存的原始dump重新恢复新clone，使用新账号、会话及request；不复用第一次评测写入的缓存。原始dump SHA、候选文件清单、query16/rerank15/scope6、固定ES物理索引及同一共享锁均核对。**真实两轮仍未通过验收**：第一句HTTP503，用时180.080秒，持久FAILED/QUERY_DEADLINE_EXCEEDED；QUERY_PLAN实际provider126.374秒、embedding1.177秒、rerank47.383秒，合计174.934秒，另锁间隔3.449秒，范围核验在provider前被期限拒绝。SEARCH为INTERRUPTED，无ANSWER，第二句按冻结规则未发送；原话Unicode与请求一致，无替换字符。

此次观测为3次provider进入、31,066 tokens，另1条零token门前拒绝；失败仍保守扣6calls/96k。两次尝试累计保守16calls/227,072tokens，未退费、未加预算、未重放、未延长180秒。独立只读复核确认该次耗时足以解释截止，但不能据单次轨迹断言只是网络波动；也不能据此宣称预算修复失败或已真实通过。私有原始回执、完整模型请求/响应与预算在 `data/reports/query-goal-completion-20261019/attempt2-raw/`，摘要为 `attempt2-failure.json`；后续收口包保留清理证明。正式文件、服务、锁及ES alias未变，临时unit停止、clone删除，未部署。

attempt2结束时的未覆盖与下一步：通用代码和工程门已实现；当时用户指定两轮的成功回答语义仍未确认，不能交付“已直接判断并解释覆盖范围”的通过结论。后续真实验收需重新固定单次规划/检索耗时与总预算，再在原6calls/96k/180秒限制下完成同一句对话；失败和本次候选不得覆盖或删去。全库语义质量、工作区其他检索实验、前端浏览器端到端及正式部署均不在本次通过范围。后续本机结果见下文，不覆盖此次失败。

## 继续排查规划延迟与真实验收

用户再次明确授权：拆分126秒规划延迟，优化后按原每轮6calls/96k/180秒完成原话两轮，必须取得实际回答并审判断、依据、覆盖范围。旧失败/预算原样保留，本轮新增固定场景单次上界12calls/192k；每次失败先记录、区分协议/工程/语义，再决定下一次实施，不用离线通过替代真实结果，也不扩大单轮预算、改模型或检索范围。

初步事实：attempt2规划provider126.374秒，TTFT122.327秒，首输出后4.047秒，queue/interval均0；input14200/output298，reasoning0、cached9016。attempt1同一第一句话input14038/output298，provider11.885秒、TTFT4.567秒，reasoning0、cached0。由此排除“本地锁排队/长输出/已计账推理导致126秒”；可定位到provider内首输出等待，不能区分其内部排队、路由、连接或预填充，更不能据一次样本把输入量断言为唯一原因。

待区分检查与方案：只读量化实际Pi请求的system prompt、工具声明、共享schema重复和会话上下文；优先删除不影响JSON约束/范围/权限的重复传输。保留全部工具预算、host最终校验、持久化完成和有界覆盖证据，版本化模型线协议变化及缓存身份。冻结正式旧基线+本轮优化delta，使用原始dump新clone/新actor/会话/request、固定物理ES、同Linux锁及原模型配置真实重跑；依据实际文本及当前工具回执独立验收。任何新失败继续追加本节，不覆旧场景。

量化与选型：attempt2紧凑UTF-8请求48,704字节，工具声明30,057字节（61.7%），其中5份重复FilterSpec共16,915字节。仅去title/default省4,431字节（9.1%），不能把字节降幅当作token/延迟同比。选用显式 `query_step_v1` 模型传输：完整QuerySpec仅声明一次，action枚举与条件按host当前能力编译；Pi再按唯一action映射原领域工具，host权限/范围/回执不变。旧Pi菜单、旧提示词和JSON fallback保留，v16 API显式选择并持久记录wire版本；不是按本例句式或数量分流。原schema验证关键词及scope/goal/evidence不得删除，以定向真实Pi与API反例守护。单轮预算不变，先冻结再执行；需要修复时先保存失败与累计预算，禁止额外模型探针和隐式重放。

继续阶段离线结果：Python82、Pi70通过，无跳过，静态语法/声明同步和6页文档检查通过。新增schema复核在真实模型前发现中性默认字段与动作白名单冲突，已保留完整QuerySpec并修正；另补trusted query_step/action=ANSWER的临时正文流，不能从其他action或旧wire泄露正文。实际pinned HTTP边界同schema+5工具的工具JSON由28,532降至10,514字节（63.2%），真实token与时延待实测。来源与命令见私有ENGINEERING-CHECKS。

最终候选08 SHA `6fab209f8c6a07e41f3d44798b95064ac2c0642bc18272b3dab56bb055230188`，相对06只叠API、query_contract、presentation、Pi runtime四文件；query_service/providers/rerank逐字不变，154文件中141旧文件保持。中间07只在本地冻结，未调用模型/远端执行。正式只读预检出现SSH端口连接超时（exit255，未到鉴权），错误已封存 `followup-readonly-production-probe.json.stderr`；当前阶段尚无新模型调用，先排查现有连接路径再恢复真实验收，不用离线通过顶替。

用户随后明确指定“用本机”，停止SSH路径排查，改本机真实API/Pi/模型回归。Windows本地5432/9200/8000/8787未监听，WSL Ubuntu-22.04停止；按仓库完整运行入口唤醒WSL并先核既有Docker服务/数据，不直接导入或重建语料。优先本机既有Linux共享模型锁与模型配置，隔离账号/会话/请求；不假称本机锁与不可达远端互斥。本机数据/模型与旧远端身份如不同需单列，不强求题数4或复制旧期望。每轮6calls/96k/180秒及真实文本验收保持不变。

本机只读核查：既有镜像API为query13/rerank19/scope5，MODEL/query/rerank名称ark-code-latest、embedding为doubao-embedding-vision；语料r294/index293/a30（与远端295/295/a32不同），小红书范围80标准题/81原始提问、7场面试/7文档。保活复用既有计划任务，未重建镜像或原语料。Docker自动恢复worker后继续了旧导入模型请求，与本次评测调用分别记账；为固定副本及避免预算排队，root取得实际Linux共享锁dev2080/inode391748空隙后保持锁，停止worker，再显式释放锁。停机时无provider在途，凭据未输出；回执 `local-worker-guard.json`。

候选08匹配rebuild_index的完整有效范围共2753 occurrences/2435 canonical、2401 unique text_hash，2401均有同embedding version/dim的合法缓存向量，缺失/非法0。可在PG副本+私有ES前缀零模型重建索引，不能直接改indexed_revision或切原alias掩盖本机版本差。后续两轮使用副本实际版本、本机原模型配置和共享模型锁，原库/索引不由评测修改。

本机attempt3已完成原话两轮真实API/Pi调用，并经独立实际文本复核 **PASS（仅此固定场景）**。第一轮“小红书的算法题”HTTP200/SUCCEEDED，78.533秒、5次provider、39,964 tokens；第二轮“小红书只有四道算法题吗？”HTTP200/SUCCEEDED，89.107秒、5次provider、43,288 tokens。每轮仍为6calls/96k/180秒、最多4次规划；阶段合计10calls/83,252tokens，无拒绝、重试或隐式重放。旧远端阶段保守账本16calls/227,072tokens独立保留，不清零或混作本机余额。

实际第二轮首句为“不能据此断言‘小红书只有 4 道算法题’”。它随后列出本轮核验的4道去重题，并说明候选核验完成不代表已全库穷举，因此不能证明总共只有4道。持久目标为SYNTHESIS，本轮SEARCH ordinal1后为NEEDS_RESPONSE；ANSWER ordinal2引用本轮evidence_tool_ordinals=[1]与4个合法题ID，完成回执COMPLETE/terminal=true。第二次规划菜单按剩余预算移除SEARCH，仍保留可承担的SQL/DETAILS/ANSWER；模型最终选择ANSWER。不是由首轮历史回执、关键词评分或模拟工具链替代语义验收。

覆盖口径：本机副本r294/i294/a30，本轮50候选、80道公司资格题、返回4道去重题（共5次原始提问），candidate_verification_status=COMPLETE、verification_complete=true、corpus_complete=false。80是小红书各题型资格范围，不是算法题总量；4也不是全库算法全集证明。公司硬筛保持小红书，题型/编程/主题/时间/岗位硬筛均空，算法目标保持软召回，前后未漂移；同一新会话version1→2。实际文字正确保留候选覆盖边界，但直出内部字段，数量覆盖可进一步用自然语言表达；此风格限制不改变本次判断、依据、覆盖范围三项通过。

本机首轮首次规划实际input8,276/output312，provider9.724秒、TTFT4.592秒；第二轮首次规划input9,644/output370，provider9.452秒、TTFT3.161秒。四次实际规划请求均只声明1个query_step工具，持久原域工具及主机授权保持；真实事件有model_wire2、tool_result4、answer_delta337、completed2。本机首请求UTF-8 29,461字节，对照旧远端48,704字节；输入token较旧远端14,200减少41.7%。这些证明传输收敛及本次预算内完成；本机/远端数据与宿主不同，不能把时延改善全部归因schema，不能声称已消除上游慢首包或P95达标。

隔离收口：临时API/Pi停止PID0并删除；clone按owner/marker/零连接核查后删除，私有ES alias/物理索引删除，私有env/cookie删除。清理时原r294/i293/a30、原ES alias、镜像/容器身份和Linux共享锁均未变；随后恢复原worker，保活计划任务回到原Ready状态，未终止有其他容器的WSL。恢复后的旧后台任务不计为评测调用，也不承诺其后语料不再变化。本轮未替换本机常驻旧镜像，未发布远端。

完整私有证据与独立结论见[本机语义验收](../../data/reports/query-goal-completion-20261019/local-attempt3-216593de/SEMANTIC-REVIEW.md)、同目录root-artifact-check.json、actual-replay-proof.json、postflight-cleanup.json及local-environment-restoration.json。原始封存包local-attempt3-final-evidence.tar.gz SHA为`08a4cbc238618fc619fb83f267452835deeaa7810f5b4aa421d4845cfe79163c`；独立复核及恢复回执在封包后单列，不改写原始回执。离线工程最终82项Python/70项Pi通过；候选另有Linux pinned Pi20项通过，工程组有重叠不累加。完整命令见私有ENGINEERING-CHECKS。全库语义质量、其他多轮场景、浏览器端到端、稳定延迟与部署仍未验收。
