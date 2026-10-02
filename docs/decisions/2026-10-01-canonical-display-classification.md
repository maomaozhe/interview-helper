# 标准题展示分类跟随当前证据

规范 §4 区分 occurrence 事实分类与 canonical 展示分类。真实维护发现：重处理来源后 occurrence 标签正确，复用的 canonical 仍保留首次导入的旧 AI 标签，导致设计题/故障题在界面继续显示错误类型。

发布事务切换当前构建后，从 INCLUDED、analytics_eligible 的当前 occurrence 统计每个 canonical 的 `(topic_id, question_type, taxonomy_version)` 联合分类。出现最多的实际分类用于展示；票数相同且原展示分类仍在最高票中则保留，否则按稳定顺序选择。联合选择保证不拼接出任何来源都未支持的分类。过期、未发布、排除和非事实行不参与。

这一步不改标准题 ID、题目文本、原问法、来源、归并判断或复习记录；频率和筛选仍使用 occurrence。分类刷新与发布同一事务，失败一起回滚；CorpusRevision 同时保存展示分类与 publication_v2_active_classification 版本。每次发布刷新当前展示，纠正早期来源已有的陈旧标签，不需要为此重复模型调用。

有不同分类的真实出现仍可共享标准题；它们的事实分类分别保留。此规则仅定义展示值，不证明分类或语义去重已经通过人工 gold 评测。终点审计检查展示值有当前最高票证据支持。
