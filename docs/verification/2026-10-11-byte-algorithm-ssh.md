# 2026-10-11 字节算法题 SSH 修复与回归

这是第一轮元数据修复的历史记录。后续逐题审核发现，27 道中混入布隆过滤器原理、容量估算、泛数据结构设计和场景题；此前只检查 SQL 范围、ID 与来源存在，未充分检查算法任务语义。这里的“回归通过”仅证明元数据链路一致，不能证明返回内容符合用户要求。后续修复与内容验收见 [算法语义回归](2026-10-12-byte-algorithm-semantic.md)。

当时已在用户提供的 SSH 网站发布元数据修复。使用同一个个人模型 `ark-code-latest`，请求“字节跳动算法题”执行 LIST，按当时 KNOWN 任务注解范围匹配 **27 道**，首屏 20 道，下一页 7 道。原来的三条历史记录保持原样；重新提问或历史重检产生新结果。

## 原因与修复

16:27 与 16:30 的请求已正常完成模型调用，但规划使用 `company=字节跳动, topic_l1=算法, question_type=ALGORITHM`，SQL 返回空。库中规范公司名是“字节”，而算法任务维度与旧主题、旧题型是独立字段。三个条件叠加造成误判。

在发布前的生产数据库直接比较三组条件：

| 条件 | 标准题 | 出现记录 | 来源文档 |
| --- | ---: | ---: | ---: |
| 字节跳动 + 旧算法主题和题型 | 0 | 0 | 0 |
| 字节 + 旧算法主题和题型 | 22 | 23 | 19 |
| 字节 + coding_focus=ALGORITHM + KNOWN | 27 | 28 | 22 |

10 月 10 日的同类修复只发布在本机，SSH 仍运行旧包；[本地验收](2026-10-10-query-metadata.md)不能作为线上生效的证据。本次从实际 SSH 包冻结基线，叠加七个文件，没有整包发布工作区中的使用统计与检索实验。

查询、入库共用公司别名表，将“字节跳动”“抖音”对齐到“字节”；模型上下文提供有效公司、岗位值与别名。普通算法题使用 `coding_focus=ALGORITHM`，包含既有 MIXED 任务，不自动叠加主题、旧题型或 CODE 限制。用户显式选择的二面、手写或旧主题仍是硬约束。

历史 LIST 重检提供原用户消息，第一次规划的上下文投影清除旧模型推导的交叉筛选，保留完整旧状态和 `last_plan` 供参考，并继续约束显式 UI 筛选。存在性质疑执行本轮 LIST，返回题目和来源。

## 自动回归

冻结的 SSH 候选包 **100 项通过，0 失败、0 跳过**。通过 `sys.path` 导入候选包，并清除 pytest 的工作区 `pythonpath` 配置；覆盖 analytics、metadata scope、harness policy、search、prompt、metadata normalization、query intents 和新增的字节算法回归。

工作区兼容检查另有 **41 项通过**，覆盖历史重检和显式筛选契约。这两组范围重叠，不相加。新增 [回归测试](../../tests/integration/test_byte_algorithm_regression.py)包含七个用例，检查三个公司别名、ALGORITHM/MIXED 与口述/手写、显式交叉约束、原空列表后重新列题及分页、历史重检投影和 UI 冲突拒绝。

候选包测试文件如下；XML 与冻结包保存在本地忽略目录：

```text
tests/integration/test_byte_algorithm_regression.py
tests/integration/test_query_metadata_scope.py
tests/integration/test_analytics.py
tests/integration/test_harness_policy.py
tests/integration/test_search.py
tests/unit/test_engineering_design_prompt.py
tests/unit/test_harness.py
tests/unit/test_metadata_normalization.py
tests/unit/test_query_intents.py
```

## 真实模型回归

八个用例均通过，使用已部署的 Python 包、Pi runtime、生产 PostgreSQL 和同一个个人模型。测试 API 与 Pi 仅临时监听服务器回环地址，使用独立回归 owner；个人 Key 在服务器内存中解密使用，没有导出，也没有创建或修改账号模型设置。测试结束后两个临时进程均已停止。

