---
id: PIT-011
status: current
resolution: resolved
resolution_scope: callback-model-attribution-and-durable-observation
scope: historical-report-and-workspace
verified_at: 2026-10-15
runtime_verified: false
tags: [query, contextvar, http, timing, model-call, observability, journal]
sources:
  - path: md/issue.md
  - path: docs/verification/2026-10-09-markdown-sse.md
  - path: docs/verification/2026-10-14-agent-observability.md
  - path: docs/verification/2026-10-14-query-timeout-frequency.md
  - path: docs/verification/2026-10-15-project-consolidation.md
  - path: src/interview_intelligence/api.py
    symbols: [create_app, record_api_call, internal_run, pi_tool]
  - path: src/interview_intelligence/agent/model_gateway.py
    symbols: [ModelGateway.complete]
  - path: src/interview_intelligence/agent/journal.py
    symbols: [QueryJournal.event, QueryJournal.fail_tool]
  - path: src/interview_intelligence/observability.py
    symbols: [AgentObservability.summary, AgentObservability.detail]
tests:
  - tests/integration/test_query_agent.py
  - tests/integration/test_agent_observability.py
  - tests/unit/test_query_streaming.py
  - tests/node/pi-observability.test.mjs
  - tests/web/test_agent_observability.cjs
---

# PIT-011：跨 HTTP 丢失运行上下文，导致工具内模型耗时漏记

[返回索引](README.md) · [观测验收](../verification/2026-10-14-agent-observability.md)

## 30 秒回忆

Pi 调 Python 工具是一条新 HTTP 请求，原请求的 ContextVar 不会自动跨过去。
只记录 Pi 规划调用会漏掉工具内部 embedding/rerank 等模型成本；只补 request_id 又不足以关联预算和当前 run。
修复应在可信内部回调恢复主机运行上下文，再沿同一回执计时、计费；历史缺失值仍保持未知。
`resolved` 限于已记录的归因/观测闭环，不表示慢请求、上游失败或 SSE 传输差额已经全部解释。

## 原始事件与后续相关事件

- [Q07. 耗时归因遗漏工具内模型调用，HTTP 回调不会自动继承 ContextVar](../../md/issue.md)：规划耗时可见，工具内部 embedding 没进入同一查询的阶段计时。
- 后续[观测验收](../verification/2026-10-14-agent-observability.md)补全工具异常、Pi 主机调用前拒绝和模型关联；这解决回执完整性，属于相邻子事件，不能倒推成 Q07 唯一原因。
- [10 月 14 日超时误判](2026-10-14-query-timeout-state.md)属于客户端状态处理，独立于 ContextVar；已有 PIT-001，不在本页重复修法。
- [真实 SSE 验收](../verification/2026-10-09-markdown-sse.md)说明后续已经出现上游真实增量；旧 Q07 时“没有 TTFT”的历史局限不能直接描述当前代码。

## 已证实的因果链

1. Python 初始查询设置 trace/run 上下文；Pi 通过网络再发起工具请求，产生新的 HTTP 执行上下文。
2. 新请求若仅拿到工具参数，没有恢复 `query_run_context`，底层 provider 回调无法累计当前 run 的计时和预算。
3. request_id 能帮助查日志，却不包含主机 run 对象、预算对象和租户/模型选择；传字符串不能替代这些边界。
4. 网关本身已记录的调用若再通过通用回调无条件累计，又会产生重复计时；补漏时需同时防止重复归因。

因此“模型耗时低但端到端很慢”首先是观测缺口的线索，不能立即断定 ES 慢、网络慢或模型快。
旧记录的缺失 provider_ms/TTFT、token 或工具状态无法从总耗时可靠反算，应显示 UNKNOWN。

## 修复步骤与当前入口

1. [api.internal_run](../../src/interview_intelligence/api.py) 验证内部调用凭据，从当前进程持有的 run 找到真实请求，并恢复对应租户/提供方及预算约束。
2. `pi_tool` 验证模型 plan 后设置 `model_request_id` 与 `query_run_context`，执行主机工具，finally 重置，避免泄漏到后续请求。
3. `record_api_call` 为没有自行携带 request_id 的底层调用累计 queue/interval/provider 时间，关联 query_run_id 并结算可获得的 token 用量。
4. [ModelGateway.complete](../../src/interview_intelligence/agent/model_gateway.py) 保留网关自己的调用/预算记录，显式身份使通用回调不重复累计；未知用量保持保守计费与来源标记。
5. [QueryJournal](../../src/interview_intelligence/agent/journal.py) 记录主机工具失败，并补记 Pi 在主机执行前发生的 schema/权限拒绝；匹配已有错误去重。
6. [AgentObservability](../../src/interview_intelligence/observability.py) 从持久化事实展示任务/工具/模型，不为查看页面而恢复、重跑或修改过期任务。

内部回调只能找到进程内所属 run；它不是跨进程队列。持久化 journal 提供状态/恢复证据，不会让任意 worker 自动获得活跃对象。
不要把不同 provider 都画成经过同一个 Pi 网关；本事故恰好来自主机工具内部还有独立模型调用路径。

## 实际检查与没有证据的“失败尝试”

Q07 记录了补上下文后的直接执行与真实内部回调检查，没有保存一串失败优化方案。
因此不能写成“试过增大超时、换模型、扩容 ES 都无效”；这些不在本事件证据里。
后续扩大观测回归确实发现以下失败，必须与原故障区分：

