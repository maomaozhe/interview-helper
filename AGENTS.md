# 面经研习：AI 开发入口

项目把 Markdown 面经转换成可追溯的题库，提供自然语言查题、精确统计和个人复习。主要运行单元是 Python API、后台 worker、Pi Node 服务、PostgreSQL 和 Elasticsearch。

## 从哪里开始

1. 先读 [项目知识库导航](docs/README.md)，按本次任务选择资料。
2. 理解整体时读 [系统架构](docs/architecture/system.md)、[数据模型](docs/architecture/data-model.md)。
3. 修改导入读 [入库数据流](docs/architecture/ingestion-flow.md)；修改问答读 [查询数据流](docs/architecture/query-flow.md)。
4. 按 [模块与测试导航](docs/reference/module-map.md) 找实际符号和验证入口。
5. 涉及模型、默认值或版本时核对 [运行配置](docs/reference/runtime-config.md)。
6. 判断哪些能力已实现、哪些仍有缺口时读 [状态摘要](docs/status/current.md)。
7. 排查相似故障或回顾踩坑时先查 [踩坑与复盘索引](docs/retrospectives/README.md)，按症状定位，再读原验收证据。

历史 `plans/` 和 `verification/` 按需查阅。不要默认从头读取全部验收流水或 `vendor/`、`data/reports/`；它们的用途与当前架构说明不同。

## 开发时保持的约束

- 原始提问 `QuestionOccurrence` 与归并题 `CanonicalQuestion` 分开；标准题数量不是提问次数。
- 有效事实来自当前 active build、INCLUDED 文档和 analytics eligible 面试；组合筛选应在同一 occurrence/面试上满足。
- PostgreSQL 是结构化事实与状态来源，ES 是检索投影。保持语料/索引版本检查。
- SQL 全范围列表/计数与 SEARCH 候选结果分开；候选池内频次排序不能称为全库高频榜。
- 原文 hash、引用 span、对象/任务证据需要校验；有引用不代表语义判断必然正确。
- 用户显式条件、继承状态、模型推断和软召回偏好有不同来源，重查时不能混为硬约束。
- 模型计划必须经过主机契约、权限和预算检查；复习写入保持当前请求授权、范围及幂等。
- SSE 是展示通道，完成状态和恢复依据是持久化回执；不要把进程内活跃 run 当作分布式任务队列。
- 功能已实现、默认启用、某环境已部署、语义质量已通过，必须分别说明。

细节、反例与测试见 [业务不变量](docs/reference/invariants.md)。

## 工作区与证据

- 开始修改前检查 `git status --short`，保留已有未提交工作，只改本次任务涉及的内容。
- 当前行为核对源码及装配入口；目标行为核对有效需求/决策；线上版本核对对应环境的发布或运行证据。出现冲突时明确指出，不能互相替代。
- `md/` 原文、`.env`、数据库、快照和 `data/reports/` 中的原始回执不是公开项目知识。文档链接到必要证据，不复制凭据、个人会话或私有全文。
- `vendor/pi` 是固定上游源码；涉及修改时先读 [上游来源说明](vendor/PI-SOURCE.md) 和该目录自身约定。
- 提示词版本参与缓存、实验和发布记录。不能仅因出现更高版本文件就替换默认，也不要为了整理目录删除历史版本。

## 先记录、核查，再决策

- 非简单任务在首次实现前，先在已有计划或问题条目留下目标/范围、已知事实与证据、待验证假设、下一步区分检查。没有合适记录时建短计划；小改动合入现有记录，不机械新建长文。
- 出现失败时，在下一次尝试前保存关键错误、环境/版本、实际结果与调用/预算记录；事实、推断和未知分开，不能无依据重复重试或先改检查条件求通过。
- 实施前记录选定方案、简要理由、主要代价及验收条件；优先从真实入口和低成本检查收敛原因，按根因查已有 PIT。
- 实施后写明验证范围、结果、剩余问题和下一步；中断/交接先更新检查点。有复用价值的结果再沉淀专题、索引与防复发约束。

具体方法和可追加的短记录见 [Agent 工作方式](docs/reference/agent-workflow.md)。保留可复核的依据与简要决策理由即可。

## 验证入口

在仓库根运行，先选择与变更相关的测试；依赖和平台要求见 [开发说明](docs/development.md)。Python 要求 3.12，Node 要求见 `package.json`。已有环境可用 `.venv\Scripts\python.exe` 替代 Windows 上的 `uv run --locked --extra dev python`。

```bash
# Python：把路径替换为本次模块涉及的测试
uv run --locked --extra dev python -m pytest -q tests/unit/test_harness.py

# Pi 构建在 Linux / WSL 验证；构建后按修改范围选 Node 测试
npm run build:pi
npm run check
node --test tests/node/pi-runtime.test.mjs

# Python 完整离线工程回归（需要时）
uv run --locked --extra dev python -m pytest -q
```

- 仅改文档时，核对相对链接、元数据、源码符号、Mermaid 和改动范围即可，不需要因此运行模型或整套业务测试。
- 离线测试或合成评测通过不代表真实语义质量通过；实际服务/模型评测依 [评测手册](eval/README.md) 的数据、隔离账号和预算约束进行。
- 真实模型调用方应复用部署环境的共享模型锁；不同平台各开一套锁不能保证互斥。
- 测试报告写清本次执行的命令、结果、跳过项；未执行的测试只作导航，不写成已通过。

## 同步维护知识

改业务契约、模块边界、数据模型、默认配置或运行路径时，同步更新对应知识页的结论、`sources` 和核对日期。纯内部实现变化不要求改全部文档。

当前知识页保持简洁；设计理由放 `docs/decisions/`，计划放 `docs/plans/`，日期验收放 `docs/verification/`。更新规则和页面映射见 [知识库维护方式](docs/README.md#维护方式)。

有复用价值的故障处理提炼到 `docs/retrospectives/`，沿用 [最小复盘格式](docs/retrospectives/README.md#新增与维护) 记录原因、实际失败尝试、修法、验证范围和防复发措施。相同根因优先更新已有专题及索引；保留原日志，不把局部修复或候选实验写成全面解决。微小修正无需机械新增复盘。

优先更新已有页面；工作完成后把有效规则并回对应当前页，删除已被完整覆盖的施工清单和重复总结。新建文档须有独立问题、读者或证据用途，不为一次小改动同时生成计划、验收、复盘三份材料。清理前检查入链及代码读取/哈希依赖；独有决策、原始失败证据和未解决问题继续保留。
