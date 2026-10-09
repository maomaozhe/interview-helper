# 开发与运行说明

项目入口与架构见 [README](../README.md)。本页补充完整服务的配置、Windows / WSL 运行和数据边界。

## 环境与配置

- Python 3.12，依赖锁定于 `uv.lock`；开发工具使用 `uv sync --locked --extra dev`。
- 本地构建 Pi 需要 Node ≥22.19，依赖锁定于 `package-lock.json`；构建步骤在 Linux / WSL 中验证。
- 完整服务使用 Docker Compose：FastAPI、worker、PostgreSQL、Elasticsearch、Pi sidecar。

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

`QUERY_PROMPT_VERSION` 默认 `query_agent_v13`，`QUERY_DEADLINE_SECONDS` 默认180秒，`QUERY_MAX_MODEL_CALLS` 默认4，`QUERY_MAX_TOKENS` 默认96,000。Compose与应用默认值保持一致，`.env` 可明确覆盖。真实证据重排会增加延迟；耗时以请求回执为准。

长期偏好由用户明确保存。任务分类核验通过草稿和发布版本管理；不要把机器分类的 SQL 范围核对解释成语义分类正确。

## Docker 与 CLI

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

## 模型调用与数据

API 与 worker 共用 Linux 原生命名卷中的 `/app/runtime/model-call.lock`，真实模型请求串行执行，默认在上次结束后等待 2 秒。Windows 源码测试可以离线运行；真实模型 CLI 应使用 `docker compose exec api ii ...`，避免 Windows 与容器各用一套锁。设计原因见 [共享模型锁决定](decisions/2026-10-01-native-model-lock.md)。

仓库不包含真实语料、数据库、不可变来源快照、个人反馈、会话、模型响应或 `.env`。历史报告中的 `data/reports/...` 是当次本地证据路径和 hash 索引，不是公开下载地址。你可以准备自己的语料，按照 [评测手册](../eval/README.md) 生成新的快照、预测和报告；复现历史分数需要相同语料、参考标注和模型版本。

当前默认抽取为 `extract_question_v2`。其他提示词和 Fast Decision 分支属于显式实验，不能仅因代码存在就当作已通过语义门禁或已启用。
