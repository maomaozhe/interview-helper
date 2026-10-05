# 开源 System One 部署调研与实测

日期：2026-10-04。结论：可以自托管 Jev 风格的类型化决策 API；当前已在 SSH 服务器部署 Laya Multilingual，完成 GPU 推理、鉴权和中文查询评测。但本项目的零样本路由准确率不足，正式查询继续使用 Pi，SQL 负责确定性执行。

本页保留首轮调研和 MVP 实测口径。2026-10-05 已补齐查询功能、偏好与恢复，并将模型进程交给用户 systemd 守护；最新数据、测试和部署边界见 [spec 实施验收](2026-10-05-query-spec-verification.md)。下文首轮 134 道算法题、250 项 Python 测试与 nohup 状态按当日记录理解。

用户提供的 [知乎文章](https://zhuanlan.zhihu.com/p/2086362435980735746) 未能读取正文，本报告不推断其中的观点。技术信息来自下列项目源码和模型卡。

## 方案比较

| 项目 | 实现方式 | Linux/NVIDIA 部署 | 本项目判断 |
| --- | --- | --- | --- |
| [Laya](https://github.com/NandhaKishorM/laya) / [Multilingual 权重](https://huggingface.co/convaiinnovations/laya-multilingual) | mmBERT 双向编码器 + 类型化决策头，约 322M 参数，choice/score/noul | PyTorch/Transformers；公开 Apache-2.0 权重；现有服务器可运行 | 已部署实测；中文业务零样本准确率不足，保留评测与影子模式 |
| [local-jev](https://github.com/amithgc/local-jev) | 小型 Qwen 的候选 logits 评分，另有 NLI 后端；兼容 Jev API | Python ≥3.10；支持 CUDA、CPU；默认准确模型 Qwen3.5-4B 约 9.32 GB 权重 | 下一阶段值得评测；更接近通用指令模型，资源和延迟高于 Laya |
| [OpenSourceJev](https://github.com/sabeel111/OpenSourceJev) | llama.cpp C API，候选 logits / 多 token 评分；Qwen GGUF | CUDA/Linux；1.7B Q8 约 1.71 GB，4B Q4 约 2.55 GB | 低磁盘占用的替代实验方案；需固定 llama.cpp ABI、量化版本并测试中文 |
| [LiteVar/system-one](https://github.com/LiteVar/system-one) | Rust 原生 Laya tokenizer/决策头 + llama.cpp，Jev 风格 API | 有跨平台构建；上游实际端到端验证主要是 macOS arm64 | 可优化分发；换运行时不能弥补相同 Laya 权重的任务准确率 |
| [mpuig/system-one](https://github.com/mpuig/system-one) | MLX 决策模型 | 主要面向 Apple Silicon | 不优先用于本服务器 |

这些都是独立开源项目，不能写成“部署了官方 Jev 权重”。本次没有取得官方 Jev 自托管权重或 TypeSafe API key。

上游性能仅作选型线索，不能当作本项目成绩。local-jev 作者报告其默认模型在 231 个公开 JevBench 项目上为 80.5%；其设备为 M4 Max。OpenSourceJev 当前 README 的 fast/accuracy profile 报告中位延迟约 312/436 ms，并声明属于研究项目。这些均未在本服务器复现，且不是中文面经路由评测。

## 已部署的模型

服务器使用现有 SSH 别名 `server-101-47-18-72`，用户 `dylan`。没有使用聊天中的密码，也没有改动服务器已有模型服务。

| 项目 | 固定值 / 实测状态 |
| --- | --- |
| 管理目录 | `/home/dylan/services/interview-jev` |
| GPU | NVIDIA L20，约 46 GiB 显存 |
| 服务地址 | 服务器 `127.0.0.1:18788`，仅私有监听 |
| API | `POST /v1/systemone`，Bearer token 鉴权 |
| 健康检查 | `GET /health`，实际模型加载与预热后才 ready |
| 源码版本 | Laya 0.3.26，commit `2e4d9c87e8b1621deb344eac7de5c7258f32f849` |
| 源码归档 SHA256 | `b68ffb2a90ddeab3e6e0f46d20816cb624e6bff8f33f717b165bdfaf1a51bd74` |
| 模型 | `convaiinnovations/laya-multilingual` |
| 权重 revision | `e4e9ddf21a7b1903b7acffd8814ad4307bf63a67`，上游 reviewed pin |
| model.safetensors SHA256 | `9d628fd971b700382ac6f65920a86f149777b2e748e0c955fb3b19695aa8f204`，已核对 |
| 下载占用 | 约 647 MiB，包括 tokenizer/config；不下载整套训练资源 |
| 执行后端 | eager，CUDA；没有启用 compile/TileLang |
| 依赖 | 只读复用已有 Python 3.11.16 / Torch 2.13.0+cu130 / Transformers 5.12.1 环境，没有修改其安装包 |
| GPU 增量占用 | 初步约 1.9 GiB；这是进程启动前后差值，非隔离压力测试 |

只下载固定 revision 的 `model.safetensors`、配置和 tokenizer/encoder 文件；权重通过 safetensors 加载。推理进程固定模型，禁止请求触发任意模型下载。完成下载后设置离线模式。

入口代码位于 `services/jev-gateway/laya_server.py`。服务器只提供 choice 决策；超过 16 个问题、过大请求、未知模型均被拒绝。状态被截断或候选选项丢失时返回错误，由上层退回 Pi，不使用部分上下文做正式路由。

## 中文业务评测

手工编写 20 个业务用例，覆盖算法 / 工程代码 / SQL、阿拉伯与中文数量、力扣题号与年份、三个月窗口、统计、语义检索、写操作、下一页、继承与重置、否定、重要性和薄弱度。

每例请求包含同一套 10 个独立 choice 问题；按该例预先标注的关键字段计分，共检查 65 个字段。不是所有字段都逐一人工标注，因此成绩只能代表这份小型探针。

| 指标 | 本服务器实测 |
| --- | --- |
| 用例全部关键字段正确 | **1 / 20** |
| 关键字段正确 | **29 / 65，44.6%** |
| 错误字段的 confidence ≥0.85 | 0 |
| 所有字段 confidence ≥0.85 的用例 | 0 / 20 |
| 一组 10 个决策的推理中位耗时 | **19.415 ms** |
| 本轮推理 p95（nearest rank） | **20.32 ms** |
| 本轮最大推理耗时 | **21.15 ms** |
| 领域校准 | 未进行 |

计时来自服务器适配器，包含 tokenize + GPU forward + decode；不包含 SSH、项目的模型排队或页面请求。服务已预热，20 个样本不能用来宣称生产 p95。结果文件见 `evals/query-routing/results/laya-l20-20261004.json`，用例见 `evals/query-routing/cases.json`。

首次集成把 65 个公司选项放在一个问题中，Laya 的固定问题头预算导致选项合并。适配器检测并拒绝了这批请求，不能把它算作模型分类成绩。随后把 Laya 的公司问题改为 NONE / INHERIT / FALLBACK；新公司实体交给 Pi 解析，再进行上表评测。没有用静默删掉公司选项的方式绕过问题。

这次结果支持两条决定：速度优势存在；领域零样本能力不能承担正式路由。调 confidence 阈值只会改变接受比例，不能把错误答案变正确。需要任务训练、更合适的基础模型以及独立测试集，才考虑启用快路径。

## 项目接入方式

正式查询链路：

```text
自然语言 + 显式筛选 + PostgreSQL 会话状态
    → Pi Agent（模型只规划一个 QuerySpec / 领域工具调用）
    → 主机校验参数、范围、版本、预算
    → SQL 列表 / 统计 / 分页，或混合检索
    → 完整事实结果 + 来源 + 计时
```

Jev 风格 provider 是可替换的决策适配器。`JEV_PROVIDER=laya` 会选择适合短编码器的输入；`JEV_DECISION_MODE=shadow` 只记录判断，不决定正式结果。当前默认 `JEV_DECISION_ENABLED=false`，避免每次查询多一次排队和调用。未校准的 Laya 即便配置 active，也不能绕过回退检查。

已实现 SQL 页面的下一页和显式筛选无需模型；没有语义重排。自由文本由模型判断路由，复杂查询保留混合检索，默认不重排。模型不能执行任意 SQL、文件、shell 或网络工具。

## 查询 MVP 的最终验证

开发栈实际运行 PostgreSQL 16.9、Elasticsearch 8.19、API、worker 和固定 Pi sidecar。172 个生效来源、185 场面试包含 2,771 次提问和 2,452 道归并题；语料与索引均为 revision 293。

所有 2,771 次提问已完成独立任务标签处理：2,703 次有明确标签，68 次因置信度不足保留 UNKNOWN，待处理为 0。处理完成不等于人工标注正确；类别列表明确提示未知数量。当前标签下，有 134 道唯一算法题、18 道要求代码作答的工程实现题。

真实模型 / PostgreSQL / ES 接口验收脚本为 `scripts/verify-query-mvp.py`。最终五组检查全部通过：

1. “前40个频率最高的算法题”经 Pi 规划为 ALGORITHM、frequency、Top N 40，完整返回 40 道；题目顺序与独立 PostgreSQL 聚合查询一致。排名基于完整匹配范围，不从检索候选中取前 40。
2. 相同 request ID 重放返回原回执，未新增模型调用；新话题“手撕代码有哪些题目”重置先前的 Top N，选择 ENGINEERING + CODE，返回 18 道。后续“只看二面”保留工程代码范围，返回 4 道。
3. 结构化 SQL 请求连续取 20 + 20 道，结果等于全局排名前 40；不调用模型。分页同步 PG 当前页，“查看第一题来源”指向第二页第一题，详情读取后“下一页”继续原列表。
4. 复杂语义问句实际执行 HYBRID，使用真实向量与 ES，没有降级。检索候选明确区别于完整类别列表。
5. 以上请求保留计划、来源、分页、阶段计时与幂等证据。本地详细报告在被忽略的 `data/reports/query-mvp-smoke.json`，不把运行语料提交到仓库。

最终这一轮的延迟样本如下；它们不是吞吐量、P50 或 P95 测试，也不能与只测 GPU forward 的 Laya 时间直接比较。

| 请求 | HTTP 完成耗时 | 可核对的阶段 |
| --- | --- | --- |
| SQL 算法 Top 40 | 111 ms | 无模型 |
| 自然语言算法 Top 40 | 6,327 ms | Pi 决策 6,231 ms，其中 provider 5,924 ms；SQL 工具 57 ms |
| SQL 工程代码列表 | 76 ms | 无模型 |
| 自然语言工程代码列表 | 8,163 ms | provider 6,179 ms，串行间隔等待 1,770 ms；SQL 工具 69 ms |
| SQL 会话分页第一页 / 第二页 | 127 / 125 ms | 无模型；工具 64 / 62 ms |
| 自然语言语义检索 | 11,655 ms | 决策 6,430 ms；检索工具 5,183 ms；整轮 provider / 排队计时包含工具内模型调用 |

这些样本说明类别 SQL 执行已经很轻，剩余自然语言等待主要来自规划模型和全局串行间隔。直接选择类别、排序与翻页可以省掉这些模型调用；用 Laya 接管中文自然语言路由则仍需先解决准确率。

真实验收曾发现模型只返回工具名，缺失筛选参数被默认值补成全库查询。当前 `query_agent_v2` 要求模型显式提供 action、完整 filters、sort、top_n、page_size 及该动作必需参数；缺字段先受限修复，校验成功前不执行工具。语义工具的内部 HTTP 回调恢复请求上下文，Embedding / Rerank 与规划调用使用同一 trace 和预算。Windows 模型锁的首字节初始化已移入锁内，修复首次竞争时的写入错误。

最终自动化回归为 Python **250 通过、1 跳过**；跳过项为需单独启用的真实 ES 测试。真实服务烟测已另行验证本机 ES 路径。Node / 前端共 **13 项通过**，其中 4 项使用真实固定 Pi 核心。真实复习写入、生产守护 / 压力测试、完整人工金标和长期偏好仍未完成，不宣布全部 spec 或生产发布完成。

最新部署的页面实际显示自然语言算法 Top 40 的全部 40 行、匹配总数 134 和 UNKNOWN 提示；工程代码查询显示 18 行及 ENGINEERING / CODE 标签。直接 SQL 算法分页显示 20 + 20 行且无重复，连续切换工程方向与代码形式后成功返回 18 行，诊断中的 `model_attempts=0`。此次浏览器 SQL 页面的完成时间为 122–137 ms，仍是少量功能样本。页面取消旧请求后，新的显式 SQL 范围读取服务器最新会话版本，并对正在收尾的旧请求作有界重试，防止快速修改筛选导致过期版本冲突。

## 部署与复现

本地开发栈：

```sh
npm ci --ignore-scripts --legacy-peer-deps
npm run build:pi
docker compose build
docker compose up -d
```

本机 WSL 的 Docker 构建默认网络出现 DNS 失败，验证时使用 `docker build --network=host` 分别构建 Python 和 Pi 镜像；没有改动全局 DNS 或数据库数据。

服务器模型服务：先把本目录的 `download_laya.py`、`laya_server.py`、`deploy.sh` 上传到管理目录，拉取并校验固定 Laya 源码。私有 `service.env` 只存 HOST/PORT/JEV_PROXY_TOKEN；权限 600。

```sh
/home/dylan/h3/.venv/bin/python download_laya.py
PYTHON_BIN=/home/dylan/h3/.venv/bin/python SERVER_SCRIPT=laya_server.py sh deploy.sh
curl -fsS http://127.0.0.1:18788/health
```

`deploy.sh` 是首轮开发启动方式，仅重启管理目录 PID 文件所指的自身进程，重启前校验 `/proc/PID/cmdline`。2026-10-05 已由 `install-user-service.sh` 接管为 `interview-system-one.service`，用户服务 enabled / active / ready，linger 原已开启；后续使用用户 systemd 管理，不要与 nohup 同时启动。尚未重启整台服务器验证，也未完成生产负载验收。

本地 Docker 通过 SSH 私有隧道访问：

```sh
sh services/jev-gateway/start-tunnel-wsl.sh
```

该脚本固定远端 loopback 目标，只在 Docker bridge `172.17.0.1:18789` 转发。复用已授权的 SSH key，临时副本权限 600，连接建立后立即移除。控制 socket 可用于停止隧道；没有开放服务器公网端口。WSL 重启或网络断开后按脚本重连。

影子评测的配置示例（token 由私有配置提供，不写进此文档）：

```dotenv
JEV_DECISION_ENABLED=true
JEV_PROVIDER=laya
JEV_DECISION_MODE=shadow
JEV_BASE_URL=http://host.docker.internal:18789/v1
JEV_MODEL=convaiinnovations/laya-multilingual
JEV_CONFIDENCE_THRESHOLD=0.85
```

在项目环境中执行 `scripts/export-decision-eval.py` 生成请求，然后在服务器运行 `evaluate.py`；报告不含鉴权 token。数据分类回填通过 `ii annotate-tasks --dry-run` 预览，正式执行受调用 / token 上限约束，使用与 API/worker 相同的模型锁；已经提交的批次不会重复处理。

## 后续选择与简历表述

近期优先验证 local-jev 的 Qwen logits 后端或 OpenSourceJev 的 4B GGUF，并沿用相同业务测试集；不能拿英文 JevBench 成绩替代中文路由成绩。先分别测 action、task focus、实体和数量提取，再判断拆分决策是否值得增加调用。

若继续训练 Laya：用人工审核的 QuerySpec 建立训练 / 校准 / 测试三个独立集合，避免同一模板改数字跨集合；重点增加“手撕代码 vs 算法”、否定、新话题重置、年份与题号。需要同时记录接受覆盖率和接受样本错误率。当前 20 个例子不足以同时训练和证明泛化。

已完成的简历事实可以写：引入固定版本 Pi 源码，构建模型规划与可信 SQL 工具分离的查询 Agent，提供持久会话、全局 Top N、版本绑定分页和调用计时；在 L20 自托管并评测开源非生成式决策模型，设置质量回退机制。不要写“Jev 毫秒级生产路由”“路由准确率 100%”或把上游性能当作自己的结果。
