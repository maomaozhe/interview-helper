# 面经研习：AI 开发入口

项目把 Markdown 面经转换成可追溯的题库，提供自然语言查题、精确统计和个人复习。主要运行单元是 Python API、后台 worker、Pi Node 服务、PostgreSQL 和 Elasticsearch。

## 从哪里开始

从 [知识库导航](docs/README.md#日常开发入口) 选本次任务，已有明确模块时直接查 [代码与测试](docs/reference/module-map.md)。排障先按 [症状](docs/retrospectives/README.md#按症状快速查找) 找已有 PIT。

**找到相关契约、代码入口和首选测试后，转源码核查。** 只有跨模块行为不清时补读架构/数据流；改模型或默认值时查 [运行配置](docs/reference/runtime-config.md)，判断项目现状时才查 [状态页](docs/status/current.md)。历史 `plans/`、`verification/`、`vendor/` 和 `data/reports/` 按需追证，不逐篇通读。

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

非简单任务首次实现前，在已有记录中写目标、事实/假设、区分检查及方案/验收；失败后先留错误、环境、结果和预算再重试，不能放宽条件求通过。完成或交接时补结果、未覆盖项和下一步。详细方法与短模板只维护在 [Agent 工作方式](docs/reference/agent-workflow.md)；小改动可在任务回复中记明依据，无需另建计划。

## 验证入口

在仓库根运行，先选择与变更相关的测试；依赖和平台要求见 [开发说明](docs/development.md)。Python 要求 3.12，Node 要求见 `package.json`。已有环境可用 `.venv\Scripts\python.exe` 替代 Windows 上的 `uv run --locked --extra dev python`。

```bash
# 文档：默认检查当前入口；也可传入本次修改的 Markdown 路径
uv run --locked --extra dev python scripts/check-docs.py

# Python：把路径替换为本次模块涉及的测试
uv run --locked --extra dev python -m pytest -q tests/unit/test_harness.py

# Pi 构建在 Linux / WSL 验证；构建后按修改范围选 Node 测试
npm run build:pi
npm run check
node --test tests/node/pi-runtime.test.mjs
```

- 仅改文档时，运行上述结构检查，人工核对正文、图和 diff；不需要运行模型或整套业务测试。结构检查不能证明语义正确。
- 需要完整离线工程回归时运行 `uv run --locked --extra dev python -m pytest -q`；私有输入与服务测试的前提见 [评测手册](eval/README.md#1-先验证框架)。
- 离线测试或合成评测通过不代表真实语义质量通过；实际服务/模型评测依 [评测手册](eval/README.md) 的数据、隔离账号和预算约束进行。
- 真实模型调用方应复用部署环境的共享模型锁；不同平台各开一套锁不能保证互斥。
- 测试报告写清本次执行的命令、结果、跳过项；未执行的测试只作导航，不写成已通过。

## 同步维护知识

改变契约、模块边界、数据模型、默认配置或运行路径时，按 [页面映射](docs/README.md#维护方式) 只更新受影响页的结论、`sources` 和核对日期。纯内部实现不要求改全部文档。

优先更新已有页，不为小改动同时新增计划、验收和复盘。清理前查入链及代码读取/哈希依赖；独有决策、历史失败、验收证据和未解决项必须保留。记录归并方法见 [工作方式](docs/reference/agent-workflow.md#将有复用价值的结果沉淀)。
