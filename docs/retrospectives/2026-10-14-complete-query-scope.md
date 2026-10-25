---
id: PIT-008
status: current
resolution: mitigated
resolution_scope: complete-sql-scope-and-audited-task-semantics
scope: historical-report-and-workspace
verified_at: 2026-10-17
runtime_verified: false
tags: [query, sql, retrieval, frequency, semantic-scope, annotation, completeness]
sources:
  - path: md/issue.md
  - path: docs/verification/2026-10-10-query-metadata.md
  - path: docs/verification/2026-10-11-byte-algorithm-ssh.md
  - path: docs/verification/2026-10-12-byte-algorithm-semantic.md
  - path: docs/verification/2026-10-14-byte-algorithm-all.md
  - path: docs/verification/2026-10-14-query-timeout-frequency.md
  - path: src/interview_intelligence/analytics/semantic_scope.py
    symbols: [semantic_input_hash, semantic_base_filters, load_semantic_scope]
  - path: src/interview_intelligence/analytics/semantic_audit.py
    symbols: [build_amendment, project_amendment]
  - path: scripts/project-semantic-review.py
  - path: src/interview_intelligence/analytics/semantic_scope_import.py
    symbols: [build_scope_import_artifact, import_semantic_scope]
  - path: scripts/import-semantic-scope.py
  - path: src/interview_intelligence/agent/query_contract.py
    symbols: [QuerySpec.validate_action]
  - path: src/interview_intelligence/agent/query_service.py
    symbols: [QueryService._execute, QueryService.finish]
tests:
  - tests/integration/test_query_agent.py
  - tests/integration/test_query_metadata_scope.py
  - tests/integration/test_byte_algorithm_regression.py
  - tests/integration/test_semantic_scope_listing.py
  - tests/integration/test_full_algorithm_query.py
  - tests/integration/test_semantic_scope_amendment.py
  - tests/integration/test_search_frequency_ranking.py
  - tests/integration/test_engineering_scope_listing.py
  - tests/integration/test_complete_engineering_query.py
  - tests/integration/test_semantic_scope_import.py
  - tests/integration/test_detail_invalid_source.py
  - tests/unit/test_engineering_evidence_recovery.py
---

# PIT-008：把候选列表当全集，或把旧标签当真实语义

[返回索引](README.md) · [最终完整范围验收](../verification/2026-10-14-byte-algorithm-all.md)

## 30 秒回忆

“找些相关题”“这个类别全部题”“按频次排”是不同契约：排序正确，也可能只排了有限候选。
另一个独立问题是类别本身：旧 `ALGORITHM` 或 `KNOWN` 标签不能证明原文要求一道算法题。
处理顺序是先确定请求全集，再证明每个 occurrence 的语义资格，最后聚合、计数和分页。
完整 SQL 能力现已存在；缺少范围审计时必须拒绝完整性承诺，不能悄悄退到 Top50。

## 原始事件与症状

- [Q01. 类别枚举被当成检索 Top K，数量与排名语义不正确](../../md/issue.md)：自然语言要 Top40，默认候选/页面 20 条却被解释成完整排名。
- [Q02. “手撕代码”与“算法题”混淆，旧标签不能直接用于新需求](../../md/issue.md)：知识主题、回答形式和编码任务混在一个旧类型里；同一标准题的不同来源要求也不同。
- [10 月 10 日元数据验收](../verification/2026-10-10-query-metadata.md)：公司别名及旧主题交集令结果为零；这是范围表达错误，不是“库里没有”。
- [10 月 12 日语义验收](../verification/2026-10-12-byte-algorithm-semantic.md)：元数据结果有原理题/设计题，也漏掉 UNKNOWN 中真实算法题。
- [10 月 14 日全量验收](../verification/2026-10-14-byte-algorithm-all.md)：追问“全部”仍走有界检索；相关性不可用又被包装成成功空列表。

这些事件相邻，但不能合成“换一个 prompt 就修好”的单根因。

## 已证实的原因与边界

