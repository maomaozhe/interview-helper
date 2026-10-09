# 检索修复测试报告

2026-10-08。完整对照为 before-v2 → after-v7，共19个场景、各23轮；两条宽查询 guard 独立统计。
这是开发 Agent 题干审核，不是人工 Gold、全库 Recall 或固定 provider model weights 的保证；模型别名与返回 revision 不证明权重不可变。固定协议包含参与修复的已知失败场景，属于回归验证，不能据此宣称未见问题的泛化效果。
修复包括 SEARCH 推断题型及topic/coding来源软化、可信范围保留、对象/任务范围判定与严格逐候选隔离；r16在primary与scope critic都区分宽Agent/Harness模块查询和窄对象/故障查询。最终返回须scope critic确认；第二阶段使用同一配置模型，属于开发Agent核验，不是独立模型或人工Gold。global/ID错误仍整轮失败，结构异常行不返回。下述结果仅是本轮观察。

r15曾完整完成23轮主集和2轮guard：主集57/63确认相关，但宽guard只保留旧34项正例中的16项，丢失18项，Harness anchor为0/3。因此r15因质量回归未部署；服务完成不等于质量验收通过。其原文、审核、旧正式结果与baseline-r15快照单独绑定，不进入本轮r16分母。

另有两条已冻结SQL漏题诊断：缓存/数据库更新策略题被planner的topic_l1=Redis与topic_l2=缓存问题联合排除，单清任一仍不eligible；短信防重复题被coding_focus=ENGINEERING排除，清coding后eligible，annotation_status=KNOWN并非该题漏出的原因。r16按来源软化自动推断的topic/coding，并仅清理本轮自动KNOWN；显式UI与可信SQL范围保留。宽Harness原本没有topic SQL排除，其回归来自primary/critic范围误拒，单独修复。SQL反事实只说明资格，不证明清筛选后必然召回；最终效果以下述新回执为准。

来源审计使用search_filter_policy版本search_semantic_dimensions_origin_v1记录planned/applied/source/decision，状态保留explicit_ui/sql_list来源；题型偏好以宽候选保底和偏好候选支路扩召回，逐候选retrieval_provenance保留各支路与facet原rank，不为preferred-only候选伪造宽支路得分。

| 完整对照 | 请求成功/23 | 服务失败 | 动作/核验失败 | 相关/返回 occurrence | NEIGHBOR | UNCERTAIN | 微Precision/标注区间 | 宏Precision（非空有效query）/n | anchor命中 | 全请求中位/P90(ms) |
|---|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|
| before-v2 | 23/23 | 0 | 0 | 39/48 | 9 | 0 | 81.25% / 81.2%–81.2% | 89.17% / 18 | 16/19 | 40798/53220 |
| after-v7 | 23/23 | 0 | 0 | 69/80 | 6 | 5 | 86.25% / 86.2%–92.5% | 87.98% / 19 | 19/19 | 48775/56482 |

主集微平均81.25%→86.25%（+5.00个百分点）；宏平均89.17%→87.98%（-1.18个百分点，n=18→19）。微平均按返回occurrence加权，宏平均让每个有效非空query等权；两者方向不同，不能称全面改善。

HTTP 2xx 只表示传输状态；UNAVAILABLE、动作错误及核验失败单列，不能算服务验收通过。空集的 Precision 为 —，不写100%。P90用nearest-rank；全请求耗时保留失败尝试，不与已完成核验的SEARCH混合。

| 返回结果已核验SEARCH | 样本数 | 中位数(ms) | P90(ms) |
|---|---:|---:|---:|
| before-v2 | 21 | 41134 | 53220 |
| after-v7 | 21 | 49008 | 56482 |

该延迟仅统计返回结果已核验且题干审核未标REQUEST_FAILED的SEARCH；失败尝试仍保留在前表全请求延迟。PARTIAL只代表返回行通过，不表示整个候选池完成核验。

| 候选结构核验 | PARTIAL批次 | 排除异常候选 | 非空partial返回已核验轮 |
|---|---:|---:|---:|
| before-v2 | 0 | 0 | 0 |
| after-v7 | 0 | 0 | 0 |

空PARTIAL一律不可用，不算VERIFIED_NO_MATCH。部分核验可交付也须提示结果可能不完整；不将隔离的结构异常伪记为零。

| 保留集合（互有重叠，不合并） | 基准相关occurrence | 丢失 | 说明 |
|---|---:|---:|---|
| 本轮fresh配对 | 39 | 3 | before-v2已确认相关是本轮保留基准 |
| 历史扩样 | 38 | 5 | 其中3项before-v2也未确认；这些不全是本轮回归 |
| 宽查询guard | 34 | 10 | 两条宽查询独立保留集合；不套用主评测historical38 |

