# 扩样检索测试报告

日期：2026-10-08。本次为测试窗口部署的一次实测，没有旧版对照；版本归属以部署时间线为准。

冻结协议 16 个 case、20 轮；HTTP 成功 20，请求失败 0，未执行 0；动作符合预期 20/20，动作及核验检查通过 20/20。整段 case 动作全部通过 16/16。报告状态：`COMPLETE_REVIEWED`。

返回 46 个题目 occurrence，已有效审核 46 个；DIRECT+SUBTASK 且无决定性约束冲突 38，UNCERTAIN 2，未审核 0。返回集合微平均 Precision 为 82.6%，标注不确定区间为 [82.6%, 87.0%]。

执行/动作/核验检查通过不等于检索质量没有失败：仍漏出 1 个 anchor occurrence，返回 6 个邻近题与 2 个不确定题。优先核对 planner 把自然语言题型转成硬过滤的范围，以及相邻子任务与当前问题之间的关系。

这是分层开发新查询和开发 Agent 题干审核，不是人工 Gold，也不是全库质量估计；没有全库 Recall。减少返回量本身可能提高返回集合 Precision。

## 被测版本与当前服务

本轮结果归属测试窗口旧 R4 image `sha256:73db29731072ffe3a5218fe80c748ba7f8397637a84a7b796c526ec7fe04d41e`。20 轮回执关联同一服务进程 owner `618ddfdd-bee3-4e8e-a9ff-4b0d07a52d1e`，旧不可变 image 的 16 个文件哈希与运行前 preflight 相同。

运行结束后，新 API 容器于 `2026-10-08T07:01:36.29670466Z` 创建、`2026-10-08T07:01:38.263296064Z` 启动，启动时间晚于最后请求完成约 37.9s。当前 localhost 对应新 image `sha256:f4e88d8375ff46133d8ad4af966732e2f967d6ed2f85164416f067a2fd7060a8`，不包含在本轮验收中。后置安装校验 matched=False，保留了 9 项文件差异；这些结束后的差异不能写成运行中混版本的事实。

部署归属依据 [deployment-attribution.json](../../data/reports/expanded-sample-20261008/deployment-attribution.json)、[deployment-timeline.json](../../data/reports/expanded-sample-20261008/deployment-timeline.json)、[ownership-receipts.json](../../data/reports/expanded-sample-20261008/ownership-receipts.json) 和 [image-source-comparison.json](../../data/reports/expanded-sample-20261008/image-source-comparison.json)。未逐请求返回 Docker image header；这是这些观察共同支持的归属结论。Docker 事件历史有界，没有保留事件不构成无事件证明。

运行后 SQL 诊断虽然从新 image 导入，但已离线比对旧/新不可变 image 的 eligible_canonical_ids、_conditions、_active_from、_effective_date AST，及 stats/contracts/models/taxonomy 依赖全文哈希一致，才采用逐 ID eligibility 归因；未据此回放模型或检索。

## 样本与执行

样本选择依据见 [sampling.json](../../data/reports/expanded-sample-20261008/sampling.json)；冻结查询见 [protocol.json](../../data/reports/expanded-sample-20261008/protocol.json)。每个 case 使用独立会话，后续轮只沿用本 case 上下文；依赖阻断保留为失败。执行期间未调业务参数、未补跑失败请求。

执行窗口：`2026-10-08T06:47:55.535741+00:00` → `2026-10-08T07:01:00.389020+00:00`。

样本来源为 corpus revision 293 的 2452 道有效题。预先排除历史 337 个 ID，再按场景动作、题型/标签等启发式规则分层；固定 seed=20261008，层内按 SHA256 排序取前四，未按本次模型结果替换。选中题与历史 ID 的交集为 0 个。

| 抽样层 | 预先规则后候选 | 固定选取 |
|---|---:|---:|
| ai_rag_agent | 47 | 4 |
| business | 5 | 4 |
| infrastructure_incident | 81 | 4 |