| 用例 | 动作 | 总量 / 本次返回 | 耗时 |
| --- | --- | ---: | ---: |
| 新会话“字节跳动算法题” | LIST | 27 / 20 | 20.27 秒 |
| 同会话“字节算法题” | LIST | 27 / 20 | 10.76 秒 |
| “面经里没有字节跳动的算法题吗” | LIST | 27 / 20 | 11.30 秒 |
| 下一页 | NEXT | 27 / 7 | 15.25 秒 |
| 一共有多少道题 | COUNT | 27 / 0 | 8.84 秒 |
| 显式 UI 限定二面手写算法题 | LIST | 3 / 3 | 13.06 秒 |
| 原空结果状态副本再次提问 | LIST | 27 / 20 | 10.98 秒 |
| 原零结果运行副本历史重检 | LIST | 27 / 20 | 12.07 秒 |

总量和 ID 以独立 SQL 列表接口为对照；两页 ID 不重复，并集恰好覆盖全部 27 道。非空列表逐行检查来源 revision 和引文，所有调用均走真实 Pi。显式二面手写条件与 SQL 一致为 3 道。

真实模型用例经私有回环验收 API 执行，覆盖已部署代码的模型、工具、持久化链路；本轮没有重新验证公网个人账号登录与试用扣费。公网 HTTPS 则独立检查 `/api/health`，确认实际提示词哈希、数据库和索引状态与发布回执一致；正式 Pi `/health` 为 ready。

## 发布与保留核对

发布完成于北京时间 **2026-10-11 16:43:55**，仅重启 API 和 worker。七个核心服务 active；Qwen 服务发布前已 inactive，发布后保持该状态。本次使用个人模型，未启动或改动该独立 GPU 服务。

七个增量文件：`analytics/stats.py`、`contracts/__init__.py`、`ingestion/pipeline.py`、`agent/harness.py`、`agent/query_service.py`、新增 `company.py`、`resources/prompts/query_agent_v13.md`。`query_service.py` 只提取本次元数据目录与重检修复，发布哈希以[证据清单](2026-10-11-byte-algorithm-ssh.evidence.json)为准。

提示词仍为 query v13，13,968 bytes，SHA-256 为 `5d00bd87c9dc29b615a16fef2580620e5310904dc322de6f9d840b06b57da905`。111 个其他包文件的 SHA-256 保持；Alembic head 仍为 `e937fb9c2160`，corpus/index 均为 293，task annotation revision 为 30，reranker 仍为 `rerank_v14_design_unit_scope_partial`。

八张既有语料与复习表的行数保持：canonical_question 2668、question_occurrence 4338、source_document 190、source_revision 190、document_build 293、interview 292、review_event 16、user_question_state 16。2668 是全表数量，不等同于当前有效题量。原三条历史完整行逐字段序列化后的 SHA-256 一致：`5450a26c07b9a8fa01ed5e474ac1a03c138377e36dd04deebe92ba5925a72523`。

备份位于 `/home/dylan/services/interview-intelligence/backups/byte-algorithm-20261011T084343Z`，含原增量文件、不存在文件状态、回滚 manifest 和一致性数据库备份。数据库备份 SHA-256 为 `3fa67c7f156a9304ffe8a37c5f70d4de6b9d989f078b89867c2b7a8a8f73971f`。

发布脚本首次有缺失 import，在改动前退出；随后两次分别因错误检查 health 字段及要求原本停止的 Qwen active 而自动恢复旧文件。修正检查以实际健康契约和发布前服务状态为准后才完成正式发布。失败与回滚回执保留，没有把它们算作成功验收。

本轮只验证查询规划、筛选与分页一致性；27 是当前 KNOWN 注解策略下的有效元数据范围，不是全库人工标注结论。未迁移数据库、重建索引或重跑完整 V1 质量门禁。私有快照、测试 XML、模型回执、失败日志及发布核对保存在 `data/reports/byte-algorithm-ssh-20261011/`；公开记录只保存摘要和哈希，不复制个人会话全文或凭据。
