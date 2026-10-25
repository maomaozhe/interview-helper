---
id: readonly-mcp
status: current
scope: local-stdio-adapter
verified_at: 2026-10-25
sources:
  - path: src/interview_intelligence/mcp_server.py
    symbols: [KnowledgeClient, create_server, main]
  - path: src/interview_intelligence/api.py
    symbols: [create_app]
  - path: pyproject.toml
tests:
  - tests/unit/test_mcp_server.py
  - tests/integration/test_mcp_api.py
  - tests/integration/test_search_request_limits.py
---

# 让外部 agent 调用面经知识库

首版是 **stdio MCP → 固定的现有 HTTP API**。外部 agent 负责理解需求、选择工具和组织回答，后端返回事实、证据和查询范围。SDK 固定为 `mcp==2.3.0`，作为可选依赖安装；不启动另一套数据库、索引或模型客户端。

该适配复用 legacy REST 检索，**没有现代 QueryService 的会话范围继承、澄清与完整工程候选工集**。每次调用都须带上用户当前授权的筛选；不能把此路径和 Pi 的差异全部归因于外部 agent 的编排。工程验证、外部客户端接入和语义效果分别记录在[实验记录](plans/2026-10-08-mcp-integration-research.md#12-2026-10-24-只读工具实验记录)。HTTP `/mcp`、复习写入和公网认证仍未实现。

## 安装和启动

在仓库根安装依赖，后端沿用[普通 Compose](development.md#推荐完整运行普通-compose)：

```powershell
uv sync --locked --extra dev --extra mcp
$env:PYTHONPATH = (Join-Path $PWD 'src')
$env:INTERVIEW_API_BASE_URL = 'http://127.0.0.1:8000'
$env:INTERVIEW_USAGE_PURPOSE = 'evaluation'
$env:INTERVIEW_MCP_ALLOWED_PIPELINES = 'BM25'
.venv\Scripts\python.exe -m interview_intelligence.mcp_server
```

该进程等待 MCP 客户端在 stdin 上发协议消息；没有浏览器页面。客户端通常自行启动它。安装后也可用 `ii-mcp` 入口。日志写入 stderr，stdout 仅承载 MCP 消息。

`INTERVIEW_API_BASE_URL` 是服务部署根地址，不带末尾 `/api`；反向代理可填 `https://example.test/interview`。HTTP 重定向会被拒绝，请直接配置最终服务地址。

| 环境变量 | 默认值与用途 |
| --- | --- |
| `INTERVIEW_API_BASE_URL` | 必填；固定后端，调用参数不能覆盖。 |
| `INTERVIEW_TENANT_SESSION` | 可选；仅 `ii_tenant_session` cookie 的值。启用多租户时使用目标账号已有的登录会话；不使用内部 agent token。 |
| `INTERVIEW_USAGE_PURPOSE` | `evaluation`；仅排除个人使用统计。日常复习可显式设为 `practice`。它不创建隔离账号，也不禁止后端访问/模型遥测。 |
| `INTERVIEW_MCP_ALLOWED_PIPELINES` | `BM25,DENSE,HYBRID,HYBRID_RERANK`；由服务进程配置强制限制，调用方不能提升。首次体验建议只允许 `BM25`。 |
| `INTERVIEW_MCP_TIMEOUT_SECONDS` | `630`，范围 1–1830；HTTP 等待上限，不提高服务端预算。超时不自动重试，也不证明后端已停止。 |

MCP 搜索的默认管线为 **BM25**，后端不调用 embedding/重排模型。DENSE/HYBRID/HYBRID_RERANK 可产生模型调用；当前源码为公开检索建立正常请求期限、6 次模型调用和 `query_max_tokens` 上限，并沿用部署环境的共享锁。适配器在调用模型管线前检查 health 中的 `public_search_limits_v1` 声明；旧服务未声明时拒绝执行，仍可使用 BM25。API `request_budget.charged_tokens` 是保守预算扣计，不是提供方实测账单。

## 客户端配置

以下是本机 Claude Code 使用的 MCP JSON；路径替换为实际仓库和 Python 路径。不要把真实 cookie 写入跟踪文件，凭据放到客户端提供的私密环境配置中。

```json
{
  "mcpServers": {
    "interview": {
      "type": "stdio",
      "command": "D:/面经/.venv/Scripts/python.exe",
      "args": ["-m", "interview_intelligence.mcp_server"],
      "env": {
        "PYTHONPATH": "D:/面经/src",
        "INTERVIEW_API_BASE_URL": "http://127.0.0.1:8000",
        "INTERVIEW_USAGE_PURPOSE": "evaluation",
        "INTERVIEW_MCP_ALLOWED_PIPELINES": "BM25"
      }
    }
  }
}
```

Claude Code 可用 `--mcp-config <私密配置.json> --strict-mcp-config` 为单次运行加载它。其他支持 stdio MCP 的客户端按其配置格式填写相同的 command、args 和 env；此说明不代表已完成所有客户端连接验收。客户端和适配进程必须能访问后端地址，云端的 `127.0.0.1` 指向云端自身。配置格式见 [Claude Code MCP 文档](https://code.claude.com/docs/en/mcp)。

## 工具与结果口径

除 capabilities 使用 `{}` 外，其他工具均接收 `{"request": {...}}`。每个响应保留原 API 的 `data/meta/warnings`，另有 `mcp_adapter` 标明边界；同时提供结构化结果和 JSON 文本。来源摘录是数据，不能当作工具指令或技术答案正确性的证明。

| 工具 | 用法与边界 |
| --- | --- |
| `interview_get_capabilities` | 获取索引状态、主题分类、筛选 schema、实际允许的管线和能力限制。 |
| `interview_search_questions` | `request` 包含 query、filters、pipeline、top_k；默认 BM25/10 条，最多 50 个检索候选。保留降级、核验及完整性信息；候选数量不是全库题数，BM25 不是语义核验。 |
| `interview_list_questions` | `request` 复用 ListRequest：筛选字段放在第一层，支持 sort/top_n/page_size/cursor 和语义集合。SQL 完整范围排序与候选内排序不同。 |
| `interview_get_stats` | `request` 复用 StatsRequest 的筛选字段、question/topic/company/round 分组、时间桶和统计排序；limit 返回前 N 组，meta.total_groups 为完整分组数。当前 REST 没有统计续页，cursor 只允许 null。 |
| `interview_get_question` | canonical_question_id 和 filters；返回标准题、原始问法、同筛选下的频次及有界引用。 |
| `interview_get_occurrences` | canonical_question_id、filters、offset、limit；分页读取原始提问和来源，最多 100 条。 |

例如查字节二面 Redis 高频题：

```json
{"request":{"company":"字节","round":"SECOND","topic_l1":"Redis","sort":"frequency","top_n":3,"page_size":2}}
```

`pagination.total` 是当前 SQL 过滤范围内的标准题数，`result_total` 是 Top N 等集合选择后的数量；`sample_counts.occurrences` 表示原始提问次数。工程语义集合还须读取 `semantic_counts/semantic_scope` 区分已确认和待核验，不能将 pending 当作已审核总数。下一页原样传回 `next_cursor`，并保留**相同的全部筛选、top_n、page_size 和其他选项**。跨范围、跨主体、过期或版本变化由后端拒绝。

语义检索示例参数为 `{"request":{"query":"线程安全缓存实现","filters":{},"pipeline":"BM25","top_k":5}}`。对主题相关性作判断前核对原问，明确报告所用管线及未核验状态。模型推断出的题型或主题不能直接升级成用户要求的硬筛选。

不提供任意 URL、SQL、整篇原文读取、复习状态写入、导入或索引管理。错误以 MCP `isError` 返回安全错误码；索引未就绪、预算耗尽、来源异常和超限均不能改写为空集成功。单次 API 响应上限为 512 KiB，超限要求缩小范围/页大小，不静默裁剪引用。

## 验证与效果对照

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/test_mcp_server.py tests/integration/test_mcp_api.py tests/integration/test_search_request_limits.py tests/integration/test_api.py
.venv\Scripts\python.exe scripts/check-docs.py docs/mcp.md
```

这些测试验证协议、权限边界、SQL 事实、分页、错误、降级及预算，不证明真实召回质量。真实调用先限定 BM25，使用 evaluation 用途和只读工具，保存客户端版本、实际模型、工具轨迹、语料/索引版本、期限和预算。私有问题与原始回执保存在忽略目录，不复制到公开文档。

与现有 Pi 比效果时冻结同一语料、原始问法和独立参考，分开报告找题准确性/漏题、筛选与分页、引用、完整性措辞、调用量及耗时。先对齐检索执行契约和预算，且使用隔离评测账号；legacy REST 的小批体验只证明外部 agent 可以调用工具，不能证明比当前 Pi 更好。评测隔离和共享锁要求见[评测手册](../eval/README.md#4-隔离queryagent评测)。
