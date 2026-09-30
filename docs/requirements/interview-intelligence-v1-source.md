# Interview Intelligence V1

## 0. 项目目标

构建一个面向个人秋招准备的 **Interview Intelligence System**。

当前已有数百篇 Markdown 格式真实面经，主要来源于小红书、牛客等，内容包含：

- 公司
- 岗位
- 面试轮次
- 面试时间/发布时间
- 八股问题
- 场景题
- 项目问题
- 算法/手撕题
- 面试官追问关系

系统需要把这些非结构化 Markdown 转换为一个：

**可统计、可搜索、可追溯、可评测、可持续回流的 Interview Corpus。**

V1 不以“聊天机器人”为核心，而以：

> Structured Corpus + Analytics + Retrieval + Agent Orchestration

为核心。

---

# 1. V1 核心用户需求

V1 必须能稳定回答以下问题。

| ID | Query 示例 | 核心能力 |
|---|---|---|
| Q1 | 后端最近最常考的 TOP20 知识点是什么？ | structured analytics |
| Q2 | 我在复习 Redis，有哪些高频题？分别出现在哪些公司、哪一轮？ | filter + group by |
| Q3 | “缓存击穿”还有哪些类似问法？一般接下来追问什么？ | semantic retrieval + relation |
| Q4 | MySQL 索引有哪些核心题、常见题和长尾题？ | frequency scoring |
| Q5 | 某公司最近有哪些手撕题？能识别的 LeetCode 题号是什么？ | algorithm extraction |
| Q6 | 我下周面某公司 Java 后端二面，应该重点准备什么？ | agent multi-tool orchestration |
| Q7 | 我刚被问了这几道题，其中两题没答好，记录下来 | user state |
| Q8 | 根据目标公司和我的掌握情况，我还有哪些明显缺口？ | stats + user state |

---

# 2. 明确的 Non-Goals

V1 **不做**：

- 爬虫系统
- Neo4j / 专用 Graph Database
- Kafka 等分布式基础设施
- 多 Agent 系统
- 通用知识问答机器人
- 自动生成完整标准答案库
- 复杂间隔重复算法
- 大型前端
- 语音模拟面试
- 复杂长期 Memory

除非完成全部 P0/P1 验收，不得增加上述功能。

---

# 3. 最重要的数据建模原则

## 3.1 QuestionOccurrence != CanonicalQuestion

必须严格区分：

### QuestionOccurrence

表示：

> 某篇真实面经中真实出现过的一道问题。

例如：

```text
字节 / Java后端 / 一面：
“Redis为什么快？”
```

这是事实数据，不允许因为去重而删除。

### CanonicalQuestion

表示多个语义等价问法归一之后形成的标准问题，例如：

```text
Redis为什么具有较高性能？
```

对应：

```text
CanonicalQuestion
        │
        ├── occurrence：Redis为什么快？
        ├── occurrence：为什么Redis性能这么高？
        └── occurrence：谈谈Redis高性能原因
```

所有频率统计必须基于：

```text
QuestionOccurrence
```

而不能基于 CanonicalQuestion 数量。

---

# 4. 核心数据模型

## 4.1 Interview

```text
id

source_type
source_url nullable

raw_file_path
raw_file_hash

company_raw
company_normalized

position_raw
position_normalized

round
interview_date nullable
publish_date nullable

created_at

extractor_version
taxonomy_version
```

要求保留 raw 字段和 normalized 字段。

---

## 4.2 QuestionOccurrence

```text
id

interview_id

raw_question

question_order

context_before nullable
context_after nullable

topic_l1
topic_l2

question_type

canonical_question_id nullable

confidence

created_at
```

`raw_question` 永远保存面经中的原始表述。

---

## 4.3 CanonicalQuestion

```text
id

canonical_text

topic_l1
topic_l2

question_type

created_at
updated_at
```

CanonicalQuestion 不应该包含公司、轮次等统计字段。

统计应该实时从 QuestionOccurrence 聚合。

---

## 4.4 QuestionRelation

```text
id

source_question_id
target_question_id

relation_type

confidence

source_interview_id nullable

created_at
```

