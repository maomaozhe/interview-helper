---
id: PIT-020
status: current
resolution: resolved
resolution_scope: initialization-and-replay-metering
scope: historical-report-and-workspace
verified_at: 2026-10-15
runtime_verified: false
tags: [quota, usage, side-effects, initialization, idempotency]
sources:
  - path: docs/verification/2026-10-09-access-panel.md
  - path: docs/verification/2026-10-09-question-usage.md
  - path: src/interview_intelligence/access.py
  - path: src/interview_intelligence/analytics/usage.py
    symbols: [record_usage]
  - path: src/interview_intelligence/web/assets/app.js
tests:
  - tests/integration/test_access.py
  - tests/integration/test_question_usage.py
  - tests/integration/test_question_usage_postgres.py
  - tests/web/test_api_params.cjs
---

# PIT-020：页面初始化和读取路径误扣额度、污染使用记录

[返回踩坑索引](README.md)

## 30 秒回忆

页面刚打开，用户还没问问题就耗掉额度；只看统计页，却产生题库浏览次数。
原因是按技术调用触发记账，没有区分自动初始化、真实交互、幂等回放和评测用途。
当次修复限定了准入与使用记录语义、延迟加载及去重；数据观测仍不能等同真实阅读或掌握。

## 两次发现与共同边界

| 原始验收 | 实际失败 | 定位 |
| --- | --- | --- |
| [访问管理](../verification/2026-10-09-access-panel.md) | 配额设为 1 时，工作台初始化已扣一次，首个真实问题被拒 | 自动 POST 结构化 SQL 列表被误算为对话准入 |
| [使用与复习](../verification/2026-10-09-question-usage.md) | 打开统计页会后台查询题库，产生用户未实际浏览的次数 | 初始化预加载触发浏览记账，不是用户真的看过题 |

共同根因是技术事件与业务事件混用。额度准入和逐题使用是两套独立计数器，各有修法，不能通过一个计数器正确推断另一个也正确。

## 处理过程与边界选择

1. 自然语言查询和连续对话按准入请求计量；SQL 结构化浏览、筛选、分页、历史和 SSE 重连不扣对话额度。
2. 查询幂等重放不重复扣准入；准入后的模型失败仍按既定策略计次，不能把该策略与“失败不计使用事件”混用。
3. 题库首次真正进入时才加载，已有会话只恢复展示；刷新统计页不顺带触发检索。
4. 逐题使用按用户、完成 run、标准题去重；多工具取并集，回执重放不追加，同一新重检是新 run。
5. 候选、核验、返回、SQL 浏览与复习分别记录；失败/取消不计完成使用事件。
6. evaluation 用途保留诊断事件但排除个人使用汇总；不是免除复习/反馈写入影响的通行标记。

使用 GET 探针时还暴露“默认 practice 被当成显式传参”而误报用途冲突；修为按真实查询参数判断显式值，明确冲突仍拒绝。
原 400 失败发生在检索前，没有产生模型调用；不能把它统计成语义检索失败。

## 真实验证范围

访问验收增加真实 HTTP 初始化回归：多次 SQL 浏览用量为 0，首问通过，第二问达到上限，SQL 浏览仍可用。
使用验收覆盖同 run 重放、多工具、失败/取消、evaluation 隔离及 PostgreSQL 并发请求。
报告的当次浏览器和数据库探针分别取证；本次只读报告和源码，未运行这些测试或改个人记录。
新功能启用前的历史日志未回灌个人汇总；零次数只代表指定观测期未记录，不证明题目无法召回或用户没学过。

`resolved` 限于上述初始化和重放计量路径。历史使用数据完整性、用户实际阅读与长期掌握不在这个结论范围内。

## 防复发与再排查顺序

1. 将页面打开、自动预取、真实提交、轮询、重连、重放、新重检分别列成事件表。
2. 对每项明确是否扣额度、是否记题目使用、是否写个人复习，不能只按 GET/POST 判断。
3. 使用隔离账号/数据库，对照请求前后记录及增量；同请求重放必须同时检查两套计量。
4. 检查用途的实际传入来源和默认值，遇到冲突先查参数契约而非先换模型。

已有守护入口：[访问](../../tests/integration/test_access.py)、[使用](../../tests/integration/test_question_usage.py)、[真实 PG 并发](../../tests/integration/test_question_usage_postgres.py)、[查询参数](../../tests/web/test_api_params.cjs)。本次未执行。
新增预取、页面或工具时仍需补对应触发检查；现有测试不自动覆盖新入口。

## 回顾自测

1. 一个 POST 请求不扣对话额度，是否一定不能产生个人题目浏览记录？
2. `usage_purpose=evaluation` 能否保证整个评测没有个人状态写入？

<details><summary>查看答案</summary>

1. 不是同一计量规则；额度和使用分别核对具体路径及业务语义。
2. 不能，它控制使用统计归属，复习、偏好、反馈等写入仍需隔离和各自契约。

</details>
