# 2026-10-09 Markdown / SSE 正式服务验收

用户截图中的 `**找面试题**` 原样显示。此前实现和自动化测试只覆盖本地；正式 SSH 服务仍是侧栏四文件发布快照，缺少随后实现的 Markdown 解析器与回答增量模块。公网 workspace 版本为 `cfa4a0dd4853`，四个新运行资源均返回404。通过 SSH 隧道打开正式会话“你是谁？”中的“你可以干啥”一轮，复现了截图中的同一回答。

本次将 Markdown/SSE 增量实际发布到正式服务：替换 api.py、agent/model_gateway.py、agent/presentation.py、web.py、web/index.html、web/assets/app.js、web/assets/query-stream.js 七个文件，新增渲染、逐字展示及官方解析器等八个资源文件。等待活跃问答结束，备份后仅重启API；发布程序核对安装哈希、健康与七个正式服务，失败时自动恢复备份。

没有整包覆盖当前工作区。查询状态、检索实验、提示词、环境、Pi runtime 与现有侧栏等17项受保护文件哈希保持一致；query v13、rerank v14、corpus/index r293保持。备份为 `/home/dylan/services/interview-intelligence/backups/markdown-sse-20261009T042106Z`，发布归档SHA256为 `081c9ff2d3754885e97e9eb1757a528345511e96e0d8f28bf7c4cadef6e88500`。

正式验收结果：

- 公网九个运行资源全部200，引用指纹与实际内容SHA匹配发布包；workspace版本变为 `9377bec16b82`。
- 真实模型公网消息/SSE请求收到231次 `answer_delta`。首段5.750秒到达，立即读取运行状态仍为 `RUNNING`；完成事件18.375秒到达，确认正文在完成前已经送出。
- 浏览器经SSH隧道直接访问正式API。重载原会话后，截图中的六个标题成为真实 `<strong>`，列表与嵌套列表正常渲染；旧回答无需重新生成。
- 浏览器另发真实模型请求，23秒时仍显示停止按钮与部分代码，正文已显示465个字符；完成后显示612个字符。标题、加粗、列表、代码块正常，控制台无错误。
- 七个指定用户服务active，数据库与索引ready。

前一轮本地277项相关测试仍是实现回归证据；本次发布另核对包内Python/JavaScript语法。以上在线验收使用正式模型提供方，没有模拟输出。浏览器验证经SSH隧道；公网传输与资源由认证HTTP独立核对，不声称公网浏览器直连点击通过。未重跑完整V1质量门禁或改变检索质量结论。

原始部署回执、运行事件、截图及运维脚本保存在本地忽略目录 `data/reports/markdown-sse-20261009/`；[公开证据摘要](2026-10-09-markdown-sse.evidence.json)不包含登录信息或模型上下文。已经打开的旧页面需要重载HTML才能取得新脚本。
