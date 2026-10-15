---
id: PIT-009
status: current
resolution: resolved
resolution_scope: history-discovery-and-authoritative-feedback-binding
scope: historical-report-and-workspace
verified_at: 2026-10-15
runtime_verified: false
tags: [history, feedback, receipt, requery, memory, ownership, pagination]
sources:
  - path: md/issue.md
  - path: docs/verification/2026-10-08-conversation-retrieval.md
  - path: docs/verification/2026-10-08-retrieval-evidence.md
  - path: src/interview_intelligence/agent/history.py
    symbols: [ConversationHistory.conversations, ConversationHistory.turns]
  - path: src/interview_intelligence/web.py
    symbols: [save_feedback]
  - path: src/interview_intelligence/agent/feedback_memory.py
    symbols: [FeedbackMemory.feedback, FeedbackMemory.matching, FeedbackMemory.update, FeedbackMemory.export]
  - path: src/interview_intelligence/agent/query_service.py
    symbols: [QueryService._restore_requery]
  - path: src/interview_intelligence/web/assets/core.js
    symbols: [feedbackOrigin, requeryBody, historyContinuation]
tests:
  - tests/integration/test_conversation_quality.py
  - tests/integration/test_query_requery.py
  - tests/web/test_core.cjs
---

# PIT-009：历史有保存，但用户找不到；反馈有记录，但原问答不完整

[返回索引](README.md) · [原历史/反馈验收](../verification/2026-10-08-conversation-retrieval.md)

## 30 秒回忆

“数据在库里”不等于用户能重新找到；“反馈已提交”也不等于它绑定了完整原问答。
这里有两个独立子事件：H02 缺少浏览和恢复入口；H03 缺少稳定回执关联与完整证据。
修复把可浏览历史、原始回执、用户反馈、新一轮重查分别建模，避免靠页面摘要或时间邻近猜原问题。
`resolved` 只指报告中的入口和绑定闭环，不表示旧匿名反馈已全部补齐，也不表示纠正后的检索质量全面达标。

## 原始问题与触发

- [H02. 数据已保存，但完整历史浏览与历史反馈入口缺失](../../md/issue.md)：PG 已有 conversation/turn，接口只给近期 10 轮，没有会话列表和向前分页；浏览器当前会话标识被清除后旧历史难以发现。
- H02 的恢复渲染还缺反馈按钮和执行过程；当时 issue 的该结论来自静态检查，不是当时已完成的浏览器复测。
- [H03. 反馈没有稳定关联原问答，证据不完整](../../md/issue.md)：部分反馈没有 query、answer、conversation/run；页面只截取前 10 条，原答复却有 15/20 条。
- H01 中曾按相邻时间和文本寻找旧反馈对应问答；这是线索，不是可写回的权威身份，检索原因另见 [PIT-002](2026-10-08-query-scope-narrowing.md)。

## 原因：已证实与仍未知

| 子事件 | 已证实 | 不能扩大解释 |
| --- | --- | --- |
| H02 历史发现 | 缺会话列表、搜索、历史分页和统一恢复渲染 | 不等于数据库丢数据，也不等于所有旧回合可恢复完整执行证据 |
| H03 反馈关联 | 浏览器提交摘要没有完整问答身份；截断候选被当作反馈证据 | 旧反馈文本相近，不能证明它属于某一 run |
| 重查生命周期 | 新请求应生成新回执；同 request_id 重放则是幂等读取 | 重新显示旧结果不等于重新调用检索 |
| 纠正记忆 | 未经明确启用，反馈不该自动成为跨会话偏好 | 用户纠正不是独立语义金标，也不是任意相似问法的通用知识 |

历史未知关联仍保留未知；本次没有读取私人反馈原文，也没有尝试按账号或运行 ID 拼接旧记录。

## 修复过程

1. 增加按用户绑定的会话列表、消息搜索和 keyset 分页；恢复同一会话的已存 turn，并显示当前会话版本。
2. 新消息与历史消息共用答复渲染，提供来源/执行过程、反馈和可重查操作；窄屏也能打开历史。
3. 浏览器保留原始问题与 run/request 身份；服务端从当前用户的 `AgentTurn` 回填权威 receipt，不以客户端前十条当完整答案。
4. 将“保存反馈”“应用本次纠正重查”“启用以后记住”拆开；启用/停用使用偏好版本检查。
5. 历史重查生成新 request_id，绑定 `requery_of_run_id`；恢复原始目标而非把旧模型扩展/旧游标当新用户硬条件。
6. 导出回归样本时同时保留原回执与后续重查，声明用户纠正的证据等级和扫描边界。

## 真正发生过的失败与误区

原 H02/H03 修复报告没有记录“换数据库”“重建索引”等失败尝试，不能补写成经历。
以下失败来自后续历史重查验收，用于说明证据链正确并不保证测试场景正确：

