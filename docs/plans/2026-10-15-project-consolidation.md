# 项目收口：版本、入口与编排职责

日期：2026-10-15。目标：维护一处当前状态，核对并按主题提交现有工作，再逐项消除重复规则、拆分 API / QueryService 的独立职责。

## 范围与依据

- 起点为 `ef6c117`；工作区原有 208 个变更路径（69 修改、6 删除、133 未跟踪），涉及功能、候选评测和知识库整理。保留全部既有工作，逐项分类；不推送、不部署、不调用真实模型。
- 当前能力与质量依据 `docs/status/current.md`、`docs/reference/runtime-config.md` 及其引用的日期验收。历史部署不能证明当前服务状态，候选版本不能替换默认。
- 已确认维护热点：API 装配/执行/路由集中，QueryService 执行与呈现集中；analytics 的 importance/gap 计算重复。Python/JS 契约另有重复，但本轮先选可独立验证的职责。

## 方案与验收

1. 将 current 页改为能力、默认版本、部署证据、质量结论、下一步的一张状态表；以普通 Compose 的 API + Pi + worker + PostgreSQL + Elasticsearch 为唯一推荐完整运行方式。其他入口明确用途，保留历史复现与兼容行为。
2. 核对现有差异、依赖和敏感内容，先建立可追溯的主题提交。记录测试实跑结果与跳过条件，不将已有日期报告改写成本次验收。
3. 基线成立后，先统一排名公式，再分别提取 API 与 QueryService 的一个独立职责；外部接口、筛选、权限、预算、持久化与幂等语义保持不变，每步运行相关回归并单独提交。

代价：继续保留尚未证明可以删除的兼容入口与实验文件；本轮不处理分布式任务接管、逐文档索引策略或语义质量门禁。验收为文档链接/来源有效、主题提交可定位、相关离线测试通过，未覆盖项明确。

## 执行检查点

- 已完成：只读现状核查；建立收口分支。下一步：并行整理文档、审阅原有变更、运行离线基线，确定主题边界。
- 基线：Windows Python 3.12.1，`.venv\\Scripts\\python.exe -m pytest -q`：1315 passed、8 failed、3 skipped（205.94 秒），完整日志保存在本地 `.tmp/project-consolidation/python-baseline.log`。失败发生在拆分前：legacy Agent 的准备流程 1 项、紧凑上下文 legacy focus 来源标识 5 项、SEARCH 测试 2 项被 `QUERY_RELEVANCE_UNAVAILABLE` 拒绝。先区分实现回归与测试替身缺少核验元数据；不得降低成功查询契约或删除断言。
- Node 22.23.2：`npm run check` 与 `node --test tests/node/*.test.mjs tests/web/test_*.cjs` 已通过，后者 91 项。日志在同一本地目录。
- WSL 环境检查发现 `node` 不在 Linux PATH，`npm` 指向 Windows 挂载路径；尚未执行 Pi Linux 构建。已记录在本地 `setup-notes.txt`，下一步使用现有 Pi 镜像中的 Linux 工具链及隔离源码完成构建，不修改运行服务。
- 审阅发现 10-11 算法报告的 `report_sha256` 与当前文件不符；保留原值并补充不一致说明及当前 hash。私有 `md/issue.md` 保留本地，不纳入公开项目提交；必要测试短问法不包含私人会话全文，完整归档仍由忽略目录提供。
- 失败定位与方案：旧 Agent 将后端语言拼成精确 position，与新的精确职位筛选冲突，改为 job_family/language；compact context 二次投影覆写 legacy 来源，保持投影幂等。SEARCH 的 trace 测试替身只实现 embedding，却请求 HYBRID_RERANK，改为明确 DENSE 并保留 trace 断言；另一个失败确认为 SQL eligible 空集被误报核验失败，允许主机证明的空范围完成，并记录 empty_sql_scope 依据，真实核验失败仍拒绝成功。
- 评测可移植性：来源复现测试依赖未分发的私有快照/Gold/历史归档。仅当必要输入缺失时明确 skip，输入存在时继续严格校验，不能把缺失数据记为质量通过。
- SEARCH 首次定向验证：29 passed、1 failed（15.08 秒，`search-baseline-fixes.log`）。空范围修复与核验失败回归已通过；trace fixture 仅设置模型 plan 的 DENSE，但主机按当前请求的 requested_pipeline 覆盖，仍返回 503。下一步在请求及 Pi run 上明确 DENSE，保持主机选择优先级；没有真实模型调用。
- 候选 provider/配置/打包相关 9 个文件验证：110 passed（5.97 秒）。隔离 Linux Pi 镜像使用当前源码、禁用网络后，`npm run build:pi`、`npm run check`、91 项 Node/Web 测试通过，未接触运行容器。
- 基线失败已逐项修复：legacy 61 项通过，SEARCH 30 项通过；私有来源用例 63 项通过、缺失输入模拟 27 项明确 skipped。原始日志保留；`.tmp/` 仅保存本地检查产物，纳入忽略规则。
- 已形成主题提交：`82105b9` 入库候选，`fcfd751` 查询/使用/观测与基线修复，`5159357` Qwen 部署证据，`5dcac49` 来源评测。各提交正文标明工作区验证范围；没有宣称每个中间提交均独立跑过全套测试。
- 下一步职责拆分：排名权重/分段/版本由同一模块提供给 Python 与 SQL；API 先分离 HTTP 错误映射（保持脱敏与状态码），QueryService 先分离答案与响应结构投影（状态、权限、预算和 journal 保持原位）。先做上述三个可独立回归的边界，避免一次重写整个查询循环。
