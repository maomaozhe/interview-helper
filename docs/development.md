# 开发与运行说明

项目入口与架构见 [README](../README.md)，能力、部署与质量结论见 [当前状态](status/current.md)，默认版本见 [运行配置](reference/runtime-config.md#提示词选择与版本身份)。本页维护运行方式与开发操作。

开发任务导航见 [项目知识库](README.md)；Settings、环境变量、Compose 的覆盖关系，以及提示词默认/候选版本见 [运行配置与提示词登记](reference/runtime-config.md)。

## 环境与配置

- Python 3.12，依赖锁定于 `uv.lock`；开发工具使用 `uv sync --locked --extra dev`。
- 本地构建 Pi 需要 Node ≥22.19，依赖锁定于 `package-lock.json`；构建步骤在 Linux / WSL 中验证。
- 唯一推荐的完整运行方式为仓库根 [compose.yaml](../compose.yaml)：FastAPI、Pi sidecar、worker、PostgreSQL、Elasticsearch。Windows 使用 WSL 中的 Docker；下面的启动脚本也是这套 Compose 的封装。

复制 `.env.example` 为 `.env`，配置以下字段；不要提交实际凭据。

| 配置 | 用途 |
| --- | --- |
| `MODEL_BASE_URL` / `MODEL_API_KEY` | 模型适配器 endpoint 与凭据；文本和 embedding 模型须与所用接口兼容。 |
| `EXTRACTION_MODEL` / `QUERY_MODEL` | 抽取与查询规划模型。 |
| `JUDGE_MODEL` / `RERANKER_MODEL` | 去重判断与相关性核验模型。 |
| `EMBEDDING_MODEL` / `EMBEDDING_DIMENSION` | 向量模型与实际输出维度，需保持一致。 |
| `MAX_MODEL_CALLS` / `MAX_MODEL_TOKENS` | 导入任务的正数预算上限，按语料规模设置。 |
| `APP_SIGNING_KEY` / `INTERNAL_AGENT_TOKEN` | 分页签名及内部 Agent 回调鉴权，分别使用随机值。 |
| `TASK_ANNOTATION_POLICY` | 默认 `VERIFIED`；`KNOWN` 允许明确的临时机器分类，用于开发诊断。 |

`QUERY_PROMPT_VERSION` 默认 `query_agent_v16`，`QUERY_DEADLINE_SECONDS` 默认600秒，`QUERY_MAX_MODEL_CALLS` 默认8次规划/回答，`QUERY_MAX_TOTAL_MODEL_CALLS` 默认24次共享模型调用，`QUERY_MAX_TOKENS` 默认256,000。这些项的 Compose 与 Settings 默认一致；其他开关不能据此类推，例如 Compose 开启 router / Pi，而直接 Settings 默认关闭。普通 Compose 只透传 YAML `environment` 中列出的字段，具体覆盖关系见 [运行配置](reference/runtime-config.md#先确定配置入口)。真实证据重排会增加延迟；耗时以请求回执为准。

长期偏好由用户明确保存。任务分类核验通过草稿和发布版本管理；不要把机器分类的 SQL 范围核对解释成语义分类正确。

## 推荐完整运行：普通 Compose

在 Linux / WSL 仓库根目录配置 `.env` 后启动；Windows PowerShell 可直接使用下方 [WSL 启动脚本](#windows--wsl)。两种命令启动的是同一套服务。

```bash
docker compose up -d --build --wait
docker compose exec api ii status
docker compose exec api ii ingest --idempotency-key first-import
docker compose exec api ii run <run-id>
docker compose exec api ii retry-failed <run-id> --idempotency-key retry-import-1
docker compose exec api ii stats --topic-l1 Redis --limit 10
docker compose exec api ii search "缓存击穿的追问"
docker compose exec api ii chat "Redis 高频问题有哪些"
```

语料放在 `md/`，通过只读挂载供导入服务读取；`md/issue.md` 是问题记录，明确排除出语料。导入在后台队列执行，相同请求重放应复用幂等键。

`GET /api/health` 区分数据库、索引和模型配置状态。事实已更新而索引尚未同步时，搜索返回明确错误，SQL 统计仍可用。来源以 `SNAPSHOT_ROOT` 下的内容 hash 定位，避免容器和 Windows 的绝对路径差异。

## 本机演示与来源核查

服务启动、导入和索引就绪后，使用同一容器入口核查统计、来源与检索。下列命令是操作示例，本次文档整理未执行；自然语言问答及向量/重排检索可能调用模型，BM25 检索本身不调用模型。

```bash
docker compose exec api ii stats --company 得物 --limit 10
docker compose exec api ii detail <canonical-question-id>
docker compose exec api ii occurrences <canonical-question-id>
docker compose exec api ii source <revision-id> --line-start 1 --line-end 20
docker compose exec api ii search "Redis 为什么快" --pipeline BM25
docker compose exec api ii chat "Redis 高频问题有哪些"
# 此命令会写入真实复习记录；演示时使用隔离数据和用户。
docker compose exec api ii review <canonical-question-id> WEAK --score 2 --idempotency-key review-1
docker compose exec api ii stats --sort gap --limit 5
```

四路比较时将 `--pipeline` 分别设为 `BM25`、`DENSE`、`HYBRID`、`HYBRID_RERANK`，检查请求路径、实际路径和降级提示。统计来自当前有效 occurrence；空结果、一次成功或四路烟测不代表质量门槛通过，正式比较依 [评测手册](../eval/README.md)。

导入的 `processed_documents` 包含处理成功的排除文档；`excluded_documents` 单列排除数，场次和提问数从统计接口读取。已生效且指纹一致的来源可跳过；同一复习幂等键与请求重放不增加事件。

## Windows / WSL

使用运行 Docker 的 WSL 发行版；启动脚本默认 `Ubuntu-22.04`，可按本机名称覆盖：

```powershell
.\scripts\start-local.ps1 -Distribution Ubuntu-22.04
# 修改代码后重建：
.\scripts\start-local.ps1 -Distribution Ubuntu-22.04 -Build
```

脚本通过 `wslpath` 将当前仓库目录转换为WSL路径，无需预先创建固定的 `/opt` 链接。`-WaitTimeoutSeconds` 默认120秒，可在30–600秒间调整。中文等非ASCII路径暂时使用普通Compose构建器，绕过当前Bake共享会话头的字符限制；此兼容路径使用 `COMPOSE_BAKE=false`，升级Compose时应复验并移除兼容分支。

脚本创建或复用当前用户的隐藏保活任务 `InterviewIntelligence-WSL-<项目与发行版摘要>`，由 Windows 任务计划程序运行 `scripts/wsl-keepalive.ps1`，并等待服务就绪。任务使用 `Interactive` / `Limited`、无限运行时间与重复实例忽略，只按需启动，没有登录触发器；终端结束不会结束该任务。启动输出显示任务名，可在任务计划程序中停止；再次执行启动脚本会复用并启动它。需要本机 ScheduledTasks PowerShell 模块及创建当前用户任务的权限。

若构建网络无法解析依赖站点，可以使用 `compose.build-wsl.yaml` 的构建配置；运行服务仍采用常规网络，不需修改全局 DNS。实际构建、数据保留和保活故障处理见[本地验收](verification/2026-10-09-local-deployment.md)。

## 其他入口的用途

以下入口继续保留，各自服务于明确的开发、评测或维护任务；完整运行仍使用上面的普通 Compose。

| 入口 | 用途与边界 |
| --- | --- |
| `tests/web/smoke_server.py` | 无凭据界面示例，使用临时数据库与合成数据；不代表真实模型问答已启动。操作见 [README](../README.md#无凭据体验界面)。 |
| [只读 MCP](mcp.md) / `ii-mcp` | 外部 agent 自己调用检索、SQL 列表、统计和详情；本地 stdio 转发固定 API，安装可选 `mcp` 依赖。当前为 legacy REST 适配，不具备现代会话范围/完整工集契约。 |
| 直接启动 Python API / worker、`Settings(...)`、Node `start:agent` | 模块调试与测试注入，需要自行提供依赖和配置；不能由单个进程推定完整服务或 Compose 默认行为。 |
| `compose.evaluation.yaml`、封存源码 overlay | 独立评测 API / Pi、账号与时钟，用于采集和复现，沿用约定的开发快照及共享模型锁；详见 [评测手册](../eval/README.md#入口分类通用评测候选实验与历史复现)。 |
| [SSH 部署](deployment-ssh.md) | 维护已有远端环境、备份与核对指定发布包；不是本地首次运行步骤，历史发布也不等于当前工作区已部署。 |
| [私有 Qwen](deployment-qwen.md) | 可选模型服务与提供方适配的专项操作；不改变普通 Compose 的默认模型选择。 |
| Legacy chat / search、可选 Jev | 保留兼容与候选执行路径；触发条件见 [系统架构](architecture/system.md#启动方式决定执行路径)，不作为新开发的完整运行推荐。 |

## 模型调用与数据

API 与 worker 共用 Linux 原生命名卷中的 `/app/runtime/model-call.lock`，真实模型请求串行执行，默认在上次结束后等待 2 秒。Windows 源码测试可以离线运行；真实模型 CLI 应使用 `docker compose exec api ii ...`，避免 Windows 与容器各用一套锁。设计原因见 [共享模型锁决定](decisions/2026-10-01-native-model-lock.md)。

仓库不包含真实语料、数据库、不可变来源快照、个人反馈、会话、模型响应或 `.env`。历史报告中的 `data/reports/...` 是当次本地证据路径和 hash 索引，不是公开下载地址。你可以准备自己的语料，按照 [评测手册](../eval/README.md) 生成新的快照、预测和报告；复现历史分数需要相同语料、参考标注和模型版本。

默认与候选版本统一查 [运行配置](reference/runtime-config.md#提示词选择与版本身份)，实验和历史运行统一查 [评测手册](../eval/README.md#入口分类通用评测候选实验与历史复现)；文件存在不代表启用、部署或通过语义门禁。
