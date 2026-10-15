---
id: PIT-015
status: current
resolution: mitigated
resolution_scope: postgres-contracts-and-json-output-types
scope: historical-report-and-workspace
verified_at: 2026-10-15
runtime_verified: false
tags: [postgresql, sqlite, testing, serialization, database-contracts]
sources:
  - path: md/issue.md
  - path: docs/bad-cases.md
  - path: docs/plans/2026-10-05-query-spec-verification.md
  - path: docs/verification/2026-10-09-local-deployment.md
  - path: docs/verification/2026-10-09-question-usage.md
  - path: src/interview_intelligence/domain/models.py
    symbols: [PipelineRun, EmbeddingCache]
  - path: src/interview_intelligence/ingestion/pipeline.py
    symbols: [IngestService.ingest_file]
  - path: src/interview_intelligence/analytics/listing.py
    symbols: [list_questions]
  - path: src/interview_intelligence/analytics/usage.py
    symbols: [_aware, _json_dates]
  - path: src/interview_intelligence/api.py
    symbols: [_response]
  - path: tests/conftest.py
tests:
  - tests/integration/test_schema_constraints.py
  - tests/integration/test_ingestion_lifecycle.py
  - tests/integration/test_query_agent.py
  - tests/integration/test_access_postgres.py
  - tests/integration/test_question_usage_postgres.py
  - tests/integration/test_question_usage.py
---

# PIT-015：样本能跑，不代表真实数据库与 JSON 输出契约一致

[返回踩坑索引](README.md)

## 30 秒回忆

- **症状**：真实 PostgreSQL 插入拒绝版本字符串；SQL Top N 聚合结果到了接口边界出现类型差异。
- **原因**：SQLite 没有暴露声明长度约束，PostgreSQL 的 bigint 计数再求和返回 Decimal。
- **处理**：短字段保存固定长度摘要、完整版本留在快照；计数在接口组装时显式转整数。
- **相邻事故**：使用统计返回 datetime 时直接交给 JSONResponse 失败；这是序列化边界，不是 PostgreSQL 独有故障。
- **记住**：业务样本测试和真实后端契约测试回答不同问题；真实 PG 测试通过也只覆盖其具体断言。

## 原记录与触发范围

