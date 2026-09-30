# 本机演示步骤

本页提供可运行的流程，不代表真实语料质量已经验收。先启动 README 中的四个服务，运行 `ii status` 确认数据库和模型配置。导入完成并同步索引后才能搜索。

## 导入与进度

```powershell
uv run --locked --no-editable ii ingest --idempotency-key full-import-1
uv run --locked --no-editable ii run <run-id>
uv run --locked --no-editable ii retry-failed <run-id> --idempotency-key retry-import-1
```

导入返回队列编号。`processed_documents` 包含处理成功的排除文档；`excluded_documents` 单独列出排除数，真实面试场次和提问数应从统计接口读取。重复导入已生效且指纹一致的文件会跳过，不再次调用模型。

## 场景 A：统计与追溯

```powershell
uv run --locked --no-editable ii stats --company 得物 --limit 10
uv run --locked --no-editable ii detail <canonical-question-id>
uv run --locked --no-editable ii occurrences <canonical-question-id>
uv run --locked --no-editable ii source <revision-id> --line-start 1 --line-end 20
```

统计数字来自当前 PostgreSQL occurrence 全集；detail 和 occurrences 提供不可变来源引用。没有真实数据时返回空结果，不能把空结果当成演示成功。

## 场景 B：四路检索

```powershell
uv run --locked --no-editable ii search "Redis 为什么快" --pipeline BM25
uv run --locked --no-editable ii search "Redis 为什么快" --pipeline DENSE
uv run --locked --no-editable ii search "Redis 为什么快" --pipeline HYBRID
uv run --locked --no-editable ii search "Redis 为什么快" --pipeline HYBRID_RERANK
```

比较响应中的请求路径、实际执行路径和降级提示。交互降级不构成检索评测结果；真实性能比较仍需冻结 query gold。

## 场景 C：复习和缺口

```powershell
uv run --locked --no-editable ii review <canonical-question-id> WEAK --score 2 --idempotency-key review-1
uv run --locked --no-editable ii stats --sort gap --limit 5
```

此步骤会写入本地用户的真实复习记录。同一幂等键和相同请求重放不会增加第二条事件。

## 场景 D：工具问答

```powershell
uv run --locked --no-editable ii chat "Redis 高频问题有哪些"
uv run --locked --no-editable ii chat "我的复习状态"
```

检查工具轨迹、事实和来源。复杂自然语言路由仍需独立评测，不能从这两个问句推断完整任务成功率。