| 精确损失诊断（与原粗层级并列） | 保留/基准 | 损失层级计数 |
|---|---:|---|
| fresh_before_v2_39 | 36/39 | {"PRIMARY_BELOW_THRESHOLD": 2, "NOT_IN_CANDIDATE_POOL": 1} |
| historical_expanded_38 | 33/38 | {"PRIMARY_BELOW_THRESHOLD": 2, "NOT_IN_CANDIDATE_POOL": 3} |
| wide_guard_34 | 24/34 | {"EVIDENCE_INVALID_EXCLUDED": 1, "PAGE_CUTOFF": 5, "NOT_IN_CANDIDATE_POOL": 1, "PRIMARY_BELOW_THRESHOLD": 3} |

精确层级来自SHA绑定的r16-review-diagnostics.json，SQL池外资格来自r16-loss-diagnostics.json；comparison中的粗layer原样保留。EVIDENCE_INVALID_EXCLUDED表示引文核验失败（INVALID_EVIDENCE），不应按粗层级INSUFFICIENT解释为主题证据不足，更不是INVALID_SCHEMA。通过primary及critic后被PAGE_CUTOFF截断也不计作语义拒绝。

| 宽查询guard | 完成/计划 | 服务失败 | 相关/返回 occurrence | NEIGHBOR | UNCERTAIN | Precision区间 | anchor观察命中 |
|---|---:|---:|---:|---:|---:|---|---:|
| before-guards | 2/2 | 0 | 34/35 | 1 | 0 | 97.1%–97.1% | 1/3 |
| after-guards-v7 | 2/2 | 0 | 34/35 | 1 | 0 | 97.1%–97.1% | 1/3 |

guard的anchor为已审核回执中的观察命中；只有预期SEARCH且服务完成的相关结果才计入34项保留集合。LIST同ID不计保留。

**最终复核更正：主23轮为0次PARTIAL；正式两条guard中Harness有1次非空PARTIAL。** 同一候选 `e31bd959…` 因 `object_scope:literal_error` 被排除，另外15条有效结果带“结果可能不完整”警告交付；该异常候选不是旧34项guard正例。下面usage已将这次异常记为1条部分schema日志/1个排除候选，不另加一次成本。原质量复核中“正式完整对照无PARTIAL”的过宽句子以[勘误](../../data/reports/retrieval-fixes-20261008/r16-quality-review-addendum.md)更正，原文与发布决定保留。pilot的1次与正式guard的1次分开统计，critic完成不表示primary全池完整。

| usage组 | 模型操作日志/有token记录 | 失败日志 | RERANK_VERIFY日志/失败；input/output | 部分schema日志/排除候选 | 已知input/output tokens | 未知input/output日志 |
|---|---:|---:|---|---:|---:|---:|
| before-v2 | 70/70 | 0 | 0/0；0/0 | 0/0 | 304978/47945 | 0/0 |
| after-v7 | 89/89 | 0 | 19/0；20647/3353 | 0/0 | 338583/60407 | 0/0 |
| before-guards | 6/6 | 0 | 0/0；0/0 | 0/0 | 22315/5228 | 0/0 |
| after-guards-v7 | 8/8 | 0 | 2/0；4393/2506 | 1/1 | 28288/7901 | 0/0 |

r15未部署历史成本（独立记录，不并入上述终版成本）：

| 历史arm | 模型操作日志 | 失败日志 | RERANK_VERIFY日志 | 已知input/output tokens |
|---|---:|---:|---:|---:|
| after-v6（r15） | 91 | 0 | 19 | 352499/60793 |
| after-guards-v6（r15） | 8 | 0 | 2 | 26861/7870 |

离线测试：pytest 668通过、1跳过、0失败、0错误；Node 34通过、0失败。SHA绑定所选tests-r16.xml与node-tests.txt，不将测试通过等同于线上语义效果。
668通过、1跳过对应冻结r16 patch/wheel/image，发生在并行COUNT/ANSWER工作树新增之前，不证明当前workspace新功能通过；工作树变化不等于评测镜像变化，边界见SHA绑定的r16-implementation-review.md。

usage来自每轮原query_run绑定的ModelCall；预算前拒绝或取消可产日志而未实际请求provider，因此日志数不一概称provider调用次数。有token记录的日志另计，缺失usage不视为0。collector无HTTP重试不等于每个请求只有一次planning。r11/r12/r13失败尝试、未部署r15的91+8条操作日志、开发pilot与三个新探针另留档，不并入完整对照成本或耗时。第二阶段RERANK_VERIFY的日志、失败与用量独立列出。

