---
id: PIT-018
status: current
resolution: resolved
resolution_scope: health-projection-ordering-and-workspace-refresh
scope: historical-report-and-workspace
verified_at: 2026-10-15
runtime_verified: false
tags: [frontend, state, revision, stale-response, observability]
sources:
  - path: md/issue.md
  - path: src/interview_intelligence/web/assets/core.js
    symbols: [selectHealthSnapshot, shouldRefreshWorkspace]
  - path: src/interview_intelligence/web/assets/app.js
    symbols: [renderHealth, refreshWorkspaceForResult]
tests:
  - tests/web/test_core.cjs
  - tests/integration/test_web.py
---

# PIT-018：同一页面的状态、页脚和真实结果不一致

[返回踩坑索引](README.md) · [相关状态机问题 PIT-001](2026-10-14-query-timeout-state.md)

## 30 秒回忆

主体已显示新语料，侧栏仍说同步中；服务已恢复，旧错误还在；结果是新版本，页脚却没更新。
共同机制是多个异步入口分别维护同一健康事实，缺少统一渲染、旧响应保护和结果触发刷新。
按报告范围已修复这些投影路径；不能由此推断所有 UI、断网和多设备场景均完成验收。

## 合并来源与因果边界

| 原问题 | 已证实原因 | 处理 |
| --- | --- | --- |
| #15 恢复后残留错误 | 成功刷新没清理旧错误，页脚仅随整体刷新变化 | 成功状态清错，并用同一健康响应更新状态和版本 |
| #27 侧栏与主体索引状态不同 | 初始请求和周期请求各更新不同区域，旧响应可覆盖新状态 | 统一渲染，按语料/索引版本选择新状态 |
| #42 查询新数据、页脚旧版本 | search/chat 只更新结果，没有刷新工作区事实 | 新结果版本或已知索引落后时重新读工作区/健康 |

三项共享“健康状态投影分散”的根因；导入文件行的排队/生效混淆归 [PIT-005](2026-10-15-ingestion-cache-recovery.md)，请求完成判定归 PIT-001，分别处理。

## 失败前提与解决过程

早期分别刷新区域不能确保同一时刻一致；页面加载时的健康信息也不能长期代表后台持续发布的事实。
原 #27 和 #42 都先有失败回归，再实现更新规则；没有证据显示换模型或重建全部索引参与修复。

1. 将状态卡、侧栏和页脚汇总到 `renderHealth`，避免重复实现相同判定。
2. `selectHealthSnapshot` 不让更旧的 corpus/index revision 覆盖新进度。
3. 同版本的新服务故障仍允许显示；“版本单调”不能冻结健康状况。
4. `shouldRefreshWorkspace` 根据结果版本和索引是否落后决定重新读取。
5. 索引 ready 来自实际健康检查，不能从搜索返回的 corpus revision 直接推定。
6. 同版本已就绪时避免重复刷新；状态刷新不重新提交模型查询。

## 验证与剩余范围

原 #27 报告有乱序与同步推进测试及真实页面各区域一致的核验。
原 #42 起初只有工程/部署检查，后续日期段另有真实查询版本同步记录；两阶段证据保持各自时点。
当前静态源码仍有统一渲染与版本选择入口，相关行为由 core 测试覆盖。本页没有重开浏览器、运行测试或核验线上。
旧 #32 的瞬时连接中断和 #35 的 localhost 延迟原因没有被本修复查明，不能标为同一根因已解决。

## 防复发与再排查顺序

1. 同时记录结果版本、健康版本、各响应到达顺序和旧错误，判断是后端事实落后还是前端投影落后。
2. 沿初始加载、周期轮询、查询完成和手动刷新检查是否都进入统一渲染。
3. 注入乱序响应：新版本先到、旧版本后到，检查旧响应被拒；同版本故障仍能显示。
4. 检查刷新只读工作区，不触发昂贵重查；不能为更新页脚重复调用模型。

已有守护见 [test_core.cjs](../../tests/web/test_core.cjs) 的版本选择、同版本故障与结果触发刷新用例，以及 [Web 集成](../../tests/integration/test_web.py)。更广的设备/网络组合仍需按改动补验收。

## 回顾自测

1. 搜索返回 corpus v75，能直接把页脚索引改为 v75 ready 吗？
2. 保留较大 revision 后，同版本数据库故障还应显示吗？

<details><summary>查看答案</summary>

1. 不能；需读取真实健康状态，数据库版本不等于索引完成版本。
2. 应显示；版本排序用于拒绝旧进度，不能吞掉同版本的新故障。

</details>
