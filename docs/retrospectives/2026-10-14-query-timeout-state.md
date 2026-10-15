---
id: PIT-001
status: current
resolution: resolved
resolution_scope: client-timeout-state-handling
scope: historical-report-and-workspace
verified_at: 2026-10-15
runtime_verified: false
tags: [query, timeout, sse, client-state, recovery]
sources:
  - path: docs/verification/2026-10-14-query-timeout-frequency.md
    symbols: []
  - path: src/interview_intelligence/web/assets/query-stream.js
    symbols: [watchQueryRun, readCommitted, inspect, checkPersisted, recover]
tests:
  - tests/web/test_query_stream.cjs
---

# PIT-001：查询已成功，网页却报超时

返回 [踩坑回顾索引](README.md) · 原始证据见 [2026-10-14 验收报告](../verification/2026-10-14-query-timeout-frequency.md)。
`resolved` 仅指报告中的客户端超时误判闭环；SSE 完成事件延迟原因仍未知，本页没有验证当前线上。

## 30 秒回忆

服务端已把结果提交为 `SUCCEEDED`，网页仍因固定计时器到点而报超时并取消。
修复核心是：**SSE 负责展示，持久化状态负责判定；超时前先读状态，状态未知不能当作仍在运行。**
把等待时间从 110 秒改到 210 秒只是其中一步，还需要周期核对、截止复核、有界读取与去重收尾。

## 症状与触发

- 历史报告中的新会话第一轮查询，网页显示“本次问答未完成／查询超时，请重试”。
- 服务端约 107.3 秒已保存成功，网页随后仍发出取消请求；其中重排约 90.4 秒。
- 当时网页固定截止为 110 秒，早于服务端 180 秒上限；截止逻辑没有核对已保存结果。
- 同一报告还处理了候选池内频次排序；它是另一问题，不是本页超时状态修复的因果解释。

## 已证实原因与未知部分

| 类别 | 能下的结论 | 证据边界 |
| --- | --- | --- |
| 已证实 | 客户端截止判定会把已完成的服务端运行误报成超时 | 历史持久态、取消时序与报告中的旧逻辑定位 |
| 已证实 | 110 秒客户端窗口不能覆盖服务端允许的 180 秒执行 | 当时配置与报告核对；不能据此解释 SSE 为何迟到 |
| 已排除 | 会话轮数导致本次问题 | 问题发生在新会话第一轮 |
| 未知 | 原完成事件为何未及时到达浏览器 | 没有该次浏览器侧传输证据，不能断言代理、网络或 EventSource 根因 |
| 未验证 | 当前线上仍运行当时修复包且浏览器交互正常 | 本次仅静态核对工作区与历史报告 |

## 修复步骤：用持久态决定收尾

1. 将客户端总等待默认改为 210 秒，与服务端执行上限留出余量。
2. SSE 打开期间按默认 8 秒间隔安排状态检查，发现已提交成功即可结束等待。
3. 截止时停止事件流，读取并排空同一 run 的 journal；处理完分页后再判定状态。
4. `SUCCEEDED` 返回已保存结果；失败返回错误码及 partial；确认仍 `RUNNING` 才发起取消。
5. 单次状态读取最多等待 12 秒；截止读取失败时报告读取错误，不擅自取消未知状态的任务。
6. 周期检查、断线恢复和截止检查共用读取队列；用 sequence 与 ended 防重复事件、重复完成。
7. 周期读取挂起超时后停止继续堆积周期请求，仍保留截止时的最后复核机会。

实现位置：[query-stream.js](../../src/interview_intelligence/web/assets/query-stream.js)。
`readCommitted` 的 `Promise.race` 限制等待时间，不会取消 `readRun` 内已发出的底层请求。
因此“读取队列串行”不能解读为“所有超时后的底层网络请求也已终止”。

## 历史验证范围

原报告记录网页测试 **59/59**，这是当时网页测试范围，不是本页所链接单文件的测试数量。
查询/重查在工作区及冻结生产包覆盖分别为 **24/24**，范围重叠，不能相加成 48 个独立用例。
当时两轮正式 HTTPS/SSE 验收分别 **20/20、21/21**，检查到 completed 与持久化成功。
这些验收同时包含同批频次排序修复；不能把全部通过项归因于客户端状态修改。
报告明确没有真实浏览器点击回归，也没有查明原 SSE 延迟的传输原因。
本页只核对报告、源码和测试断言，未运行测试、模型或线上服务；历史结果不代表当前运行验证。

## 容易误判的地方

- “页面没收到 completed”不等于“服务端没成功”；先查同一个 run 的回执。
- “延长超时即可解决”遗漏静默 SSE、读取挂起和重复收尾；时间余量不能代替状态机。
- “读不到状态就取消”把未知当成 RUNNING，会再次伤害已完成但暂时不可读的运行。
- “某次 HTTPS/SSE 成功”不能证明原传输原因已修复，也不能代替真实浏览器交互验证。
- 上述是防误判提醒；没有证据的重启、换模型或改会话轮数，不记为历史失败尝试。

## 防复发：按这个顺序排查

1. 对齐页面提交、服务端 accepted/finished 和取消时序；先确认是否同一请求，避免重发混淆。
2. 查持久态、错误码、partial 和 last_sequence，再比较客户端已消费 cursor。
3. 若已成功但页面未结束，定位 `inspect → consume → cleanup`，检查分页排空、去重和迟到事件。
4. 若状态读取挂起，检查 `readCommitted` 超时及 `statusPollingStalled`，不要只放大总超时。
5. 若仍运行，再查服务端各阶段耗时和 deadline；SSE 传输问题另采样浏览器链路证据。

防复发用例在 [test_query_stream.cjs](../../tests/web/test_query_stream.cjs)，按以下名称前缀定位：

| 重点场景 | 测试名称前缀 |
| --- | --- |
| 静默 SSE、临时读取失败后成功 | `silent SSE discovers persisted success` |
| 截止成功/失败/RUNNING/状态不可读四分支 | `watchdog reads`、`watchdog returns`、`watchdog cancels`、`watchdog does not cancel` |
| 首次读取挂起、永久挂起 | `watchdog can recover success`、`hanging status reads` |
| 并行触发、迟到完成、事件分页 | `status checks and disconnect`、`a late status check`、`disconnect drains journal pages` |

修改状态机时可定向执行 `node --test tests/web/test_query_stream.cjs`；本页未执行此命令。

## 自测两问

1. 截止时 SSE 没有完成事件，状态接口返回 SUCCEEDED，但 journal 还有下一页，应怎么处理？
2. 截止读取因超时拿不到状态，能否发送 cancel？为何 210 秒本身还不够？

<details><summary>查看答案</summary>

1. 排空同一 run 的剩余事件并去重，再以 completed 或持久化成功结果结束，不重发查询、不取消。
2. 不能根据不可读推断仍 RUNNING；报告状态读取错误。总等待余量无法解决事件未送达、读取挂起与收尾竞争。

</details>