| case/轮 | before-v2 相关/返回；动作核验 | after-v7 相关/返回；动作核验 | after anchor |
|---|---|---|---:|
| business-group-stock/1 | 1/1；通过 | 1/1；通过 | 1/1 |
| business-sms-login/1 | 0/0；通过 | 2/2；通过 | 1/1 |
| business-product-search/1 | 1/1；通过 | 2/3；通过 | 1/1 |
| business-payment-state/1 | 4/4；通过 | 2/2；通过 | 1/1 |
| infra-device-ordering/1 | 1/1；通过 | 1/1；通过 | 1/1 |
| infra-syn-flood/1 | 1/1；通过 | 1/2；通过 | 1/1 |
| infra-cache-consistency/1 | 9/9；通过 | 14/14；通过 | 1/1 |
| infra-redis-expansion/1 | 1/1；通过 | 1/1；通过 | 1/1 |
| ai-tool-quality/1 | 2/8；通过 | 2/2；通过 | 1/1 |
| ai-multimodal-navigation/1 | 2/2；通过 | 2/2；通过 | 1/1 |
| ai-api-failure/1 | 3/3；通过 | 8/8；通过 | 1/1 |
| ai-weekly-report/1 | 1/1；通过 | 1/1；通过 | 1/1 |
| clarify-api-then-redis/1 | 0/0；通过 | 0/0；通过 | 0/0 |
| clarify-api-then-redis/2 | 4/5；通过 | 11/11；通过 | 1/1 |
| clarify-api-then-redis/3 | 1/1；通过 | 1/1；通过 | 1/1 |
| clarify-ordering-then-product-search/1 | 0/0；通过 | 0/0；通过 | 0/0 |
| clarify-ordering-then-product-search/2 | 1/1；通过 | 1/2；通过 | 1/1 |
| clarify-ordering-then-product-search/3 | 1/1；通过 | 2/3；通过 | 1/1 |
| no-sample-tokamak/1 | 0/0；通过 | 0/0；通过 | 0/0 |
| no-sample-marine-metagenome/1 | 0/0；通过 | 0/0；通过 | 0/0 |
| challenge-tool-upgrade-quality/1 | 0/2；通过 | 7/12；通过 | 1/1 |
| challenge-api-blocked-recovery/1 | 4/4；通过 | 8/10；通过 | 1/1 |
| challenge-sms-dedup-implementation/1 | 2/2；通过 | 2/2；通过 | 1/1 |

剩余问题逐条保留：