relation_type 至少包括：

```text
OBSERVED_FOLLOWUP
SIMILAR
RELATED
```

### OBSERVED_FOLLOWUP

必须是原始面经中可以观察到的真实追问关系。

例如：

```text
Redis为什么快？
↓
既然是单线程为什么还能这么快？
```

### RELATED

允许由模型推断。

必须严格区分：

> observed fact

和：

> model inferred relation

LLM 推断关系绝对不能作为真实面经事实展示。

---

## 4.5 UserQuestionState

```text
id
user_id

canonical_question_id

status

last_reviewed_at
review_count

last_score nullable

note nullable
```

status：

```text
UNSEEN
REVIEWED
WEAK
MASTERED
```

V1 不实现复杂遗忘曲线。

---

## 4.6 PipelineRun

```text
id

pipeline_version
extractor_version
taxonomy_version
embedding_version

start_time
end_time

processed_documents
processed_questions

failed_documents

input_tokens
output_tokens
estimated_cost

status
```

---

# 5. Topic Taxonomy

Topic 不允许 LLM 自由生成。

系统维护版本化 taxonomy。

初始版本：

```text
Java
    Java基础
    集合
    并发
    JVM
    IO

数据库
    MySQL索引
    MySQL事务
    MVCC
    MySQL锁
    SQL优化
    数据库架构

Redis
    数据结构
    持久化
    缓存问题
    高可用
    分布式锁
    性能优化

Spring
    IOC
    AOP
    Spring事务
    SpringBoot
    SpringMVC

中间件
    Kafka
    RocketMQ
    消息可靠性
    消息顺序
    消息积压

分布式
    分布式事务
    CAP与一致性
    分布式ID
    限流
    服务治理

计算机基础
    网络
    操作系统
    数据结构
    Linux

系统设计
    高并发
    高可用
    缓存
    数据库设计
    线上排障

算法
    数组
    链表
    树
    图
    动态规划
    搜索
    字符串
    大数据场景

AI
    LLM
    RAG
    Agent
    MCP
    Prompt
    AI系统设计

项目
    项目深挖
    系统设计
    故障场景
    性能优化

其他
    HR
    开放问题
    UNKNOWN
```

LLM 只能：

```text
选择现有 taxonomy
```

不能创造：

```text
Redis高级问题
高级缓存
数据库底层
```

这样的新分类。

Taxonomy 必须带：

```text
taxonomy_version
```

---

# 6. Question Type

V1 使用固定枚举：

```text
KNOWLEDGE
PRINCIPLE
SCENARIO
SYSTEM_DESIGN
PROJECT
ALGORITHM
AI
HR
OTHER
```

不要扩展几十种类型。

---

# 7. 数据处理 Pipeline

整体：

```text
Markdown
    ↓
Document Discovery
    ↓
Hash / Idempotency Check
    ↓
Metadata Extraction
    ↓
Atomic Question Extraction
    ↓
Topic Classification
    ↓
Question Normalization
    ↓
Dedup Candidate Retrieval
    ↓
Semantic Dedup Decision
    ↓
Persistence
    ↓
Search Index
```

---

# 8. 文档幂等

每篇源文件计算：

```text
raw_file_hash
```

同时记录：

```text
extractor_version
taxonomy_version
```

如果：

```text
hash unchanged
AND
extractor_version unchanged
AND
taxonomy_version unchanged
```

再次执行 ingestion 时：

```text
不得重新调用 LLM
不得产生 duplicate Interview
不得产生 duplicate QuestionOccurrence
```

---

# 9. Atomic Question Extraction

必须按照“问题语义”拆，而不是按 token 长度 chunk。

例如：

```text
面试官：Redis为什么快？
我回答……
面试官：单线程为什么还这么快？
接着又问6.0为什么引入多线程……
```

应该抽取：

```text
Q1 Redis为什么快？
Q2 Redis单线程为什么还能保持较高性能？
Q3 Redis 6.0为什么引入多线程？
```

同时：

```text
Q1 -> Q2 OBSERVED_FOLLOWUP
Q2 -> Q3 OBSERVED_FOLLOWUP
```

