# 工具契约收敛与代码结构核对

日期：2026-10-16。目标：收敛 Python / Pi 的静态工具声明，保持工具顺序、描述、参数 schema、校验、授权及运行行为；通过相关验证后单独提交。工作区已有其他未提交工作，已保存路径清单和重叠文档基线，本轮只提交相关增量。

已知事实：`query_contract.py` 与 `runtime.mjs` 分别维护动作、公共/动作必填字段；Pi 另维护字段投影。Python 与 Pi 工具顺序不同，ANSWER 的 Pi 必填字段更多。主机业务校验、动态 ToolPolicy 和 Pi 模型交互限制承担不同职责，需要保留。

选定方案：以纯 Python 静态定义作为唯一编辑来源，派生主机动作及必填字段；生成 runtime.mjs 中有标记的声明区，Pi 的通用 schema 投影消费它。生成产物随现有 runtime 文件发布，保留独立 Pi 调用、请求形状、Docker/隔离评测挂载和运行身份覆盖，不引入跨目录运行资源依赖。代价是提交生成区，通过确定性生成检查防止手工漂移。

区分检查与验收：先冻结当前八个模型可见工具的顺序、描述及完整参数 schema；前后精确比较，再验证授权刷新、读写限制、COUNT/SEARCH/ANSWER 边界、取消与预算。检查 wheel 安装后的声明可用性，执行 Linux Pi 构建和相关 Python/Node 测试。仅做离线工程验证，不把通过记为语义质量或部署证明。

## 实现与验证

`agent/tool_definitions.py` 统一动作、公共/动作必填、Pi 字段投影、属性限制及描述；`query_contract.py` 从中派生主机声明。`runtime.mjs` 的标记区只由 `python scripts/sync-tool-contract.py` 生成，`--check` 和 Python 测试检查漂移。Pi 保留通用投影、动态权限交集和刷新；业务验证、页范围、写授权及预算继续由主机执行。

保留两项原有差异：Python 映射 COUNT 在 DETAILS 前，Pi 菜单 DETAILS 在 COUNT 前；主机 ANSWER 仅额外必填 answer_text，Pi 还要求 answer_kind / answer_basis / final。生成投影复制 schema 和覆盖对象，不共享可变参数；内部请求形状、默认模型/提示词和工具描述均保持。

| 实际检查 | 结果 |
| --- | --- |
| 拆分前 Python：query_intents、harness、harness_policy、query_agent、query_filter_contract | 81 passed，1 条既有弃用警告，10.33 秒 |
| 拆分前真实 Pi 菜单冻结 | 1 passed；冻结八工具的顺序和完整模型可见声明 hash（含描述、参数、constrainedSampling），固定在 tests/fixtures/query-tool-contract-before.json |
| 拆分后 Python 定向回归 | 128 passed，1 条既有 Starlette/httpx 弃用警告，18.47 秒 |
| 拆分后 Pi runtime 与完整菜单快照 | 18 passed，0 failed / skipped |
| 仅 HEAD + 本次暂存增量的隔离副本 | Python 128 passed（16.62 秒，1 条既有弃用警告）；Node 18 passed，无跳过；确认不依赖其他未提交功能 |
| wheel 安装探针 | uv 离线构建 wheel，以 --no-deps --no-index 安装临时 target；仓库外 cwd 导入已安装包，八工具与原始 schema 相同 |
| 生成检查与 JS 语法 | scripts/sync-tool-contract.py --check、npm run check 通过 |
| Linux Pi 构建与相关 Node 回归 | 禁网临时容器中 build:pi / check 通过，22 passed、0 failed / skipped；含运行身份检查 |
| 文档结构检查、人工正文/diff 与提交范围 | 当前工作区 4 页、189 链接、41 sources、47 符号通过；隔离提交版本 4 页、181 链接、34 sources、37 符号通过；人工核对正文与暂存 diff，已有工作继续保留 |

