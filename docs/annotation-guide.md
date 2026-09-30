# Interview Intelligence V1 标注指南

本指南用于建立人工确认的评测集。每条标注必须引用原始 Markdown 的 SHA-256、文件相对路径、不可变快照中的字符跨度与行号；不得根据标题、模型回答或常识补写原文没有的公司、轮次和日期。

## 文档与场次

先标文档类型：真实面经、题目汇总、教程、混合、其他或未知。只有能归属具体面试场次的面试官提问才作为真实 occurrence。多轮帖按明确标题拆场次；题目无法分配到某轮时，轮次记 null。候选人反问、答案里的疑问句和教程例题只作为排除样本。

## 原子问题

每个独立提问标一个 gold_question_id。raw_question 对应逐字原文 span；normalized_question 可以补充省略主语，但不得增添新的事实或约束。同一事件被正文和 OCR 重复记录时是一条问题的多个 span；明确不同场次出现的同题是多条 occurrence。

逐题标 Topic L1/L2、Question Type 和 evidence_kind。Topic 只能使用 `config/taxonomy/v1.yaml`；不确定的 topic 选“其他/UNKNOWN”。Type 遵循 spec 第 5 节的优先级。日期另标 interview_date_raw 与 publish_date_raw、证据和精度；缺年份时 date=null。

## 追问与算法

观察追问两端必须是同一场次的问题 ID，并标出“接着问”“追问”等证明关系的原文 span。相邻编号或主题相同不足以标成真实追问。手撕题保留题干与约束；LeetCode 题号只有原文明确出现或人工核对版本化题库后才确认为已识别。

## 去重与检索

题目对使用 SAME、RELATED、DIFFERENT。SAME 需问题意图、主体、关键约束一致；把“Redis 为什么快”与“为何单线程”标为 RELATED。Gold 的语义等价组有独立 ID，不依赖系统生成的 canonical ID。

检索 query 标 relevant gold_question_id 及相关性 0/1/2，并保存 FilterSpec 和固定 as_of；无相关题的查询另列 negative set。路由 query 标 intent、必要工具、禁止工具、参数、允许的替代计划和期望结果。写操作只在用户明确要求记录时标允许。

## 冻结与裁决

开发集与测试集按来源帖分组隔离，转载也归同组。两位标注者意见冲突时保留原意见与裁决理由，再更新标签。任何模型生成的标签都必须人工逐条核验才将 human_verified 设为 true。测试集冻结后只新增新版本，不为提高分数悄悄改标签。运行 `python -m eval.validate_gold --dataset data/gold/v1` 检查最低规模和泄漏。