禁止因为固定 token chunking 把一个问题拆散。

---

# 10. LLM Structured Output

所有 extraction 必须使用结构化 Schema。

禁止解析自然语言输出。

Extraction result 至少包含：

```json
{
  "company": "",
  "position": "",
  "round": "",
  "interview_date": null,
  "questions": [
    {
      "raw_question": "",
      "topic_l1": "",
      "topic_l2": "",
      "question_type": "",
      "context_before": "",
      "context_after": ""
    }
  ]
}
```

Schema validation 失败：

```text
retry
```

达到最大 retry：

```text
record failed task
```

不能静默丢数据。

---

# 11. Question Dedup

禁止：

```text
embedding_similarity > threshold
=> directly merge
```

必须使用两阶段策略。

## Stage 1：Candidate Generation

新 Question 通过 embedding 搜索已有 CanonicalQuestion：

```text
Top K = 5~10
```

目标：

> 高召回。

---

## Stage 2：Semantic Decision

对候选判断：

```text
SAME
RELATED
DIFFERENT
```

只有：

```text
SAME
```

才允许 merge 到已有 CanonicalQuestion。

例如：

```text
Redis为什么快？
Redis为什么性能高？
```

应倾向 SAME。

但：

```text
Redis为什么快？
Redis为什么采用单线程？
```

应该是 RELATED，而不是 SAME。

Dedup 设计原则：

> 错误 merge 的成本高于漏 merge。

因此 Precision 优先于 Recall。

---

# 12. Search Architecture

推荐默认技术栈：

```text
PostgreSQL
    structured truth store

Elasticsearch / OpenSearch
    BM25
    dense vector
    metadata filter
```

Docker Compose 一键启动。

不得为了 V1 引入：

```text
Redis
Kafka
Neo4j
Qdrant
```

除非有明确必要。

如果 Elasticsearch 明显影响开发进度，可以降级：

```text
PostgreSQL + pgvector
```

但必须保留 Hybrid Retrieval 抽象接口。

---

# 13. Retrieval Pipeline

语义搜索走：

```text
Query
   ↓
BM25 Retrieval
   +
Dense Retrieval
   ↓
RRF
   ↓
Top N candidates
   ↓
Reranker
   ↓
Top K
```

必须保留至少四种 pipeline 用于 evaluation：

```text
BM25
Dense
Hybrid
Hybrid + Rerank
```

---

# 14. Analytics 和 Retrieval 必须分离

下面的问题：

> 最近三个月 Redis 高频题有哪些？

必须通过：

```text
SQL / structured aggregation
```

解决。

绝对不能：

```text
vector search top 50
→ LLM统计
```

因为这是不完整采样，会产生错误统计。

Semantic Retrieval 主要负责：

```text
类似题
不同表述
模糊查询
```

---

# 15. Agent Tool Design

Agent V1 只允许使用以下核心 Tools。

## Tool A：query_question_stats

```text
query_question_stats(
    company?,
    position?,
    topic_l1?,
    topic_l2?,
    round?,
    start_date?,
    end_date?,
    group_by,
    limit
)
```

支持：

```text
question
topic
company
round
```

聚合。

主要解决：

```text
TOP问题
趋势
公司分布
轮次分布
```

---

## Tool B：search_questions

```text
search_questions(
    query,
    filters?,
    top_k
)
```

内部：

```text
Hybrid Retrieval
```

主要解决：

```text
类似问题
语义查询
不同表达
```

---

## Tool C：get_question_detail

```text
get_question_detail(
    canonical_question_id
)
```

返回：

```text
canonical question
variants
frequency
companies
rounds
recent occurrences
observed followups
related questions
source references
```

---

## Tool D：get_topic_overview

```text
get_topic_overview(
    topic,
    company?,
    position?,
    time_range?
)
```

返回该 topic 下：

```text
question
frequency
company coverage
recent frequency
```

---

## Tool E：get_user_question_state

查询：

```text
UNSEEN
REVIEWED
WEAK
MASTERED
```

---

## Tool F：record_review

记录：

```text
question
status
score?
note?
```

