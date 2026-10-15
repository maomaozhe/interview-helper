---
id: PIT-013
status: current
resolution: mitigated
resolution_scope: native-shared-model-lock-and-bounded-file-publication
scope: historical-report-and-workspace
verified_at: 2026-10-15
runtime_verified: false
tags: [model-lock, windows, wsl, filesystem, concurrency, receipts]
sources:
  - path: md/issue.md
  - path: docs/decisions/2026-10-01-native-model-lock.md
  - path: src/interview_intelligence/providers/gate.py
    symbols: [ModelCallGate, ModelCallGate.call, ModelCallGate._acquire]
  - path: src/interview_intelligence/ingestion/extraction_cache.py
    symbols: [ExtractionStageCache.save]
  - path: scripts/receipt_io.py
    symbols: [atomic_write_json]
  - path: compose.yaml
tests:
  - tests/unit/test_model_call_gate.py
  - tests/unit/test_query_gate_limits.py
  - tests/integration/test_extraction_stage_cache.py
  - tests/unit/test_receipt_io.py
---

# PIT-013：锁文件存在，不代表所有调用方真的共享同一道门

[返回踩坑索引](README.md)

## 30 秒回忆

- **症状**：模型请求前锁文件 PermissionError；抽取已完成却无法保存缓存；Windows 多进程偶发失败。
- **原因**：共享目录的瞬时文件拒绝、跨系统锁边界、Windows 首字节初始化竞态分别发生在不同位置。
- **处理**：真实调用统一 Linux 原生共享卷；先拿操作系统锁再初始化；文件发布仅有限重试。
- **记住**：不能因拿锁失败绕过串行门，也不能为重写报告重复调用模型。

## 原记录与触发范围

以下来源均为 [原问题日志](../../md/issue.md)：

| 原编号或标题 | 对应边界 |
| --- | --- |
| #4「串行锁权限与跨系统调用」 | 早期锁权限故障，以及 Windows/Linux 不能假设互斥的边界 |
| #21「Windows/WSL 共享目录偶发拒绝原子重命名」 | 抽取产物临时文件无法原子替换到正式路径 |
| 「2026-10-01 15:42 运行环境修复」 | 再次出现锁打开拒绝，迁移到 Linux 原生命名卷 |
| #24「小样本对照报告写入出现瞬时拒绝」 | 模型评测已完成，报告落盘失败；处理时保存既有结果 |
| Q08「Windows 模型锁首字节初始化也需要放在临界区」 | 新空文件并发初始化时，字节区域已被另一进程锁住 |

#21 和 #24 不是模型锁算法失败，而是相邻的文件发布故障。
把它们放在一起是为了辨认失败发生在“拿锁、初始化、缓存发布还是报告发布”，不能互换修法。
串行等待与 Elasticsearch 快照有效期的相互作用见 [PIT-019](2026-10-01-search-snapshot-lifetime.md)（原 #20）。

## 定位与原因

| 判断 | 证据 | 确定性 |
| --- | --- | --- |
| 单纯 chmod 不能解释所有拒绝 | 锁在权限修复后复发；缓存故障时 uid、目录权限、目标状态均正常 | 已证实其不足 |
| 共享目录某占用方导致瞬时拒绝 | 20 次同类写入探针成功，原失败偶发，未定位具体占用者 | 机制推断；占用方未知 |
| 杀毒软件一定是根因 | 没有对应占用证据 | 未证实，不能当成结论 |
| Windows 首字节写入在临界区外会竞争 | Q08 多进程用例在初次 write/flush 失败，代码先写再锁 | 历史定位已证实 |
| Windows 本机测试能证明与 Linux 容器互斥 | 两侧使用不同系统锁接口，部署约束明确禁止混用真实调用 | 不能据此证明 |

[原生锁决策](../decisions/2026-10-01-native-model-lock.md) 将故障归因保留为“具体占用方未知”。
后续文档不应把早期权限猜测或扫描器猜测变成已确认根因。

## 做过的处理与弯路

| 实际尝试 | 结果与处理依据 |
| --- | --- |
| 修目录权限、共用 `/app/data/model-call.lock` | 当时可运行，后来再次 PermissionError，说明不足以消除共享目录边界 |
| 对缓存替换和锁打开加有限 PermissionError 重试 | 短暂拒绝可以恢复；永久拒绝继续失败，不发布或进入模型请求 |
| 停止旧调用方，再迁移到共享 `model_runtime` 卷 | 避免旧路径和新路径同时各放行一个请求；API/worker 使用同卷同路径 |
| Windows 空文件先初始化字节再加锁 | Q08 多进程测试暴露竞态，废弃该顺序 |
| 先拿系统锁，再初始化首字节并读取上次结束时间 | Q08 修法；仍保留线程锁、取消、截止时间、释放与完成后间隔 |
| 报告写失败后换 v2 产物、有限重试 | #24 保留已完成模型结果，没有为补报告再跑一遍模型 |