- {"case_turn": "business-product-search/1", "kind": "UNCERTAIN", "canonical_question_id": "8e4a4420-1274-4a58-b0c9-2f58266749c6", "reason": "未说明实时搜索是否指输入联想、商品名模糊匹配或流式结果推送，网络协议问题与指定功能的紧密关系缺少证据。"}
- {"case_turn": "infra-syn-flood/1", "kind": "UNCERTAIN", "canonical_question_id": "807fff1d-b984-4351-a3fc-4b1aa03a9cb3", "reason": "未说明连接是否停在SYN半连接阶段，也可能是正常完整连接耗尽，不能确认等同SYN洪泛。"}
- {"case_turn": "clarify-ordering-then-product-search/2", "kind": "NEIGHBOR", "canonical_question_id": "0ac565f4-f2c4-4e31-9dcd-80354fad8352", "reason": "只问RabbitMQ消费顺序，未明确同设备消息或其到达乱序，须假设设备系统使用RabbitMQ才能匹配。"}
- {"case_turn": "clarify-ordering-then-product-search/3", "kind": "UNCERTAIN", "canonical_question_id": "8e4a4420-1274-4a58-b0c9-2f58266749c6", "reason": "未说明实时搜索是输入联想还是结果流式推送，指定商品模糊匹配任务与协议选型的紧密关系缺少证据。"}
- {"case_turn": "challenge-tool-upgrade-quality/1", "kind": "UNCERTAIN", "canonical_question_id": "d8e8cdce-a4f2-4a33-9a51-43f61103c9ed", "reason": "未说明评价对象是否为Agent或工具升级后的能力，只有输出结果评判不足以确认对象。"}
- {"case_turn": "challenge-tool-upgrade-quality/1", "kind": "NEIGHBOR", "canonical_question_id": "5429a087-bb6d-409c-bc79-021936857090", "reason": "明确对象为智能复盘产品，未说明Agent或工具升级，须补充产品与目标Agent的关联。"}
- {"case_turn": "challenge-tool-upgrade-quality/1", "kind": "NEIGHBOR", "canonical_question_id": "0a5e9cf3-7853-45a1-baa9-322673ab938b", "reason": "智能复盘指标未明确Agent或工具升级能力，不能仅凭评测字样匹配指定对象。"}
- {"case_turn": "challenge-tool-upgrade-quality/1", "kind": "UNCERTAIN", "canonical_question_id": "316e4dcc-5fa0-4b4c-a6f5-6d32e0268f5b", "reason": "未给出这套能力的对象，无法确认回归用例涉及Agent工具升级。"}
- {"case_turn": "challenge-tool-upgrade-quality/1", "kind": "NEIGHBOR", "canonical_question_id": "e2a75fc2-3665-40d6-86ee-dc76232fffbe", "reason": "异常发生后的链路定位未说明能力评测或升级非退化验证，诊断与验证属于邻近任务。"}
- {"case_turn": "challenge-api-blocked-recovery/1", "kind": "NEIGHBOR", "canonical_question_id": "52e756bc-33ce-4a94-9d38-473f03f993c1", "reason": "明确故障是错误工具选择及重复调用，未说明API或工具执行阻塞，不能把不同故障原因当成指定场景。"}
- {"case_turn": "challenge-api-blocked-recovery/1", "kind": "NEIGHBOR", "canonical_question_id": "b15ff1d1-172a-453c-8d9e-cd4ad33a01c4", "reason": "只问特定Flux内部处理，没有明确Agent API或工具执行对象，需要额外假定目标系统使用Flux。"}
- {"kind": "POSITIVE_LOSS", "group": "fresh_before_v2_39", "case_id": "business-payment-state", "turn_index": 0, "canonical_question_id": "62ad67eb-c7e0-4b6e-ab71-1bcabb5bf17b", "layer": "RERANK_REJECTED", "precise_diagnostic_layer": "PRIMARY_BELOW_THRESHOLD"}
- {"kind": "POSITIVE_LOSS", "group": "fresh_before_v2_39", "case_id": "business-payment-state", "turn_index": 0, "canonical_question_id": "5ddba396-b2d0-436f-9a05-4b930651796e", "layer": "RERANK_REJECTED", "precise_diagnostic_layer": "PRIMARY_BELOW_THRESHOLD"}
- {"kind": "POSITIVE_LOSS", "group": "fresh_before_v2_39", "case_id": "ai-tool-quality", "turn_index": 0, "canonical_question_id": "edb610d2-9faf-427f-a3c2-4f19882637be", "layer": "PRE_RERANK_FILTER_OR_CANDIDATE_MISS_NOT_YET_DISTINGUISHED", "precise_diagnostic_layer": "NOT_IN_CANDIDATE_POOL"}
- {"kind": "POSITIVE_LOSS", "group": "historical_expanded_38", "case_id": "business-payment-state", "turn_index": 0, "canonical_question_id": "62ad67eb-c7e0-4b6e-ab71-1bcabb5bf17b", "layer": "RERANK_REJECTED", "confirmed_in_before_v2": true, "precise_diagnostic_layer": "PRIMARY_BELOW_THRESHOLD"}
- {"kind": "POSITIVE_LOSS", "group": "historical_expanded_38", "case_id": "business-payment-state", "turn_index": 0, "canonical_question_id": "5ddba396-b2d0-436f-9a05-4b930651796e", "layer": "RERANK_REJECTED", "confirmed_in_before_v2": true, "precise_diagnostic_layer": "PRIMARY_BELOW_THRESHOLD"}
- {"kind": "POSITIVE_LOSS", "group": "historical_expanded_38", "case_id": "infra-cache-consistency", "turn_index": 0, "canonical_question_id": "ccbbec98-2806-4096-bcd8-689de48edb57", "layer": "PRE_RERANK_FILTER_OR_CANDIDATE_MISS_NOT_YET_DISTINGUISHED", "confirmed_in_before_v2": false, "precise_diagnostic_layer": "NOT_IN_CANDIDATE_POOL"}
- {"kind": "POSITIVE_LOSS", "group": "historical_expanded_38", "case_id": "infra-cache-consistency", "turn_index": 0, "canonical_question_id": "64ca452a-35e5-450b-bc5f-b77d95650722", "layer": "PRE_RERANK_FILTER_OR_CANDIDATE_MISS_NOT_YET_DISTINGUISHED", "confirmed_in_before_v2": false, "precise_diagnostic_layer": "NOT_IN_CANDIDATE_POOL"}
- {"kind": "POSITIVE_LOSS", "group": "historical_expanded_38", "case_id": "infra-cache-consistency", "turn_index": 0, "canonical_question_id": "54e07f6b-71b4-43c7-b410-93e1aae96090", "layer": "PRE_RERANK_FILTER_OR_CANDIDATE_MISS_NOT_YET_DISTINGUISHED", "confirmed_in_before_v2": false, "precise_diagnostic_layer": "NOT_IN_CANDIDATE_POOL"}
- {"kind": "POSITIVE_LOSS", "group": "wide_guard_34", "case_id": "guard-agent-design", "turn_index": 0, "canonical_question_id": "0d92a837-25a1-4bfe-a5fb-db555ccb1e29", "layer": "INSUFFICIENT_DIAGNOSTICS", "precise_diagnostic_layer": "EVIDENCE_INVALID_EXCLUDED"}
- {"kind": "POSITIVE_LOSS", "group": "wide_guard_34", "case_id": "guard-agent-design", "turn_index": 0, "canonical_question_id": "6d0c78e3-d07a-4d27-8702-d5705c108fde", "layer": "PAGE_CUTOFF", "precise_diagnostic_layer": "PAGE_CUTOFF"}
- {"kind": "POSITIVE_LOSS", "group": "wide_guard_34", "case_id": "guard-agent-design", "turn_index": 0, "canonical_question_id": "3349bf23-809b-452e-ad7c-87ab3b3eda1f", "layer": "PRE_RERANK_FILTER_OR_CANDIDATE_MISS_NOT_YET_DISTINGUISHED", "precise_diagnostic_layer": "NOT_IN_CANDIDATE_POOL"}
- {"kind": "POSITIVE_LOSS", "group": "wide_guard_34", "case_id": "guard-agent-design", "turn_index": 0, "canonical_question_id": "39943923-a31c-4a11-bccb-d520e80a45bc", "layer": "RERANK_REJECTED", "precise_diagnostic_layer": "PRIMARY_BELOW_THRESHOLD"}
- {"kind": "POSITIVE_LOSS", "group": "wide_guard_34", "case_id": "guard-agent-design", "turn_index": 0, "canonical_question_id": "c94e379c-73c5-48b1-8edb-89bc1a865a69", "layer": "RERANK_REJECTED", "precise_diagnostic_layer": "PRIMARY_BELOW_THRESHOLD"}
- {"kind": "POSITIVE_LOSS", "group": "wide_guard_34", "case_id": "guard-agent-design", "turn_index": 0, "canonical_question_id": "d77619bf-58a1-4796-a1cc-b67c0467e7a9", "layer": "RERANK_REJECTED", "precise_diagnostic_layer": "PRIMARY_BELOW_THRESHOLD"}
- {"kind": "POSITIVE_LOSS", "group": "wide_guard_34", "case_id": "guard-agent-design", "turn_index": 0, "canonical_question_id": "a727ea32-929a-409e-82c1-510fad33ac14", "layer": "PAGE_CUTOFF", "precise_diagnostic_layer": "PAGE_CUTOFF"}
- {"kind": "POSITIVE_LOSS", "group": "wide_guard_34", "case_id": "guard-agent-design", "turn_index": 0, "canonical_question_id": "e9eb5f6d-0ae6-4096-9cf4-b1a4abd4dd31", "layer": "PAGE_CUTOFF", "precise_diagnostic_layer": "PAGE_CUTOFF"}
- {"kind": "POSITIVE_LOSS", "group": "wide_guard_34", "case_id": "guard-harness", "turn_index": 0, "canonical_question_id": "9f51ca2f-5278-48cb-a759-2a4646733f6d", "layer": "PAGE_CUTOFF", "precise_diagnostic_layer": "PAGE_CUTOFF"}
- {"kind": "POSITIVE_LOSS", "group": "wide_guard_34", "case_id": "guard-harness", "turn_index": 0, "canonical_question_id": "003f282e-f6f2-440f-85ac-4faebb4d2fb5", "layer": "PAGE_CUTOFF", "precise_diagnostic_layer": "PAGE_CUTOFF"}
- {"kind": "GUARD_NEIGHBOR", "case_id": "guard-agent-design", "turn_index": 0, "occurrences": 1}