---

# 16. Agent 职责

Agent 负责：

```text
Intent Understanding
Tool Selection
Argument Generation
Multi-Step Planning
Result Composition
```

Agent 不负责：

```text
直接计算真实频率
猜测公司分布
自行生成面经事实
凭模型记忆判断近期趋势
```

---

# 17. Query Routing

至少支持：

```text
ANALYTICS
SEARCH
DETAIL
USER_STATE
COMPOSITE
```

示例：

```text
“最近三个月Java后端Redis高频题”
→ ANALYTICS

“缓存击穿还有哪些类似问法”
→ SEARCH

“Redis为什么快主要哪些公司问”
→ DETAIL / ANALYTICS

“我下周腾讯二面，该复习什么”
→ COMPOSITE
```

COMPOSITE Query 允许 Agent 连续调用多个 Tool。

---

# 18. 复杂 Query 示例

用户：

```text
我下周面字节Java后端二面，
帮我看看最近的面经里哪些是重点，
再结合我已经复习过的内容给我一个冲刺清单。
```

期望 Agent Plan：

```text
1. query_question_stats(
      company=字节,
      position=Java后端,
      round=二面
   )

2. get_user_question_state()

3. 对高频且 UNSEEN / WEAK 的问题调用
   get_question_detail()

4. 生成冲刺清单
```

最终结果中的真实频率、公司、轮次必须全部来自 Tool。

---

# 19. Importance Score

为了回答：

> 哪些是核心题？

不能完全依靠 LLM 感觉。

可以提供确定性 score：

```text
importance_score =
    0.5 * frequency_score
  + 0.3 * company_coverage_score
  + 0.2 * recent_frequency_score
```

参数后续允许调整。

系统可以把问题分为：

```text
CORE
COMMON
LONG_TAIL
```

但 UI / 文档必须注明：

> 这是基于当前 corpus 数据计算的优先级，不代表整个行业的绝对结论。

---

# 20. Source Traceability

这是硬性要求。

所有统计或题目都必须能够回溯：

```text
CanonicalQuestion
→ QuestionOccurrence
→ Interview
→ original markdown
```

用户应该能看到类似：

```text
Redis为什么快？  17次

字节 Java 一面
腾讯 后端 二面
美团 Java 一面
...

查看原始来源
```

不得返回无法定位来源的 corpus 事实。

---

# 21. Evaluation Design

项目必须包含独立：

```text
/eval
```

目录。

Evaluation 不是开发完成后的附加项，而属于核心功能。

---

# 22. Extraction Gold Set

从真实 corpus 手工选择：

```text
至少 30 篇 Markdown
```

人工标注：

```text
metadata
questions
topic
question_type
```

指标：

```text
Question Precision
Question Recall
Question F1

Company Accuracy
Position Accuracy
Round Accuracy

Topic L1 Accuracy
Topic L2 Accuracy
```

最低验收目标：

| Metric | Target |
|---|---:|
| Question Precision | >= 0.92 |
| Question Recall | >= 0.88 |
| Question F1 | >= 0.90 |
| Company Accuracy | >= 0.95 |
| Round Accuracy | >= 0.90 |
| Topic L1 Accuracy | >= 0.90 |
| Topic L2 Accuracy | >= 0.85 |

对于原文不存在的字段：

```text
null
```

不视为 extraction error。

严禁 LLM 编造日期、公司、轮次。

---

# 23. Dedup Gold Set

人工建立至少：

```text
150 Question Pairs
```

标签：

```text
SAME
RELATED
DIFFERENT
```

重点评估：

```text
SAME Precision
SAME Recall
Macro F1
```

最低目标：

```text
SAME Precision >= 0.95
SAME Recall >= 0.85
```

Precision 更重要，因为错误合并会污染后续统计。

必须进行至少一次消融：

```text
Embedding Threshold
vs
Embedding Candidate + Semantic Judge
```

---

# 24. Retrieval Benchmark

构建至少：

```text
50 user queries
```

每个 query 人工标注 relevant canonical questions。

至少比较：

```text
BM25
Dense
Hybrid
Hybrid + Rerank
```