| 原来源 | 事故范围 |
| --- | --- |
| [bad-cases「PostgreSQL 字段长度」](../bad-cases.md#postgresql-字段长度) | 完整去重版本超过 `PipelineRun.embedding_version` 的 64 字符上限；该摘要未标明独立事故日期 |
| [原问题 Q10「真实 PostgreSQL 和 WSL 部署暴露了样本环境未覆盖的差异」](../../md/issue.md) | 2026-10-04，真实 PG 计数再次 SUM 得到 Decimal，需要在接口序列化前归一化 |
| [10-05 查询 spec 验收](../plans/2026-10-05-query-spec-verification.md) | 后续查询功能与部署证据；不能用单条 Q10 替代整套查询验收 |

Q10 同时记有 WSL 构建 DNS 与跨 Windows/WSL SSH 命令展开问题，但它们不由数据库类型导致。
构建边界见 [PIT-014](2026-10-04-runtime-artifact-drift.md)，进程保活见 [PIT-012](2026-10-09-process-lifecycle.md)。
原日志的隧道脚本处理只是当时连接方式，不是本篇确认了完整隧道守护或长期部署。

## 定位与原因

| 判断 | 依据 | 确定性 |
| --- | --- | --- |
| 完整去重版本不适合直接塞进 64 字符列 | 历史真实插入失败；当前 ORM 仍将 `PipelineRun.embedding_version` 定义为 `String(64)` | 已记录故障与当前约束 |
| SQLite 测试未暴露该长度差异 | 历史摘要明确区分；当前导入与 schema 样本仍使用 SQLite | 已证实测试环境差异 |
| PG 聚合计数类型与样本返回不同 | Q10 记录 bigint count 再 SUM 得到 Decimal | 历史实测记录 |
| 接口应保留整数计数契约 | 当前 `list_questions` 对样本计数显式 `int(...)` | 当前源码事实 |
| 所有 SQL 字段在两后端都已等价 | 现有个别后端测试无法证明全部查询、迁移与约束 | 未证明 |

共同根因不是“ORM 不可靠”，而是把轻量样本后端的行为当成生产后端契约。
字段长度和聚合返回类型发生在不同层：一个在写入约束，一个在查询结果及 JSON 边界。
因此修复不能只在 API 层统一字符串化，也不能单纯扩大一个列就认为完成后端兼容。

## 做过的处理与弯路

| 实际路径 | 结果与保留理由 |
| --- | --- |
| 直接把完整 dedup 版本写入短列 | SQLite 样本未阻止，真实 PG 才拒绝；保留这条后端差异教训 |
| 改为 SHA-256 摘要，同时把完整版本存入配置快照 | 满足固定长度身份与可追溯诊断；不是直接截断版本字符串 |
| 将 PG 聚合计数直接沿用样本处理 | Q10 暴露 Decimal，增加明确整数转换后真实 Top N 接口验收通过 |
| 在真实 PG 上验收查询结果 | 验证当时真实后端与接口组合，而非用更多 SQLite 样本冒充后端测试 |

原材料没有记录“扩大数据库列”“更换驱动”或“把所有 Decimal 统一转字符串”的实际尝试。
这些不能被补写成曾经失败的方案；本篇仅记录有依据的处理路径。
旧故障摘要也没有提供完整失败堆栈，不事后编造具体 SQLSTATE 或响应错误码。

## 相邻边界：数据库值可读，HTTP 仍可能失败

[10-09 题目使用验收](../verification/2026-10-09-question-usage.md) 记录日期对象交给 JSONResponse 无法序列化。
实际处理是输出带时区的 ISO 字符串，并回归使用期、最近返回和复习日期；无需改变底层统计事实。
当前 [`_response`](../../src/interview_intelligence/api.py) 直接构造 JSONResponse，不能假设框架会再自动编码其 data。
[`_json_dates`](../../src/interview_intelligence/analytics/usage.py) 将 datetime 编成 ISO 字符串，`_aware` 给无时区值补 UTC。
这与 Decimal 一样要检查最终 HTTP 边界，但日期故障不能归因为“SQLite 与 PG 不兼容”。
用途冲突、页面初始化和重放误计数另见 [PIT-020](2026-10-09-read-side-effect-metering.md)。

## 当前源码的处理

[`IngestService.ingest_file`](../../src/interview_intelligence/ingestion/pipeline.py) 组合 encoder、judge 等去重版本。
创建 PipelineRun 时，`embedding_version` 保存该组合的 SHA-256 十六进制摘要。
完整 `dedup_version` 留在 `config_snapshot`，供核查配置来源与重跑身份。
摘要长度为 64，与 [ORM 的 `PipelineRun`](../../src/interview_intelligence/domain/models.py) 列约束对应。
不要与 `EmbeddingCache.embedding_version` 混为一列：后者当前是 `String(128)`，有独立缓存身份含义。

[`list_questions`](../../src/interview_intelligence/analytics/listing.py) 在组装 `meta.sample_counts` 时转换为 `int`。
其中 `selected_occurrences` 来自所选问题 occurrence_count 的 SUM，空结果以零处理。
它仍表示所选标准题对应的提问次数，不能因此改称标准题总数。
显式归一化发生在计数输出边界，不需要改变 SQL 排序口径或绕过精确统计。

## 测试分别能证明什么

| 入口 | 实际环境与覆盖边界 |
| --- | --- |
| [schema constraints](../../tests/integration/test_schema_constraints.py) | 明确创建内存 SQLite，覆盖题序唯一、关系与幂等等样本约束 |
| [ingestion lifecycle](../../tests/integration/test_ingestion_lifecycle.py) | 使用临时 SQLite，覆盖重复导入、失败保留旧 build 等流程 |
| [query agent](../../tests/integration/test_query_agent.py) | 基于 SQLite 合成语料，覆盖全范围 Top N、顺序、分页与签名范围 |
| [access postgres](../../tests/integration/test_access_postgres.py) | 显式 PG 测试连接、临时 schema，验证跨实例并发额度准入 |
| [question usage postgres](../../tests/integration/test_question_usage_postgres.py) | 显式 PG 连接、临时 schema，验证使用统计迁移与并发聚合 |

[`tests/conftest.py`](../../tests/conftest.py) 默认将 DATABASE_URL 设为 SQLite，不能凭 pytest 总通过数推定真实 PG 已执行。
两个专用 PG 测试分别要求 `ACCESS_TEST_DATABASE_URL` 与 `USAGE_TEST_DATABASE_URL`，未配置时跳过。
它们断言后端确为 PostgreSQL，创建并清理自己的随机 schema，不以业务表作为可抛弃测试数据。
这些入口不等于已经专门覆盖本篇的超长版本插入与 Top N Decimal 输出。

## 验证结果与未解决部分

- `resolution: mitigated` 限定为已知长度和聚合计数类型差异；不宣称数据库兼容性已穷尽。
- 原故障摘要确认固定摘要与完整配置快照处理；Q10 记录真实 PostgreSQL Top N 接口通过。
- [10-09 部署报告](../verification/2026-10-09-local-deployment.md) 另有真实 PG 额度并发验收，证明的是准入边界。
- 后续题目使用统计 PG 测试验证迁移与并发聚合，不能借它关闭所有 SQL 查询的类型问题。
- 日期转换的历史回归见使用验收；[使用统计测试](../../tests/integration/test_question_usage.py) 覆盖实际 API 读取与时间窗口，仍不能替代所有响应类型检查。
- 本次核查的入口中未见专门将这两项事故组合成真实 PG 回归的测试，应作为防复发补项。
- 本次没有访问数据库、运行测试、调用服务或读取连接凭据；所有运行结果均归属于原报告日期。

## 防复发与再遇到时的排查顺序

1. 先记录数据库方言、驱动、迁移版本、失败边界和返回值类型；别只记录“SQL 报错”。
2. 写入失败时比对实际值长度与列定义，确认组合版本是否误写入短摘要字段。
3. 查询输出失败时区分 SQL 执行、ORM 返回类型和 JSON 编码，核对 count/SUM 的实际类型。
4. 先用隔离 schema 的最小非模型用例复现，再修正边界；不要直接对正式库插入破坏性测试数据。
5. 对涉及长度、聚合类型、锁和并发的变更补真实 PG 验证；报告明确执行、跳过与样本范围。

| 守护措施 | 当前状态 |
| --- | --- |
| 版本摘要与完整配置快照分存 | 已有，见 PipelineRun 写入 |
| SQL 计数输出显式归一化 | 已有，见 list_questions |
| 使用统计 datetime 转带时区 ISO 字符串 | 已有，见 _json_dates；HTTP 日期回归见使用统计测试 |
| 明确后端、随机 schema 的 PG 验证模式 | 已有，可复用隔离方式，不可假定覆盖全部业务 |
| 超长组合版本与 PG Top N JSON 类型定向回归 | 待补，本次未新增业务测试 |
| 发布报告列出后端专用测试的执行/跳过原因 | 维护要求，不能只贴总通过数 |

## 回顾自测

1. 同一 ORM 模型在 SQLite 插入成功，是否证明 `String(64)` 的超长值在 PostgreSQL 也能写入？
2. 真实 PG 并发额度测试通过，能否据此说 Top N 的 JSON 类型和全部迁移都已验证？

答案要点：不能，长度约束与类型由后端执行；一次真实后端测试只证明其输入、操作及断言覆盖的契约。