主集负例挑战：3项，1项进入保存候选池，1项第一阶段拒绝，0项scope critic拒绝，0项返回；另2项未入池，未实际覆盖重排拒绝路径，不能称全部负例均被重排正确拒绝。宽guard负例：0项，0项入池，0项观察返回。
未入自然rerank池的negative仅表示未覆盖；第一阶段BELOW_THRESHOLD/INVALID_EVIDENCE与第二阶段SCOPE_REJECTED分开计数，INVALID_SCHEMA是结构排除，PAGE_CUTOFF是页截断。SQL eligibility反事实仅说明筛选资格，不能称实际召回。

先前首次before采集因外部页面部署中断，保留6轮完成结果；r11候选完成8轮，其中5轮核验不可用，第9轮PENDING后停止，具体字段UNKNOWN。三个独立新search-only探针未重现原字段错误，不证明原故障根因。r12候选另保留9轮FINISHED、2轮核验不可用、1轮PENDING。两候选均不并入本轮23轮分母。

r12安全遥测定位到SYN的 `ValidationError@r[1].focus_relation:literal_error+1`，及缓存题的 `ValidationError@r[11].object_relation:literal_error+10`（后者共10个错误，仅首loc/type已知）。未捕获非法字面值，不能推断具体枚举值或其他9处错误；第10轮PENDING取消产生的ValueError不算新的模型结构错误。r13据此移除重复relation输出、宿主两轴派生，效果仍以完整新对照为准。

r13失败候选另保留7轮FINISHED、2轮核验不可用、1轮PENDING；安全首loc/type为ValidationError@r[21].object_scope:literal_error+3, ValidationError@r[13].object_scope:literal_error+4，具体非法值及其他错误未知。它与pilot-r14均不并入终版23轮分母、相关性或成本对照。

r13旧候选business-group-stock曾返回10项，题干审核标注{"DIRECT": 1, "NEIGHBOR": 8, "IRRELEVANT": 1}；该语义回归仅属于已停止的历史候选。

