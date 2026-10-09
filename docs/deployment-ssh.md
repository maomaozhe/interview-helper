# SSH 服务器部署

面经助手可以在没有系统 Docker 权限的 SSH 账号下运行。使用独立目录、用户级 systemd 和回环端口，PostgreSQL / Elasticsearch / API / Pi / worker 分别管理；Web 入口由 Caddy 验证访问密码。

2026-10-08 的部署迁移了本机正在运行的应用和题库。工作区另有检索实验，因此导出的是容器内实际安装的代码与资源，而不是重新构建尚未完成评测的工作区。私有部署回执、备份和登录信息位于本地被忽略的 `data/reports/ssh-deployment-20261008/`，不提交凭据或个人数据。

## 当前发布版本

2026-10-09 当前 SSH 使用 query v13 / context v4 / tool policy v2 / `rerank_v14_design_unit_scope_partial`，规划调用配置为4、请求总调用上限为6、截止180秒。工程系统设计修复见文末对应章节。随后发布4文件侧栏增量，支持拖动、隐藏和移动抽屉；公网资源哈希、正式页面交互及七个服务核验通过，后端版本与语料 r293/index293 保持。详见 [侧栏验收](verification/2026-10-09-sidebar.md)。

## 上一轮发布记录：v12-r2

2026-10-08 已发布会话目标修复 v12-r2：query v12 / context v4 / tool policy v2 / rerank v10。在上一轮实际验证的 SSH r10 基线上仅叠加6个文件，保留本地 r16 检索实验与 SSH 发布的独立归属。当前支持裸总量继承讨论范围、纠正范围保留 COUNT、旧会话目标兼容及未指定 N 的完整分页；模型请求预算为96,000，仍限制每轮3次规划。

公网消息 / SSE 真实 Pi 语义检查12/12通过；页面连续三句和刷新历史另经SSH隧道18085→18082验证。部署独立核对7个正式服务、6个发布文件hash和3个r10检索基线hash均通过。发布备份为 `/home/dylan/services/interview-intelligence/backups/conversation-focus-v12-20261008-200731`；原v11发布和首个v12候选失败证据保留。详见 [会话目标与范围修复](verification/2026-10-08-conversation-focus.md)。以下保留首次迁移与日常管理步骤；本次未重新恢复题库，也未重跑完整 V1 质量门禁。

## 初次部署

部署目录默认是 `$HOME/services/interview-intelligence`；可用 `SERVICE_DIR` / `--service-dir` 覆盖。要求 Linux x86_64、用户 systemd、已安装的 uv 和 Caddy，以及编译 PostgreSQL 所需的 gcc、make、zlib 开发文件。`loginctl show-user "$USER" -p Linger` 应显示 `yes`，确保退出 SSH 后和开机时继续运行。

1. 在本地运行 Docker 的 Linux / WSL 环境，从仓库根目录导出运行版本，使用新的输出目录：

   ```sh
   bash scripts/export-active-deployment.sh data/reports/ssh-deployment-new/release
   ```

   导出包括 PostgreSQL 一致性备份、完整搜索向量、面经、来源快照、反馈，以及应用、Pi 和 Python 依赖。复制二进制依赖的路径要求两端使用相同架构和 Python 3.12；安装脚本会实际检查导入。

2. 上传运行环境脚本，在服务器执行：

   ```sh
   bash deploy-ssh-runtime.sh
   ```

   安装固定的 PostgreSQL 16.9、Elasticsearch 8.19.0、Python 3.12.10、Node 22.23.0。下载的 PostgreSQL、Elasticsearch、Node 归档均验证官方校验和。安装日志在部署目录的 `logs/bootstrap-*.log`。

3. 将导出的 `release/` 和以下脚本上传至部署目录：`deploy-ssh-services.py`、`restore-ssh-state.py`、`deploy-ssh-proxy.py`。在 `release/` 执行 `sha256sum --check SHA256SUMS`，并将目录权限设为 700、私有文件权限设为 600。Windows OpenSSH 的默认 SFTP 在本次传输中中断，改用保留 known_hosts 校验的可续传 SFTP 完成迁移；普通上传也可使用 `scp -O -o IPQoS=none -o ServerAliveInterval=15`。所有归档均须校验。