业务层取 4/5 只覆盖规则筛出的候选的 80%，并非覆盖全部业务题库。12 道选中真实题派生 12 个单轮查询，另加两个三轮澄清/换题 case 和两个无样本领域 case，共 16 case、20 轮；其中四个多轮 SEARCH 目标复用已选 anchor。单轮有 7 个窄目标、5 个宽目标。AI 层实际选中 Agent/tool 和 AI 应用场景，没有 RAG，结论不推广到 RAG。两类无样本预设来自全有效库中领域词及中英文别名命中为零的检查；词面缺失不是语义上绝无相关题的证明。

## 分层结果

| 分层 | 轮次 | HTTP成功 | 动作通过 | 相关/返回 | UNCERTAIN | Precision区间 | anchor命中 |
|---|---:|---:|---:|---:|---:|---|---:|
| business | 4 | 4 | 4 | 6/7 | 0 | 85.7%–85.7% | 3/4 |
| infrastructure_incident | 4 | 4 | 4 | 17/19 | 2 | 89.5%–100.0% | 4/4 |
| ai_rag_agent | 4 | 4 | 4 | 8/11 | 0 | 72.7%–72.7% | 4/4 |
| multi_turn | 6 | 6 | 6 | 7/9 | 0 | 77.8%–77.8% | 4/4 |
| no_sample | 2 | 2 | 2 | 0/0 | 0 | — | 0/0 |

微平均按每个查询的返回 occurrence 汇总，同一题跨查询不合并；宏平均只覆盖成功且已审核的非空返回轮：89.6%（n=15）。同轮重复 ID 0，跨查询去重后 ID 40；后者不是召回分母。

| 逐条裁决 | 数量 |
|---|---:|
| DIRECT | 18 |
| SUBTASK | 20 |
| NEIGHBOR | 6 |
| IRRELEVANT | 0 |
| UNCERTAIN | 2 |
| UNREVIEWED | 0 |

## 逐轮结果

| case / 轮 | 用户原话 | HTTP / 动作 | 相关/返回 | UNCERTAIN | 耗时 | anchor |
|---|---|---|---:|---:|---:|---:|
| business-group-stock / 1 | 想看看团购业务中库存很快耗完该怎么办的面试问法。 | 200 / SEARCH | 1/2 | 0 | 64.04s | 1/1 |
| business-sms-login / 1 | 找些围绕短信登录防重复发送的场景题。 | 200 / SEARCH | 0/0 | 0 | 33.17s | 0/1 |
| business-product-search / 1 | 想找商品搜索联想功能的设计面试题，输入过程中就能模糊匹配商品名。 | 200 / SEARCH | 1/1 | 0 | 48.75s | 1/1 |
| business-payment-state / 1 | 有哪些围绕支付流程状态机建模的面试题？ | 200 / SEARCH | 4/4 | 0 | 36.41s | 1/1 |
| infra-device-ordering / 1 | 找一些同一设备的消息到达顺序不对时怎么处理的面试题。 | 200 / SEARCH | 1/1 | 0 | 37.78s | 1/1 |
| infra-syn-flood / 1 | 服务端遭遇 SYN 洪泛时怎么应对，有哪些相关场景问法？ | 200 / SEARCH | 1/1 | 0 | 39.22s | 1/1 |
| infra-cache-consistency / 1 | MySQL 与 Redis 对不上数据该如何处理？想找缓存一致性方面的面试题。 | 200 / SEARCH | 14/16 | 2 | 47.65s | 1/1 |
| infra-redis-expansion / 1 | Redis 扩容过程中仍访问旧节点的情况，面试一般会怎么问？ | 200 / SEARCH | 1/1 | 0 | 50.40s | 1/1 |
| ai-tool-quality / 1 | 想看 Agent 工具结果质量诊断与优化的场景题。 | 200 / SEARCH | 2/5 | 0 | 41.43s | 1/1 |
| ai-multimodal-navigation / 1 | 用多模态判断页面跳转时，结果可靠性怎么保障？想找相关场景题。 | 200 / SEARCH | 2/2 | 0 | 33.99s | 1/1 |
| ai-api-failure / 1 | 找一些 Agent 请求 API 没成功后的处理场景题。 | 200 / SEARCH | 3/3 | 0 | 44.77s | 1/1 |
| ai-weekly-report / 1 | 想看 AI 自动完成用户周报发送流程的系统设计题。 | 200 / SEARCH | 1/1 | 0 | 47.30s | 1/1 |
| clarify-api-then-redis / 1 | 这种调用失败要怎么处理，有哪些面试题？ | 200 / CLARIFY | 0/0 | 0 | 7.04s | — |
| clarify-api-then-redis / 2 | 我说的是 Agent 调用 API 没成功这种情况。 | 200 / SEARCH | 4/6 | 0 | 45.87s | 1/1 |
| clarify-api-then-redis / 3 | 现在换个话题，想看 Redis 扩容时请求仍去旧节点的处理题。 | 200 / SEARCH | 1/1 | 0 | 44.23s | 1/1 |
| clarify-ordering-then-product-search / 1 | 这个乱序问题有哪些面试问法？ | 200 / CLARIFY | 0/0 | 0 | 6.92s | — |
| clarify-ordering-then-product-search / 2 | 同一台设备的消息到达顺序乱了。 | 200 / SEARCH | 1/1 | 0 | 38.74s | 1/1 |
| clarify-ordering-then-product-search / 3 | 换成商品搜索框的设计题：输入时就能模糊匹配商品名。 | 200 / SEARCH | 1/1 | 0 | 32.51s | 1/1 |
| no-sample-tokamak / 1 | 帮我找托卡马克装置中等离子体磁约束控制的面试场景题。 | 200 / SEARCH | 0/0 | 0 | 42.69s | — |
| no-sample-marine-metagenome / 1 | 有没有面向海洋浮游生物的宏基因组分类与丰度估计的面试题？ | 200 / SEARCH | 0/0 | 0 | 36.63s | — |