相同四题协议的独立开发pilot：r14为21/29相关（72.4%），r15为20/23（87.0%）；r15仍有NEIGHBOR 0、UNCERTAIN 3。UNCERTAIN不计为已确认相关，pilot空集不记100%。r14污染使完整after-v5从未运行；两个pilot的结果、成本和耗时均不并入正式23轮对照，不能用pilot改善代替正式验收。

r16另用六题开发pilot（含两条宽guard），确认相关53/56（94.6%），NEIGHBOR 1、UNCERTAIN 2，服务失败0轮。它与r14/r15四题pilot分母不同，不比较总体提升；也不并入正式23/2轮相关性、成本或耗时。

r16 pilot初排结构核验：COMPLETE 5轮、PARTIAL 1轮，隔离1个异常候选；非空partial交付1轮、空partial不可用0轮。scope critic为COMPLETE 6轮，仅指实际送检的合法候选，不能抹去初排PARTIAL或声称全池完整。逐轮状态保留在evidence.json。

r14/r15四题pilot未观察到真实逐候选结构隔离，该边界此前仅由合成测试覆盖；r16 pilot首次观察到真实隔离并交付非空partial。这个观察不代表全部异常路径已实测，完整新23+2轮仍单独验收。

六题pilot的旧宽查询正例保留23/34：未入保存Top50 2项、primary误拒2项、PAGE_CUTOFF 7项。分页项均已primary ACCEPTED且scope CONFIRMED，不能称为语义误拒；池外项只定位到重排之前，未做同scope召回回放。逐项题干与audit见绑定的pilot-r16-review-diagnostics.json。

pilot Harness anchor返回1/3；未返回项的model rank/层级为[{"rank": 22, "layer": "PAGE_CUTOFF"}, {"rank": 26, "layer": "PAGE_CUTOFF"}]。隔离的异常行均不属于旧34项确认正例，不能拿它解释旧正例损失。

pilot同scope同题干的各开发reference标签变化0项；历史扩样裁决差异另有1项，保留原标签变化而不计作检索改善：[{"case_id": "infra-cache-consistency", "turn_index": 0, "canonical_question_id": "64ca452a-35e5-450b-bc5f-b77d95650722", "question_text": "Cache-Aside旁路缓存有什么问题？", "old_label": "SUBTASK", "new_label": "UNCERTAIN", "same_label": false}]。

六题pilot仅含原短信登录问题，未包含工程短信challenge，因此不据此宣称coding_focus导致的工程短信漏题已修复。旧工具质量正例池外1项：[{"id": "edb610d2-9faf-427f-a3c2-4f19882637be", "question": "假设流程是「取数据 → 用 pandas 分析 → 给结论」，实际会出三种问题：模型跳过分析那一步、给假分析（拿上一次结果直接返回）、每次分析结果都不稳定。有什么工程化的办法解决。"}]。完整23+2结果另行验收，pilot保留/分页观察不替代正式结论。

同case的原文审核一致性见SHA绑定的review-consistency-r16.json；旧review-consistency.json为r15历史记录，保持原字节。这里只链接原记录，不推断所有裁决一致。

多轮只按逐轮结果报告；动作序列通过不等于对话状态绑定或完整主题切换已证明。
发布定位为定向增量：H01未全面解决，完整V1六阻断仍保留；具体质量判断与限制见绑定一致的release-decision.json和r16-quality-review.md。
已核对本地8000 API（Docker /interview-intelligence-api-1）使用评测候选镜像 `sha256:51f9186a59cd99a43472e4365c5d00987609d72a9d78144e771cb0ff9bc7b862`，21个安装文件SHA与manifest一致，health为query_agent_v10 / rerank_v16_scope_verified。本轮模型评测在localhost独立18008进行，不涉及SSH服务更新；部署后快照时间：2026-10-08T10:52:20.078312+00:00。

原始证据与SHA见同名evidence.json：

