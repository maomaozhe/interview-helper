---
id: PIT-010
status: current
resolution: resolved
resolution_scope: explicit-model-plan-and-conversation-goal-contract
scope: historical-report-and-workspace
verified_at: 2026-10-15
runtime_verified: false
tags: [query, schema, conversation, intent, pagination, count, evaluation]
sources:
  - path: md/issue.md
  - path: docs/verification/2026-10-06-resume-quality.md
  - path: docs/verification/2026-10-06-quality-optimization.md
  - path: docs/verification/2026-10-06-quality-refinement.md
  - path: docs/verification/2026-10-08-agent-intents.md
  - path: docs/verification/2026-10-08-conversation-focus.md
  - path: docs/verification/2026-10-15-project-consolidation.md
  - path: src/interview_intelligence/agent/query_contract.py
    symbols: [query_model_schema, validate_model_plan]
  - path: src/interview_intelligence/agent/harness.py
    symbols: [ContextCompiler.conversation_focus, ContextCompiler.compile]
  - path: src/interview_intelligence/agent/query_service.py
    symbols: [QueryService._remember]
  - path: services/pi-agent/runtime.mjs
    symbols: [runQuery]
tests:
  - tests/integration/test_query_agent.py
  - tests/integration/test_query_intent.py
  - tests/integration/test_conversation_focus.py
  - tests/unit/test_query_intents.py
  - tests/node/pi-runtime.test.mjs
---

# PIT-010：模型计划缺参数，以及页面状态不能代表会话目标

[返回索引](README.md) · [多轮目标修复验收](../verification/2026-10-08-conversation-focus.md)

## 30 秒回忆

同样是“筛选突然丢了”，可能发生在不同边界：模型没输出 filters，被默认值掩盖；或模型收到了状态，却把当前页当成当前目标。
解决前者需要完整模型契约，解决后者需要独立的会话 goal 与分页状态，不能仅多塞几条聊天记录。
HTTP 200、Pi provider 正确、SSE 完成，都不能证明这一轮业务目标正确。
本页 resolved 指已记录的契约/多轮用例闭环，未宣称全部自然语言意图和 V1 质量已通过。

## 原始事件与独立根因

| 事件 | 原始症状 | 根因边界 |
| --- | --- | --- |
| [Q04. 模型只输出工具名，默认参数掩盖了会话范围丢失](../../md/issue.md) | “手撕代码”后“只看二面”输出不完整 LIST，执行成全库列表 | 模型计划与主机/UI 默认值共用宽松入口 |
| [Q05. 页面分页、Agent 指代与会话版本需要保持同一个范围](../../md/issue.md) | 翻第二页后“第一题”仍可能指上一页；中断后浏览器版本落后 | 页面导航没有统一提交 Agent 状态；取消 HTTP 不等于服务器没提交 |
| [10 月 8 日 conversation-focus](../verification/2026-10-08-conversation-focus.md) | Redis 列表后问总数，先数成 20，再错误清范围数全库 | 缺少“最近已完成用户目标”，last_response/当前页/last_count 互相替代 |

第三项不是 Q04 的简单复发：当时 plan 可以完整，表达的目标仍然错误。
也不是全部范围都应继承；明确换题、只取消某个条件、继续同一目标各有不同语义。

## 已证实的过程

Q04 的真实模型先给仅工具名/不完整参数，服务端默认 filters 为空掩盖了范围遗漏。
修复将模型可见 schema 要求的 action、完整 filters、sort、top_n、page_size 显式化；`null` 表示明确无条件，缺字段不是同义词。
Pi 有界修复计划后才调用主机；不能先执行错误默认结果再解释，也不能无限重试。

Q05 让页面列表请求与自然语言共用持久化会话状态；详情读取保留列表游标，NEXT 使用原列表请求。
结构化页面请求可更新上下文而不调用规划模型；当前用户、过滤条件、语料/标注及个人复习版本参与范围校验。
客户端中止 HTTP 后，服务器可能完成原回合；版本冲突必须保留，只对明确的 QUERY_IN_PROGRESS 做有限等待/重试。

后续 Redis 案例的已部署 v11 并非“旧包没上线”：真实结果仍错，是状态设计与测试预期都存在问题。
旧测试把“那总共有多少题”解释为清空 Redis，本身就是错误基线；原报告已留存并加纠正说明。
只有最近两句聊天、last_count 或旧 last_plan 都不能可靠代表用户正在做的事。

## 实际失败尝试

| 尝试 | 结果 | 后续处理 |
| --- | --- | --- |
| 默认参数接受不完整 LIST | HTTP 成功，范围却扩大 | 模型计划必须完整，执行前验证 |
| v11 用独立 COUNT 替代当前页 STATS | 能数全集，却把 Redis 清掉 | COUNT 能力与范围继承分别验收 |
| v12 首候选只改善 focus | 连续三轮仅 2/3；纠正后仍退回 LIST | 旧状态独立副本 3/3 不能抵消连续对话失败；候选未部署 |
| 最终 COUNT 意图规则修正 | 明确“同一目标的纠正仍是 COUNT”，保留真实范围 | 连续与 legacy 两套场景都验证 |