多轮 case 逐段保留：前一轮失败后，后续明确新对象仍可能沿用更早成功会话完成独立检索；这种末轮成功不能覆盖前面失败，也不能证明完整主题切换成功。下表只有在前两轮动作及核验通过、第二轮有返回目标时，才把第三轮成功非空检索计为观察到完整切换；这是流程证据，不是相关性评分的替代。

| 多轮 case | 全轮动作通过 | 前两轮建立目标 | 末轮独立检索非空 | 完整切换流程观察 |
|---|---|---|---|---|
| clarify-api-then-redis | True | True | True | True |
| clarify-ordering-then-product-search | True | True | True | True |

两条澄清的实际内容与题干审核意见：

| 用户原话 | 实际澄清 | 文本审核理由 |
|---|---|---|
| 这种调用失败要怎么处理，有哪些面试题？ | 你说的“这种调用失败”具体指哪一类调用？请补充场景或对象，我再按对应面试题检索。；选项：["第三方接口/HTTP调用失败", "RPC/微服务调用失败", "数据库/缓存调用失败", "AI模型/Agent工具调用失败"] | 无先前上下文，无法确定调用类别；针对对象发起必要澄清。 |
| 这个乱序问题有哪些面试问法？ | 你说的“乱序问题”具体指哪一类？；选项：["消息队列/分布式消息乱序", "网络数据包乱序", "数组/链表乱序算法题", "其他乱序问题"] | 无先前上下文，乱序可能涉及消息、网络或其他对象，针对类别澄清合理。 |

## 空结果与运行状态

成功 SEARCH 18 轮，其中空结果 3 轮。空结果不赋 100% Precision：

```json
{
  "VERIFIED_NO_MATCH": 3
}
```

`RELEVANCE_VERIFICATION_UNAVAILABLE` 是重排/预算核验未完成，不能当作没有匹配题的合理拒答。`VERIFIED_NO_MATCH` 是系统在当前 eligibility/候选范围下完成核验后的状态，不代表全库没有相关题；短信登录漏题就是反例。无 eligible 候选及未经核验的空集分别报告。预设空结果仅形状检查通过 2/2，要求完整核验后通过 2/2。核验不可用 SEARCH 共 0 轮，纳入失败列表。

