# 项目收口验收：状态、提交与职责边界

日期：2026-10-15。范围：`codex/project-consolidation` 工作区工程验证；不代表服务器发布或真实语义质量验收。当前结论统一维护在 [状态表](../status/current.md)，本页只保存本轮决策、失败、提交与验证证据。

## 范围与决定

起点为 `ef6c117`；工作区原有 208 个变更路径（69 修改、6 删除、133 未跟踪）。本轮按主题核对并提交已有工作，保留私有 `md/issue.md` 的本地改动。未推送、部署、调用真实模型或改写私人原始数据。

实施前选择三条边界：一处状态表、一种推荐完整运行方式、每次一个可独立验证的职责。先验证和修复既有基线，再依次统一排名政策、提取结果呈现和 HTTP 错误映射。代价是继续保留尚无删除依据的兼容入口与实验文件；不借整理切换提示词默认、不重写查询生命周期。

- [当前状态](../status/current.md) 只用一张表汇总能力、默认版本、部署证据、质量结论、下一步；其他当前页负责导航或专题事实。
- [普通 Compose](../development.md#推荐完整运行普通-compose) 是唯一推荐完整运行方式：API + Pi + worker + PostgreSQL + Elasticsearch，Windows 经 WSL。模块直启、界面示例和专项部署在 [其他入口](../development.md#其他入口的用途) 标清用途。
- 通用评测、候选实验、历史复现统一从 [评测手册](../../eval/README.md#入口分类通用评测候选实验与历史复现) 进入。默认仍为抽取 v2、任务分类 v4、归并 v1；候选结果不能替代默认或部署证据。
- 完成后的施工检查点已并入本验收记录，不再维护另一份收口计划。此前六份已删除文档的独有内容、入链及读取/哈希依赖已核对；必要历史失败与决策继续保留。

## 主题提交

| 提交 | 范围与边界 |
| --- | --- |
| `82105b9` | 入库来源证据、可选质量候选及打包；默认版本保持 |
| `fcfd751` | 查询范围、持久化回执、使用记录/观测、迁移和既有基线修复；这些运行链路存在依赖，合为一个主题 |
| `5159357` | 固定 Qwen 运行身份、验证脚本与已有部署证据；本轮未远端执行 |
| `5dcac49` | 来源评测、候选与复现协议；私有输入缺失明确跳过 |
| `a42b125` | 一处状态矩阵、普通 Compose 入口与知识导航 |
| `9bd0c74` | SQL / Python 共用排名政策，保留公式计算顺序、范围及分页语义 |
| `1db3afb` | QueryService 仅提取结果文案/响应结构；状态、权限、预算和 journal 留在执行层 |
| `f0689fb` | API 仅提取五类 HTTP 错误映射；注入 request ID，保留脱敏、状态码和重试语义 |

这些提交正文记录各自验证范围。定向测试在当时工作区执行，不宣称每个中间提交都单独跑过全套回归；最终组合由下方完整回归验证。

## 原始失败、修复与防复发

Windows Python 3.12.1 初次完整基线：`.venv\Scripts\python.exe -m pytest -q`，**1315 passed、8 failed、3 skipped**，205.94 秒；失败发生在职责拆分之前。

| 实际失败或限制 | 已核实原因、处理及验证边界 |
| --- | --- |
| legacy 准备流程 1 项无结果 | 旧 Agent 把宽泛 Java 后端请求拼成精确 `position`，与精确职位 SQL 过滤冲突。生产端改用 `job_family=BACKEND` / `language=JAVA`，保留精确职位语义；新增不同职位表述回归。 |
| legacy focus 来源 5 项不符 | compact context 二次投影把 `legacy_saved_state` 覆盖成已完成工具来源；保持已识别来源的投影幂等，并验证不改持久状态、不虚构 ANSWER 范围。相关 61 项通过。 |
| trace fixture 的 SEARCH 被拒绝 | 模拟 retriever 仅执行 embedding，却请求 `HYBRID_RERANK`。首轮仅改 plan 后仍失败：主机按请求覆盖 pipeline，结果为 29 passed、1 failed（15.08 秒）。随后请求、plan、Pi run 都明确 `DENSE`，保留 trace/timing 及 UNVERIFIED 断言；相关 30 项通过。 |
| SQL 空资格范围被拒绝 | 主机已证明 `eligible_count=0`，没有候选和 retriever 调用，却被当作缺核验元数据。以 `empty_sql_scope` 记录空范围依据，允许成功和重放；非空范围真实 rerank 失败仍拒绝。不是模型核验已经执行。 |
| 私有评测复现输入未随仓库分发 | 必要快照/Gold/归档缺失才 skip；数据存在时继续严格检查 JSON、hash 和语义，不吞掉坏数据失败。有本地输入时 63 项通过；临时目录模拟缺失时 27 项明确 skipped，未移动原数据。 |
| WSL 中无 Linux Node，npm 指向 Windows 挂载路径 | 使用已有 Pi 镜像内 Linux 工具链、只读源码归档、禁网临时容器完成构建及测试；未安装工具或修改运行容器。 |
| 10-11 算法报告身份不一致 | 既有 `report_sha256` 与当前 Markdown 不符。保留原值，补充当前 hash 与审计说明；原因未知，不能声称原报告身份已重新验证。 |

原始日志保存在本地忽略目录 `.tmp/project-consolidation/`，包括 `python-baseline.log`、`search-baseline-fixes.log`、`search-baseline-fixes-v2.log` 和各定向回归。工具层的路径/补丁锚点失败另记 `setup-notes.txt`；没有通过改弱成功条件消除失败，也没有真实模型调用或模型预算消耗。

复用规则已补回已有 [意图/状态](../retrospectives/2026-10-08-query-intent-state.md)、[查询范围](../retrospectives/2026-10-08-query-scope-narrowing.md)、[上下文/观测](../retrospectives/2026-10-14-query-context-observability.md) 专题。

## 实际验证

| 范围 | 实际命令或检查 | 结果 |
| --- | --- | --- |
| 最终 Python 完整离线回归 | `.venv\Scripts\python.exe -m pytest -q -rs` | **1338 passed、3 skipped、1 warning**，245.62 秒；日志 `python-final.log` |
| 候选 provider / 配置 / 打包 | 9 个相关测试文件，日志 `candidates.log` | 110 passed，5.97 秒 |
| 排名职责 | 下方排名命令 | 41 passed，11.71 秒；固定预期覆盖四种复习权重、零分母与空范围 |
| QueryService 呈现职责 | 下方呈现命令 | 75 passed，19.19 秒；facts/warnings 引用及 plan 独立快照保持 |
| API 错误职责 | 下方 API 命令 | 拆分前新增端点边界 17 passed；拆分后 93 passed，44.05 秒；五类 handler AST 仅回调名称不同，独立审阅未发现回归 |
| Windows Node 22.23.2 | `npm run check`；`node --test tests/node/*.test.mjs tests/web/test_*.cjs` | 检查通过，91 passed |
| Linux Pi 构建及 Node | 现有 `interview-intelligence-pi-agent:latest` 临时容器中依次运行 `npm run build:pi`、`npm run check`、上述 Node 测试 | 构建/检查通过，91 passed；容器禁网，当前源码覆盖镜像旧源码，日志 `pi-linux-baseline.log` |
| 文档、来源与删除项 | 相对路径/标题锚点、front matter、源码符号 AST、Mermaid 围栏、删除文档依赖、`git diff --check` | 首轮 1071 条公开链接、207 条 sources 路径、219 个符号、33 页元数据、13 处 Mermaid 静态检查通过；104 条私人引用仅分类，未复制原文。最终改动另做增量核对。 |

三个职责拆分的实际命令（仓库根执行）：

```powershell
.venv\Scripts\python.exe -m pytest -q tests/integration/test_ranking.py tests/integration/test_analytics.py tests/integration/test_query_spec_completion.py tests/integration/test_semantic_scope_listing.py tests/unit/test_stats_contract.py
.venv\Scripts\python.exe -m pytest -q tests/unit/test_query_response.py tests/integration/test_intent_answers.py tests/integration/test_search_frequency_ranking.py tests/integration/test_full_algorithm_query.py tests/integration/test_query_partial_rerank.py tests/integration/test_query_requery.py tests/integration/test_query_semantic_filter_scope.py
.venv\Scripts\python.exe -m pytest -q tests/integration/test_api.py tests/integration/test_access.py tests/integration/test_tenants.py tests/integration/test_query_agent.py tests/integration/test_question_usage.py tests/integration/test_full_algorithm_query.py tests/integration/test_query_partial_rerank.py
```

legacy 修复的 61 项回归命令为 `.venv\Scripts\python.exe -m pytest -q tests/integration/test_agent.py tests/integration/test_conversation_focus.py tests/unit/test_harness.py tests/integration/test_query_metadata_scope.py`。

最终文档增量检查通过：327 条链接、65 条 sources 路径、58 个符号、8 页元数据和 1 处 Mermaid 静态检查。旧计划路径已无入链；删除项粗检的 12 个同名命中均指向新的 verification 路径，逐条核对有效，没有代码读取/哈希依赖遗留。

Linux 容器实际通过 WSL 执行 `docker run --rm --network none --user root`，将本地 `pi-input.tar` 只读挂载后解到 `/app`，再运行上表三条命令。该归档仅包含相关源码和测试，没有挂入 `.env`、运行数据库或个人会话；此结果验证构建，不是部署验收。

最终跳过的 3 项为 `tests/integration/test_access_postgres.py`、`test_question_usage_postgres.py`（均需显式 PostgreSQL 测试连接），以及 `test_elasticsearch_live.py`（需本地 Elasticsearch）；未把跳过记为通过。已有 Starlette/httpx 弃用警告保留，未借本轮调整依赖。

## 剩余边界

API 和 QueryService 仍较大；本轮只拆出错误映射与结果呈现。接下来按 [模块导航](../reference/module-map.md) 继续逐个处理跨语言工具契约与运行生命周期，每项先明确职责与行为守护。共享排名规则已有独立固定值回归，不能仅用共享公式两端互相比较证明正确。

未执行真实 PostgreSQL/Elasticsearch 活体验证、远程部署、模型调用或语义质量评测。完整 V1 质量阻断、召回/误并问题、分布式任务接管、逐文档索引与事务边界继续开放；本轮离线通过不能消除这些限制，也不能证明当前服务器运行这些提交。
