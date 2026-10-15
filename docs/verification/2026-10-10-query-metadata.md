# 2026-10-10 查询意图与结构化筛选验收

本报告仅证明 10 月 10 日的本地发布。SSH 网站于 10 月 11 日另行冻结、发布和回归，见 [SSH 验收记录](2026-10-11-byte-algorithm-ssh.md)。

已更新本地 `http://localhost:8000` 的 API。岗位方向由 LLM 选择 `job_family`、`language`；明确的岗位名称使用 `position` 精确匹配。SQL 执行这些字段，移除原先看到“后端/服务端/后台”就扩大岗位范围及自动增加 Java 语言条件的分支。

原“字节跳动算法题”被模型规划成 `company=字节跳动, topic_l1=算法`，而库中公司规范名为“字节”。随后“面经里没有字节跳动的算法题吗”只调用计数和回答工具，没有展示题目。公司值未对齐与任务维度混淆共同导致误判。

现在查询、UI 与入库共用既有公司别名表；模型上下文提供当前有效公司值、岗位名称、别名与目录是否完整。模型先应用精确别名，再选择字段。普通算法题使用 `coding_focus=ALGORITHM`，未明确要求时不叠加主题、旧题型或必须写代码的限制。存在性质疑重新调用本轮 LIST，展示题目与来源。

历史 LIST 重检提供原用户消息；第一次规划时，旧模型推导的筛选仅保留在 `last_plan` 作参考，用户显式 UI 筛选继续作为约束。原运行状态及历史记录保持，重检在新会话中执行。

## 测试结果

专项回归 **118 通过**，覆盖精确岗位、方向与语言组合、公司别名、LIST/COUNT/STATS/SEARCH 资格一致、模型上下文、历史重检、UI 约束和既有会话规则。仅有既有 Starlette TestClient/httpx 弃用提示。

最终本地真实 Pi 模型顺序调用五个用例，再重检原失败记录，均通过；非空结果均检查来源引文，数量与直接 SQL 列表接口一致。

| 用户请求 | 实际筛选与结果 |
| --- | --- |
| 字节跳动算法题，首次及同会话重复 | 字节 + ALGORITHM 任务；总计 27 道，首屏 20 道，执行 LIST。 |
| ？面经里没有字节跳动的算法题吗？ | 同范围重新 LIST，总计 27，返回题目与来源。 |
| 字节跳动 Java 后端方向的面试题 | 字节 + BACKEND + JAVA，position=null；当前元数据匹配 0，与 SQL 一致。 |
| 岗位名称恰好为 Java后端，不额外限定范围 | position=Java后端，其他筛选为空；匹配 1。 |
| 重检原失败的字节算法题 LIST | 重新理解原消息，清除旧模型推导的算法主题交叉限制；匹配 27，新会话保留原记录。 |

开发中的真实调用曾暴露三个问题：模型被旧 `#字节跳动` 值误导、历史推导主题被当成约束、普通算法题被额外限制为 CODE。分别通过别名优先级、历史筛选来源分离和字段说明修复；前次失败材料留存，最终结果来自最后发布版本。

## 本地发布核对

镜像 `709809a7fa3358ebe2ed5e4c33e8041b8330426eb63ff0be635964f1972da8a5`。7 个发布文件与工作区及已安装包 SHA-256 一致，112 个其他包文件保持；原对话记录与八张语料/复习表数量保持，corpus/index 均为 293。API 健康，实际模型输入包含 58 个公司值、57 个岗位值及“字节跳动→字节”别名。

本轮验证查询规划与筛选执行，算法题采用既有 KNOWN 注解策略；27 为当前有效元数据范围的数量。本轮未评估全库注解准确率或语义检索质量，也未重新标注、迁移或重建索引。

测试命令：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/integration/test_analytics.py tests/integration/test_harness_policy.py tests/integration/test_query_metadata_scope.py tests/integration/test_query_requery.py tests/integration/test_search.py tests/unit/test_engineering_design_prompt.py tests/unit/test_harness.py tests/unit/test_metadata_normalization.py tests/unit/test_query_filter_contract.py tests/unit/test_query_intents.py
```

发布文件、最终实测条件、数量和证据 SHA-256 见[证据摘要](2026-10-10-query-metadata.evidence.json)。原始 XML、HTTP 回执、模型调用失败与发布前后核对保存在本机忽略目录 `data/reports/query-metadata-20261010/`，不随报告发布完整题干及会话。