SEARCH 执行状态分布（保留原始 pipeline、requested_pipeline、executed_pipeline 字段）：

```json
{
  "pipeline": {
    "HYBRID_RERANK": 18
  },
  "requested_pipeline": {
    "HYBRID_RERANK": 18
  },
  "executed_pipeline": {
    "HYBRID_RERANK": 18
  },
  "rerank_status": {
    "COMPLETED": 18
  },
  "degraded": {
    "False": 18
  },
  "relevance_status": {
    "VERIFIED": 18
  }
}
```

## anchor 与退出层级

有限已选 anchor occurrence 命中 15/16，去重 ID 命中 11/12；多轮复用同一 anchor 不会变成新的已知相关题。这是样本 anchor coverage，不是全库 Recall。文本裁决冻结后才查看候选诊断。

| 退出层级 | 数量 |
|---|---:|
| RETURNED | 15 |
| ELIGIBILITY_BY_PLANNER_TYPE_FILTER | 1 |

只有成功 SEARCH 返回才计 anchor 命中；其他动作即使碰巧含同 ID，也只记录 observed_hit。缺失 anchor 逐条如下：

| case / 轮 | 已选题干 | 退出层级 | SQL eligibility | 实际 planner filters | 输入/模型排名 |
|---|---|---|---|---|---|
| business-sms-login / 1 | 短信登录中怎么防止重复发送短信？ | ELIGIBILITY_BY_PLANNER_TYPE_FILTER | False | {"question_type": "SCENARIO"} | — / — |

逐 ID SQL eligibility 来自运行后只读快照，使用实际 planner filters；其中可能含模型推断标签，不能直接解释为用户显式排除。完整候选诊断能够证明 ID 未进入该候选池；缺少逐 ID eligibility 时，不能进一步声称是 SQL 筛除或索引错误。PAGE_CUTOFF 与重排拒绝分开；未返回不等于相关性过滤成功。完整原 ID 和保存判定见 comparison.json。

唯一漏出示例「短信登录中怎么防止重复发送短信？」：只读 PG 与冻结抽样快照一致，实际 occurrence 类型为 ['PROJECT']，planner 却加了 question_type=SCENARIO，足以把该题在 SQL eligibility 阶段排除，原候选池也没有该 ID。用户的自然语言‘场景题’不是明示数据库枚举限制；这是 planner/题型范围不一致，不是重排拒绝或分页截断。依据见 [anchor-diagnostics.json](../../data/reports/expanded-sample-20261008/anchor-diagnostics.json)，未去掉类型条件重新检索来推算反事实效果。

## 延迟样本

| 请求集合 | n | 中位数 | 样本P90 |
|---|---:|---:|---:|
| 全部已执行HTTP请求 | 20 | 40.33s | 48.75s |
| 独立case首轮请求 | 16 | 40.33s | 50.40s |
| 成功SEARCH请求 | 18 | 42.06s | 50.40s |
| 失败请求 | 0 | — | — |

P90 使用 nearest-rank：排序后第 ceil(0.9×n) 个样本，包含失败请求的耗时另列；依赖阻断没有发请求，不作为 0ms 混入。一个查询的模型阶段、embedding 和重排耗时不拆成多个独立样本。这些是少量串行开发请求，不是生产延迟分位数。

关联模型操作日志共 57 条，类型为 {"QUERY_PLAN": 21, "EMBEDDING": 18, "RERANK": 18}，状态为 {"SUCCEEDED": 57}，最大记录队列等待 3ms。collector 无 HTTP 重试不等于每轮仅一次规划：本轮 QUERY_PLAN 记录 21 条。已知输入/输出 usage 为 229965/42582 tokens（有 usage 的记录分别 57/57 条）；不据此换算费用。

## 失败与返回边界

本轮没有 HTTP、动作、核验不可用或预设空结果检查失败。

以下列出所有非 DIRECT 返回（包含通过的 SUBTASK，以便检查相关性边界）；裁决依据原始用户目标和题干，不采用模型打分：

