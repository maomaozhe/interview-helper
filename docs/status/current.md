---
id: current-project-status
status: current
scope: workspace-and-scoped-ssh-runtime-evidence
verified_at: 2026-10-25
runtime_verified: false
sources:
  - path: src/interview_intelligence/agent/goal_contract.py
    symbols: [UserGoal, completion_for, restore_completion]
  - path: prompts/query_agent_v16.md
  - path: docs/plans/2026-10-19-query-goal-completion.md
  - path: src/interview_intelligence/agent/evidence_projection.py
    symbols: [project_row, project_meta]
  - path: src/interview_intelligence/agent/tool_definitions.py
  - path: scripts/sync-tool-contract.py
    symbols: [generated_section, generated_evidence_section]
  - path: src/interview_intelligence/config.py
    symbols: [Settings]
  - path: compose.yaml
  - path: src/interview_intelligence/agent/query_service.py
    symbols: [QueryService, QueryService._requested_pipeline]
  - path: src/interview_intelligence/worker.py
    symbols: [build_worker_services]
  - path: src/interview_intelligence/agent/task_annotation.py
    symbols: [annotate_tasks]
  - path: src/interview_intelligence/dedup/provider.py
    symbols: [OpenAICompatibleJudge]
  - path: docs/verification/2026-10-14-query-timeout-frequency.md
  - path: docs/verification/2026-10-15-source-optimization.md
  - path: docs/verification/2026-10-15-project-consolidation.md
  - path: eval/suite.py
    symbols: [run_suite]
  - path: eval/release.py
    symbols: [verify_report, check_bundle]
  - path: eval/query_results.py
    symbols: [build_query_results, score_query_results, source_references_for_case]
  - path: eval/answer_quality.py
    symbols: [validate_answer_rubric, score_answer]
  - path: eval/answer_reviews.py
    symbols: [load_answer_reviews]
  - path: src/interview_intelligence/search/scenario_scope.py
    symbols: [scenario_proof_assessment]
  - path: src/interview_intelligence/search/reranker.py
    symbols: [LLMReranker.repair_primary_evidence, LLMReranker.repair_scope_evidence]
  - path: src/interview_intelligence/search/evidence.py
    symbols: [candidate_source_context]
  - path: src/interview_intelligence/search/design_scope.py
    symbols: [design_proof_assessment]
  - path: src/interview_intelligence/analytics/semantic_audit.py
    symbols: [build_amendment, project_amendment]
  - path: src/interview_intelligence/analytics/semantic_scope.py
    symbols: [load_semantic_scope, semantic_scope_version]
  - path: src/interview_intelligence/agent/collection_selection.py
    symbols: [CollectionSelection]
  - path: src/interview_intelligence/analytics/engineering_categories.py
    symbols: [engineering_collection_categories]
  - path: src/interview_intelligence/analytics/listing.py
    symbols: [list_questions]
  - path: src/interview_intelligence/analytics/semantic_scope_import.py
    symbols: [build_scope_import_artifact, import_semantic_scope]
  - path: scripts/import-semantic-scope.py
    symbols: [main]
  - path: prompts/query_agent_v15.md
  - path: docs/plans/2026-10-15-evaluation-readiness.md
---

# 当前工作区状态与证据入口

10-25本机复杂查询预算已更新并通过健康接口核对：默认8次规划/回答、24次共享模型调用、256000 tokens、600秒，工具上限8次。API、Pi、worker已使用当前工作区镜像，数据库与索引ready，Windows首页HTTP200，保活任务Running；浏览器等待跟随持久run租约。此次运行核对仅覆盖本机配置和服务，不增加真实模型质量结论；SSH环境仍需按各自发布证据判断。调整依据、失败记录及验证命令见[复杂查询预算记录](../plans/2026-10-19-query-goal-completion.md)。下文10-19的未部署说明保留为当次历史结论。