另见[意图验收](../verification/2026-10-08-agent-intents.md)：曾发生 COUNT JSON 错误后预算耗尽、fallback provider 断言失败；压缩上下文后部分学习计划仍耗尽预算。
这些是模型输出/预算问题，不能归为同一个 focus 根因，也不能把 fallback 的成功答复算作 Pi 通过。
端口冲突、请求携带不支持的字段、随机 UUID 同分排序断言属于验收装配问题，原报告保留，不能算模型语义失败。

2026-10-15 收口补充：离线基线发现 5 个 legacy focus 用例失败，紧凑上下文对已投影的旧状态再次投影，把 `legacy_saved_state` 误标为 `completed_host_tool`。修复保留已知来源的投影幂等性，旧 ANSWER 不补造范围，也不回写原会话；与旧 Agent 修复相关的回归合计 61 项通过。命令与范围见[收口验收](../verification/2026-10-15-project-consolidation.md)；这是离线工程验证，不证明模型语义质量或已部署。

## 当前源码定位

- [query_model_schema / validate_model_plan](../../src/interview_intelligence/agent/query_contract.py) 要求模型完整表达公共字段及动作专属字段；主机/UI 的便捷默认值仍独立保留。
- [Pi runQuery](../../services/pi-agent/runtime.mjs) 的工具契约与 Python 验证共同约束计划；检查模型可见 schema 与主机实际收到的 plan，不能只看 Python 类型定义。
- [ContextCompiler.conversation_focus](../../src/interview_intelligence/agent/harness.py) 为旧状态做可归因投影；没有可靠范围的旧 ANSWER 不能套用陈旧页面 filters。
- [QueryService._remember](../../src/interview_intelligence/agent/query_service.py) 记录完成目标；COUNT/ANSWER 可以改变 goal，但不自动销毁已有页面。
- `list_goal` 保留列表起始目标；NEXT 翻页仍跟随它。新 SEARCH 清除旧列表目标，DETAILS/澄清/复习中间动作不抢占已完成目标。
- 列表分页事实、当前页证据、上一次计数回执及会话 focus 分开保存；原始历史 turn 不为兼容而重写。

这组边界使“继续原列表”“问该范围总数”“只查某题细节”能共享会话，又不互相覆盖。

## 验收数字不能混用

Q04 原 issue 记载修复后真实 ENGINEERING+CODE+SECOND 范围为 4，五组多轮通过；Q05 记载第二页详情/续页及 UI 零规划调用。
这些验证证明指定范围的状态传递，不代表模型所有轮次都正确。
[focus 最终验收](../verification/2026-10-08-conversation-focus.md)记载连续 3/3、legacy 独立副本 3/3、公开 12/12，以及 SSH 隧道浏览器三轮和刷新；初始失败记录仍保留。

10 月 6 日的意图评测也有三个不同证据层次：

- [resume-quality](../verification/2026-10-06-resume-quality.md)：同一开发 50 例从 42/50 到 49/50；不能拿来代表新集表现。
- [quality-optimization](../verification/2026-10-06-quality-optimization.md)：新 50 场景/70 轮为 43/50；遗漏意图、残留筛选和枚举表示问题分别记录。
- [quality-refinement](../verification/2026-10-06-quality-refinement.md)：已见集达到 50/50；预留集先 46/50，纠正两处 gold 枚举后同一预测重评分为 48/50，零新增调用，不能称模型提升。

修正 gold 要保留旧版和理由；不能为了“看起来全过”把期望改成模型当前行为。评测基线漂移详见 [PIT-017](2026-10-06-evaluation-reference-drift.md)。
初次整理只阅读这些报告及工作区代码/测试；没有复跑上述评测。后续收口的离线验证见前述日期补充，仍未验证当前线上模型或 prompt。

## 防复发与排查顺序

1. 先看 raw plan：字段缺失，还是完整但目标错误；前者查 schema，后者查意图和上下文。
2. 对照原用户句、明确 UI 条件、当前 focus 与列表 goal；不要仅看最近工具结果。
3. 核页面实际 ID 顺序、持久化当前页和会话版本；“第一题”绑定页面中的第一项。
4. 在新连续会话和旧状态独立副本分别复现；不要复用已被上一次测试推进的 fixture。
5. 同时检查结果范围、COUNT/LIST 动作、provider/预算、状态；技术成功只是一部分证据。

已有守护：[页面状态/重放](../../tests/integration/test_query_agent.py)、[目标与分页分离](../../tests/integration/test_conversation_focus.py)、[意图与扩展](../../tests/integration/test_query_intent.py)、[Pi 完整 schema](../../tests/node/pi-runtime.test.mjs)。初次整理时未执行；本轮实际执行范围见收口验收。
待补更多未见表达及长会话独立测试；已有开发集、legacy 副本和公开固定用例不能替代未见集。

## 自测两问

1. 模型漏写 filters 和模型显式把全部 filters 写成 null，可以使用同一个默认值处理吗？
2. Redis 列表第二页后问总数，再点 NEXT，COUNT 和 NEXT 应分别用什么状态？

<details><summary>查看答案</summary>

1. 不可以。缺字段是模型契约不完整，必须修复/拒绝；null 是显式无约束，但仍需主机核对当前用户条件与权限。
2. COUNT 围绕当前完成目标/明确范围统计；NEXT 继续原列表的 list_goal 与游标。计数不应把列表状态覆盖成当前页或全库。

</details>