| 实际情况 | 结论 |
| --- | --- |
| 计时集成测试的模拟 retriever 缺相关性核验回执 | 当前契约返回 QUERY_RELEVANCE_UNAVAILABLE；不是看到测试失败就认定新观测包装损坏业务 |
| 停用本次执行包装再跑仍复现 | 原报告保留该失败，不宣称工作区全量通过；需要独立修订测试 fixture/证明业务契约 |
| 旧记录无明确计时或状态 | 保留未知，不生成看似精确的历史分解 |

上述失败来自[原观测验收](../verification/2026-10-14-agent-observability.md)，初次整理时未重跑；原验收保留当时结论，后续修订另记如下。

2026-10-15 收口补充：trace 测试替身只执行 embedding，却请求 `HYBRID_RERANK`；首次仅改模型 plan 为 `DENSE`，仍被当前请求的 pipeline 覆盖，定向回归为 29 passed、1 failed。随后将请求、plan 和 Pi run 明确统一为 `DENSE`，保留 embedding 调用归因、耗时及 `UNVERIFIED` 断言，同组 30 项通过，未削弱真实 rerank 失败契约。过程见[收口验收](../verification/2026-10-15-project-consolidation.md)；这是离线测试装配修订，不证明模型语义质量或已部署。

## 计时与失败率应怎么读

| 指标 | 契约 | 常见误读 |
| --- | --- | --- |
| queue / interval / provider | 分别记录共享锁排队、调用间隔和提供方时间 | 全部叫“模型推理耗时” |
| TTFT | 真实上游首个增量的观测值，缺失保持未知 | 首个工具事件或页面首字等于上游 TTFT |
| 任务失败率 | FAILED + INTERRUPTED + PARTIAL_FAILURE 除以这些状态与 SUCCEEDED 的总数 | 把取消、排队、运行中都加入失败分母 |
| 工具失败率 | 有明确成功/失败结局的工具调用，未定/取消另列 | Pi 拒绝和主机失败重复算两次 |
| 模型失败率 | 持久化模型回执 FAILED / 调用数 | 成功 HTTP 就代表模型/业务成功 |

指标按运行开始时间与任务类型取同一批任务；列表的状态/关键词筛选不会改变上方指标总体。
跨账号相同 request_id 不能直接合并；先用运行所有权建立关联，旧记录缺关联不得猜测。

## 历史验证范围

Q07 的一次自然语言 Top40 记录端到端约 6327ms，其中规划约 6231ms、provider 约 5924ms、SQL 约 57ms。
这些是开发样本，不是 P95；结构化 SQL 与自然语言请求也不能直接作为同一链路比较。
[10 月 9 日 SSE](../verification/2026-10-09-markdown-sse.md)记录公开请求 231 个真实增量，首次约 5.75 秒、最终约 18.38 秒；单次样例不建立稳定延迟目标。
[10 月 14 日观测](../verification/2026-10-14-agent-observability.md)记载冻结包 Python 61 项、Node 21 项及实际 PostgreSQL 只读查询通过。
浏览器的本地 205 条隔离日志与 SSH 隧道正式页面 16 步执行记录是两种不同验证，不可混成同一真实故障样本。
该验收保留上述扩大回归失败；冻结包通过不能替代当前工作区全量通过，更不等于全部历史 run 都有完整计时。
初次整理仅静态核对报告和 2026-10-15 工作区；本轮补充了上述离线回归，仍未访问线上观测页、真实模型端点或线上数据库。

## 防复发与排查顺序

1. 从持久化 run 确认终态和所属用户，再列出规划、工具、工具内 provider 的实际调用；不要只看一个总耗时。
2. 检查每个 HTTP/线程边界是否恢复了 trace、run、tenant 与限额；用真实内部回调路径验证，直接函数调用不够。
3. 对照 ModelCall 与阶段累计，检查遗漏和重复；未知用量/历史空值不填零冒充测量。
4. 检查 Pi 主机前拒绝、主机失败、取消与中断是否各有结局；同一调用不重复计数。
5. 若后端完成而页面仍等待，转到 [PIT-001](2026-10-14-query-timeout-state.md) 检查客户端状态和传输，不把差额全部归于模型。

已有守护：[归因测试及 fixture 范围](../../tests/integration/test_query_agent.py)、[所有权/分母/只读/拒绝去重](../../tests/integration/test_agent_observability.py)、[真实增量契约](../../tests/unit/test_query_streaming.py)、[Pi 拒绝记录](../../tests/node/pi-observability.test.mjs)。初次整理时未执行；本轮实际执行范围见收口验收。
待补证据包括生产慢请求的分段关联和历史缺口覆盖率；未定位的 SSE/公网耗时差不能由静态代码推断。

## 自测两问

1. Pi 工具回调已经带 request_id，为什么仍可能漏计 embedding 耗时？
2. 观测页发现历史工具没有结束时间，应补成零耗时成功吗？

<details><summary>查看答案</summary>

1. 新 HTTP 不继承原 ContextVar；还必须在可信主机恢复所属 run、预算和租户上下文，底层回调才能归因。
2. 不应。应保持未知/未定，并按实际回执追查；页面读取不能制造结局或触发恢复写入。

</details>
