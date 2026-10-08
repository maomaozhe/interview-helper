# 项目展示与发布验证

本次发布将 README、真实界面截图与已部署验证的功能一起整理，避免介绍与远端源码脱节。

## 发布范围

- README 包含项目场景、架构与设计取舍、无凭据启动、完整服务配置、评测口径、代码导览和已知限制。
- 四张效果图来自本地实际页面：[澄清问答](../images/chat-clarification.jpg)、[题库](../images/library.jpg)、[执行过程](../images/query-process.jpg)、[原文溯源](../images/question-detail.jpg)。执行过程截图展示的是已完成请求。
- 后端以已验证部署镜像 `sha256:c348ec72ecc066ceb1990d79998a32ed24e1766551d44db80fb482819d1be323` 为基线；当前发布使用 `query_agent_v10` / `rerank_v10_explicit_object`。
- 后续检索实验保留在本地，未纳入本次发布；个人反馈、真实语料、参考标注、数据库和凭据不随仓库分发。

## 验证结果

对 Git 暂存内容导出独立目录后验证，避免混入工作区中尚未完成的实验。

| 检查 | 结果 |
| --- | --- |
| Python 全量测试 | 477 passed，1 skipped；跳过项仍以测试环境条件为准。 |
| Pi 与前端 Node 测试 | 34 passed；Windows 与 Linux 环境均执行。 |
| Pi 编译 | Linux Node 22.23.0，通过；使用已有部署镜像中的锁定依赖编译本次发布源码。 |
| 依赖与语法 | `uv lock --check`、`npm ci --ignore-scripts --legacy-peer-deps`、`npm run check` 通过。 |
| 无凭据示例 | HTTP 200，可加载页面；修复 Windows 下示例 SQLite 连接未关闭的问题。 |
| 合成评测 | 六层合成报告生成成功；只验证评分流程，不代表真实语义质量。 |
| 文档与凭据 | 32 个相对链接通过；工作区及暂存差异与 4 个已配置凭据值比对，未发现匹配。 |

本机 Windows Node 的 Pi 构建异常退出，完整 Docker 从零构建受 registry 镜像 DNS 阻断；因此不声称这两条路径已通过。README 将 Pi 本地构建指向已验证的 Linux / WSL 环境，GitHub Actions 负责后续干净环境检查。

离线工程验证与语义质量门禁分别记录。历史完整 V1 质量门禁仍为 `BLOCKED`，本次文档及界面验证不改变该结论。
