# 独立 Agent 金标与歧义裁决

按用户“你自己跑，来打金标，不确定的再给我审核”的授权执行。原文和上下文先独立标注，封存后再比较机器预测。Agent 行始终保留 `human_verified=false`，逐字段人工答复另记，不发布生产分类。

本批数据绑定 `evaluation-snapshot-complete-20261005` 和 `evaluation-workbench-v2` 的不可变 hash。`reviewed_extraction.py` 保存逐题原文行、拆分、主题、题型、场次与追问依据；`build.py` 保存200条随机候选的独立决策及11条工程/SQL边界补充。技术话题解释为 NONE，明确构造任务才按 ENGINEERING，细则见 [标注指南](../../docs/annotation-guide.md)。

## 重现基础版本

所有命令在仓库根目录执行，Windows 使用 `.venv/Scripts/python.exe`。目录名必须新建；原有冻结数据不能覆盖。

```powershell
python -m evals.agent_gold.build --snapshot data/reports/evaluation-snapshot-complete-20261005 --workbench data/reports/evaluation-workbench-v2 --output data/gold/agent-reviewed-rebuild-v2
python -m eval.collect --dataset data/gold/agent-reviewed-rebuild-v2 --section extraction --split test --review-policy delegated_agent --adapter published --snapshot-export data/reports/evaluation-snapshot-complete-20261005 --output data/reports/agent-reviewed-rebuild/extraction.predictions.jsonl
python -m eval.collect --dataset data/gold/agent-reviewed-rebuild-v2 --section task_labels --split test --review-policy delegated_agent --adapter published --snapshot-export data/reports/evaluation-snapshot-complete-20261005 --output data/reports/agent-reviewed-rebuild/task_labels.predictions.jsonl
python -m evals.agent_gold.align propose --dataset data/gold/agent-reviewed-rebuild-v2 --predictions data/reports/agent-reviewed-rebuild/extraction.predictions.jsonl --output data/reports/agent-reviewed-rebuild
```

基础版本为32篇、511题、210条任务分类，C++模板题暂不纳入任务分类。仅逐字问题一致、忽略编号空白且原文跨度重叠时提议自动对应；本批412项如此提议，另外99项须逐项裁决，19项没有对应预测。proposal 不标成已审核。

对当前固定预测的99项决定已保存为 `data/reports/agent-reviewed-20261005-v2/alignment.decisions.json`。每项有 prediction_id 或明确 null 及依据，只能选择同来源跨度候选，最终一对一。只有结果内容 hash 与既有预测一致时才可复用该决定：

```powershell
python -m evals.agent_gold.align finalize --proposals data/reports/agent-reviewed-rebuild/alignment.proposals.jsonl --decisions data/reports/agent-reviewed-20261005-v2/alignment.decisions.json --output data/reports/agent-reviewed-rebuild/alignments.jsonl
```

## 用户裁决与新版本

用户已明确确认 C++ 模板题的焦点为 MIXED、《boss java一面》的公司为 BOSS直聘。原答复在 `data/reports/agent-reviewed-20261005-v3/user-decisions.json`。命令仅修改对应待审项，并生成父版本绑定的新封存，不把整篇或整条分类伪装成人工金标：

```powershell
python -m evals.agent_gold.adjudicate --base data/gold/agent-reviewed-rebuild-v2 --decisions data/reports/agent-reviewed-20261005-v3/user-decisions.json --output data/gold/agent-reviewed-rebuild-v3
```

v4仅补充审核过程说明，标签与v3一致；首次v1标注先于预测比较，v2曾在比较后依据原文裁决拆分及岗位口径。保留历史版本和过程说明，当前不称为未见测试集：

```powershell
python -m evals.agent_gold.adjudicate --base data/gold/agent-reviewed-rebuild-v3 --provenance-note data/reports/agent-reviewed-20261005-v4/provenance-note.json --output data/gold/agent-reviewed-rebuild-v4
```

用户随后确认“LRU 缓存裁定为手写”，原答复在 `data/reports/agent-reviewed-20261005-v5/user-decisions.json`。生成当前冻结数据 [v5](../../data/gold/evaluation-agent-reviewed-20261005-v5/manifest.json)，旧题型映射为ALGORITHM，移除题型待审标记；此前两条答复保留在完整人工裁决记录中：

```powershell
python -m evals.agent_gold.adjudicate --base data/gold/agent-reviewed-rebuild-v4 --decisions data/reports/agent-reviewed-20261005-v5/user-decisions.json --output data/gold/agent-reviewed-rebuild-v5
```

211条分类中200条来自随机候选，11条为边界补充，两部分分别报告，不视为生产分布的无偏估计。当前待审0项；LRU所在文档漏掉了全部19题，用户裁决不会消除这些FN，也不凭旧题型推导新的coding_focus标签。

## 评分与范围

复跑命令见 [操作手册](../../eval/README.md)。本轮只审计既有 r293 抽取/分类输出，没有新模型推理。抽取 F1 96.09%，分类联合准确率75.36%；公司字段纳入用户裁决后为25/31，题型362/492。抽取门槛未通过，分类门槛尚待校准。

`report.py` 从冻结数据、绑定其hash的抽取/分类运行目录生成HTML、Markdown和审核原文包；其他模块只引用 `supporting-runs.json` 指定的历史运行。当前入口为 [报告](../../data/reports/agent-reviewed-20261005-v5/summary.md)。

引用有效率只证明已引片段存在于原文，不能证明题干与约束引用完整；负文档只有2篇、显式追问只有3条，均须保留分母。版本、样本、归一化口径改变后的分数不能直接解释成模型提升。测试集已暴露问题，后续若据此改提示词，应把这些案例作为回归集，并另留未见来源评估泛化。
