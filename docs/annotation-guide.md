# Interview Intelligence V1 标注指南

本指南用于建立独立金标。默认采用人工审核；用户明确委托 Agent 标注时使用本文末尾的授权审核规则。每条标注必须引用原始 Markdown 的 SHA-256、文件相对路径、不可变快照中的字符跨度与行号；不得根据模型回答或常识补写原文没有的公司、轮次和日期。

查询任务分类现在区分 `NEEDS_REVIEW`（机器分类待核验）、`VERIFIED`（人工确认）和 `UNKNOWN`。在“分类核验”页面打开不可变原文、核对作答形式和任务焦点、填写判断依据，先保存草稿，再勾选发布。发布批次校验原文指纹与标注 revision，在同一事务更新标签和 revision；任何来源过期则整批不发布。`MIXED` occurrence 同时满足工程和算法筛选，所有筛选必须落在同一 occurrence。`KNOWN` 允许临时机器分类且显示提示，正式默认 `VERIFIED`。人工修正数据仍需另行冻结评测划分，不能直接当成独立测试金标。

## 文档与场次

先标文档类型：真实面经、题目汇总、教程、混合、其他或未知。只有能归属具体面试场次的面试官提问才作为真实 occurrence。多轮帖按明确标题拆场次；题目无法分配到某轮时，轮次记 null。候选人反问、答案里的疑问句和教程例题只作为排除样本。

## 原子问题

每个独立提问标一个 gold_question_id。raw_question 对应逐字原文 span；normalized_question 可以补充省略主语，但不得增添新的事实或约束。同一事件被正文和 OCR 重复记录时是一条问题的多个 span；明确不同场次出现的同题是多条 occurrence。

同一行包含可分别作答的不同任务时拆开，例如“RAG 的完整流程”与“时间衰减如何影响召回”。同一要求附带理由、约束、举例或改述时保留为一个问题，不能靠多拆问题增加召回率。标题、机构推广题目和没有具体面试事件的汇编也需检查提问资格，标题出现“一面”不能单独证明整篇属于同一场面试。

逐题标 Topic L1/L2、Question Type 和 evidence_kind。Topic 只能使用 `config/taxonomy/v1.yaml`；不确定的 topic 选“其他/UNKNOWN”。Type 遵循 spec 第 5 节的优先级。日期另标 interview_date_raw 与 publish_date_raw、证据和精度；缺年份时 date=null。

新增独立任务标签，旧 `question_type` 不替代它们：

- `response_form`：VERBAL 为口头解释 / 设计，CODE 为明确要求代码，SQL 为写 SQL，UNKNOWN 为证据不足。“如果让你实现，你会怎么做”通常是口头方案；“解释后手写代码”仍为 CODE。
- `coding_focus`：ALGORITHM 为算法求解，ENGINEERING 为工程组件 / 语言功能实现，MIXED 需明确同时包含两种任务，NONE 为没有编程任务，UNKNOWN 为不足以裁定。SQL 归 ENGINEERING；算法原理讨论不因算法词汇自动变成求解任务。
- 手写线程池、单例、并发任务处理器、工程 LRU 为 CODE/ENGINEERING；明确 LeetCode 146 或算法题环节的 LRU 为 CODE/ALGORITHM；不能用裸词“手撕”或旧 ALGORITHM 标签替代证据。
- 解释 MCP、Redis 持久化、MVCC、缓存穿透，介绍已有项目实现或诊断 OOM，通常为 VERBAL/NONE。明确要求构造新组件、给出实现步骤或命令参数时可为 VERBAL/ENGINEERING；口述具体算法求解思路可为 VERBAL/ALGORITHM。“工程技术话题”不等于“工程实现任务”。

任务回填保存 occurrence ID、输入 hash、producer 版本、置信度和原文关联。低置信度保留 UNKNOWN，完整列表显示待处理与不确定数量。模型标注没有人工审核时不能称为 human gold。

## 追问与算法

观察追问两端必须是同一场次的问题 ID，并标出“接着问”“追问”等证明关系的原文 span。相邻编号或主题相同不足以标成真实追问。手撕题保留题干与约束；LeetCode 题号只有原文明确出现或人工核对版本化题库后才确认为已识别。

## 去重与检索

题目对使用 SAME、RELATED、DIFFERENT。SAME 需问题意图、主体、关键约束一致；把“Redis 为什么快”与“为何单线程”标为 RELATED。Gold 的语义等价组有独立 ID，不依赖系统生成的 canonical ID。

检索 query 标 relevant gold_question_id 及相关性 0/1/2，并保存 FilterSpec 和固定 as_of；无相关题的查询另列 negative set。路由 query 标 intent、必要工具、禁止工具、参数、允许的替代计划和期望结果。写操作只在用户明确要求记录时标允许。

## 冻结与裁决

开发集与测试集按来源帖分组隔离，转载也归同组。两位标注者意见冲突时保留原意见与裁决理由，再更新标签。任何模型生成的标签都必须人工逐条核验才将 human_verified 设为 true。测试集冻结后只新增新版本，不为提高分数悄悄改标签。运行 `python -m eval.validate_gold --dataset data/gold/v1` 检查最低规模和泄漏。

## V2 文件契约与复核顺序

执行步骤见 [操作手册](../eval/README.md)，严格检查范围见 [搭建规范](plans/2026-10-05-evaluation-system-spec.md)。manifest的任务可以内嵌数组，或填写相对JSONL路径；路径必须留在数据集目录。每条有独立id、group_id、split、tags和human_verified。确认后填写如下真人记录，保留争议原意见和裁决理由：