记录：

```text
Recall@5
Recall@10
MRR
nDCG@10
```

最低期望：

```text
Recall@10 >= 0.85
```

同时必须输出完整 ablation table。

如果 Hybrid 没有超过单路 baseline：

> 不允许伪造提升。

应保留实际表现最好的方案，并在 README 中分析失败原因。

---

# 25. Agent Routing Evaluation

准备至少：

```text
50 Queries
```

人工标注：

```text
expected tools
expected filters
```

例如：

```text
最近三个月Redis高频题
→ query_question_stats

缓存击穿类似问题
→ search_questions

我下周字节二面怎么准备
→ stats + user state + detail
```

评估：

```text
Tool Selection Accuracy
Argument Accuracy
Task Success Rate
```

最低目标：

```text
Tool Selection Accuracy >= 90%
```

---

# 26. 数据完整性验收

同一 Corpus 连续执行 ingestion 两次：

第二次必须满足：

```text
新增 Interview = 0
新增 QuestionOccurrence = 0
不必要 LLM 调用 = 0
```

除非：

```text
source hash changed
or
pipeline/extractor/taxonomy version changed
```

---

# 27. Failure Handling

所有 LLM / embedding 调用需要：

```text
timeout
retry
exponential backoff
maximum retry count
```

最终失败任务必须进入：

```text
failed_task
```

或等价持久化结构。

禁止：

```text
catch exception
→ print
→ ignore
```

---

# 28. Observability

每次模型调用至少记录：

```text
model
operation_type
prompt_version

input_tokens
output_tokens

latency
status

retry_count
```

Pipeline 结束能够得到：

```text
processed docs
processed questions
failed docs

total tokens
estimated total cost
average latency
```

---

# 29. 最小 API

V1 至少提供：

```text
POST /api/ingest

GET /api/topics
GET /api/questions/stats
GET /api/questions/search
GET /api/questions/{id}

GET /api/review/state
POST /api/review

POST /api/agent/chat
```

具体 REST 路径可以调整，但功能必须存在。

---

# 30. UI

UI 不是重点。

可以：

```text
CLI
or
简单 Web UI
```

至少支持：

```text
查看高频题
搜索问题
查看 Question Detail
查看原始来源
记录复习状态
输入复杂 Agent Query
```

不得因为前端影响核心 Pipeline 和 Evaluation 的交付。

---

# 31. 工程结构建议

建议清晰拆成：

```text
src/

  ingestion/
  extraction/
  normalization/
  dedup/
  taxonomy/

  repository/

  retrieval/
  analytics/

  agent/
    tools/
    routing/

  review/

  api/

eval/
  extraction/
  dedup/
  retrieval/
  routing/

data/
  raw/
  gold/

scripts/

tests/
```

领域逻辑不能全部写在 Controller / Agent Prompt 中。

---

# 32. Prompt Versioning

所有生产 Prompt 必须：

```text
有独立文件
有 version
```

例如：

```text
extract_question_v1
dedup_judge_v2
router_v1
```

不得把大量 Prompt 散落硬编码在业务代码中。

---

# 33. 开发顺序

必须按照以下依赖顺序开发：

```text
Schema
↓
Gold Set
↓
Ingestion
↓
Extraction
↓
Taxonomy
↓
Canonicalization / Dedup
↓
Structured Analytics
↓
Retrieval
↓
Evaluation
↓
Agent Tools
↓
Agent Routing
↓
Review State
↓
Demo / README
```

禁止一开始先开发 Chat UI / Agent。

---

# 34. Milestone 1：Structured Corpus

完成标准：

```text
100+ 篇真实 Markdown 成功导入
```

可以查询：

```text
Interview
QuestionOccurrence
CanonicalQuestion
```

可以回答：

```text
Redis有多少道题？
哪家公司问过？
哪一轮？
出现多少次？
```

并且每一条可以回溯 raw markdown。

---

# 35. Milestone 2：Search

完成：

```text
BM25
Dense
Hybrid
Rerank
```

运行 retrieval benchmark。

必须生成：

```text
eval/retrieval/results.*
```