| case / 轮 | 返回题干 | 裁决 | 具体理由 |
|---|---|---|---|
| business-group-stock / 1 | 请讲讲 Redis 库存问题。 | NEIGHBOR | 只有泛化的 Redis 库存主题，没有团购、快速耗尽或解决该问题的任务；把它认定为本场景的直接子任务需要补充假设。 |
| business-payment-state / 1 | 如果用户进行支付宝支付时失败了怎么办，例如用户没有进行支付、输入密码错误等？为什么你要设置为 15 分钟？如果 15 分钟后依然没有支付，怎么做？ | SUBTASK | 支付失败、未支付以及15分钟超时后的处置是支付状态与状态迁移的核心子任务，题干明确这些状态及处理。 |
| business-payment-state / 1 | 用乐观锁解决订单支付和关单问题时，乐观锁是怎么实现的？ | SUBTASK | 支付成功与关闭订单的竞争涉及支付/关单状态转移的原子性，是支付状态机建模中的紧密子任务。 |
| business-payment-state / 1 | 高并发场景下订单未支付超时如何处理更合适？订单取消后库存应该怎么办？ | SUBTASK | 未支付超时转为订单取消及库存补偿，是支付流程状态迁移及其副作用的核心子任务；文本明确未支付、超时与取消。 |
| infra-cache-consistency / 1 | 如何保证双写一致性？ | UNCERTAIN | 题干未说明双写的两个对象；可能是数据库/缓存，也可能是其他存储双写。缺少原上下文，无法确认符合用户对象。 |
| infra-cache-consistency / 1 | 讲下 Redis 如何保持数据一致性。 | UNCERTAIN | 未说明 Redis 与哪个对象保持一致，无法区分 Redis 内部复制一致性与用户请求的数据库/缓存一致性；缺少原上下文。 |
| infra-cache-consistency / 1 | 你认为缓存和数据库的数据不一致是怎么产生的？ | SUBTASK | 诊断缓存与数据库不一致的产生原因，是处理缓存一致性问题的直接子任务。 |
| infra-cache-consistency / 1 | 数据先写到 Redis，再异步写入数据库，这种方案有什么问题？ | SUBTASK | 明确缓存与数据库的异步双写顺序，评估其一致性风险是请求的紧密子任务。 |
| infra-cache-consistency / 1 | 在当前讨论的场景下，读线程进来触发缓存构建，会发生什么一致性问题？ | SUBTASK | 缓存构建中的并发一致性问题是缓存与数据库对齐的紧密子任务；具体业务场景缺失不妨碍这一明确主题。 |
| infra-cache-consistency / 1 | 你提到更新缓存会有时序问题，请具体描述一个多线程并发写的场景，说明为什么同步更新缓存会导致脏数据污染。 | SUBTASK | 明确并发更新缓存造成脏数据的时序原因，属于缓存一致性设计中的核心子任务。 |
| infra-cache-consistency / 1 | Cache-Aside旁路缓存有什么问题？ | SUBTASK | 旁路缓存是数据库与缓存读写协调的核心策略，询问其问题直接覆盖该策略的一致性局限。 |
| infra-cache-consistency / 1 | 针对主从延迟导致的缓存二次污染，业界经典的延迟双删是怎么做的？为什么第二次删除一定要延迟？延迟时间应如何评估？ | SUBTASK | 明确缓存二次污染及延迟双删的一致性处理方案，属于紧密子任务。 |
| infra-cache-consistency / 1 | 如果大促或高并发写入导致从库同步链路严重阻塞、主从延迟大幅拉长，而双删延时已经过期，双删失效后怎么办？有哪些更强硬的强一致性分流手段？ | SUBTASK | 明确主从延迟使双删失效后的一致性补救方案，是缓存一致性处理的紧密子任务。 |
| infra-cache-consistency / 1 | 当核心业务数据（如商品库存、价格）发生变更时，你们是怎么操作缓存的？ | SUBTASK | 源业务数据变化后的缓存更新/失效操作是维持缓存一致性的核心子任务。 |
| infra-cache-consistency / 1 | 为什么选择删除缓存而不是更新缓存？ | SUBTASK | 删除与更新的策略选择直接影响缓存一致性，是用户主题的紧密子任务。 |
| infra-cache-consistency / 1 | 权限修改后缓存怎么失效？ | SUBTASK | 源权限数据变更后的缓存失效是缓存一致性的具体子任务；用户未限定只能某一业务数据。 |
| ai-tool-quality / 1 | 当 Agent 输出结果不符合预期时，如何完整捞取一次调用的全链路信息以定位问题？ | SUBTASK | 收集一次 Agent 调用的全链路信息定位输出问题，是诊断工具结果质量的核心观测子任务。 |
| ai-tool-quality / 1 | 当出现 Badcase 时，如何快速定位具体是哪个 Agent 环节出了问题？如何判断应该对哪个 Agent 做 SFT 优化？ | NEIGHBOR | 讨论广义 Agent Badcase 与微调对象，没有明确工具输出；将 SFT 归因到工具结果需额外假设。 |
| ai-tool-quality / 1 | 如果 Agent 的质量评分突然下降，怎么排查问题？ | NEIGHBOR | 整体 Agent 质量评分下降未明确工具结果，可能涉及模型或提示词等其他环节。 |
| ai-tool-quality / 1 | Agent 搜索有遇到什么问题？是怎么解决的？ | NEIGHBOR | 泛搜索问题未明确输出质量或质量定位任务，可能是延迟、覆盖等问题。 |
| ai-multimodal-navigation / 1 | 如何解决多模态带来的记忆污染、幻觉导致误判的问题？ | SUBTASK | 控制多模态幻觉和误判是保障判断可靠性的紧密子任务。 |
| ai-api-failure / 1 | 工具调用失败时，Agent 是怎么重试和降级的？ | SUBTASK | Agent API 调用属于工具调用；失败后的重试降级是核心恢复子任务。 |
| ai-api-failure / 1 | Agent 工具调用失败或超时了怎么办？ | SUBTASK | 明确 Agent 工具调用失败/超时的处置，覆盖 API 失败恢复子任务。 |
| clarify-api-then-redis / 2 | 工具调用失败时，Agent 是怎么重试和降级的？ | SUBTASK | Agent API 调用属于工具调用；失败后的重试降级是核心恢复子任务。 |
| clarify-api-then-redis / 2 | Agent 工具调用失败或超时了怎么办？ | SUBTASK | 明确 Agent 工具调用失败/超时的处置，覆盖 API 失败恢复子任务。 |
| clarify-api-then-redis / 2 | 如何保障Agent Function Calling的可靠性？如果模型一直调用同一个错误工具该如何处理？ | SUBTASK | 函数/工具调用可靠性及反复错误调用的处理，是 Agent 调用失败恢复的紧密子任务。 |
| clarify-api-then-redis / 2 | 如果Agent调用三个工具后开始死循环，你的异常处理写在哪里？ | NEIGHBOR | 多工具执行死循环未说明 API 请求失败，将其归因到请求失败需要补充假设。 |
| clarify-api-then-redis / 2 | Agent 反思机制有没有从执行失败中不断学习的策略？可以从哪些方面去实现？ | NEIGHBOR | 广义执行失败后的反思学习未明确 API/工具调用失败与当次处置。 |

