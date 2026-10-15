---
id: PIT-014
status: current
resolution: mitigated
resolution_scope: runtime-artifact-and-effective-configuration-identity
scope: historical-report-and-workspace
verified_at: 2026-10-15
runtime_verified: false
tags: [packaging, configuration, deployment, pi, dependencies, fingerprints]
sources:
  - path: md/issue.md
  - path: docs/bad-cases.md
  - path: docs/verification/2026-10-09-local-deployment.md
  - path: docs/verification/2026-10-08-library-deployment.md
  - path: docs/verification/2026-10-09-markdown-sse.md
  - path: pyproject.toml
  - path: src/interview_intelligence/resources.py
    symbols: [resource_path]
  - path: src/interview_intelligence/config.py
    symbols: [Settings, load_settings]
  - path: src/interview_intelligence/ingestion/extraction_cache.py
    symbols: [extraction_config_hash, ExtractionStageCache]
  - path: scripts/build-pi.mjs
  - path: scripts/start-local.ps1
  - path: services/pi-agent/Dockerfile
  - path: vendor/PI-SOURCE.md
  - path: compose.build-wsl.yaml
  - path: compose.yaml
tests:
  - tests/unit/test_packaged_resources.py
  - tests/integration/test_extraction_stage_cache.py
---

# PIT-014：工作区文件、安装产物和运行中配置不是同一个身份

[返回踩坑索引](README.md)

## 30 秒回忆

- **症状**：源码测试通过，容器缺提示词；脚本看不到新字段；配置修改后缓存指纹仍不匹配。
- **原因**：导入来源、安装资源、构建输入和进程实际配置没有同步，源代码版本不能代替运行身份。
- **处理**：明确打包资源、固定 Pi 生成数据、核对实际导入路径与容器文件，保留缓存配置校验。
- **记住**：安装成功、构建成功、服务健康、真实任务完成需要各自证据，不能替代彼此。

## 原记录与触发范围

来源为 [原问题日志](../../md/issue.md)，以下条目共有“运行身份与观察对象不一致”的边界：

| 原编号或标题 | 直接触发 |
| --- | --- |
| #17「新查询规划提示词遗漏在安装包之外」 | API 启动时缺少 `prompts/search_plan_v1.md`，源码测试未暴露 |
| #19「本地审计脚本加载了旧的已安装代码」 | 独立脚本看到的 Settings 没有工作区已存在的 `snapshot_root` |
| #44「85 篇来源的旧 UnsupportedModel 错误历史」 | 汇总旧错误，不能据今天配置猜测旧请求的确切模型 |
| #46「小篇幅来源连续截断及诊断缓存恢复」中的配置分歧 | 新建任务配置与仍运行 worker 不同，两次指纹检查阻止错误缓存归档 |
| Q09「Pi 源码快照缺生成资源，安装通过仍不保证可离线构建」 | 固定源码包缺生成的模型目录数据，另有 npm 可选 peer 冲突 |
| Q10「真实 PostgreSQL 和 WSL 部署暴露了样本环境未覆盖的差异」中的构建项 | 默认构建网络 DNS 失败，host 构建网络成功 |
| 「2026-10-09 本地部署、范围纠正回归与代码整理」 | 中文路径触发 Bake 错误；基础镜像元数据请求又单独超时 |