迁移锁不是“换一个好写的文件名”即可完成：所有真实调用方必须同时遵守同一互斥域。
维护容器、审计脚本与 API/worker 都在核对范围内，不能只检查两个常驻服务。

## 当前源码里的防线

[`ModelCallGate.call`](../../src/interview_intelligence/providers/gate.py) 先取同路径线程锁，再打开文件并取系统锁。
打开文件的 PermissionError 最多尝试 5 次，退避累计最多约 3 秒；持续失败不会进入请求体。
Windows 使用单字节 `msvcrt` 锁，Linux 使用 `fcntl.flock`，首字节初始化位于系统锁内。
请求结束后写入完成时间，再释放锁；最小间隔在共享门内执行，默认部署要求至少 2 秒。
取消或到达请求期限的等待者退出，不能污染后续持锁者。

[`compose.yaml`](../../compose.yaml) 给 API/worker 挂载同一命名卷，锁路径均为 `/app/runtime/model-call.lock`。
真实 Windows CLI 不能与 Docker 调用混用；当前规则见 [模型锁决策](../decisions/2026-10-01-native-model-lock.md)。
本地 Windows 离线测试使用独立临时锁，验证的是其测试环境内的互斥。

[`ExtractionStageCache.save`](../../src/interview_intelligence/ingestion/extraction_cache.py) 先写完整临时文件、flush/fsync，再原子替换。
替换仅重试 PermissionError，共 5 次；永久失败清理临时文件，不登记已缓存或发布。
重试使用同一份已验证结果，不重新抽取，也不扩大模型预算。

[`atomic_write_json`](../../scripts/receipt_io.py) 当前使用独立的 3 秒重试窗口，不能照抄旧报告“5 次”描述。
初次发布用原子硬链接拒绝覆盖已有回执，更新使用原子替换；旧完整回执在失败时保留。
这解决的是回执写入，不能把报告存在当成模型语义通过。

## 验证结果与未解决部分

- 修复状态为 `mitigated`：已知竞态有代码防线，共享目录瞬时拒绝未被宣称彻底根除。
- 10-01 记录核对 API/worker 同一原生卷，并用三个进程验证真实锁路径互斥与至少 2 秒间隔；该探针不调用模型。
- 同日后续真实向量和判同调用未再观察到锁 PermissionError；这只证明当时运行。
- Q08 记录 Windows 多进程与工程回归通过，不能外推为 Windows/Linux 跨系统互斥验收。
- 当前 [门控测试](../../tests/unit/test_model_call_gate.py) 覆盖线程、进程、瞬时打开拒绝、永久拒绝和失败后的间隔。
- [截止时间测试](../../tests/unit/test_query_gate_limits.py) 覆盖取消等待与过期请求不进入 provider。
- [缓存测试](../../tests/integration/test_extraction_stage_cache.py) 覆盖瞬时/永久替换拒绝；[回执测试](../../tests/unit/test_receipt_io.py) 覆盖重试、竞争和不重复 HTTP。
- 本次只核对源码和历史记录，以上测试未重新执行，也未检查今天容器挂载与实际调用者。

## 防复发与再遇到时的排查顺序

1. 保留失败调用栈，确认失败在 open、字节初始化、模型网络、缓存 replace 还是报告写入。
2. 核对所有调用者的系统、解析后锁路径及实际挂载；同字符串不一定对应同文件。
3. 确认是否存在旧脚本或两套活动锁；迁移前停旧调用方，禁止失败后自动换独立锁。
4. 用不调用模型的并发探针验证互斥与完成后间隔，再考虑受控真实请求验证。
5. 若只是产物发布失败，先复用已完成结果并校验完整性，避免重复模型调用或覆盖旧回执。

| 守护措施 | 当前状态 |
| --- | --- |
| 原生共享卷、禁止跨系统真实调用混用 | 已有部署配置和运行规则 |
| 先锁后初始化、有限重试、失败拒绝放行 | 已有代码和定向回归入口 |
| 所有临时调用者共享锁的自动部署审计 | 待补；本篇未找到覆盖所有入口的自动证明 |
| 瞬时文件拒绝的具体占用方取证 | 仍未知，不能用无限重试掩盖 |

## 回顾自测

1. 两个容器各有一个可写锁文件，为什么仍可能同时调用模型？
2. 四条真实评测已完成、报告写失败，应该重跑模型还是先恢复产物发布？

答案要点：互斥要求同一文件和同一锁域；报告修复应复用已完成结果，有限重试并保留失败证据。
