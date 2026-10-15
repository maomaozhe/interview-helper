---
id: pitfalls-source-map
status: current
scope: historical-evidence-triage
verified_at: 2026-10-15
runtime_verified: false
sources:
  - path: md/issue.md
  - path: docs/bad-cases.md
  - path: docs/retrospectives/2026-10-04-query-agent.md
---

# 原始记录到根因专题的映射

[返回踩坑索引](README.md) · [Agent 工作方式](../reference/agent-workflow.md)

覆盖原日志 51 个早期编号问题、Q01–Q10、H01–H03、后续日期记录和 33 份 Markdown 验收。归并只改变知识导航，不重编号或把旧失败改写成成功。
同事件可以关联多个机制；相同报错不证明同根因。只有现象的瞬时失败保留未知，检查器错误与业务失败分开。具体状态及范围见各专题。

## 编号问题逐条映射

表中“关联”不表示所有子事件同根因；专题保留各自的已证实机制。空缺的根因保持未知或操作附记。

| 原始定位 | 归并/关联专题 | 处理说明 |
| --- | --- | --- |
| [#1. 前端地址与测试数据混淆](../../md/issue.md#1-前端地址与测试数据混淆) | [PIT-015](2026-10-04-real-runtime-compatibility.md) | 样本页面与真实链路不等价；不是数据丢失 |
| [#2. 服务器部署不具备条件、本机服务中断](../../md/issue.md#2-服务器部署不具备条件本机服务中断) | [PIT-012](2026-10-09-process-lifecycle.md) | 远端条件不足、本地发行版生命周期分别取证 |
| [#3. 模型超时及输出截断](../../md/issue.md#3-模型超时及输出截断) | [PIT-004](2026-10-15-extraction-output-contract.md) | 生成超时/截断，保留失败而非发布半成品 |
| [#4. 串行锁权限与跨系统调用](../../md/issue.md#4-串行锁权限与跨系统调用) | [PIT-013](2026-10-04-shared-lock-filesystem.md) | 锁权限与跨系统互斥边界 |
| [#5. 去重候选 ID 遗漏或改写](../../md/issue.md#5-去重候选-id-遗漏或改写) | [PIT-004](2026-10-15-extraction-output-contract.md) | 身份绑定子机制：短ID、枚举、精确集合 |
| [#6. 分类不在固定 taxonomy 中](../../md/issue.md#6-分类不在固定-taxonomy-中) | [PIT-004](2026-10-15-extraction-output-contract.md)、[PIT-006](2026-10-15-source-eligibility-policy.md) | 闭集合法性与标签语义正确性分别处理 |
| [#7. 下游失败导致重复抽取](../../md/issue.md#7-下游失败导致重复抽取) | [PIT-005](2026-10-15-ingestion-cache-recovery.md) | 有效抽取缓存需在文档发布事务前独立提交 |
| [#8. 导入慢、页面长时间看起来没有变化](../../md/issue.md#8-导入慢页面长时间看起来没有变化) | [PIT-005](2026-10-15-ingestion-cache-recovery.md)、[PIT-011](2026-10-14-query-context-observability.md) | 任务进度与真实阶段计时分别说明，不把等待当卡死 |
| [#9. 文件清单把失败/运行中误显示为未导入](../../md/issue.md#9-文件清单把失败运行中误显示为未导入) | [PIT-005](2026-10-15-ingestion-cache-recovery.md) | 文件当前状态、运行状态、历史失败分开 |
| [#10. 问题记录会污染面经总数](../../md/issue.md#10-问题记录会污染面经总数) | [PIT-006](2026-10-15-source-eligibility-policy.md) | 操作日志属于非语料来源，扫描/显式导入均排除 |
| [#11. 每次失败仅记录异常类型，诊断信息不足](../../md/issue.md#11-每次失败仅记录异常类型诊断信息不足) | [PIT-004](2026-10-15-extraction-output-contract.md) | 保留具体契约错误；后续先记录规则见Agent工作方式 |
| [#12. RAG / Agentic RAG 尚不能凭页面和健康检查宣称可用](../../md/issue.md#12-rag--agentic-rag-尚不能凭页面和健康检查宣称可用) | [PIT-015](2026-10-04-real-runtime-compatibility.md)、[PIT-017](2026-10-06-evaluation-reference-drift.md) | 健康、工程与语义验收各自限定范围 |
| [#13. 跨多次面试的经验汇总被算成具体场次](../../md/issue.md#13-跨多次面试的经验汇总被算成具体场次) | [PIT-006](2026-10-15-source-eligibility-policy.md) | 汇编与真实面试场次资格不同 |
| [#14. 混合检索会填满不相关结果，原路由不能理解隐式问法](../../md/issue.md#14-混合检索会填满不相关结果原路由不能理解隐式问法) | [PIT-002](2026-10-08-query-scope-narrowing.md)、[PIT-010](2026-10-08-query-intent-state.md) | 召回相关性与意图路由两类子原因分别定位 |
| [#15. 服务恢复后仍残留连接错误提示](../../md/issue.md#15-服务恢复后仍残留连接错误提示) | [PIT-018](2026-10-01-ui-state-projection.md) | 成功状态应清除旧错误并统一健康投影 |
| [#20. 串行模型等待会使 Elasticsearch 检索快照过期](../../md/issue.md#20-串行模型等待会使-elasticsearch-检索快照过期) | [PIT-019](2026-10-01-search-snapshot-lifetime.md) | ES快照生命周期跨越模型排队 |
| [#21. Windows/WSL 共享目录偶发拒绝原子重命名](../../md/issue.md#21-windowswsl-共享目录偶发拒绝原子重命名) | [PIT-013](2026-10-04-shared-lock-filesystem.md)、[PIT-005](2026-10-15-ingestion-cache-recovery.md) | 文件系统重命名主归013，缓存落盘影响关联005 |
| [#22. 重排把可推测的关联当成查询的真实问法](../../md/issue.md#22-重排把可推测的关联当成查询的真实问法) | [PIT-002](2026-10-08-query-scope-narrowing.md) | 可推测关联不等于用户目标中的相关性证据 |
| [#23. 不带“问法”措辞的技术提问被误当作全库统计](../../md/issue.md#23-不带问法措辞的技术提问被误当作全库统计) | [PIT-010](2026-10-08-query-intent-state.md) | 技术问句意图不能靠是否含“问法”一词路由 |
| [#24. 小样本对照报告写入出现瞬时拒绝](../../md/issue.md#24-小样本对照报告写入出现瞬时拒绝) | [PIT-013](2026-10-04-shared-lock-filesystem.md) | 瞬时文件占用/写入恢复，不是模型质量失败 |
| [#25. 两条早期排除记录缺少具体原因](../../md/issue.md#25-两条早期排除记录缺少具体原因) | [PIT-006](2026-10-15-source-eligibility-policy.md) | 排除决定必须留下具体依据 |
| [#26. 重排非流式响应超时，Agent 错误未结构化](../../md/issue.md#26-重排非流式响应超时agent-错误未结构化) | [PIT-004](2026-10-15-extraction-output-contract.md) | 关联输出生命周期与错误结构化；非流式rerank超时与抽取具体根因不混同 |
| [#16. 模型把原文中的错别字“修正”进了 raw_quote](../../md/issue.md#16-模型把原文中的错别字修正进了-raw_quote) | [PIT-004](2026-10-15-extraction-output-contract.md) | 原始引用必须逐字，不能自动纠错 |
| [#17. 新查询规划提示词遗漏在安装包之外](../../md/issue.md#17-新查询规划提示词遗漏在安装包之外) | [PIT-014](2026-10-04-runtime-artifact-drift.md) | 源码新prompt未进入运行资源 |
| [#18. “反问”环节标题被抽取为面试官问题](../../md/issue.md#18-反问环节标题被抽取为面试官问题) | [PIT-006](2026-10-15-source-eligibility-policy.md) | 标题/反问阶段不自动成为面试官真实提问 |
| [#19. 本地审计脚本加载了旧的已安装代码](../../md/issue.md#19-本地审计脚本加载了旧的已安装代码) | [PIT-014](2026-10-04-runtime-artifact-drift.md) | 诊断脚本实际导入旧安装包 |
| [#27. 导入页侧栏与主体显示不同的索引状态](../../md/issue.md#27-导入页侧栏与主体显示不同的索引状态) | [PIT-018](2026-10-01-ui-state-projection.md) | 异步入口健康投影与旧响应覆盖 |
| [#28. 导入终点审计异常缺少终止状态](../../md/issue.md#28-导入终点审计异常缺少终止状态) | [PIT-005](2026-10-15-ingestion-cache-recovery.md) | 审计失败需终止状态；原记录为故障注入，非全量已失败 |
| [#29. 重启控制程序时路径匹配漏掉旧进程](../../md/issue.md#29-重启控制程序时路径匹配漏掉旧进程) | [PIT-012](2026-10-09-process-lifecycle.md) | 相对/绝对路径不足以判进程身份 |
| [#30. 诊断命令对实际环境和模型字段的假设不符](../../md/issue.md#30-诊断命令对实际环境和模型字段的假设不符) | [工作记录规则](../reference/agent-workflow.md) / 原记录保留 | 诊断字段/数据库/路径猜测：纳入Agent工作方式，未改成模型故障 |
| [#31. 混合项目帖中的真实提问因元数据缺失被排除](../../md/issue.md#31-混合项目帖中的真实提问因元数据缺失被排除) | [PIT-006](2026-10-15-source-eligibility-policy.md) | 混合来源真实提问资格与元信息缺失分开 |
| [#32. 状态查询出现短暂连接中断](../../md/issue.md#32-状态查询出现短暂连接中断) | [工作记录规则](../reference/agent-workflow.md) / 原记录保留 | 未归因：瞬时状态连接中断；业务继续，根因未复现 |
| [#33. AI 领域被误当成提问类型](../../md/issue.md#33-ai-领域被误当成提问类型) | [PIT-006](2026-10-15-source-eligibility-policy.md) | 技术领域与任务类型独立，Schema合法不保证语义正确 |
| [#34. 去重忽略完整问题范围和对象](../../md/issue.md#34-去重忽略完整问题范围和对象) | [PIT-003](2026-10-15-dedup-overmerge.md) | 完整任务范围/对象不等价，合并到既有去重专题 |
| [#35. localhost 状态连接异常缓慢](../../md/issue.md#35-localhost-状态连接异常缓慢) | [工作记录规则](../reference/agent-workflow.md) / 原记录保留 | 未归因：localhost与127.0.0.1延迟差有实测，IPv6/转发原因未证实 |
| [#36. 开发检查器漏掉原始题目中的空格](../../md/issue.md#36-开发检查器漏掉原始题目中的空格) | [PIT-017](2026-10-06-evaluation-reference-drift.md)、[PIT-007](2026-10-01-canonical-display-drift.md) | 主归检查锚点错误；007只保留维护中的误诊支线 |
| [#37. 后台导入推进导致旧搜索截图的详情版本变化](../../md/issue.md#37-后台导入推进导致旧搜索截图的详情版本变化) | [PIT-017](2026-10-06-evaluation-reference-drift.md) | 查询与详情跨发布版本，不能硬断言同快照 |
| [#38. 来源分类修复后标准题仍展示旧标签](../../md/issue.md#38-来源分类修复后标准题仍展示旧标签) | [PIT-007](2026-10-01-canonical-display-drift.md) | 来源事实已修而canonical展示投影未刷新 |
| [#39. 修复任务过多时原目录导入进度被挤出页面](../../md/issue.md#39-修复任务过多时原目录导入进度被挤出页面) | [PIT-005](2026-10-15-ingestion-cache-recovery.md) | 维护批次遮挡主导入进度，不是事实丢失 |
| [#40. 同一句原文重复出现时抽取未指定出现位置](../../md/issue.md#40-同一句原文重复出现时抽取未指定出现位置) | [PIT-004](2026-10-15-extraction-output-contract.md) | 重复原句必须绑定准确出现位置 |
| [#41. Schema 合法仍可能违背实际任务类型](../../md/issue.md#41-schema-合法仍可能违背实际任务类型) | [PIT-006](2026-10-15-source-eligibility-policy.md) | 结构合法与任务政策正确性分开 |
| [#42. 搜索返回新数据而页脚仍停在页面初始版本](../../md/issue.md#42-搜索返回新数据而页脚仍停在页面初始版本) | [PIT-018](2026-10-01-ui-state-projection.md) | 结果推进后工作区/健康需有条件刷新 |
| [#43. 成功重试和排队状态可能掩盖失败历史](../../md/issue.md#43-成功重试和排队状态可能掩盖失败历史) | [PIT-005](2026-10-15-ingestion-cache-recovery.md) | 成功重试不覆盖单次失败历史 |
| [#44. 85 篇来源的旧 UnsupportedModel 错误历史（2026-10-02 汇总）](../../md/issue.md#44-85-篇来源的旧-unsupportedmodel-错误历史2026-10-02-汇总) | [PIT-014](2026-10-04-runtime-artifact-drift.md) | 85篇旧UnsupportedModel归运行入口/模型配置，不按来源新建85篇复盘 |
| [#45. 续跑核对与诊断脚本的两个读取错误（2026-10-02）](../../md/issue.md#45-续跑核对与诊断脚本的两个读取错误2026-10-02) | [工作记录规则](../reference/agent-workflow.md) / 原记录保留 | 操作附记：错模块/ORM字段、CRLF匹配；见Agent工作方式，截断证据另接004 |
| [#46. 小篇幅来源连续截断及诊断缓存恢复（2026-10-02）](../../md/issue.md#46-小篇幅来源连续截断及诊断缓存恢复2026-10-02) | [PIT-004](2026-10-15-extraction-output-contract.md)、[PIT-005](2026-10-15-ingestion-cache-recovery.md)、[PIT-014](2026-10-04-runtime-artifact-drift.md) | 同事件三条边界：截断诊断、缓存复用、运行指纹不同 |
| [#47. 后排队批次遮住了实际运行的文件状态（2026-10-02）](../../md/issue.md#47-后排队批次遮住了实际运行的文件状态2026-10-02) | [PIT-005](2026-10-15-ingestion-cache-recovery.md) | 当前运行任务优先于后排队展示 |
| [#48. API 的外层 shell 没有转发停止信号（2026-10-02）](../../md/issue.md#48-api-的外层-shell-没有转发停止信号2026-10-02) | [PIT-012](2026-10-09-process-lifecycle.md) | PID1外层shell未传停止信号 |
| [#49. 明确增加“无法冻结”的追问被误归为同题（2026-10-02）](../../md/issue.md#49-明确增加无法冻结的追问被误归为同题2026-10-02) | [PIT-003](2026-10-15-dedup-overmerge.md) | 新增任务约束不应误判同义，归并既有PIT003 |
| [#50. 已生效/已排除筛选误用排队状态，导致已有数据消失（2026-10-02）](../../md/issue.md#50-已生效已排除筛选误用排队状态导致已有数据消失2026-10-02) | [PIT-005](2026-10-15-ingestion-cache-recovery.md) | 生效/排除事实不能由排队状态替代 |
| [#51. 新来源抽取发生读取超时，有限重试中（2026-10-02 03:20）](../../md/issue.md#51-新来源抽取发生读取超时有限重试中2026-10-02-0320) | [PIT-004](2026-10-15-extraction-output-contract.md) | 读取超时及有界重试，原当时状态不改成最终成功 |

## Q/H系列映射

| 原始定位 | 专题 | 处理说明 |
| --- | --- | --- |
| [Q01. 类别枚举被当成检索 Top K，数量与排名语义不正确](../../md/issue.md#q01-类别枚举被当成检索-top-k数量与排名语义不正确) | [PIT-008](2026-10-14-complete-query-scope.md) | 类别枚举/全局TopN不走有限候选近似 |
| [Q02. “手撕代码”与“算法题”混淆，旧标签不能直接用于新需求](../../md/issue.md#q02-手撕代码与算法题混淆旧标签不能直接用于新需求) | [PIT-008](2026-10-14-complete-query-scope.md)、[PIT-006](2026-10-15-source-eligibility-policy.md) | 旧题型、作答形式、任务焦点不可互代 |
| [Q03. 回填让模型复写 UUID 与原文，增加输出成本并触发严格校验失败](../../md/issue.md#q03-回填让模型复写-uuid-与原文增加输出成本并触发严格校验失败) | [PIT-004](2026-10-15-extraction-output-contract.md) | 宿主承担ID与原文身份映射，模型只输出必要判断 |
| [Q04. 模型只输出工具名，默认参数掩盖了会话范围丢失](../../md/issue.md#q04-模型只输出工具名默认参数掩盖了会话范围丢失) | [PIT-010](2026-10-08-query-intent-state.md) | 缺字段不可被客户端默认值伪装为完整计划 |
| [Q05. 页面分页、Agent 指代与会话版本需要保持同一个范围](../../md/issue.md#q05-页面分页agent-指代与会话版本需要保持同一个范围) | [PIT-010](2026-10-08-query-intent-state.md) | 当前页、分页、指代与会话版本绑定 |
| [Q06. Laya 选项预算导致候选丢失，接口成功加载不等于能正确决策](../../md/issue.md#q06-laya-选项预算导致候选丢失接口成功加载不等于能正确决策) | [PIT-016](2026-10-06-fast-decision-admission.md) | 选项完整性与模型业务准入分别测试 |
| [Q07. 耗时归因遗漏工具内模型调用，HTTP 回调不会自动继承 ContextVar](../../md/issue.md#q07-耗时归因遗漏工具内模型调用http-回调不会自动继承-contextvar) | [PIT-011](2026-10-14-query-context-observability.md) | HTTP边界不继承进程内ContextVar |
| [Q08. Windows 模型锁首字节初始化也需要放在临界区](../../md/issue.md#q08-windows-模型锁首字节初始化也需要放在临界区) | [PIT-013](2026-10-04-shared-lock-filesystem.md) | 锁元数据初始化也必须在临界区 |
| [Q09. Pi 源码快照缺生成资源，安装通过仍不保证可离线构建](../../md/issue.md#q09-pi-源码快照缺生成资源安装通过仍不保证可离线构建) | [PIT-014](2026-10-04-runtime-artifact-drift.md) | 源码快照缺生成资源，安装成功不等于可离线构建 |
| [Q10. 真实 PostgreSQL 和 WSL 部署暴露了样本环境未覆盖的差异](../../md/issue.md#q10-真实-postgresql-和-wsl-部署暴露了样本环境未覆盖的差异) | [PIT-015](2026-10-04-real-runtime-compatibility.md)、[PIT-014](2026-10-04-runtime-artifact-drift.md) | 真实DB契约与构建/安装配置分属不同根因 |
| [H01. Harness 查询缺少概念扩展，并返回无关题目](../../md/issue.md#h01-harness-查询缺少概念扩展并返回无关题目) | [PIT-002](2026-10-08-query-scope-narrowing.md) | 检索相关性/覆盖仍未全面解决，后续定向改善不闭全库问题 |
| [H02. 数据已保存，但完整历史浏览与历史反馈入口缺失](../../md/issue.md#h02-数据已保存但完整历史浏览与历史反馈入口缺失) | [PIT-009](2026-10-08-history-feedback-receipts.md) | 历史数据持久化与可发现入口不同 |
| [H03. 反馈没有稳定关联原问答，证据不完整](../../md/issue.md#h03-反馈没有稳定关联原问答证据不完整) | [PIT-009](2026-10-08-history-feedback-receipts.md) | 反馈需绑定完整持久回执，原反馈保留 |

## 日期记录与重复失败的处理

| 记录组 | 归并方式 | 保留的边界 |
| --- | --- | --- |
| 自动单次模型失败、文件终态错误历史（85个标题） | 按输出契约/任务恢复/环境与模型配置分别关联 [PIT-004](2026-10-15-extraction-output-contract.md)、[PIT-005](2026-10-15-ingestion-cache-recovery.md)、[PIT-012](2026-10-09-process-lifecycle.md)、[PIT-014](2026-10-04-runtime-artifact-drift.md) | 单次尝试、文件终态、旧85篇来源汇总可能重复描述，不能相加为独立事故数；未知错误不强归因 |
| 10-02～10-03 连续截断、无场次/无问题误拒、重复local ID、未知追问 | [PIT-004](2026-10-15-extraction-output-contract.md)、[PIT-006](2026-10-15-source-eligibility-policy.md) | 非流式/上游截断与资格政策误拒分别处理，未解决来源仍开放 |
| 10-02 21:35 retry-failed空run；多次WORKER_RESTART、休眠与保活空窗 | [PIT-005](2026-10-15-ingestion-cache-recovery.md)、[PIT-012](2026-10-09-process-lifecycle.md) | 重试路径缺口与进程退出分开；页面隐藏不是API闭环 |
| 10-03 用户决定跳过反复失败文档、10-04订阅阻断与恢复 | [PIT-004](2026-10-15-extraction-output-contract.md)、[PIT-006](2026-10-15-source-eligibility-policy.md)、[PIT-014](2026-10-04-runtime-artifact-drift.md) | 有限止损不等于文档问题已修；订阅/权限恢复不等于模型质量改善 |
| 10-05 spec补齐、历史问答留证；10-06首测/回归/重评分 | [PIT-008](2026-10-14-complete-query-scope.md)、[PIT-009](2026-10-08-history-feedback-receipts.md)、[PIT-010](2026-10-08-query-intent-state.md)、[PIT-016](2026-10-06-fast-decision-admission.md)、[PIT-017](2026-10-06-evaluation-reference-drift.md) | 各轮执行/业务断言/参考修订分开，首测暴露后不再称未见 |
| 10-08多轮范围、相关性、澄清、来源绑定和重检 | [PIT-002](2026-10-08-query-scope-narrowing.md)、[PIT-009](2026-10-08-history-feedback-receipts.md)、[PIT-010](2026-10-08-query-intent-state.md)、[PIT-018](2026-10-01-ui-state-projection.md) | UI四选项的历史缓存原因缺证据，保留未知，不等同检索已修 |
| 10-09本地构建、保活、COUNT回归、提示词超预算 | [PIT-010](2026-10-08-query-intent-state.md)、[PIT-012](2026-10-09-process-lifecycle.md)、[PIT-014](2026-10-04-runtime-artifact-drift.md) | 提示词6轮成功但超预算的中间版本仍是失败，不通过抬门槛放行 |
| 10-10～10-14公司别名/算法语义、全量范围、频次/超时 | [PIT-001](2026-10-14-query-timeout-state.md)、[PIT-002](2026-10-08-query-scope-narrowing.md)、[PIT-008](2026-10-14-complete-query-scope.md)、[PIT-010](2026-10-08-query-intent-state.md) | 元数据全量、语义审计全集、Top50候选排序分别说明 |
| 单纯状态快照、进度数字、既有流程恢复补录 | 链回上述对应专题及原日期记录 | 不另建事故，不把历史“最新”改成今天状态 |

## 历史验收的复用入口

同名 JSON 作为原报告证据保留，不另造事故。主题关联只覆盖表中注明的机制，其他局部修补仍在原报告。

| 原验收 | 关联专题 | 复用范围 |
| --- | --- | --- |
| [2026-10-01-import-rag-smoke.md](../verification/2026-10-01-import-rag-smoke.md) | [PIT-004](2026-10-15-extraction-output-contract.md)、[PIT-006](2026-10-15-source-eligibility-policy.md)、[PIT-007](2026-10-01-canonical-display-drift.md)、[PIT-018](2026-10-01-ui-state-projection.md) | 真实数据/页面与抽取、展示修复；原样本范围保留 |
| [2026-10-02-code-push-check.md](../verification/2026-10-02-code-push-check.md) | [PIT-014](2026-10-04-runtime-artifact-drift.md)、[PIT-015](2026-10-04-real-runtime-compatibility.md) | 代码交付与实际运行分别核对；不替代完整质量 |
| [2026-10-06-quality-optimization.md](../verification/2026-10-06-quality-optimization.md) | [PIT-002](2026-10-08-query-scope-narrowing.md)、[PIT-003](2026-10-15-dedup-overmerge.md)、[PIT-004](2026-10-15-extraction-output-contract.md)、[PIT-006](2026-10-15-source-eligibility-policy.md)、[PIT-016](2026-10-06-fast-decision-admission.md)、[PIT-017](2026-10-06-evaluation-reference-drift.md) | 已见/未见、判断池、局部执行与语义失败分开 |
| [2026-10-06-quality-refinement.md](../verification/2026-10-06-quality-refinement.md) | [PIT-004](2026-10-15-extraction-output-contract.md)、[PIT-006](2026-10-15-source-eligibility-policy.md)、[PIT-010](2026-10-08-query-intent-state.md)、[PIT-017](2026-10-06-evaluation-reference-drift.md) | 原分数、参考纠正与新挑战分别留证 |
| [2026-10-06-resume-quality.md](../verification/2026-10-06-resume-quality.md) | [PIT-003](2026-10-15-dedup-overmerge.md)、[PIT-011](2026-10-14-query-context-observability.md)、[PIT-016](2026-10-06-fast-decision-admission.md)、[PIT-017](2026-10-06-evaluation-reference-drift.md) | 计时/候选性能/小模型准入/质量门禁口径 |
| [2026-10-08-agent-intents.md](../verification/2026-10-08-agent-intents.md) | [PIT-010](2026-10-08-query-intent-state.md) | 动作、上下文和回答功能，不自动证明连续范围正确 |
| [2026-10-08-chat-interaction.md](../verification/2026-10-08-chat-interaction.md) | [PIT-001](2026-10-14-query-timeout-state.md)、[PIT-018](2026-10-01-ui-state-projection.md) | 状态/流式展示关联；选项缓存具体成因未知，保留原说明 |
| [2026-10-08-conversation-focus.md](../verification/2026-10-08-conversation-focus.md) | [PIT-010](2026-10-08-query-intent-state.md) | 目标与列表范围、原失败和后续修复分开 |
| [2026-10-08-conversation-retrieval.md](../verification/2026-10-08-conversation-retrieval.md) | [PIT-002](2026-10-08-query-scope-narrowing.md)、[PIT-009](2026-10-08-history-feedback-receipts.md) | 相关性和历史/反馈闭环分别处理 |
| [2026-10-08-engineering-design.md](../verification/2026-10-08-engineering-design.md) | [PIT-002](2026-10-08-query-scope-narrowing.md) | 原8次候选、失败、覆盖与公开验收限定 |
| [2026-10-08-expanded-sample.md](../verification/2026-10-08-expanded-sample.md) | [PIT-002](2026-10-08-query-scope-narrowing.md)、[PIT-017](2026-10-06-evaluation-reference-drift.md) | 扩样结果、空结果和未运行项不混分母 |
| [2026-10-08-library-deployment.md](../verification/2026-10-08-library-deployment.md) | [PIT-014](2026-10-04-runtime-artifact-drift.md)、[PIT-018](2026-10-01-ui-state-projection.md) | 入口差异与误带后端的构建边界；交互不替代语义 |
| [2026-10-08-project-showcase.md](../verification/2026-10-08-project-showcase.md) | [PIT-014](2026-10-04-runtime-artifact-drift.md) | 交付范围/展示验证，未新增独立根因 |
| [2026-10-08-retrieval-evidence.md](../verification/2026-10-08-retrieval-evidence.md) | [PIT-002](2026-10-08-query-scope-narrowing.md)、[PIT-009](2026-10-08-history-feedback-receipts.md)、[PIT-017](2026-10-06-evaluation-reference-drift.md) | 候选/核验/页面分层、原意与扩展、历史重查 |
| [2026-10-08-retrieval-fixes.md](../verification/2026-10-08-retrieval-fixes.md) | [PIT-002](2026-10-08-query-scope-narrowing.md)、[PIT-008](2026-10-14-complete-query-scope.md) | 推断硬筛、异常隔离和旧题损失保留 |
| [2026-10-09-access-panel.md](../verification/2026-10-09-access-panel.md) | [PIT-020](2026-10-09-read-side-effect-metering.md)、[PIT-021](2026-10-09-trusted-proxy-identity.md)、[PIT-015](2026-10-04-real-runtime-compatibility.md) | 自动准入、代理身份、真实PG；管理路径小修保留原报告 |
| [2026-10-09-local-deployment.md](../verification/2026-10-09-local-deployment.md) | [PIT-010](2026-10-08-query-intent-state.md)、[PIT-012](2026-10-09-process-lifecycle.md)、[PIT-014](2026-10-04-runtime-artifact-drift.md) | COUNT回归、提示词预算、保活和构建各自取证 |
| [2026-10-09-markdown-sse.md](../verification/2026-10-09-markdown-sse.md) | [PIT-014](2026-10-04-runtime-artifact-drift.md) | 本地实现未进入正式资源；404与部署身份 |
| [2026-10-09-model-ab.md](../verification/2026-10-09-model-ab.md) | [PIT-014](2026-10-04-runtime-artifact-drift.md)、[PIT-016](2026-10-06-fast-decision-admission.md)、[PIT-017](2026-10-06-evaluation-reference-drift.md) | 模型/服务身份与比较协议；不是所有对照均同模型同条件 |
| [2026-10-09-question-usage.md](../verification/2026-10-09-question-usage.md) | [PIT-015](2026-10-04-real-runtime-compatibility.md)、[PIT-020](2026-10-09-read-side-effect-metering.md) | 序列化、初始化、参数显式性及使用计量 |
| [2026-10-09-qwen-runtime.md](../verification/2026-10-09-qwen-runtime.md) | [PIT-004](2026-10-15-extraction-output-contract.md)、[PIT-014](2026-10-04-runtime-artifact-drift.md) | 实际模型协议与运行身份，语法通过非业务质量 |
| [2026-10-09-qwen-tenants.md](../verification/2026-10-09-qwen-tenants.md) | [PIT-004](2026-10-15-extraction-output-contract.md)、[PIT-014](2026-10-04-runtime-artifact-drift.md)、[PIT-017](2026-10-06-evaluation-reference-drift.md) | 候选身份约束、提供方范围、错误验收输入与失败保留 |
| [2026-10-09-sidebar.md](../verification/2026-10-09-sidebar.md) | [PIT-018](2026-10-01-ui-state-projection.md) | 关联界面状态/发布；布局、焦点和窄屏修补保留原报告，不并为健康投影根因 |
| [2026-10-10-query-metadata.md](../verification/2026-10-10-query-metadata.md) | [PIT-008](2026-10-14-complete-query-scope.md)、[PIT-010](2026-10-08-query-intent-state.md) | 别名、字段语义、历史推断和显式筛选 |
| [2026-10-11-byte-algorithm-ssh.md](../verification/2026-10-11-byte-algorithm-ssh.md) | [PIT-002](2026-10-08-query-scope-narrowing.md)、[PIT-008](2026-10-14-complete-query-scope.md) | 空结果从范围和资格逐层排查 |
| [2026-10-12-byte-algorithm-semantic.md](../verification/2026-10-12-byte-algorithm-semantic.md) | [PIT-002](2026-10-08-query-scope-narrowing.md)、[PIT-008](2026-10-14-complete-query-scope.md) | 算法问题语义与旧题型/关键词区别 |
| [2026-10-14-agent-observability.md](../verification/2026-10-14-agent-observability.md) | [PIT-011](2026-10-14-query-context-observability.md) | 观测与计费口径、实际保存范围 |
| [2026-10-14-byte-algorithm-all.md](../verification/2026-10-14-byte-algorithm-all.md) | [PIT-008](2026-10-14-complete-query-scope.md)、[PIT-010](2026-10-08-query-intent-state.md) | 审计全集与继续会话动作/失败状态 |
| [2026-10-14-dedup-answer-boundary-candidate.md](../verification/2026-10-14-dedup-answer-boundary-candidate.md) | [PIT-003](2026-10-15-dedup-overmerge.md)、[PIT-004](2026-10-15-extraction-output-contract.md) | 回答范围、guard真实代价、输入身份 |
| [2026-10-14-query-timeout-frequency.md](../verification/2026-10-14-query-timeout-frequency.md) | [PIT-001](2026-10-14-query-timeout-state.md)、[PIT-008](2026-10-14-complete-query-scope.md) | 客户端误判与候选频次排序是两个问题 |
| [2026-10-14-source-gold.md](../verification/2026-10-14-source-gold.md) | [PIT-003](2026-10-15-dedup-overmerge.md)、[PIT-004](2026-10-15-extraction-output-contract.md)、[PIT-006](2026-10-15-source-eligibility-policy.md)、[PIT-017](2026-10-06-evaluation-reference-drift.md) | 来源/粒度/缺失，不强映射或改旧结构成绩 |
| [2026-10-15-dedup-source-context-candidate.md](../verification/2026-10-15-dedup-source-context-candidate.md) | [PIT-003](2026-10-15-dedup-overmerge.md)、[PIT-004](2026-10-15-extraction-output-contract.md) | 来源绑定接口与历史尚无结果时点 |
| [2026-10-15-source-optimization.md](../verification/2026-10-15-source-optimization.md) | [PIT-003](2026-10-15-dedup-overmerge.md)、[PIT-004](2026-10-15-extraction-output-contract.md)、[PIT-006](2026-10-15-source-eligibility-policy.md)、[PIT-017](2026-10-06-evaluation-reference-drift.md) | 真实候选改善、旧回归、联合干预和剩余失败 |

## 明确保留的未知与未闭环

- #32瞬时连接中断、#35 localhost延迟的具体网络原因未复现；恢复或地址绕行不等于查明根因。
- PIT-003/006的去重及资格/分类质量仍开放；多篇mitigated案例还缺指定运行、恢复或语义验证，见各自下一步。
- 首个结构错误字段、历史澄清缓存原因等缺失证据不通过后来成功补写；没有实际尝试就不写失败经过。
- 布局、按钮焦点、管理页路径等特定界面细节保留原验收，不为数量另造根因专题。
- 来源/模型/数据已暴露的开发回归不升级为未见泛化；原文件中的状态按原日期解释。

维护时从新增条目链接现有专题，确有新机制再分配 PIT 编号。原日志与验收的事实、数字、失败和结论保持；新证据追加或另建记录，允许修正纯导航链接，不覆写旧结果。