4. 使用 uv 安装的 Python 3.12 执行初始化：

   ```sh
   "$HOME/.local/bin/python3.12" deploy-ssh-services.py
   systemctl --user start interview-intelligence-postgres interview-intelligence-elasticsearch
   "$HOME/services/interview-intelligence/.venv/bin/python" restore-ssh-state.py
   ```

   自定义部署目录时替换最后一条中的路径。初始化脚本拒绝覆盖已有数据库；恢复脚本校验每份来源快照、事实数量、向量数量与运行版本，然后启动并启用六个服务。不要重复运行初次恢复来更新已有服务。

5. 默认 Web 只监听 `127.0.0.1:8092`，可经 SSH 隧道访问。若服务器已有可管理的 Caddy HTTPS 入口，初始化时提供独立域名和本机管理 API：

   ```sh
   "$HOME/.local/bin/python3.12" deploy-ssh-services.py \
     --public-hostname YOUR_HOSTNAME \
     --caddy-admin-url http://127.0.0.1:YOUR_ADMIN_PORT
   # 数据恢复完成后启用独立域名路由：
   systemctl --user enable --now interview-intelligence-proxy
   ```

   这与第 4 步的初始化命令二选一。代理服务仅增加带独立 ID 的域名路由，保留其他网站；共享 Caddy 重启或重新加载配置后会恢复本应用路由。域名需解析到服务器，现有 Caddy 负责证书。登录信息保存在部署目录的 `access.private.json`；密码不通过命令行参数传递。

## 日常管理

```sh
systemctl --user status 'interview-intelligence-*.service'
journalctl --user -u interview-intelligence-api -n 80 --no-pager
systemctl --user restart interview-intelligence-api interview-intelligence-pi interview-intelligence-worker
curl -fsS http://127.0.0.1:18082/api/health
```

| 服务 | 本机端口 | 持久状态 |
| --- | --- | --- |
| PostgreSQL | 15432 | `state/postgres/` |
| Elasticsearch | 19200 / 19300 | `state/elasticsearch/` |
| API | 18082 | `data/snapshots/`、`data/feedback/` |
| Pi | 18787 | 数据由 API 持久化 |
| 访问代理 | 8092 | `Caddyfile`、`access.private.json` |

模型配置在 `app/.env`，内部 Pi 配置在 `pi.env`；两者权限均为 600。API 和 worker 共用 `state/run/model-call.lock`，保留本机的模型串行与预算设置。Fast Decision 保持关闭；迁移保留既有 `KNOWN` 标签政策，不改变分类质量口径。

备份数据库时使用部署目录的 `pgpass`，避免将密码放入命令行：

```sh
PGPASSFILE="$HOME/services/interview-intelligence/pgpass" \
  "$HOME/services/interview-intelligence/runtime/postgresql/bin/pg_dump" \
  -h 127.0.0.1 -p 15432 -U interview -d interview_intelligence \
  --format=custom --no-owner --no-acl > interview-backup.dump
```

同时备份 `md/`、`data/snapshots/`、`data/feedback/` 和私有配置。`release/` 保存本次可复核的原始部署归档；`deployment-receipt.json` 保存恢复验证结果。

## 2026-10-09 工程系统设计题修复

线上已采用 `query_agent_v13`（prompt SHA256 `41c2a7ef8d72e388bdc814c34d1e4db838cc99f595baf0e49d2566cbc190a82f`）和 `rerank_v14_design_unit_scope_partial`。发布以此前线上包为基线，仅叠加九个 Python/prompt 文件及一个 Pi runtime 文件；完整十文件 SHA 清单见[验收证据](verification/2026-10-08-engineering-design.evidence.json)。本地其他重排实验没有整包发布。

API、worker、Pi 三个服务已重启并验证 active，API 与 Pi 均通过健康检查，十个文件和受保护的 harness 均通过 SHA 校验。`app/.env` 使用 `QUERY_PROMPT_VERSION=query_agent_v13`、`QUERY_MAX_MODEL_CALLS=4`、`QUERY_DEADLINE_SECONDS=180`；其中 4 是规划调用配置，既有请求总调用上限仍为 6。数据库及索引仍为 r293。

本次原文件、文件不存在状态、完整环境及 rollback manifest 的备份目录为 `/home/dylan/services/interview-intelligence/backups/engineering-design-v13-20261008T162535Z`。独立回滚先核验备份路径、文件 SHA 和服务白名单，再恢复；成功条件包括原文件和环境逐字一致、三个服务 active、API 与 Pi 健康。私有运维脚本、部署 receipt 和公开网址回放原始记录保存在本地忽略目录 `data/reports/engineering-design-20261008/`。完整评测和限制见[验收报告](verification/2026-10-08-engineering-design.md)。
