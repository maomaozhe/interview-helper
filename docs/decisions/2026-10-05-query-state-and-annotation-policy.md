# QueryAgent v3：持久运行与分类可信度

采用 `AgentTurn.id` 作为可恢复 run ID；请求内容 hash 与用户 request ID 绑定。每次重新接受在同一 run 上追加 accepted 事件，event cursor 指向本次接受之前，避免新订阅重放旧失败。读检查点可在相同数据版本继续；未知写入只复用已存意图、action ID 与业务回执。

所有排名继续沿用 importance_v1 / gap_v1 的公式，改为 SQL window 聚合和 score / key 游标；复习状态筛选的 BEFORE_TOP_N / AFTER_TOP_N 显式进入签名范围。分组统计仅允许 frequency，不使用题目级复习状态给公司等分组打分。

任务分类默认 `VERIFIED`。机器 `NEEDS_REVIEW` 可以经显式开发策略 `KNOWN` 使用，但结果显示未核验提示；KNOWN 要求作答形式与任务焦点均明确，旧134题口径因此收窄为132道算法题。MIXED 同时允许两类匹配，仍在同一 occurrence 上约束公司 / 场次 / 分类。该变动不改 canonical 身份、原始来源、频率定义或人工评测集。

分类草稿绑定原文 hash / span / revision / 用户，批量发布与 annotation revision 更新原子提交。只由管理 API / 页面明确写长期偏好，模型首期没有自由长期记忆写工具。保持有限状态的优先关系：本次明确要求、界面筛选、会话、明确长期偏好、默认值。

规划最多3次，工具最多8个，每次规划一个工具，写操作只能终结。整轮预算默认65536 tokens，使用前保守预留，未知usage保守收费；重试继续已审计预算。Node不持有模型供应商密钥。实际界面与服务验收见 [报告](../plans/2026-10-05-query-spec-verification.md)，此决策不降低人工金标 / 检索消融 / 生产负载门槛。