| 子事件 | 已证实原因 | 不能据此推断 |
| --- | --- | --- |
| Q01 全局排名 | 检索 Top K、Top N 结果集、当前页混用 | 页里频次降序就代表全库排名 |
| Q02 类别口径 | 旧标签混合主题、形式、任务；canonical 不能替代 occurrence 的原文要求 | 有 CODE 或算法主题就一定有算法求解任务 |
| 公司查询为零 | 别名未归一，模型又继承旧 topic 硬筛；这些条件实际求交 | SQL 查零等于真实零题 |
| 语义精度 | 引文存在，但引文没有证明本题要求具体算法操作或计算任务 | “引用可定位”等于“判断正确” |
| 全部追问 | 有界候选无法证明全量；核验不可用仍成功属于另一项状态契约错误 | 上游 APIError 的根因已查明 |

Q02 后续采用 occurrence 级 `response_form` / `coding_focus`，保留旧类型以兼容历史。
当时手撕工程代码与算法求解按不同口径查询；历史回填数量和 KNOWN 数不是当前语义正确率。

## 实际修复过程与失败尝试

1. Q01 改为 SQL 在有效 occurrence 范围内汇总标准题频次，再做全局 Top N 和稳定分页；区分 `matching_total`、结果集总数与当前页。
2. 元数据修复增加公司别名/有效值目录，历史重查以原始用户目标为准；旧模型 filters 只能提供参考，显式 UI 条件仍是约束。
3. 10 月 12 日改为语义候选核验：旧类型作为软召回线索，逐题核当前题的任务与连续原文引文，不能借邻题补出要求。
4. 10 月 14 日为“完整算法范围”补充持久化正/负审计，再让 LIST/COUNT 走 SQL；限定当前来源范围和版本。
5. 核验不可用改为 `QUERY_RELEVANCE_UNAVAILABLE`、失败回执及 API 503；真正核验后的零结果仍可成功。

| 实际尝试 | 结果与下一步 |
| --- | --- |
| 只改公司别名 | 仍受旧主题/任务口径影响；修正数量不等于语义已正确 |
| 10 月 12 日首个语义候选 | 5 组中 1 组历史重查把泛泛数组/链表设计误判为算法实现；未部署，收紧任务证据 |
| 10 月 14 日继续重复检索 | 首次 APIError 后再规划/重复调用，未知用量保守计费，后续预留超过预算；不能靠重试证明全集 |
| 全量验收 v2 | 合法 page_size=100 返回全部，但脚本硬期待 20；修正测试输入，保留失败记录 |
| 全量验收 v3 | COUNT 数值正确，却额外读工具并触发 fallback；收束为终止 COUNT，不靠加预算绕过 |

这些尝试详见各原验收；未记录原因的上游 APIError 仍未知。发布脚本/环境差异另查对应部署复盘。

## 当前实现如何守住契约

- [QuerySpec.validate_action](../../src/interview_intelligence/agent/query_contract.py) 限制 SEARCH 候选大小且不接受全局 `top_n`；`semantic_task=ALGORITHM_PROBLEM` 只用于 LIST/NEXT/COUNT/STATS。
- [load_semantic_scope](../../src/interview_intelligence/analytics/semantic_scope.py) 核验当前匹配范围内**全部** occurrence 的 ACCEPT/REJECT，包括负例；缺失、过期、无效均抛出 `SEMANTIC_SCOPE_INCOMPLETE`。
- `semantic_input_hash` 绑定 occurrence、原问题、source spans、来源版本和解码文本 hash；审计版本、连续引文及 reason 也须有效。
- `semantic_base_filters` 拒绝与显式旧 `coding_focus` / `annotation_status` 冲突的组合；不会静默清掉用户条件。
- SQL 只按接受的 occurrence 聚合：同一 canonical 的工程来源不能增加算法频次；`scope_hash` 参与分页一致性。
- SEARCH 的 frequency 模式先对已核验候选按过滤后的 SQL occurrence 次数排序，再截页；仍是候选池内频次，不是全库榜。

自然语言 SQL 请求仍可调用模型规划；结构化列表/计数的事实执行不依赖检索，不能笼统写成所有 SQL 请求“无模型”。

