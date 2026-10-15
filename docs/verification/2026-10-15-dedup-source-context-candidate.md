# 去重 v3 与独立原文证据候选

v3 已实现为显式实验选项。生产默认仍是 v1，未启用上下文、复核或新阈值。原有 gold、旧运行时封存与线上归并未修改。

上一轮真实 206 对的 v2+guard 未达到质量门：SAME precision 从 80.77% 到 81.82%，recall 从 97.67% 降至 83.72%；55 次复核中，拦住 4 个误并同时错误降级 7 个真同义。v3 因此支持先运行单阶段候选。尚无 v3 真实模型结果，下面的检查只证明接口、控制流与证据契约可用，不能证明语义质量改善。

## 变化与边界

`prompts/dedup_judge_v3.md` 加入通用完整范围规则：答案交集或包含关系不代表整体等价；已有项目实践事实与一般用途/可选方案不同；缩写需核实实体，不凭词形把不同标准概念合并。真实同义、错别字、可解析指代仍可 SAME。提示词没有 gold 问句、ID、答案映射或 canonical 标签。

`dedup/context.py` 的 `compile_source_evidence` 逐端校验原始文件字节 SHA-256、按 ingestion decoder 归一化的 Unicode offset、原始 quote，以及 `raw_question == "\n".join(source_spans.quote)`。目标 span 不能重叠或乱序。逐字保留空白，不受普通业务模型的自动 strip 影响。没有重新抽取或扩大目标题干。

证据分为目标题干与带原始位置的 `reference_only` 引用：最近 24 行内的一个 section heading、题干同一行前后缀、最多前 4 行与后 2 行。每条引用最多 500 字符，总量最多 4000 字符。前后引用可能包含独立邻题，v3 明确只允许用来解析本题对象、必要指代或同一条目的作答形式，不能加入邻题的任务、故障范围、代码条件或追问限制。缺少证据时保留原有文本调用。上下文不能恢复没有可观察信号的遗漏条件，也不保证短泛问一定能确定具体产品。

证据协议版本为 `dedup_source_context_v1`。每端有自己的 `source_hash`、`source_path`、`original_path`、真实 `raw_question`、`source_spans` 和带角色的 `references`。编译器不接收另一端、gold label 或 canonical ID。provider 检查证据与传入问题逐字绑定；provider 自身不读取磁盘，文件认证由编译器/调用方负责。生产 resolver 继续使用原有调用；本轮仅开放显式证据接口，没有全库写入或新增原文恢复机制。

## 接口

```python
compile_source_evidence(raw_bytes, source_spans, *, raw_question,
                        source_hash, source_path=None, original_path=None)

judge(incoming, candidate, *, incoming_evidence=None, candidate_evidence=None)

judge_many(incoming, candidates, *, incoming_evidence=None, candidate_evidence=None)

ProviderCollector(section, dataset, settings, gate, budget, *,
                  extraction_prompt_version=None,
                  dedup_prompt_version=None,
                  dedup_verify_equivalence=None,
                  dedup_source_context=False)
```

batch 的 `candidate_evidence` 是实际 candidate ID 到该候选独立证据的映射，可以只提供部分候选，未知 ID 或绑定到不同题干的证据在模型调用前被拒绝。batch 使用原有 c0… 别名输出，并在 telemetry 记录别名到真实 ID 的映射；逐项复核事件记录对应真实 ID。

新的 CLI 选项是 `--dedup-prompt-version dedup_judge_v3 --dedup-source-context`。复核保留原有 tri-state 参数：`None` 继承 settings，True/False 显式覆盖。建议本轮实际候选显式采用：

```python
dedup_prompt_version="dedup_judge_v3"
dedup_verify_equivalence=False
dedup_source_context=True
```

同样可显式使用 v3 无上下文作消融；没有自动启用规则。v3 首判 provider 版本是 `dedup_judge_v10_source_evidence`，显式开启复核是 `dedup_judge_v10_verified_source_evidence`。collection 指纹记录提示词、guard、schema、是否复核及 `source_context_version`；实际每次响应模型身份仍以 `model_revision` 为准，不能以请求别名证明同权重。

## 可观察性与验证

每个成功 `on_call` 事件新增 `parsed_result`，保存首判完整结构或 guard 全部布尔值/原因；`comparison_context` 保存端点证据 hash、batch ID 映射或具体 guard candidate ID。全部数据随调用局部变量传递，没有共享 `last_result` 状态。

collector JSONL 新增 `dedup_trace.first_stage`、`guards`、`final`，以及实际使用的两端 `source_evidence`。能够直接区别首判误差与 guard 回退，不再依赖最终 label 反推首判。失败和重试仍由 model_calls 保存；预算按实际输入和各次调用原有机制计算。现有 production worker 的 ModelCall 数据表仍只持久标准计费字段，本轮新增 payload 存在 provider telemetry 事件和 collector JSONL，未做数据库 schema 迁移。

验证结果：62 项去重 provider、候选、resolver、collector 与 canonicalization 相关单元/集成检查通过。新检查包括原字节/BOM/CRLF/span 校验、空白逐字保留、多 span、引用上界、左右源隔离、错误绑定零调用失败、batch 证据映射、首判与原始 guard 布尔保留、无上下文兼容和 v3 单阶段控制流。均无外部模型调用。

另按冻结 `source-reviewed-dedup-20261014-v2` 的 206 对逐端编译真实原文：412 端全部通过，45 个源文件；section 引用 330 条、前文引用 1411 条、后文引用 664 条、同行前缀 305 条、同行后缀 49 条，每端至少一条引用，实际最大引用总量 509 字符。全部来源仍是旧 r293 快照，不能声称新来源泛化。

实际评分应由新封存运行时在共享 Linux gate 统一采集后进行。v3 prompt、证据编译器、provider 与 collector 应共同封存；不要改写旧 sealed runtime 来复跑旧实验。