10-25 工作区补齐评测的逐轮来源参考及最终回答分项：v2 来源契约按原话和持久工具 ordinal 检查后续轮，最终 ANSWER 的完成、引用和独立语义审核分别验收；外部审核绑定数据集、预测、回答和 rubric，缺审核保留 UNKNOWN，随报告封存并参与复算、恢复和比较。该改动只扩展工程评测能力，未对当前模型新增真实调用或发布；最终验证及未覆盖范围续记于[评测工作记录](../plans/2026-10-15-evaluation-readiness.md#多轮结果与最终回答评测2026-10-25实施前)。

10-19工作区查询默认v16，新增独立用户目标与host完成契约；综合目标在原共享预算内可多轮取证后ANSWER，候选覆盖/零结果及复习状态摘要贯通回答证据，主机、Pi和恢复按持久completion终止。真实专项先暴露重复SEARCH耗尽调用，再出现首规划126秒超时；已补成本准入/回答预留及显式query_step_v1单schema传输，旧失败保留。用户指定“用本机”后，冻结候选在隔离API/Pi真实完成“小红书的算法题”→“小红书只有四道算法题吗？”，两轮78.53/89.11秒、各5次调用，均未超6calls/96k/180秒；独立文本验收确认直接判断、引用当轮证据并说明候选核验不能证明全库只有4道。该固定场景通过、离线检查和清理证据见[本次工作记录](../plans/2026-10-19-query-goal-completion.md)。正式服务仍是显式v15，本机常驻旧镜像也未替换；本轮未部署，单场景通过不代表全库或任意多轮语义质量门禁通过。

工程收口：静态工具声明和证据字段/限额统一由 Python 生成 Pi 数据；SEARCH 的 requested_pipeline 独立解析。证据保留 host / pi_legacy 兼容口径及主机优先回退，验证与结构审视见 [10-16 收口记录](../verification/2026-10-16-tool-contract.md)。后续核对兼容检索入口，再梳理执行生命周期。

**核心功能已有实现；SSH 实际服务身份已核验，完整 V1 语义质量门禁仍未通过。**

10-16工程修复发布前的服务基线为 r295 / i295 / a31、query v13 / rerank v15 / scope v6，活跃个人账号使用 Ark 查询与重排。现已以该实际包为基线发布工程全集/分类选择的必要23文件，正式query v15、阈值100、r295 / i295 / a32，保留原rerank v15 / scope v6；未整包发布工作区v34及其他实验。相同旧业务包的隔离 HTTP 两批完成 13 场景 / 17 轮：既有先导及[用户追加五类短问](../plans/2026-10-15-evaluation-readiness.md#用户追加的五类查题10-16)已发现范围漂移、旧题型硬筛漏题，整体语义质量未达标；不包含浏览器/SSE 验收。旧过程与预算统一记录在[评测检查点](../plans/2026-10-15-evaluation-readiness.md#第二批执行授权与检查点)，本轮部署见[SSH当前版本](../deployment-ssh.md#当前发布版本)。`runtime_verified: false` 继续表示工作区实现尚未被整体认证为当前部署。

10-16“所有工程系统设计题”修复时的工作区候选为 query v15 / rerank v34：完整工程请求走 `ENGINEERING_DESIGN` occurrence 级审核集合的 SQL 聚合与签名分页，普通相关找题仍是最多50候选的 SEARCH；包括有明确交付的 AI/Agent 及工程组件机制。工程列表并列展示已确认与待核验行，未决范围标记 `complete=false`、保留原问和原因，并与已确认计数、频次分开；算法仍要求完整审核。来源审核通用导入脚本支持默认 dry-run、显式 apply、精确集合/hash/span/版本校验、历史与回滚证据；允许绑定有效的工程 UNRESOLVED 以待决状态导入。当前已独立审核 r295 全部2807次提问，冻结 Agent 对照301道确认题及19道待核验题，并在隔离 SQLite 经实际查询 API 完成16页无重复重放、找回6个已知漏项；不是人工金标。工程审核物现已通过正式PostgreSQL校验并原子发布a32，其他注解内容未变；未获得v34模型语义质量通过结论，v34也未部署。实现入口和验证导航见[查询完整语义集合](../architecture/query-flow.md#完整语义集合与来源审核导入)，证据范围见[本次工作记录](../retrospectives/2026-10-14-complete-query-scope.md#2026-10-16-工程设计全集修复工作记录)。

大集合浏览选择已部署：工程列表可见去重题数超过默认100时先报告确认、待核验与可浏览数量，用户可选已有主题分类或全量分页；同范围已选全量后不反复澄清。分类按当前来源主题对标准题互斥分桶，分类不明确和待核验单列，保留原筛选和所有匹配来源。沿用冻结 r295 Agent 对照，正式HTTPS已完成71个成功逻辑请求，独立复算真实回执确认全量16页320道、13类互斥并集完整，含4道分类不明确及19道待核验；原审核物未改。中途一次未接纳的代理502留档，确认turn/admission均不存在后补交同ID并继续，未重发已完成请求；总72次POST尝试，PG实核71条SUCCEEDED与0条ModelCall。本地证据见[选择回放](../../data/reports/complete-engineering-selection-20261016/replay-result.json)，正式证据见[线上验收](../../data/reports/complete-engineering-live-20261016/acceptance-summary.json)及[工作记录](../retrospectives/2026-10-14-complete-query-scope.md#2026-10-16-线上部署与真实分页验收工作记录)。10-17用户恢复站点访问后，浏览器补验已完成：13类入口及数量、待核验19题与分类不明确4题均匹配gold；通过可见UI遍历16页320道唯一题，独立对照缺失/额外/重复/标题差异均为零，原漏6题全部出现。全量首屏/末屏reload保持同run、会话和题目顺序，刷新后原句重问、继续下一页、按钮重新检索均不重复澄清；两个分类的刷新也已通过。先前Chrome拦截及连接超时记录仍保留，具体拦截源未定位，未绕过防护；只读健康和workspace身份保持。详见[浏览器验收](../../data/reports/complete-engineering-browser-20261016/browser-acceptance.json)及[补充记录](../retrospectives/2026-10-14-complete-query-scope.md#2026-10-16-浏览器补充验收工作记录)。本次证明冻结Agent审核集合的页面完整性与交互，不新增人工金标或真实模型语义质量认证。

10-17用户新增实证：数量澄清后补充“具体业务系统设计”并选择候选，正式SEARCH仍误收泛基础组件，且在未获用户授权时以传统范围排除AI业务应用；字段中原限定保留，失败在语义边界执行。本次未循环，但不能把澄清出现当作需求落实。工作区已实现有原话授权的范围对象，绑定目标召回、两级语义证明和持久化候选分页/重查；v38 / scope v21的第三阶段真实对照仍为NO-GO（固定58例37项精确分类，实际19道业务正例8道未召回）。v39 / scope v22实际完整重查仍为NO_GO：19道业务正例8确认、9待核验、2未召回，5道边界题3道未召回；首组固定23例原评分18/23，其余35未执行。第五阶段v40已实现完整持久候选范围逐批核验和同源证明修复，离线2689通过/3跳过、Node85通过，真实SQL320/ES及来源预检通过；首组固定23题仍为NO_GO：10正例确认、11负例排除，但2道边界题仅保留1道，首轮判负绕过独立复核导致另一道误排。原评分22/23及任务诊断错误保留，其余35未执行。第六阶段v41转为所有可能硬排题均须独立复核，未复核保留待核验，候选数与已确认业务题数分开；完整Python2717通过/3跳过、相同Pi工程输入的隔离Linux构建及30项测试通过，零模型完整320候选预检及独立回放通过。但真实首组23题仍为NO_GO，精确分类17/23，任务及查询关系也有独立失败；23题均已进入独立复核，修复调用正常完成，不能归因为跳过复核或预算未执行。累计51次实际调用/413977tokens全结算，其余35题未准入，未部署。10-18继续实现v42的业务活动证据与有界、可追溯的追问指代；原58例标签、评分器和严格语义门槛不变，新的结果另记于[修复工作记录](../retrospectives/2026-10-08-query-scope-narrowing.md#10-17-系统性修复工作记录)。此前SQL全集、分类、分页通过结论保持其原有范围，不能代替本次候选语义验收。

[五问固定参考与修复](../plans/2026-10-15-evaluation-readiness.md#五问固定对照与范围修复10-16)已从原文冻结597条逐问审核；query v14绑定用户原话，修自然场景标签硬筛及无授权算法范围。上一隔离v14/v31/v15五问固定评分为2 UNKNOWN/3 FAIL，发现4条概念负例、设计核验漏题及算法审计分歧；原回执与金标保留。

上一[v32四问实测](../../data/reports/user-scope-four-v4-live-20261016/REPORT.md)保留2 UNKNOWN/2 FAIL：Redis/MQ原概念负例未返回但固定正例降为5/3；工程15固定正例，Agent在180秒修复阶段超时。原失败检查点与未知用量保留；算法更正仍仅本地28→29/a32，未发布。

[最新e21实测](../../data/reports/user-scope-four-v6-live-20261016/REPORT.md)使用v14/v33/v17、来源上下文v3，四原问各一次、无重问：Agent90.7秒返回13（7固定正例、1边界、5未标注），仍有主排证据漏题；本次未触发repair，不能认证修复截止策略。Redis/MQ在主重排记录APIError，工程因门禁累计排队133.6秒触及180秒截止，三问语义不可评估，概念误收改善尚未证实。固定评分1 UNKNOWN/3 FAIL；四段隔离均已停止，生产、固定金标和门槛未改。记录13调用、3条usage未知，保守计324,917 tokens；累计80条持久记录、保守1,059,964 tokens/4次未知。用户已取消额外累计预算上限，每轮6调用/96k/180秒保护保留；组合报告不冒充完整HTTP采集。

[该候选离线证据](../../data/reports/user-scope-five-v5-offline-20261016/REPORT.md)的758项相关回归和177源封存保持不变；原题预算、同来源证明与时间策略有离线验证，不能代替上述真实结果。同步阻塞读取无严格墙钟中断、主排任务证据选择和项目复述边界仍待解决。

10-18来源边界及容量继续收口：已修正raw问/span错配、畸形span和编号项跨新问借证，保留严格嵌套子要求的原有效正控制。真实语料20题完整两阶段上界99253超过96000，改为至多20题窗口内按本轮实际预算处理连续前缀，未处理尾部在同一签名工集中继续，待核验不当负例。真实最坏20纯选择为18题93752，未删来源/字段或改预算；分页、SQL持久刷新/重查及失败恢复定向68项通过。首错、撤回的首次冻结与全部旧语义失败保留；新的真实23/58题、完整多轮及部署/浏览器尚未通过。

目前主要缺口：

- 质量：抽取、归并和任务分类仍有未解决边界，检索仍有漏召回与延迟。
- 运行：查询执行仍依赖 API 进程；导入版本记录、索引策略和事务边界待整理。
- 证据：SSH 基线与隔离评测有独立文件、进程和锁证据；页面/SSE 链路、未见来源质量与完整发布结论仍待验证。

完整运行从 [普通 Compose / Windows WSL](../development.md#推荐完整运行普通-compose) 开始；默认开关、模型和提示词只查 [运行配置](../reference/runtime-config.md)。本页分别登记工作区源码与本批 SSH 基线核验，`verified_at` 不代表部署日期。

<a id="能力运行版本与验收状态"></a>

## 能力与验收状态

“已实现”指源码具备能力，不替代功能或质量验收。下表保留主要开放问题；实现细节和验证入口见 [模块导航](../reference/module-map.md)、[业务不变量](../reference/invariants.md)。

| 已实现能力 | 剩余边界与下一步 |
| --- | --- |
| 入库：快照、抽取校验/缓存、单文档发布、索引重建 | 全量召回、独立问题拆分和轮次仍有缺口。对齐 API 入队固定 v2 与 worker 实际配置，明确批次/文档 run 父子关系，评估逐文档全量索引及长事务。 |
| 归并：候选检索、Judge、canonical 归并 | 误并/漏并仍在；明确 `needs_review` 未被 materialize 消费时的复核状态契约，以及事务内模型调用边界。今天的生产 resolver 待核验。 |
| 分类：独立标注、审核草稿及发布 | 先裁清新旧政策冲突与未裁来源，验证新旧参考集，再扩展未见来源；查询默认要求 `VERIFIED`。 |
| SQL：有效范围 LIST / COUNT / STATS、签名分页 | `ALGORITHM_PROBLEM` / `ENGINEERING_DESIGN` 使用独立来源审核和版本；工程完整列表不再受 SEARCH50 限制。守护 occurrence 同范围筛选、标准题与提问次数区分、审核覆盖及签名分页；不完整证据不能称全集，SQL 正确不证明上游分类/归并或审核语义正确。 |
| 检索：BM25 / Dense / Hybrid、相关性核验 | 验证真实来源的召回与误报，调查核验损失、完成事件延迟。SEARCH频次只覆盖候选；普通SEARCH仍最多50候选；工作区对原明确工程全集再细化业务的请求已实现完整工作集逐批核验和分页，语义质量及线上发布验收尚未通过。 |
| 查询 Agent：大集合数量预览、分类/全量选择、工具、回答、重查、预算权限、持久化事件/SSE/轮询/取消 | runs / live_tasks 在 API 进程内，Pi 回调依赖其所有权；多实例接管待协议与验证。工具、pipeline 与证据声明已收敛；下一步核对兼容检索入口，逐项拆执行生命周期。 |
| 用户功能：账号、个人模型、偏好、纠正记忆、复习、使用记录 | 保持授权、隔离和幂等，核对实际账号/模型配置。共享题库、向量与导入模型不随个人模型切换；纠正记忆按规范化查询匹配，使用次数不证明检索质量。 |
| 评测：统一 suite 入口、预检、恢复、版本封存、来源结果评分与门禁 | [手册](../../eval/README.md) 含固定五问参考及历史复现；最新候选四问实测仅Agent成功，主重排错误、共享门禁等待与证据漏题仍阻断。算法更正的隔离事实发布/运行绑定仍缺，不能只换代码宣称真实全集已修；完整 V1 阻断、历史数据与门槛保留。 |

<a id="工程收口与后续边界"></a>

## 按问题查看证据

只需了解现状可读到这里；以下供追溯具体结论。历史报告的测试数不是本次重新执行结果。

- **工程收口**：[10-15 验收](../verification/2026-10-15-project-consolidation.md) 记录主题提交、SQL / Python 排名共用政策，以及错误映射/结果呈现的首轮拆分与验证；分布式接管、入库索引策略、语义门禁仍开放。
- **来源质量**：[10-15 来源评测](../verification/2026-10-15-source-optimization.md) 绑定历史 r293 / i293 / a30，非最新生产库。抽取 v6 定向修复通过但仍有缺口；归并 v3 在同一 206 对上 SAME precision 为 91.30%，低于 95% 门槛，来源上下文仅由实验入口显式使用；分类 v9 新集改善但旧回归仍阻断。三个候选均未部署，默认保持；抽取 v6 亦未启用。先完成未裁边界和新旧回归，再扩展未见来源。
- **部署证据**：本批 SSH 基线与隔离先导见[执行检查点](../plans/2026-10-15-evaluation-readiness.md#第二批执行授权与检查点)。[10-09 本地服务](../verification/2026-10-09-local-deployment.md)、[账号与模型](../verification/2026-10-09-qwen-tenants.md)、[使用与复习](../verification/2026-10-09-question-usage.md) 保留各自历史范围。
- **SQL 与查询专项**：[10-14 算法全集](../verification/2026-10-14-byte-algorithm-all.md)、[频次与超时修复](../verification/2026-10-14-query-timeout-frequency.md)、[管理观测](../verification/2026-10-14-agent-observability.md) 仅证明当次范围，不等于完整 V1 或今天生产版本。
- **未闭环事项**：[MCP 接入](../plans/2026-10-08-mcp-integration-research.md) 未实施；[10-12 业务设计修复](../plans/2026-10-12-business-design-spec.md) 的最终验收文件缺失，工作区代码不能补足发布/验收证据。

候选与历史复现从 [评测入口分类](../../eval/README.md#入口分类通用评测候选实验与历史复现) 查找。文件版本更高、已打包或有测试均不表示已启用；旧报告中的“尚无结果”属于当时状态，归并 v3 结果以上述 10-15 报告为准。历史问题见 [复盘索引](../retrospectives/README.md)；返回 [知识库首页](../README.md)。