```json
{"human_verified":true,"review":{"reviewer":"实际审核者标识","reviewed_at":"2026-10-05T16:00:00+08:00","guide_version":"annotation_v2"}}
```

不要复制示例审核者、时间或机器草稿来假装已核验。代码可检查记录完整性，不能证明审核真实发生；冻结仍需团队人工确认。

| 模块 | 标签和证据 |
|---|---|
| extraction | sample_kind positive/negative，source_path/hash；sessions的id及metadata company/position/round/日期，未知值null；questions的独立id、session_id、raw_question、source_spans、topic_l1/l2、question_type；followups两端与evidence_spans |
| task_labels | 原文路径/hash、occurrence_id、raw_question及spans，response_form/coding_focus；阅读上下文后裁定，不以旧question_type推导 |
| dedup | left/right各有独立id/text，可附来源group/hash；label为SAME/RELATED/DIFFERENT；评测集覆盖三类及容易混淆的限制条件 |
| retrieval | query、filters、sample_kind及relevance独立gold ID→0/1/2；四路联合候选池+池外补查，负查询必须确实无相关题 |
| routing | message或完整turns；required_tools、forbidden_tools、tool_order/allowed_plans、expected_plan字段及assertions；写轮明确allow_write并给实际终态期望 |
| sql | request及all_pages；独立期望ID、次序、次数、总数、范围、来源及零模型调用断言 |

Gold的metadata值用归一化结果；原词和证据另存。公司/轮次准确率报告非null分母，含null的总体准确率另报，防止大量未知字段抬高成绩。span字符位置针对 `decode_source` 后文本，UTF-8 BOM和换行按项目解码规则处理。正式分类样本需要原文snapshot及可核验span。

抽取alignment另存，格式如下（示意ID）：

```json
{"id":"文档样本id","prediction_sha256":"对应result对象的规范JSON哈希","human_verified":false,"review":{"reviewer":"","reviewed_at":"","guide_version":"annotation_v2"},"pairs":[{"gold_id":"g-question","prediction_id":"p-question"}],"sessions":[{"gold_id":"g-session","prediction_id":"p-session"}]}
```

一个问题最多匹配一次。补全/拆分造成意图不一致则保持未匹配；多场次不能通过对齐合并。检查引用及追问证据，再由人工确认。合成演示使用明确synthetic_alignment且human_verified=false；该捷径只允许fixture，真实语料不接受。

检索manifest使用id_namespace=gold，canonical_mapping包含items（gold_question_id、canonical_question_id、equivalence_group）、corpus_revision及review。人工查看canonical所有variants后决定对应关系；不同等价组不允许指向同一canonical，同组也不能分裂到多个canonical。发现生产错误归并时先记录测评缺陷，在独立新版本修复与重新绑定，不能为凑Recall改gold。

assertions采用受控路径和比较操作，例如：

```json
{"id":"top40","dimension":"process","path":"result.plan.top_n","op":"eq","value":40}
```

允许eq/ne、set_eq、contains/subset、length/unique、approx/lte/gte、exists/not_empty；支持数组索引与`*`逐项路径，不执行代码。仅检查结果存在不足以证明业务正确，真人须补完整集合、来源和多轮状态断言。开发工作包的routing种子仅是起点。

正式test最低规模为30篇正抽取文档、150对去重、50条正检索、50个独立Query场景、200条任务分类、20个SQL场景，负例另计。先在少量样本上由两人独立标注、统计分歧并裁决统一口径，再扩充和冻结；机器预测保存在独立文件供误差分析，不参与首次盲标。当前没有自动裁判，因此尚无可报告的人机一致性成绩。

## 用户授权的 Agent 审核

用户已明确要求“你自己跑，来打金标，不确定的再给我审核”。本次按此授权独立阅读原文并标注，用 `--review-policy delegated_agent` 校验和评分。默认 `human` 校验仍拒绝 Agent 数据，不自动切换审核策略。

Agent 标注保留 `human_verified=false`、`agent_verified=true`，review 写 `reviewer_kind=agent`、依据、时间和指南版本；manifest 保存用户授权。标注配方在 [evals/agent_gold](../evals/agent_gold/README.md)，候选顺序与原文快照均绑定 hash。封存之后再读取预测做一对一对齐，无法裁定的对应保持未匹配。

首次原文标注与后续裁决要分开记录：本批v1首次标注先于预测比较，v2曾在比较后回看原文裁决拆分和岗位口径，v3加入用户字段答复，v4只补充该过程说明，v5确认LRU手写并清空待审项。即使标签有原文依据，也不能把已经查看并裁决过的整批数据称为未见测试集；后续调参另留新来源验证。

只将有歧义的字段提交用户。题型待定使用 `attributes_to_review`，公司等待定使用 `metadata_to_review`；相应准确率暂不计该字段，逐样本记录 UNKNOWN。漏掉整道题仍计 FN，负文档误抽仍计 FP。不能以“待审核”隐藏提问身份或原文证据问题。

用户对单个字段的答复保存到 `field_reviews`，记录原问题、原答复、时间与 `direct_user_reply` 来源；整条 Agent 标注仍为 `human_verified=false`。裁决生成新版本并保留父版本 hash，旧文件不覆盖。当前用户已裁决 C++ 模板题焦点为 MIXED、boss 标题的公司为 BOSS直聘、裸词LRU为手写题；这些裁决只适用于对应样本，不扩展成所有模板题或 boss 标题的通用规则。该LRU按旧Question Type映射为ALGORITHM；作答方式CODE与任务焦点独立，不能据此推定coding_focus也是ALGORITHM。

Agent 审核分数用于项目诊断，并注明审核主体与范围。两人独立标注、人机一致性和全新模型重跑需要各自的实验，不能从本次已有输出审计推算。