主要命令（仓库根）：

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/test_tool_contract.py tests/unit/test_query_intents.py tests/unit/test_harness.py tests/unit/test_query_filter_contract.py tests/unit/test_jev_query.py tests/integration/test_harness_policy.py tests/integration/test_query_agent.py tests/integration/test_intent_answers.py tests/integration/test_query_requery.py
node --test tests/node/tool-contract.test.mjs tests/node/pi-runtime.test.mjs
.venv\Scripts\python.exe scripts/sync-tool-contract.py --check
uv build --wheel --offline --out-dir .tmp/tool-contract-20261016/wheel
.venv\Scripts\python.exe scripts/check-docs.py docs/status/current.md docs/reference/module-map.md docs/architecture/query-flow.md docs/verification/2026-10-16-tool-contract.md
```

Linux 验证使用已有 Pi 镜像中的临时容器，禁网、只读挂载相关源码/测试归档，依次执行 `npm run build:pi`、`npm run check`、`node --test tests/node/pi-runtime.test.mjs tests/node/pi-observability.test.mjs tests/node/tool-contract.test.mjs tests/node/runtime-identity.test.mjs`。CI 和 `npm run test:agent` 已纳入新的菜单快照。新声明作为 Python 包模块和 runtime 内生成区随现有发布路径交付，未增加运行资源、挂载或构建工具依赖。

首次独立 Python 探针未设置 PYTHONPATH，导入到旧安装包而找不到 query_contract；按 pytest 的源码入口显式设置 PYTHONPATH=src 后核对成功。一次基线测试命令将已存在的 unit 测试误写为 integration 路径，pytest 退出 4、未运行用例；修正路径后 81 项通过。错误与结果先记录在本地 setup-notes.txt，未放宽断言，没有真实模型调用或预算消耗。原始日志保存在忽略目录 `.tmp/tool-contract-20261016/`。

隔离副本首次为 127 passed / 1 failed：Windows Git 归档将提示词 70 个 LF 转为 CRLF，字节从实际源码的 13967 变成 14037，触发原有 14000 字节预算断言。以 `git -c core.autocrlf=false archive` 重建并逐字节核对 git blob 后，原阈值下 128 项通过；没有修改提示词或测试条件。暂存文档的首次 raw blob 写入也因 CRLF 被 diff --check 拒绝，已仅规范化准备的索引字节，不覆盖其他工作区改动。补丁锚点错误与 pytest 临时缓存警告保留在本地记录。

## 代码结构判断与后续顺序

现有 SQL/检索、领域事实、会话状态和展示层有明确边界；目录分层可以继续使用。`presentation.py` 处理未完成模型 JSON 的临时展示，`query_response.py` 处理完成工具的回执，两者职责不同。Pi schema 限制与主机安全/业务校验也需要同时保留。

1. **静态声明的多处维护已收敛。** 后续只改 Python 定义并重新生成；新增工具的菜单快照与独立业务断言需人工核对，不能靠自动刷新基线消除失败。
2. **SEARCH 存在多余上下文构建。** `QueryService._execute` 为读 requested_pipeline 再调用 model_context，后者会读取偏好/反馈/记忆并构建 filter catalog，最多逐公司核对 100 个语义范围。建议下一次仅提取 pipeline 解析职责；当前未测量性能收益。
3. **证据投影存在两套字段口径。** `harness.evidence_meta` 与 Pi evidenceMeta 分别包含不同的排名、语义范围和核验字段。下一次围绕 host 的版本化证据投影收敛；先保留缺少 host evidence 时的 JS 行投影兼容路径。
4. **兼容入口仍承担业务规则。** `AgentService._search`、API question_search 和 QueryService SEARCH 分别组织检索与核验，当前契约并不完全相同。先查调用者，再决定退役或共用执行策略。
5. **核心编排依旧集中。** 本次审查快照中 API 919 行、create_app 约 780 行；QueryService 884 行、_execute 约 308 行。后续按查询所有权提取规划、取消/超时和活跃任务协调，再拆检索执行；仅机械分路由文件不能减少状态恢复的理解成本。

上述结构结论来自当前工作区静态核查，定位入口见 [模块导航](../reference/module-map.md)；本轮只实施静态工具契约收敛。其他未提交功能、分布式接管、真实服务/语义质量和部署不在本轮验证范围。