| 实际尝试 | 发生了什么 | 如何处理 |
| --- | --- | --- |
| 首轮分页输入写“返回 2 道” | 模型形成 top_n=2，随后 NEXT 澄清；不能当分页连续性测试 | 明确用 page_size=2，保持完整结果范围 |
| 第二轮 Windows/WSL 验收 | 三次 HTTP 200 后，保存本地 checkpoint 遇 PermissionError，NEXT 尚未完成 | 记为记录流程中断；只重试文件原子发布，不重复 HTTP |
| 原历史无稳定 run 关联 | 文本/时间只能缩小候选，无法恢复权威原问答 | 保留缺失；为新反馈修复身份绑定，不伪造旧证据 |

重查前两轮及后续成功流程保留在[检索证据报告](../verification/2026-10-08-retrieval-evidence.md)；不得把未完成步骤写成已通过。

## 当前源码中的职责分界

- [ConversationHistory](../../src/interview_intelligence/agent/history.py) 的 `conversations` / `turns` 查询用户自己的持久化历史；游标签名绑定用户、查询范围和种类，读历史不改旧 turn。
- [save_feedback](../../src/interview_intelligence/web.py) 在提供 run/request 身份时检查当前用户的 `AgentTurn`，复制 message/status/result 完整回执；原子写入反馈 JSON。
- [feedbackOrigin / requeryBody](../../src/interview_intelligence/web/assets/core.js) 携带原问题与来源身份；[app.js](../../src/interview_intelligence/web/assets/app.js) 的 `requery` 创建新的 request_id。
- [QueryService._restore_requery](../../src/interview_intelligence/agent/query_service.py) 校验来源属于当前用户且满足可重查约束；不会借旧回执重新授权复习写入。
- [FeedbackMemory](../../src/interview_intelligence/agent/feedback_memory.py) 将反馈文件绑定用户；文件名必须是合法 UUID。已启用纠正按规范化后的原问句精确匹配，最多取 3 条。
- `FeedbackMemory.update` 用 expected_version 控制启用/停用；`export` 最多扫描最近 1000 轮，并输出 `human_verified=false`，不能宣称导出是全历史或人工金标。

反馈 API 保留兼容输入，不能把“服务端能绑定带身份的新反馈”写成“所有历史反馈都有身份”。

## 历史验证范围

[10 月 8 日验收](../verification/2026-10-08-conversation-retrieval.md)记载本地浏览器完成会话搜索、分页、恢复反馈/来源/重查，390px 窄屏检查通过。
真实反馈保留原答复全部 20 条；带本次纠正重查得到 6 条。启用记忆后新会话同问句也是 6 条；停用后新会话恢复 20 条。
这些数字只证明指定用例的绑定/启停行为，不能证明 6 条一定是完整或正确的检索全集。
验收结束后测试纠正记忆处于停用状态；原用户反馈保留，不能把测试记忆当默认偏好。
当时报告包括 404 个测试通过、1 个跳过及不同子集验证；子集有重叠，不应相加成更多独立测试。
环境为当时本地 localhost 服务；本次仅静态核对 2026-10-15 工作区，没有重启服务或复测线上。

## 防复发与排查顺序

1. 历史找不到：先分辨“库中无 turn”“列表不可发现”“游标范围不对”“前端只展示当前会话”。
2. 反馈不准：检查 run/request/user 是否确定，再比较服务器 receipt 的完整 result 与页面预览。
3. 重查没变化：核对是否生成新 request_id、是否绑定正确原 run，以及新回执状态；同 ID 重放本就不应重跑模型。
4. 跨会话纠正没生效：查看是否明确启用、偏好版本、原问句规范化键；不要自动扩大到语义相似问题。
5. 导出回归：核对原回执、新重查及扫描上限；缺字段和旧关联不明必须保留为缺口。

已有守护：[完整反馈/用户隔离/历史分页](../../tests/integration/test_conversation_quality.py)、[历史重查授权与范围](../../tests/integration/test_query_requery.py)、[浏览器来源与续接](../../tests/web/test_core.cjs)。本次未执行。
待补的是旧无身份反馈的可确认覆盖率和更多未见浏览器场景；没有证据时不要批量补关联或宣布全历史修复。

## 自测两问

1. 浏览器只展示前 10 条，怎样保存对原 20 条答复的完整反馈证据？
2. 用户点“重新检索”和刷新同 request_id，为什么不该做同一件事？

<details><summary>查看答案</summary>

1. 携带原 run/request 身份，由服务端确认当前用户所有权并复制持久化完整回执；浏览器摘要仅用于展示。
2. 前者是新的授权查询和新回执；后者是幂等重放已有结果，避免刷新触发重复调用或写入。

</details>
