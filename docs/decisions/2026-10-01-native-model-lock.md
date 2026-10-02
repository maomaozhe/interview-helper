# 共享模型锁采用 Linux 原生卷

真实模型请求统一由 Docker API/worker 发起，保持最大并发 1、请求完成后至少间隔 2 秒。

2026-10-01 真实导入再次出现 `/app/data/model-call.lock` 的 PermissionError。容器目录和文件权限正常，Windows/WSL 共享目录也发生过一次缓存原子重命名拒绝；无法确认具体占用方。对缓存写入及锁文件打开分别增加有限的 PermissionError 重试，持续失败仍拒绝处理。

Compose 的模型锁改放在共享命名卷 `model_runtime`，容器路径 `/app/runtime/model-call.lock`，API 与 worker 使用相同卷和路径。切换前停止所有旧调用方，才允许新路径的调用方启动；不存在两套活动锁。用户语料、快照和验证产物保留在原有目录。

Windows 下的真实模型 CLI 不能与 Docker 服务混用。运行真实模型命令请在 API 容器内执行，或使用网页/API；源码测试可以在 Windows 运行，测试使用独立临时锁文件。

验收包括安装包初始化、API/worker 两个容器的共享挂载核对、跨进程互斥回归和真实导入/检索的串行运行。该决定不改变模型、分类、统计或去重口径。