10-16 离线追查发现，旧审计还曾按“工程组件”类别直接覆盖一个具有输入、计算操作及返回目标的任务为 REJECT；SQL 正确执行了该决定。修复不能在查询按题名放行或修改金标，应保留旧审计，生成绑定完整旧范围、来源和逐题更正证据的审核物。[审核投影入口](../../scripts/project-semantic-review.py) 目前只向内存 SQLite 投影并递增本地 annotation revision，原决定与新 scope hash 均保留；未重审的条目不得冒称按新规则审核。该入口不发布共享事实，真实服务仍须独立发布适配及运行核验；证据见[本批记录](../plans/2026-10-15-evaluation-readiness.md#五问剩余质量与耗时修复10-16离线批)。

## 历史验证能说明什么

| 阶段 | 当时结论 | 限制 |
| --- | --- | --- |
| Q01 | Top40 与独立 PostgreSQL 查询一致；20+20 无重复 | 证明数据库口径一致，不证明标签语义 |
| 10 月 10–11 日 | 公司算法旧标签范围 27；COUNT/NEXT/过滤一致 | 10 日是本地，11 日另有 SSH 发布；27 不是语义真值 |
| 10 月 12 日 | 5 组隔离加 2 组公开语义验证；固定参考命中 24/26 | 有 2 个已知漏题，不能称完整总数 |
| 10 月 14 日 | 32 个来源范围内当前 565 条 occurrence 全部有正负审计；31 个正例归并为 28 道标准题 | Agent 审核，非人工金标；不代表公司所有历史来源 |

全量修复还恢复了同一来源版本下两个被错误新 active build 遮蔽的旧有效 build；旧坏 build 标记待核验，未靠重复插题补数。
最终 v4 记载隔离 7 组、公开 7 组及全局约束通过；这些是历史证据，不是本次运行结果。
与[频次验收](../verification/2026-10-14-query-timeout-frequency.md)中的候选排序共存；新增来源未经审计时不得沿用旧“完整”声明。

## 防复发与排查顺序

1. 先确认用户要全量、Top N 还是相关候选；读取真实 action/semantic_task 和响应完整性元数据。
2. 核显式筛选、继承来源、公司别名及同一 occurrence 的联合条件；再检查 active build 和来源版本。
3. 看正负审计是否全覆盖、hash/版本是否有效；缺口不能用旧 KNOWN 标签补齐。
4. 比较完整范围计数、标准题计数、occurrence 频次、分页并集；最后才检查页面展示。
5. 核验失败要查持久化失败状态和预算回执；不把成功 HTTP、空数组或自动重试当作业务成功。

已有守护：[范围审计/负例/新来源](../../tests/integration/test_semantic_scope_listing.py)、[全量多轮/超过 50 分页](../../tests/integration/test_full_algorithm_query.py)、[候选频次](../../tests/integration/test_search_frequency_ranking.py)。原整理阶段只静态核对；10-16 工程修复实际执行结果见下文。
待补证据是更多未见来源的独立语义审核，以及新来源进入后的审计运维闭环；现有样例不能替代它们。

## 2026-10-16 工程设计全集修复工作记录

- 目标：按用户原话“列出所有工程系统设计题”提供来源绑定的完整范围与稳定分页；先独立审核当前有效语料形成 gold，再验证六个已知漏项及额外正负例，不按题名或 ID 放行。
- 已知事实：正式查询在 r295 上从 2482 道可搜索标准题召回 50 个候选，仅返回 15 道；无分页，规划额外排除 AI/Agent；延时消息队列等四题未召回，购票系统证据异常、停车场证据字段超长被排除。原文 hash/span 已核对，私有证据在 `data/reports/query-behavior-review-20261016-6001fd88/`。
- 区分检查：有效 occurrence 的工程任务与纯概念/项目复述/算法、完整范围与候选、语义否定与核验未决分开；当前工作区 v14/r33 及正式 v13/r15 分开；保留全部已有未提交工作。
- 选定方案：复用完整算法范围的 occurrence 级正负审计契约，泛化 `ENGINEERING_DESIGN`，SQL 全范围枚举/计数与签名分页；主机按用户原话路由并保留显式筛选；来源异常保持未决，补核验不放宽证据；全量审核未覆盖时拒绝完整声明。
- 验收：全量来源审核与独立复核、六个漏项、超过 50 题分页并集与无重复、COUNT 同 occurrence 口径、显式筛选、未决/过期/新增来源阻断完整声明但仍可见、普通 SEARCH 与算法回归、工具契约及文档检查。真实模型/部署与离线工程检查分别报告，未执行项不写成已通过。
- 实施结果：新增独立版本 `ENGINEERING_DESIGN` 来源审计与通用导入；宽范围全集走 SQL，按当前有效 occurrence 聚合、计数及签名分页，不再运行 Top50 召回。无授权不能加入后端、旧题型等硬筛。可信 UI 条件跨续问保留，明确前 N 与每页 N 均尊重；未知额外语义不能默默丢弃。
- 异常策略：工程范围 ACCEPT 与待核验项共同分页，未决独有题按确认频次 0 展示，不计已确认频次/评分；混合标准题分别列两类提问，COUNT、分组、模型证据和页面保留状态。非法引用可查看原问与原因，不能生成伪来源按钮。算法原严格完整范围契约保持。r34 的一次有界证据恢复仍遵守同一来源、范围复核、预算与共享模型锁。
- 独立对照：冻结 r295/i295/a31 的 172 个来源、2807 次有效提问、2482 道归并题，raw/decoded hash 与 span 校验均通过。Agent 原文审阅及边界二审得出 310 次 ACCEPT（301 道归并题）、2478 次 REJECT、19 次 UNRESOLVED（19 道），不是人工金标。语义决定先冻结再运行实现，不用候选结果补标签。
- 真实输入重放：在独立内存 SQLite 中导入相同来源与审核物，经实际 `/api/query` 遍历每页 20 道的 16 页；并集 320 道＝301 确认＋19 待核验，无重复，与冻结对照一致。六个已知漏题均确认找回；COUNT 返回确认 301 道/310 次，另列待核验 19 道/19 次并说明完整总数尚未确定。全过程 0 模型调用、0 共享库写入。
- 私有回执：`data/reports/complete-engineering-20261016/` 下保留 `gold-final.json`、`gold-question-list.txt`、版本绑定 `scope-import.json`、导入 PREPARED/APPLIED/rollback、`query-pages.json`、`replay-result.json` 与失败/复测记录 `attempts.txt`；坏抽取 span 的单独修复线索也保留。一次导入引文超出 occurrence 的失败未被放宽条件绕过，保留完整审核引文并使用已校验的当前原问锚点；没有修改原始抽取来凑验证。
- 验证结果：`.venv/Scripts/python.exe -m pytest -q` 最终全量离线批为 **2115 passed / 3 skipped**（330.22 秒，原有 Starlette 弃用警告）；跳过项要求显式 PostgreSQL 连接或本地 ES。最后增加结构化列表任务/分组保留两例，并对工程查询、API、query agent、响应做补充回归 **74 passed**。实际 Pi/工具契约/证据投影/页面标记 Node 测试 **27 passed**；`npm run check`、`sync-tool-contract.py --check`、默认14页文档结构检查及 `git diff --check` 通过。首次全量批的5个旧版本/调用次数预期失败与更正依据均留在 attempts，未移除错误拒绝断言或放宽来源条件。
- 发布边界：本次未部署线上，也未向共享 PostgreSQL 发布审核物；实际 Ark 模型质量、生产 PostgreSQL 执行与浏览器/SSE 发布后验收尚未执行。线上仍不能视为已切换到本地 v15/r34。

## 2026-10-16 数量预览与分类选择工作记录

- 目标：大工程集合先报告确认/待核验规模，允许分类浏览或全量分页；全量选择在同范围续问中保持，分类不明确与待核验不能漏掉。
- 实施前事实：已有 SQL 完整列表和签名 cursor；冻结对照为确认301道、待核验19道。当时完整请求直接 LIST，尚无按数量触发的浏览选择；模型通用澄清最多4项，不能假装已有可靠后端/AI语义子类。
- 方案：主机配置数量阈值（默认100道可见去重题），按实际 Top N/复习筛选后集合判断；COUNT/STATS不拦。使用现有 occurrence 主题 L1 组织浏览，含未决的标准题放待核验桶，缺失/冲突/未知分类放分类不明确桶，分桶并集必须等于原可见集合。选择类时按 canonical 集合保留其全部匹配 occurrence；全量不添加分类约束。状态绑定原筛选、审计/语料版本、集合hash与选择阶段，重复回执幂等，变更快照重新预览。
- 验收：小集直接返回、大集先报数量、分类并集对齐gold、全量16页无重复/不重问、未知/待核验桶、混合来源与同occurrence口径、Top N与COUNT/STATS例外、续问/重查/状态版本与签名防错、页面大于4分类可达；保留既有未提交修改，不调用真实模型或部署共享库。
- 已实现：`CollectionSelection` 在主机和直接工具入口统一解析持久化选择；大集先报确认/含待核验/可见去重数，按已有主题或全量分页；同范围的全量选择跨 NEXT、计数、排序和重查保留。改筛选不会复活旧范围，输入未知类别返回菜单，结构化请求的语义授权不会退为全库计数。签名绑定分类来源与审计指纹，跨日沿原集合 `as_of`；列表分类按原 Top N/复习选集的标准题分桶，不按单个来源标签删掉该题其他提问。
- Gold 验证：同一冻结语料与语义金标未修改。独立从冻结 gold/occurrence 主题建立分类预期，再经实际 API 检查13类互斥且并集320；含分类不明确4道、待核验19道。全量16页无遗漏/重复，6个已知漏项全部保留，COUNT301道/310次确认及19道/19次待核验；选择后分页、排序与重查不再澄清。私有输入、每次独立 run、源码身份与完整回执在 `data/reports/complete-engineering-selection-20261016/`；这是导入审核物后的枚举/交互验证，不是新增模型语义质量验收。
- 独立检查：发现并修复 COUNT 改公司后旧选择残留、未知分类绕过 API 捷径变无范围 LIST、结构化语义来源丢失、分类分组 NEXT 丢集合及选择分类同时变更 UI 筛选后丢分类的问题；失败现场和保持原断言的复测见私有 TESTS/attempts。全量离线复测 **2165 passed / 3 skipped**（328.79秒，原有 Starlette 弃用警告）；该批启动后最后补充的筛选边界修复另以工程选择/状态/分类/全集专项 **94 passed** 验证，重叠项不相加。前端/实际Pi/工具投影 **99 passed**；`npm run check`、工具契约同步检查、文档结构检查及 `git diff --check` 通过。
- 最终回执：`runs/20261016T091254699028Z` 在业务模块 import 前、执行开始与结束强校验11份源码/配置 SHA256 一致，71次实际 API 请求均通过；冻结语料、gold 与导入物 SHA256 前后不变。首次全量批为2162通过/1失败/3跳过，未知分类绕过工具入口的失败已保留断言并修复，未按题目 ID 特批。上述结果是本地隔离 SQLite 的工程及枚举验证；未执行真实模型质量评测、生产 PostgreSQL 验收、共享库发布及浏览器发布后验收，未部署线上。

## 2026-10-16 线上部署与真实分页验收工作记录

- 目标与授权：用户明确要求部署本轮工程全集/分类选择修复到既有线上，再以原话执行并核对同一冻结 gold；验收包括分类与全量分页、未知/待核验保留及全量选择持续生效。
- 事实/待核查：工作区包含其他未提交实验，不能整包覆盖线上；本地最终重放320道、13类与工程回归已通过，线上当前运行物、数据版本和审核发布情况仍需独立读取。
- 选定路径：捕获实际线上基线和运行身份，仅叠加本次必要依赖；冻结SHA清单，保存源码/配置与一致性数据库备份及回退证据。审核物先按原始来源hash/span、有效集合及版本dry-run，再原子发布；不重建语料或改gold。沿用现有模型预算及共享锁，测试使用独立evaluation账号/会话。
- 验收：正式HTTPS实际原话先报数量、全量与每类分页集合逐项对照冻结gold；未决与不明可达、六漏题保留，NEXT/计数/排序/重查不重复澄清；运行文件/静态资源hash、服务健康、语料和索引状态前后留证。失败先留现场再修复/重试，部署与质量结论分别记录。
- 部署结果：以142份线上文件为基线冻结23文件增量，无schema迁移，保留线上模型与rerank v15/scope v6；组合包205项Python、73项前端、26项Pi测试通过。正式PostgreSQL审核2807次提问全部绑定有效（310接受/2478拒绝/19未决），原子发布a31→a32；其他注解内容摘要相同。备份、PREPARED/APPLIED/rollback、源码SHA和七服务健康回执在私有 `data/reports/complete-engineering-live-20261016/`；正式query v15，workspace `c45654a3ce8d`，三份修改静态资源hash与HTML指纹匹配。
- 实测检查点：正式HTTPS前31次均持久SUCCEEDED、0模型；全量16页320道精确且无重复，确认/待核验状态集合与冻结gold一致，六漏题保留；NEXT/COUNT/排序/requery不重问，AI分类131道7页精确。第32次首查出现非JSON且回执404；独立PG核对turn/admission均不存在，Caddy记录上游EOF/502（0.00083秒），API没有对应POST。原FAILED验收包保留，仅在已确认未接纳后补交同request_id并继续未完成分类，已完成POST不重放；未修改gold/业务断言或放宽预算，代理瞬时EOF根因不冒称已修。
- 最终正式验收：71个逻辑请求全部有SUCCEEDED持久回执，实际HTTP POST为72次（含上述一次未接纳502）；原31条只本地逐一对持久结果重验，续跑仅发送剩余40条，无第二次异常。13类逐类精确且互斥并集320，含分类不明确4道、待核验19道；全量16页、确认301道/310次与待核验19道/19次、六漏题、NEXT/COUNT/重要性排序/原句requery均通过。原gold三文件hash不变；诊断补强前脚本事后逆patch重建的SHA与原运行采样SHA完全一致，7个业务函数AST不变，重建时间明确，不冒称事前副本。
- 收尾：独立正式PG复核为71条SUCCEEDED、0条ModelCall，全部冻结运行文件SHA仍一致，API/worker显式使用同一模型锁路径，原预算与r295/i295/a32保持。已撤销临时eval会话、密码和个人provider副本，保留账号与全部回执，原个人账号未改。最终线上回执在 `runs/resume-20261016T093937473321Z/`，原FAILED包 `runs/20261016T093153724147Z/` 保留；未完成浏览器视觉点选（IAB屏蔽URL、Chrome连接不可用），不能把HTTPS/资源hash与渲染单测替代该项，也不认证新模型语义质量。

## 2026-10-16 浏览器补充验收工作记录

- 目标：按用户追加要求，通过正式浏览器点选分类入口、待核验和分类不明确，遍历全量分页，刷新后继续同一会话验证不重复澄清；截图/DOM与冻结gold独立核对，不能以旧HTTP结果替代。
- 初始事实：正式包与gold已通过上一轮HTTPS验收，浏览器视觉点选仍未执行。当前Chrome清单可见站点，通用标签读取40秒超时；按URL绑定浏览器后DOM接管返回Debugger unattached。尚未提交新业务请求，也未改部署/来源/gold；失败与恢复进度保留在私有浏览器验收目录。
- 路径/验收：优先恢复现有Chrome控制或建立独立测试标签，只经可见UI提交和点击；记录每页可见题目/状态、分类菜单和刷新前后结果、真实会话/请求身份与截图。保留用户现有会话，必要时仅使用独立验收会话；模型预算与共享锁保持，发现异常先留证再修。
- 已实际完成：通过Chrome独立标签提交原句，页面先报320/301/19并显示两入口；点按分类后13类及数量与gold相同。实际点击待核验得到19个精确ID并能展开原问/理由；实际点击分类不明确得到4个精确ID。两类分别经浏览器reload，最新run与会话/题目顺序保留，没有新澄清。DOM与截图在 `data/reports/complete-engineering-browser-20261016/` 的01–08，待核验run为 `96edb805-482e-47a8-bc90-3b0ccdd1198f`，不明确run为 `59133c2f-c012-4802-b4fb-43fd48366a5e`，同会话 `ca59d5d6-6486-46a8-961c-a0ff37240421`；隐藏library初始20行已排除，按最新assistant的answer-results核对。
- 10-17恢复检查点：截图采集后调试连接反复断开，最后一次新标签导航超时并被中断。次日Chrome已重启、旧测试标签消失；新标签导航正式URL被 `ERR_BLOCKED_BY_CLIENT` 拦截，尚未提交新query。已请用户手动打开同一正式站点以恢复控制。全量16页和选全量后刷新续问仍待实际浏览器执行，不能用前轮HTTPS通过代替，也不能把本次阻塞写成已验收。
- 10-17阻塞复核：用户确认手动打开也显示Chrome `ERR_BLOCKED_BY_CLIENT`。同日只读访问正式首页、`/api/health`、`/api/workspace/version`均为HTTP 200，数据库/索引ready、r295/i295与workspace `c45654a3ce8d`保持；这是站点健康诊断，不是浏览器验收，也不足以定位具体扩展或策略。未关闭防护、改域名或重发业务请求；保持部分通过，等待浏览器可访问后继续原会话的全量选择、16页及刷新续问。独立核对报告为 `partial-browser-acceptance.json`，诊断为 `20261017-site-health.json`，均位于上述私有浏览器目录。
- 10-17访问恢复后：用户确认正式站点已可打开，沿原浏览器会话恢复分类结果，实际重新打开13类菜单并点击全量分页。经可见UI逐页点击16页，每页20题，独立核对320行/320唯一ID与冻结gold精确一致，缺失/额外/重复/标题差异均为零；301确认、19待核验标记一致，分类不明确4题和原漏6题全部出现，19待核验保留原问原因入口及0确认次数，末页无下一页并显示结束。
- 刷新与续问：全量首屏、末屏实际reload后均保持同run、同会话、同20题顺序；刷新后重新输入原句直接返回全量首屏，再点下一页与原第2页顺序相同，按钮重新检索也直接返回全量首屏，全程无新增范围澄清。01–08为此前分类检查，09–17为恢复与全量/刷新/续问证据；旧partial与失败现场保留，最终独立结论见同目录 `browser-acceptance.json`。第4页仅漏采辅助字段assistant_count，核心题目ID/标题/状态/按钮证据完整，不据此改写原观测。页面读取每次约21秒，首次组合调用超时后按当前状态恢复，未重复提交未确认请求。
- 最终只读持久核对：该浏览器会话共25轮，全部SUCCEEDED、0条ModelCall，当前选择为all，正式r295/i295/a32保持；原句复问为LIST，续页为NEXT，重查请求引用对应NEXT运行。独立31项最终检查全部通过，前述分类/全量断言有交叉，不相加凑测试数。本轮未在浏览器逐题遍历其余11类，也未执行浏览器COUNT/排序；这些沿用前轮正式HTTPS的独立证据，不能混称本轮UI实测。全量与指定特殊分类、刷新及同范围续问的用户追加验收已补齐。
- 验收范围纠正：同日用户补充“具体业务系统设计”后的真实SEARCH反例未通过，详见[PIT-002新增诊断](2026-10-08-query-scope-narrowing.md#2026-10-17-澄清后的业务范围未落实)。上述通过仅证明已有冻结工程集合的枚举、浏览选择和恢复；不能据此声称自由表达的细分需求已被正确澄清、检索及核验，也不把SQL的0模型路径覆盖冒充模型语义分支覆盖。

## 自测两问

1. 20 条结果按频次严格降序，为什么仍可能不满足“全公司最常问的算法题”？
2. 当前范围有 31 个正例，负例中一条审计过期，能先返回“完整 28 道”吗？

<details><summary>查看答案</summary>

1. 可能只排了有限 SEARCH 候选；还可能使用了不等价的旧标签。必须证明完整来源范围、语义资格和 SQL 全范围聚合。
2. 不能。负例也属于完整性证明；过期后可能变成正例，必须拒绝完整声明并重新审计。

</details>