独立复算发现 46/46 条返回的 object/focus 两段引用都能在原题中匹配，但其中仍有 6 个邻近题。引用真实只能证明内容有出处，不能替代对象和任务相关性判断。依据见 [independent-checks.json](../../data/reports/expanded-sample-20261008/independent-checks.json)。

- 分层开发新查询，非独立人工Gold、非概率样本。
- 只评估测试窗口服务，不估计相对旧版提升。
- DIRECT+SUBTASK且无决定性约束冲突计相关；UNCERTAIN区间是标注边界，不是统计置信区间。
- 返回集合Precision和有限anchor命中不能推出全库Recall或发布门禁通过。
- 延迟按独立HTTP请求计；串行少量开发请求的样本P90不是生产P90。
- 全部HTTP失败、动作错误、依赖阻断及空结果留在协议分母；缺失裁决不默认相关。

## 证据与复现

原始 current.json、protocol.json、review-protocol.json、review-input.json 与 reviews.json 均不由本脚本修改。每题裁决及所有失败详见 [comparison.json](../../data/reports/expanded-sample-20261008/comparison.json)；源文件 SHA256 同时记录，deployment/runtime 快照单独列证。传输异常类型本身不能证明 provider 故障原因；只有关联 request/run 的诊断证据才能支持阶段归因。

