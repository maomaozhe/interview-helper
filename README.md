# Interview Intelligence V1

将本地 Markdown 面经整理成可追溯的题目事实库，提供完整语料统计、关键词与向量检索、复习状态和本地问答接口。行为依据见 [Implementation Spec](docs/implementation-spec-v1.md)，原始需求见 [存档](docs/requirements/interview-intelligence-v1-source.md)。

## 当前状态

代码已覆盖数据库、导入任务、结构化抽取适配、去重、统计、检索、题目详情、来源、复习记录、API、CLI 和评测脚手架。两篇真实文档试运行得到 1 场面试、8 次提问，并排除 1 篇题目汇总；来源追溯、四种检索和重复导入零新增模型调用均已验证。**这仍不是完成验收的 V1**：190 篇目录正在后台导入，人工 gold 尚未冻结，四路检索和路由也没有真实的 50 条查询评测。`data/gold/v1/extraction_candidates.json` 是待标注清单，不是 gold。

按用户明确选择，模型接口使用火山方舟 `https://ark.cn-beijing.volces.com/api/coding/v3`，文本模型为 `ark-code-latest`，向量模型为 `doubao-embedding-vision`。实际预检已验证严格 JSON Schema 抽取和 1024 维向量。API 与 worker 共用文件锁，模型请求逐个执行，默认在上一次请求结束后等待 2 秒。抽取默认通过流式响应接收完整 JSON，完成和原文校验后才入库；响应中断不发布部分结果。密钥仅存放在被 Git 忽略的 `.env`。

## 启动

环境：Python 3.12、Docker Compose。依赖已锁定在 `uv.lock`。

```powershell
uv sync --locked --no-editable --extra dev
docker compose up -d postgres elasticsearch
uv run --locked --no-editable alembic upgrade head
uv run --locked --no-editable uvicorn interview_intelligence.api:app --host 127.0.0.1 --port 8000
```

本机 Docker 位于 WSL Ubuntu-22.04。已建立持久目录链接 `/opt/interview-intelligence-workspace` 指向 `D:\面经`，可在 PowerShell 执行：

```powershell
wsl -d Ubuntu-22.04 -- bash -lc 'cd /opt/interview-intelligence-workspace && docker compose up -d --build'
```