[旧导入程序记录](../bad-cases.md#同目录的旧导入程序) 还记载曾有入口直接构建 extractor，缺少当前 worker 的门控与预算。
该旧入口已被停止并移除；未知历史 usage 不能写成零消耗。
不能据此断言 #44 的全部旧错误都来自同一脚本或同一个模型别名。

两个日期验收补充了同一发布边界：

- [10-08 题库部署](../verification/2026-10-08-library-deployment.md)：首次整包构建误带共享工作区其他后端改动，随后以已验证镜像为基底，仅发布冻结页面增量。
- [10-09 Markdown/SSE 正式验收](../verification/2026-10-09-markdown-sse.md)：本地实现和测试已有，SSH 服务仍是旧发布快照，四个新运行资源 404。

前者是发布范围扩大，后者是实际目标没有收到资源；都不能只靠“工作区有文件”来判断部署完成。

## 定位与原因

| 判断 | 证据 | 确定性 |
| --- | --- | --- |
| Python 包没有包含新提示词 | #17 定位到 wheel 的 force-include 缺项 | 已证实 |
| pytest 与独立脚本导入对象不同 | #19 独立脚本读旧安装包；pytest 配置将 `src` 加入搜索路径 | 已证实 |
| 配置文件改变不等于运行进程改变 | #46 任务配置与正常 worker 不同，指纹保护拒绝混存 | 已证实 |
| 指纹校验不稳定 | 同一事件已发现有效配置不同 | 被证据否定，不能绕过校验 |
| Q09 缺的是固定版本生成资源 | GitHub 快照不含目录数据，同版本 npm 发布物含该资产 | 已证实，见 Pi 来源记录 |
| 85 篇来源旧错误的确切模型/阶段 | 部分旧任务没有完整 ModelCall，服务端仅报不支持模型 | 未知，不能反推精确参数 |

## 做过的处理与弯路

| 实际处理 | 结果与边界 |
| --- | --- |
| 补 wheel 资源清单，并加入全部提示词清单回归 | #17 安装包初始化与实际接口恢复；不是只验证源码目录可读 |
| 审计脚本显式指向工作区 `src` | #19 结构核查可继续；真实模型仍走容器共享锁，不在 Windows 另开调用 |
| 按新任务配置直接保存旧 worker 的诊断输出 | #46 两次被指纹拦截，没有强行改 hash 或冒充另一模型 |
| 在实际 worker 环境重验原 JSON、版本与配置，再经正常缓存 API 保存回读 | #46 缓存恢复这一步没有新增模型调用；不代表此前截断根因已消失 |
| 固定同版 Pi npm 数据，按 telemetry → ai → agent 构建 | Q09 恢复离线编译所需资产，不在线拉取最新模型目录 |
| `npm ci --ignore-scripts --legacy-peer-deps` | 避开当时安装冲突，随后显式构建；未宣称修复上游全部依赖问题 |
| WSL 构建采用 host 网络覆盖 | Q10 实际成功；覆盖只作用于构建，不修改服务运行网络 |
| 中文工作区构建时关闭 Compose Bake | 10-09 解决非 ASCII 共享会话头错误，动态 `wslpath` 替代固定链接 |
| 普通构建器继续拉取基础镜像 | 仍遇到 registry 元数据超时，说明 Bake 与网络超时是不同故障 |
| 以已有官方服务镜像为缓存基底重新装依赖并复制完整文件 | 10-09 当次发布通过；没有证明空缓存构建成功 |
| 题库首次整包发布后纠正为页面增量 | 冻结发布文件、核对原后端摘要，初次发布证据保留；共享工作区不是可直接全量发布的版本 |
| 正式服务补齐 Markdown/SSE 运行资源 | 等待在途请求、备份、定向发布并验 hash；旧回答重载后可渲染，无须重新生成 |

## 当前源码的身份边界

[`resource_path`](../../src/interview_intelligence/resources.py) 先查安装包资源，再回退源码路径；开发树会掩盖安装包缺项。
[`pyproject.toml`](../../pyproject.toml) 显式打包运行提示词与 taxonomy；pytest 的 `pythonpath` 包含 `src` 和根目录。
[资源清单测试](../../tests/unit/test_packaged_resources.py) 枚举提示词并核对清单，但它本身不构建或安装 wheel。
因此新增资源后还要核对安装产物，不能把这个测试名称误读成完整安装验收。

[`load_settings`](../../src/interview_intelligence/config.py) 显式传 `_env_file=".env"`；直接 `Settings()` 的装配边界不同。
常规 Compose 通过明确列出的 environment 传值，不能假设宿主文件中的任意键都进入容器。
API、worker、维护脚本、租户 profile 的最终配置需要分别核对，完整规则见 [运行配置](../reference/runtime-config.md)。
本篇没有读取 `.env`，也不以历史报告的模型名充当当前进程配置。

[`build-pi.mjs`](../../scripts/build-pi.mjs) 在编译前复制固定模型数据，并在编译后复制到 dist。
[Pi 来源说明](../../vendor/PI-SOURCE.md) 固定上游提交和同版本 npm 数据摘要；构建脚本不在线更新目录。
[`Pi Dockerfile`](../../services/pi-agent/Dockerfile) 分别复制源码与生成数据，锁定依赖后显式构建。
[`compose.build-wsl.yaml`](../../compose.build-wsl.yaml) 仅设置构建 `network: host`；运行网络仍以 Compose 主文件为准。

## 验证结果与未解决部分

- 状态为 `mitigated`，限定为已知缺资源、导入来源与配置身份问题；不宣称所有环境可复现构建。
- #17 记录了安装态初始化与 HTTP 恢复；Q09 记录了构建、容器与 Pi 核心测试通过，均为当时结果。
- [10-09 部署报告](../verification/2026-10-09-local-deployment.md) 核对 API/worker 各 88 个源码/资源文件摘要及 Pi、提示词、公开资源。
- 同次安装依赖检查通过，但使用缓存基底，空缓存依赖下载与基础镜像构建仍未验收。
- 题库报告核对 19 项安装摘要；Markdown/SSE 报告核对资源 200、指纹与增量先于完成事件，受保护文件保持一致。
- 两项发布验收均不等于检索语义门禁通过；本地发布与 SSH 发布各按对应报告，不混称同一环境。
- 以上部署只证明报告指定本机环境；服务器版本及今天实际配置要查独立运行证据。
- 本次只核对源码、清单和历史材料，没有构建镜像、安装依赖、读私有配置或调用模型。

## 防复发与再遇到时的排查顺序

1. 先确认失败进程的 Python/Node、模块文件路径、工作目录、安装版本与镜像，避免拿源码解释旧安装包。
2. 对资源报错先核对打包清单与安装目录；对配置分歧核对实际 provider、版本与任务快照，不先改指纹。
3. 分别检查依赖安装、生成资产、编译、镜像构建和运行初始化，保留第一失败阶段。
4. 构建网络失败与运行网络失败分开定位；复用缓存的验收必须注明缓存边界。
5. 发布后核对实际文件和提示词摘要，再执行相应受控功能验收；历史错误保持原样。

| 守护措施 | 当前状态 |
| --- | --- |
| 提示词打包清单检查、固定 Pi 源码/数据来源 | 已有源码与回归入口 |
| 抽取配置身份、原文 hash 和缓存回读验证 | 已有，见缓存实现与测试 |
| 干净环境下安装 wheel 后初始化的持续回归 | 历史曾验；本篇未发现该清单测试自动执行完整流程 |
| 冻结发布清单、受保护文件摘要、目标资源 HTTP 检查 | 历史发布已有实践；不能仅依赖工作区测试 |
| 空缓存镜像构建、跨平台版本矩阵 | 待补，不以缓存构建替代 |

## 回顾自测

1. pytest 能读取新提示词，为何安装后的 API 仍可能启动失败？
2. 缓存指纹不同但原文相同，为什么不能直接重写指纹继续保存？

答案要点：源码路径可能补上缺失资源；缓存身份还绑定实际模型、提示词与配置，混存会伪造结果来源。