```powershell
python data/reports/expanded-sample-20261008/build_report.py
```

运行前安装校验 matched=True，核对 16 个安装文件哈希；query=query_agent_v10，reranker=rerank_v10_explicit_object，端到端预算=90.0s，共享模型调用间隔=2.0s，单请求 QUERY_PLAN 规划调用上限=3；embedding/rerank 另外记账。

| 源证据 | SHA256 |
|---|---|
| protocol.json | `59eacc209b0db664451c38107a1a7b753cbfac08439dfd53ca0d5cc49b75611f` |
| sampling.json | `96ce841f5888ac433da76894f80f82ce4ab9547b0ac69f3b83a88d5d15fea3e7` |
| review-protocol.json | `8f43173aa09dbb633cbb5672b302f2346fccc179f064a6037ca72961c972f4be` |
| current.json | `d86bd25fd6ce05efb9cec637192afa4d7ac6064a3123f1f9dbc5d91e33ac025a` |
| review-input.json | `bedf80237aba8748a20af6a28db8f5300d9ee767ad45a6c4f9a7d7e93435abde` |
| reviews.json | `092dfeb9f16eb2ea2f20787fa48d12f718c3209e4317b3862205fab94a40f7e5` |
| diagnostics.json | `6ed8a370da2a9787d99b839b8a7bf966650a23440f57983803e5094f6730eb32` |
| deployment-before-current-verifier.json | `7fc9b46cc5cac901c83f484f34a2af9a634bda647ff8e3ab1b807d2a40e7c6bd` |
| deployment-before.json | `8d683c399e20e9ae2e651fd50457846744cd36829107a7f57a5cab5b0aefd338` |
| deployment-after.json | `d9cc2e804e19cd23b845b35dbaafc50072844da7ea0acc609115ea526f9e3a56` |
| runtime-before.json | `5b8f7d02c149d54843bb0e082f964b9444df4dca9d62d00c603213b082e37cc3` |
| deployment-timeline.json | `da4a1fa1b7b23313f42655320523ff1185cdfbe79443539498d80a9dca1e3ecb` |
| deployment-attribution.json | `2641b0cacbbb86dda0cecb9997bc238eb84ed29a1f4a8fe7c8539979a7343c84` |
| ownership-receipts.json | `a5fd7f4578bee567a73923b1f42019eaf25bbc8f4f57a4ed76f42978d3ee8ecc` |
| image-source-comparison.json | `fcc744e9051b0195058a9b7e67789407a85d5a3ab64ffc37319885666347ecce` |
| image-sources-old.json | `4487c4667edb9a8392823323f36aca41ce5286972c21c6a01537e33b581519fc` |
| image-sources-new.json | `4038f722db36c5fcbad1e26f9c844a524627e9fbcaff04b74367098237fb9825` |
| anchor-diagnostics.json | `1361fd8bf83281930faddacdb5cd3bf1d295097c84ad28cec52a97b357af6730` |
| independent-checks.json | `c142fc97e5df1f8b3e33aa873c73eb4bc63172e0a228afbc9c4d81a9cb931d18` |
| run.py | `ec1449fe3be6ff89649afc01b1af8e66150dc442ae6a51e897d2c0b499a3b871` |
| receipt_io.py | `a74dcbc3430f3827f2641ffc0d4a577fbb427f9c844aacc6a3ab2f91234fba1e` |
| build_report.py | `3f4a76a7ef46d124541e2d6a6ee04c1680e86249131e3d97e8694f68e25c2eb8` |
| collect_diagnostics.py | `eb96b3577d769c488c60935b09d7e87376470f454fdd67662a7979ad2cbb4edd` |