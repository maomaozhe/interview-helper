# QueryAgent spec 实施与验收（2026-10-05）

功能补齐已部署到开发环境 `http://127.0.0.1:8000/`；API、worker、Pi 使用新构建，PostgreSQL 迁移 `c21d4857a941`。迁移前备份：本机忽略文件 `data/pre-query-completion-20261005.dump`。功能补齐期间保留 `md/issue.md` 中用户已有记录，后续提交整理追加了当日验收摘要，历史故障仍按各条日期理解。

实现范围：偏好与分类核验页面；异步 run、PG事件流、取消、原请求恢复；完整 SQL 排名与分组游标；Top N 前后复习筛选；顺序多步骤工具；写意图与稳定 action ID；整轮 token / deadline / 调用预算；真实上游 SSE 与 TTFT；跨请求统一 model-call trace。结构化 SQL 不调用模型，复杂语义检索保留 HYBRID 及显式重排。

## 工程验证

- Python 全量：266通过、1跳过，跳过项为默认未启用的真实 Elasticsearch。
- 真实 Elasticsearch 单独启用：1通过。
- 固定真实 Pi 核心与前端逻辑：14通过，包括顺序多工具、工具参数受限修复、终止与取消。
- 新集成回归覆盖：全范围Top40；BEFORE / AFTER不同集合；importance / gap 与独立公式一致；分组 keyset 全页；偏好 CAS / 删除 / 用户隔离；写入提交前后故障恢复；run receipt / SSE / 取消 / 过期lease；分类草稿原子发布、版本变更拒绝、MIXED；读取检查点恢复与新event cursor。
- 流式单测：真实增量在provider完成前交付；半截和length结尾不能完成；完整工具参数组合；TTFT / token记录；成功失败均释放共享门；预算不足不进入provider。

随后新增 SQL 分阶段计时、机器未知标签状态一致性、偏好首次创建并发 CAS 的受限重读与页面恢复提示，受影响集成测试19项及分类 / 导入 / 恢复24项通过；Node / 前端14项再通过。真实PG的importance / gap各前100题通过37+37+26 keyset分页，与独立旧公式的ID顺序及分数一致；原始报告为 `data/reports/query-score-pages-20261005.json`。Git非忽略文件的4个配置密钥泄漏扫描与diff空白检查通过。

## 真实数据与 provider

语料 / 索引revision293、标注revision30，172生效来源、185场面试、2771 occurrences、2452 canonicals。2703次明确分类、68次UNKNOWN；人工 VERIFIED 目前为0。开发 `.env` 明确选用 `TASK_ANNOTATION_POLICY=KNOWN`，生产默认 VERIFIED。

当前 KNOWN 范围为132唯一算法题与18工程代码题。与前一日134算法题的口径不同：新增 KNOWN 要求作答形式和任务焦点都非UNKNOWN，暂时排除两道作答形式未知的题目；MIXED允许两类匹配。排名正确性仅针对声明的当前分类范围，不能据此证明标签语义准确率。

`scripts/verify-query-mvp.py` 五组真实检查通过：自然语言Top40的40个ID、次数和顺序与独立完整PG SQL一致；幂等重放不新增模型调用；手撕代码为ENGINEERING+CODE且不继承旧TopN；二面继承；结构化20+20分页与当前页来源指代；HYBRID语义检索。真实Top40样本约7443ms，SQL工具121ms；手撕工程代码约8940ms，其中含1628ms强制间隔。这是单次样本，未当成分位数或模型准确率。

`scripts/verify-query-write.py` 在独立用户 `query-spec-verification-20261005` 上运行实际Pi / Ark / PG：只标记当前页3题MASTERED；重放原请求仍仅3条业务事件；普通用户local事件数不变。一次请求因受限修复使用2次规划，总时间约21.7s，provider分别8624/10191ms，真实TTFT1617/1642ms，token分别13224/14353。随后重建验证API，再由 `scripts/verify-query-restart.py` 重放同请求，结果一致、没有新增模型或复习事件。测试用户审计数据保留，临时验证容器结束后关闭。

原始本机报告（Git忽略）：`data/reports/query-mvp-smoke.json`、`query-native-write-20261005.json`。不将它们或自动回填结果冒充冻结人工gold。

## SQL 性能

`scripts/benchmark-query-sql.py`：100次GET、并发4，轮换算法Top40、工程CODE、importance、gap。P50 164.665ms，P95 246.022ms，最大399.787ms，21.978请求/s，期间ModelCall增加0。首次四例282–339ms；没有清空PG/OS缓存，不是严格冷缓存评测。

Windows宿主CPU i7-12650H，16逻辑处理器；WSL2 Linux5.15，PG16.9、ES8.19。API/worker同一共享模型锁，最大并发1，完成后最小间隔2秒。本报告没有模拟同时导入 / 索引 / 多用户模型排队，也没有证明生产容量或页面首结果P95。原始报告：`data/reports/query-sql-benchmark-20261005.json`。

## 页面与远端服务

浏览器核对两个新页面可用，待核验分类可打开不可变原文并高亮引用行；没有擅自发布真实数据为人工核验。隔离浏览器的“按公司统计算法题出现次数，每页5组”由Pi返回5组，共38组；下一页换为不同5组，HTTP页面耗时291ms，刷新恢复同一第二页。问答取消后显示取消状态，用原请求恢复后返回40题，旧失败事件不会再次终止新订阅。

Laya使用固定模型revision及L20 CUDA，经已有私有SSH隧道访问。用户systemd `interview-system-one.service` 已enabled / active / ready，用户linger原已开启；安装脚本只接管已部署目录中的确切进程。该服务不是官方Jev。此前20个中文探针仅1例完整关键字段正确，仍关闭正式decision路由；未校准provider即使配置active也会回退。未重启整台服务器，SSH隧道仍由开发脚本管理。

## 发布边界

功能代码与开发部署完成。完整人工分类 / 路由金标、置信度校准、检索四路消融、生产混合负载、完整V1出口仍待真实评估。关闭Pi或新router可保留PG会话 / 分类与独立SQL接口；任务过滤有单独开关。状态、偏好和写入范围均由宿主验证，Node没有上游模型密钥。