- [independent-acceptance.json](../../data/reports/retrieval-fixes-20261008/independent-acceptance.json)：`b05e86962afa8773cabda590b86d9714bf260585b4ab57143e3f4b757c6b03be`
- [comparison.json](../../data/reports/retrieval-fixes-20261008/comparison.json)：`28c06f79a3e9e7cd99769bcaa6c16306624cb1d19c23b7b78613c148c15d8ad9`
- [guard-comparison.json](../../data/reports/retrieval-fixes-20261008/guard-comparison.json)：`4d308eb69a657871e0552948acef9d8efdc2487509214a62ca906d1027fba421`
- [protocol.json](../../data/reports/retrieval-fixes-20261008/protocol.json)：`200928d9724b0ad0b26fd71d8dea46a56da9714b681f93c93ec86a49188c124b`
- [before-v2/current.json](../../data/reports/retrieval-fixes-20261008/before-v2/current.json)：`8b87f577eede7bec1694d2b8451c89f4854072118267b0c6e731c49d75984006`
- [after-v7/current.json](../../data/reports/retrieval-fixes-20261008/after-v7/current.json)：`8922d6ab2382a4c0cf94c42154a5ade3f56a2eed8167561c95e0692b3b46951a`
- [before-guards/current.json](../../data/reports/retrieval-fixes-20261008/before-guards/current.json)：`7ea42cbefb84efe2952aea069f172d1b370f8ea72f97eebde78e4b9a37dfffd6`
- [after-guards-v7/current.json](../../data/reports/retrieval-fixes-20261008/after-guards-v7/current.json)：`b08a1592a719f7e3587dfb095581132351f633280df15668a3e419e029b7e359`
- [before/current.json](../../data/reports/retrieval-fixes-20261008/before/current.json)：`358321f0f87ab7ee2440a6792c3fce539802531355bce4a489e0ddf1c618514c`
- [after-v2/current.json](../../data/reports/retrieval-fixes-20261008/after-v2/current.json)：`33d8c30e4bb790d7b6869868d97daf8108367fbe6c1aebe1f899379d6efc1184`
- [after-v3/current.json](../../data/reports/retrieval-fixes-20261008/after-v3/current.json)：`067d78d12a222c8576a5525c60cd6a546896c8fa686e465519bd8bbd48b0ee64`
- [after-v4/current.json](../../data/reports/retrieval-fixes-20261008/after-v4/current.json)：`6813f7440dd94c4fdd24623cfe6f5ec46c73cbe39a09aaad762021506bb691c9`
- [r11-failure-summary.json](../../data/reports/retrieval-fixes-20261008/r11-failure-summary.json)：`8b3f96841bd928ff907dda2d706f16eff8670a6417f9940ed567e10c82b3e08b`
- [r12-failure-summary.json](../../data/reports/retrieval-fixes-20261008/r12-failure-summary.json)：`92f46392e8fb84da76cf2357cbdb3fe7c56d57219ff102327c091b243a042fcf`
- [r13-failure-summary.json](../../data/reports/retrieval-fixes-20261008/r13-failure-summary.json)：`4b66ca80e1532861f1a1a07e34ce8ef749051eba72f5534d886e23fa44b22371`
- [r14-pilot-summary.json](../../data/reports/retrieval-fixes-20261008/r14-pilot-summary.json)：`ef4c57efc97f8e9b1f8239c00b634f8482e514f522d99fd3f5d9d8fb85a5fe62`
- [r15-pilot-summary.json](../../data/reports/retrieval-fixes-20261008/r15-pilot-summary.json)：`2c1c1c67441535ae92291f2354145a15881af4780d20221ff12d28697f9b1e51`
- [r16-pilot-summary.json](../../data/reports/retrieval-fixes-20261008/r16-pilot-summary.json)：`2ee90ed46ac9f21abadf16fd40bef054f00aece88cad3aa2eafb3a06f6ab53c7`
- [pilot-r14-metrics.json](../../data/reports/retrieval-fixes-20261008/pilot-r14-metrics.json)：`b8c04473d1514ffa001db5545bb1226ccb87d37e19b67a0d6aab0c94a272eed8`
- [pilot-r15-metrics.json](../../data/reports/retrieval-fixes-20261008/pilot-r15-metrics.json)：`c30111df0ea3335fcc77feb14057f2fdd7364b0d653d0bb37ddd002d8348b991`
- [pilot-r16-metrics.json](../../data/reports/retrieval-fixes-20261008/pilot-r16-metrics.json)：`5dc8b20ff2d3a858d73dbebc544e0abd02b085ce1f9d76a87bbcce1f854ff5e3`
- [r12-syn-failure-log.json](../../data/reports/retrieval-fixes-20261008/r12-syn-failure-log.json)：`ac6c8e63a98957dd1e849e96f7210df09e5b5ec48b996710b54e127762a0221f`
- [r12-cache-failure-log.json](../../data/reports/retrieval-fixes-20261008/r12-cache-failure-log.json)：`a31453077f9cf4f8f3e83fdac4aa65c23354c1f911a0e284aa737f9a3e47d6f4`
- [probe-r11-payment.json](../../data/reports/retrieval-fixes-20261008/probe-r11-payment.json)：`32deceb7876f176257c5efccdd8a064df42510ea1b63ff715007a2a702e529f0`
- [probe-r11-product.json](../../data/reports/retrieval-fixes-20261008/probe-r11-product.json)：`eadd00aded2585d91f9e7bf5366b7f871616697126b191461be52dc0c2702e49`
- [probe-r11.json](../../data/reports/retrieval-fixes-20261008/probe-r11.json)：`81245bf948bf841be4494da3fe6ee209b2d8dc3969c10e64659030fd5c45baab`
- [pilot-r14/current.json](../../data/reports/retrieval-fixes-20261008/pilot-r14/current.json)：`f7a83cbd06d20d5951aa04e89bbeb6e82d6b5d243ea99685405d0a092df80891`
- [pilot-r15/current.json](../../data/reports/retrieval-fixes-20261008/pilot-r15/current.json)：`8443b639542e75f8a1610857ab23ffa214b73129c7468e29e2dd93a5e2c34e00`
- [pilot-r16/current.json](../../data/reports/retrieval-fixes-20261008/pilot-r16/current.json)：`4519278b8d8456fb3a8c8ef3a9c5c1ca051161e979f5ab23a8de5703054cf59a`
- [review-consistency.json](../../data/reports/retrieval-fixes-20261008/review-consistency.json)：`ab970ceca4481cd60e371e272c19f8e68bf98baa354b2ead4e9c20cf52c7efc0`
- [review-consistency-r16.json](../../data/reports/retrieval-fixes-20261008/review-consistency-r16.json)：`a799d037ba92e1c9b46ce05e3482de493cdedd86cc6790fc3662a9764b7d0673`
- [r15-quality-failure-summary.json](../../data/reports/retrieval-fixes-20261008/r15-quality-failure-summary.json)：`705069676497f295484c4a3e05efd2f468cf9496808f9b3a28b963b71a66c0e2`
- [r15-loss-diagnostics.json](../../data/reports/retrieval-fixes-20261008/r15-loss-diagnostics.json)：`57ce6ea5778889d0f8511ef21343bc649911fa28d7d7147e13eae36732575c12`
- [r15-comparison.json](../../data/reports/retrieval-fixes-20261008/r15-comparison.json)：`e3c3f6884094bf66551c1c0b15fabf7410acd122285b38a731e933e4730d3edd`
- [r15-guard-comparison.json](../../data/reports/retrieval-fixes-20261008/r15-guard-comparison.json)：`3721fd83e0bae81690ac83416781ca5025ef64b4c862a0c7f84659b418b6a807`
- [r15-independent-acceptance.json](../../data/reports/retrieval-fixes-20261008/r15-independent-acceptance.json)：`13c0563cc1a6327d70bd29eaaab76eea971be67276e01ab963a5a9baab3c16e5`
- [after-v6/current.json](../../data/reports/retrieval-fixes-20261008/after-v6/current.json)：`4f52ff137564c445c3f6b2f677e4be46c913a0b513db76121b803e999925678a`
- [after-v6/reviews.json](../../data/reports/retrieval-fixes-20261008/after-v6/reviews.json)：`e3a4e2e9ebc5631a14af210cec5dcf32d984d45bbef9f721700de96327170c80`
- [after-guards-v6/current.json](../../data/reports/retrieval-fixes-20261008/after-guards-v6/current.json)：`741b750708ba7f4bfa49384e8f495e5fdd1dd886a361053b07f6c3e8119eabfe`
- [after-guards-v6/reviews.json](../../data/reports/retrieval-fixes-20261008/after-guards-v6/reviews.json)：`4019c7deb084e477a60e2ee6f8bdd77365340ef7ec512eeb3b8638f18cc0c0bd`
- [baseline-r15/deployment-manifest.json](../../data/reports/retrieval-fixes-20261008/baseline-r15/deployment-manifest.json)：`906600f2df1921bc248892ac55cb9440dc9a7b1eaa6472062669d2a7b3b98820`
- [pilot-r16-review-diagnostics.json](../../data/reports/retrieval-fixes-20261008/pilot-r16-review-diagnostics.json)：`3ac17933996ebb805709a89ab16bebd4f60636a3e7c927d0a5115b49061e7c78`
- [r16-implementation-review.md](../../data/reports/retrieval-fixes-20261008/r16-implementation-review.md)：`150a8d1cc67fbdf6a2bab33628318d99abe257439964e69118195fe2741bdf70`

报告构建器v2兼容旧r14指标的arms.after结构和critic统计字段缺失（保持未记录），并明确本地部署范围；发布决定绑定的原构建器与decision原字节保持，修订链记录原SHA及v2自身SHA，不改变业务或评测结果。
- [r16-loss-diagnostics.json](../../data/reports/retrieval-fixes-20261008/r16-loss-diagnostics.json)：`73683205d95691e806d9d0ef9fbe1d27ee98d6589a3f7098e9611508d036e630`
- [r16-review-diagnostics.json](../../data/reports/retrieval-fixes-20261008/r16-review-diagnostics.json)：`24167e75d2e95984fc494a382087e13a44dc91112b2692ad309347ac0c2e0468`
- [release-decision.json](../../data/reports/retrieval-fixes-20261008/release-decision.json)：`bbe5c2968c99ba4e1e06e0c570601858ae3c9c261fa0ba72a0362d9d080a1796`
- [r16-quality-review.md](../../data/reports/retrieval-fixes-20261008/r16-quality-review.md)：`8f17c93962e891e3c7f7e2e7634322e0d1b7112ee05bb9e9df5c5e6d3f64b325`