保存所有实验参数和结果。

---

# 36. Milestone 3：Agent

Agent 可以正确执行至少以下真实案例：

### Case A

```text
最近三个月后端Redis最常考的10道题
```

不得调用 semantic search 代替统计。

### Case B

```text
缓存击穿还有哪些类似问法？
```

必须使用 search。

### Case C

```text
我下周面字节Java二面，
根据近期面经和我的复习记录给我一个冲刺清单。
```

必须多 Tool 调用。

### Case D

```text
记录一下：
Redis持久化答得不错，
Spring事务传播没答出来。
```

必须更新 UserQuestionState。

---

# 37. Definition of Done

只有同时满足以下条件，Interview Intelligence V1 才算完成。

| Category | Definition of Done |
|---|---|
| Corpus | 至少处理 100 篇真实面经，最终目标跑完整 corpus |
| Traceability | 所有 Question 可回溯原始 Markdown |
| Extraction | 有独立 Gold Set，并达到基础质量要求 |
| Dedup | 有 SAME/RELATED/DIFFERENT benchmark |
| Analytics | 支持 topic/company/round/time 过滤和聚合 |
| Retrieval | BM25/Dense/Hybrid/Rerank 有完整消融 |
| Agent | 能根据 Query 正确路由 SQL/Search/User tools |
| Review | 能记录 WEAK/MASTERED 等状态 |
| Idempotency | 同一 corpus 重复导入不产生重复数据 |
| Reliability | 失败任务可重试、可追踪 |
| Observability | 有 token、latency、cost、failure 数据 |
| Evaluation | 所有核心模块都有真实评测结果 |
| README | 有架构、数据流、设计取舍、Benchmark、Demo |
| Demo | 可以实际完成至少 4 个核心用户场景 |

---

# 38. README 最终必须回答的工程问题

README 不能只写“如何启动”。

必须解释：

```text
为什么 QuestionOccurrence 和 CanonicalQuestion 分开？

为什么统计查询不能使用 RAG？

为什么需要固定 Topic Taxonomy？

为什么使用 Hybrid Retrieval？

为什么不能直接使用 embedding threshold 去重？

为什么区分 OBSERVED_FOLLOWUP 和 RELATED？

Agent 在系统中真正负责什么？

哪些模块是 deterministic，哪些依赖 LLM？

系统如何防止重复 ingestion？

系统如何评测 extraction / dedup / retrieval / agent？

当前有哪些 bad cases？
```

这些也是后续面试重点。

---

# 39. V1 成功后的最终数据看板

项目结束至少需要能输出：

```text
Corpus
------
Documents: XXX
Interview experiences: XXX
Question occurrences: XXX
Canonical questions: XXX
Dedup ratio: XX%
Topics: XX

Extraction
----------
Question Precision: XX
Question Recall: XX
Question F1: XX
Topic Accuracy: XX

Dedup
-----
SAME Precision: XX
SAME Recall: XX
F1: XX

Retrieval
---------
BM25 Recall@10: XX
Dense Recall@10: XX
Hybrid Recall@10: XX
Hybrid + Rerank Recall@10: XX

nDCG@10: XX

Agent
-----
Routing Accuracy: XX
Task Success Rate: XX

Engineering
-----------
Total LLM calls: XX
Total tokens: XX
Estimated cost: XX
Failed/retried tasks: XX
```

禁止在简历或 README 中填写没有真实实验支撑的提升数字。

---

# 40. 项目的核心技术叙事

最终项目不是：

> “基于 RAG 做了一个面试助手。”

而应该体现：

```text
真实非结构化面经
        ↓
Domain-specific Extraction
        ↓
Structured Interview Corpus
        ↓
Canonicalization / Semantic Dedup
        ↓
SQL Analytics + Hybrid Retrieval
        ↓
Agent Tool Orchestration
        ↓
Personal Review Loop
        ↓
Evaluation-driven Iteration
```

整个项目优先保证：

```text
数据正确性
> 可评测性
> 检索效果
> Agent能力
> UI
```

任何技术选择都应服务于上述顺序，而不是为了增加技术栈数量。