不要将这一链接放在 `/tmp`，WSL 重启会清理它。Docker API/worker 均只映射到本机回环地址。代码仓库为 [maomaozhe/interview-helper](https://github.com/maomaozhe/interview-helper)。

复制 `.env.example` 为被 Git 忽略的 `.env`，按需填写 `DATABASE_URL`、`ELASTICSEARCH_URL`。真实模型导入还需填写 `MODEL_API_KEY`、`MODEL_BASE_URL`、`MAX_MODEL_CALLS`、`MAX_MODEL_TOKENS`；模型标识和维度可按 `config/models.yaml` 检查。`MODEL_MIN_INTERVAL_SECONDS` 控制请求间隔，`MODEL_LOCK_PATH` 在 API 与 worker 中必须指向同一个共享文件。`MODEL_REQUEST_TIMEOUT_SECONDS` 默认 180 秒，`EXTRACTION_STREAM` 默认开启。

```powershell
uv run --locked --no-editable ii status
uv run --locked --no-editable ii ingest --idempotency-key first-import
uv run --locked --no-editable ii run <run-id>
uv run --locked --no-editable ii retry-failed <run-id> --idempotency-key retry-import-1
uv run --locked --no-editable ii stats --topic-l1 Redis --limit 10
uv run --locked --no-editable ii search "缓存击穿的追问"
uv run --locked --no-editable ii chat "Redis 高频问题有哪些"
```

API 文档在本机 `http://127.0.0.1:8000/docs`。导入是后台队列；重复请求必须复用相同幂等键。`GET /api/health` 区分数据库、索引和模型配置状态。每篇成功导入后同步索引；尚未完成同步时，搜索返回明确错误，SQL 统计仍可用。快照按 `SNAPSHOT_ROOT` 下的 SHA-256 文件名定位，避免 Windows 和容器绝对路径不兼容。

在本机 Docker 部署中，API 与 worker 共用 Linux 原生命名卷中的 `/app/runtime/model-call.lock`，避免 Windows 共享目录偶发拒绝打开锁文件。真实模型 CLI 请通过 `docker compose exec api ii ...` 执行，或直接使用网页/API；不要同时从 Windows 的 CLI 发起真实模型调用。Windows 源码测试仍可正常运行。切换方式及原因见 [共享模型锁决定](docs/decisions/2026-10-01-native-model-lock.md)。

## Web 操作台

打开 `http://localhost:8000/` 即可使用连接真实题库的中文操作台，不需要另启前端服务。

- **题库与检索**：空查询浏览高频题；输入技术问题进行检索。默认混合检索加相关性重排，支持主题、公司、轮次和题型筛选，四种检索方式仍可选择。
- **题目详情**：查看标签、不同原始问法、真实频次、来源快照和原帖；保存复习状态、评分和笔记。
- **面经问答**：受限语义规划理解缩写和口语，再检索、重排并核对详情。可以直接问“oom 的常见问法有哪些”或“线上内存一直涨，怎么定位且不影响业务”，不用补写 JVM 分类；可展开执行过程并跳转到真实原文。
- **导入与状态**：查看本地文件是否生效或已修改、用户提交的批次进度和失败信息；导入所选/新增文件、同步索引或重试失败任务。
- **问题记录**：点击题目旁的旗帜，将偏题、标签、归并或来源问题连同查询上下文保存；记录位于 `data/feedback/`，支持页面查看及 JSON 导出。

数据来自当前已发布语料。检索按相关度排序，浏览高频题按真实提问频次排序。排查信息包含实际执行方式、降级提示、筛选条件、数据版本和请求编号。

界面组件验收可使用 `python tests/web/smoke_server.py` 启动 `http://localhost:8011/` 的隔离测试数据；它不会写入真实语料或本地用户的复习记录。真实端到端验收使用 `http://localhost:8000/` 和真实题库、模型及检索索引。前端范围转换测试使用 `node --test tests/web/test_core.cjs`。

## 数据口径和取舍

一条 `QuestionOccurrence` 代表某次真实面试中的一次提问；`CanonicalQuestion` 代表语义归一后的标准题。两者分离，才能既把“Redis 为什么快”的不同问法合在一起展示，又保留每次出现的公司、轮次、原文和来源。全局频率由 PostgreSQL 对当前有效 occurrence 计数，不能由 RAG 检索的 Top K 样本推断。

固定 taxonomy 将模型的分类结果限制在版本化的 L1/L2 叶子里，避免随意造标签。Embedding 只负责提出去重候选；相近向量仍可能是“为什么快”和“为什么单线程”这类不同问题，因此合并还须经过三分类语义判断。`OBSERVED_FOLLOWUP` 要有同场次的原文证据；`RELATED` 是推断关联，详情页分别展示。

检索在 SQL 中先求满足同一次 occurrence 上全部筛选条件的 canonical ID，再让 BM25 和 dense 共用这一范围。Hybrid 用应用层 RRF；Hybrid + Rerank 只重排同一 Top 50 候选。是否优于单路必须由冻结检索集实测决定，当前没有可报告的性能结论。Agent 只编排六个受限工具，数字和来源由工具产生；个人状态仅在用户明确提出写入时改变。

导入靠文件 hash、处理指纹、不可变快照和原子发布做到重复运行不增加事实；旧版本保留用于回溯。模型调用需要预设次数和 token 上限，并记录可获得的 usage。未知价格或 usage 不会被当作已知的零费用。

## 测试与验收

```powershell
uv run --locked --no-editable --extra dev pytest -q
python -m eval.validate_gold --dataset data/gold/v1
```

第一条运行自动化测试。第二条目前会拒绝草稿 gold，这是预期行为。人工标注至少需冻结 extraction 30 篇、dedup 150 对、retrieval 50 条和 routing 50 条独立 test 样本；之后才能使用 `python -m eval.run` 计算并保存真实指标。算法题号只接受原文明确编号或经题库/人工验证的编号，模糊描述保留为未匹配。

当前明确的限制：长文档语义分段、分阶段缓存、生产级租约恢复和人工纠错流程仍需加强；自动化测试通过不能代替真实语料质量门槛。运行方式和观察到的故障见 [实施状态](docs/implementation-status.md)、[问题与处理记录](md/issue.md)、[演示步骤](docs/demo.md) 和 [坏案例](docs/bad-cases.md)。
