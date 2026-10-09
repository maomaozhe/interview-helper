# 会话目标、范围继承与纠正：故障和修复记录

2026-10-08，用户指出 SSH 面经助手没有理解同一会话中的追问：“我说的是redis的高频率问题啊，这个会话不是才说过”，仍然返回 20 道 Redis 题。本次读取原始会话、重现公网行为，并增加独立会话目标状态及旧会话迁移。**首个 v12 候选因真实纠正失败而未发布；r2 隔离连续三轮及三个旧状态副本均通过，随后正式发布到 SSH。发布后公网同会话 12/12 检查通过，正式 API 的 SSH 隧道界面三轮与刷新恢复检查通过。**

本报告补充并更正 [上一轮意图能力报告](2026-10-08-agent-intents.md) 中关于裸总量问句的范围判断。HTTP 成功、Pi 执行成功和部署文件正确，均不能替代对实际问句语义的检查。

## 原始截图与五轮会话

截图对应正式 SSH 公网会话 `5cbdbc88-c2ad-4b33-b8a7-3ebab463612b`，标题为“Redis 高频问题有哪些？”。原会话五轮均持久化为 SUCCEEDED，`planning.provider` 均为 `pi`；前三轮使用 query v10，后两轮使用已部署的 query v11。会话跨越一次升级，不能把五轮都归入同一提示版本。

下表时间为 Asia/Shanghai。问句保留原始措辞，第三轮包含空格。

| 时间 | 用户原句 | 实际执行及结果 | 判断 |
| --- | --- | --- | --- |
| 18:13:32 | Redis 高频问题有哪些？ | v10 LIST，Redis，`top_n=null`，第一页 20 题；`total=result_total=152`，可继续翻页。 | 原始列表范围为 Redis。 |
| 18:14:57 | 下一页 | v10 NEXT，Redis，offset=20，返回 20 题，仍可继续。 | 延续原列表。 |
| 18:15:23 | `一共有多少条数据 ？` | v10 STATS，Redis，按 question 分组，只回答“返回 20 条结果。”；实际总集合为 152。 | 错把本页行数当总数；此处继承 Redis 范围本身符合上下文。 |
| 19:01:56 | 一共有多少道题 | v11 COUNT，清除 Redis，返回全库 2,452 道去重题 / 2,771 次提问 / 185 场面试 / 172 篇面经。 | COUNT 动作正确，范围错误。 |
| 19:02:28 | 我说的是redis的高频率问题啊，这个会话不是才说过 | v11 LIST，Redis，`top_n=20`，返回 20 题；`total=152`、`result_total=20`，没有下一页。 | 用户在纠正上一计数的范围，模型却切回列题，并自行增加 Top20。 |

对应 run ID，依次为：`a42458be-c451-4048-9638-fce639956287`、`2a4dc75d-e6c4-4218-b313-7198bd2ce229`、`b92f0113-efe0-4a6f-9217-d191ce6c0a5b`、`025fc12b-d15a-4603-b53d-6bfc8f3ad344`、`918400d3-0e59-4b02-b9d4-d9c2fe443a0e`。

公网健康回执为 query v11 / context v3 / rerank v10，提示 SHA-256 为 `1175453f40cb2672b2ce04b28d400f2b573f32274e0726cd778f0b8d95eac85a`，corpus / index revision 为 293 / 293。后两轮计划版本及原始模型事件也为 v11。因此这次故障发生在已生效的 SSH v11 上，证据不支持归因于“没有部署”。同期本地 8000 为 query v10 / rerank v16；本地仅检查最近 50 个会话，未命中该截图原句，不能据此推断全部本地历史。

## 对上一轮验收口径的更正

上一轮把“Redis 高频问题有哪些？”之后的“一共有多少条数据？”预期为全库 COUNT，并将清除 Redis 当作成功。这一测试预期本身错误。v11 提示明确把该裸总量问法默认成全库，最终十例虽然满足旧脚本断言，也不能证明本次连续对话的范围语义正确。

本轮采用以下目标规则：有明确会话目标时，未声明切换范围的“共有多少”“其中腾讯呢”应结合该目标；“全库”“整个题库”等明确全局问法才清除隐含范围，当前显式 UI 条件仍按契约处理。没有可归属的会话目标时，不能凭旧分页或旧计数随意猜范围。纠正句只重申 Redis 范围时，应修正上一 COUNT 的条件并继续计数；在已正确返回 Redis COUNT 后再次重申相同范围，也不应切回 LIST。

这不改变 COUNT 对完整匹配集合做 SQL 聚合的能力。问题在于把哪个集合交给 COUNT，以及把用户纠正理解成哪个任务。上一报告有关裸总量默认全库的表述，以本报告修订口径为准；其他能力和验证边界仍须分别阅读原报告。

## 根因和宿主修复

原始末轮 `model_request` 事件保存了实际发送的模型上下文：最近两条消息都是总量问句；`last_response` / `last_count` 指向错误的全库 COUNT；`last_plan` / `list_request` 仍指向 Redis STATS；没有独立的 `conversation_focus`。原始 Redis 首问已离开最近两条消息窗口，但 Redis 筛选仍存在。只有历史消息、旧列表和最近计数，无法明确表达当前正在完成的用户目标。

| 问题 | 本轮处理 | 验证边界 |
| --- | --- | --- |
| 裸总量强制全库，丢失 Redis 目标 | 修订 v12 范围继承提示，优先参考当前明确目标及显式筛选。 | 提示规则需要真实模型回归；不能由注入计划证明自然语言识别正确。 |
| 纠正范围时重新列题 | 新增 `conversation_focus`，保存终结查询 / 回答实际执行的 intent、message、filters、排序、Top N、复习范围及 run ID。r2 进一步说明：已正确 COUNT 后只重申范围，仍保持 COUNT。 | 首候选的真实纠正仍失败，因而未发布；r2 隔离连续三轮与三个旧状态副本均通过。 |
| 当前目标与可续页列表混在一起 | focus 与 `list_request` / 当前页 / 签名游标独立；COUNT 和 ANSWER 保留旧列表。新 LIST 可以切换目标，旧 `last_count` 留作回执，不得覆盖更新后的目标。 | 自动化覆盖范围切换、计数后续页和重启恢复。 |
| NEXT 在 COUNT 后取错目标原句 | 增加仅宿主使用的 `list_goal`，绑定原 LIST / STATS 的 message、intent 和 run ID；NEXT 使用其原目标，并记录 `source_action=NEXT`。SEARCH 清除旧列表目标。 | LIST / STATS → COUNT → NEXT 均有自动化断言；`list_goal` 不直接投影给模型。 |
| 中间读取或复习操作污染目标 | 仅成功的终结查询 / 回答更新 focus；中间读取、DETAILS、CLARIFY、复习读写保留目标。最终 ANSWER 记录其实际执行范围与回答依据。 | 显式计划测试覆盖这些状态转换；focus 不授予复习写权限。 |
| 原有会话没有新字段 | ContextCompiler 从可归属的 `last_response`、`last_plan` 和 COUNT 对应 `last_count` 投影旧 focus；仅模型投影，不写回原记录。旧 ANSWER 缺少范围依据时不把旧分页范围当回答主题。 | compact / noncompact 两条路径及重启有自动化覆盖；r2 三个真实旧状态副本均得到 COUNT Redis 152 / Pi。 |
| 未指定 N 却变成 Top20 | 提示区分 `page_size=20` 与用户指定的 `top_n`；没有明确 N 时应为 `top_n=null`，允许继续分页。 | 正式公网 Redis / Java 首问为 `top_n=null`；Java COUNT 后 NEXT 返回第二页且不重叠。I12 仅将本轮场景标为修复，其他问法不据此全部关闭。 |

COUNT 的可见回答同时标出实际公司 / 主题范围，以便用户识别范围错误。此项只改变范围说明，不替代精确 SQL 数量或模型意图判断。

## 真实复测：保留失败，不混合成功分母

**公网 v11 before：三例语义检查全部失败。** 在新诊断会话 `27c55050-0e03-4476-a8b3-e81b4673acce` 中，通过正式公网的 `POST /api/conversations/{id}/messages`、SSE 和运行回执执行三轮。三轮均执行成功、provider=pi，SSE 可用，但结果不满足语义断言：

| 输入 | 实际结果 | 失败断言 |
| --- | --- | --- |
| Redis 高频问题有哪些？ | LIST Redis，隐式 Top20，无下一页。 | 未指定 N 应 `top_n=null`；可继续分页。 |
| 一共有多少道题 | COUNT 全库 2,452。 | 应继承 Redis，返回 152。 |
| 我说的是redis的高频率问题啊，这个会话不是才说过 | LIST Redis，再列 20 题。 | 应保持 COUNT，并返回 Redis 152。 |

原始回执为 `data/reports/intent-retest-20261008/public-before-v11-20261008-195218-8e98281d.json`，其中 `passed=false`，三组失败检查均保留。HTTP / SSE / provider 成功不应写成三例通过。

**首个 v12 候选：连续新会话 2/3，旧状态副本 3/3。** 私有 SSH staging 连续三轮中，未指定 N 的 Redis 列表和随后 Redis COUNT 满足检查，但最后的范围重申仍被识别成 LIST。这个候选没有发布。另三个独立 legacy fork 各得到 Pi / COUNT / Redis 152，不能用这组成功覆盖连续对话的失败：

| 副本来源 | 提交原句 | 已完成记录 |
| --- | --- | --- |
| `025fc12b…` 的 `state_before`：旧 Redis STATS | 一共有多少道题 | COUNT Redis 152，Pi。 |
| `918400d3…` 的 `state_before`：错误全库 COUNT | 我说的是redis的高频率问题啊，这个会话不是才说过 | COUNT Redis 152，Pi。 |
| `918400d3…` 的 `state_after`：错误 Redis LIST Top20 | 我问的是一共有多少道题，不是让你再列一遍 | COUNT Redis 152，Pi。 |

旧状态复测使用 `eval-conversation-focus-20261008` 隔离用户，原会话只读，三个副本互不接续；没有使用原用户签名游标执行 NEXT。脚本走 UI 的消息提交接口及 GET run 轮询，**没有消费 SSE 帧**。旧会话读库、复制状态和回执 hash 的执行记录，须与自然语言结果一起留存。

首候选连续三轮回执为 `server-evidence-complete/public-stage-v12-20261008-195823-ca7111bf.json`，失败纠正 run 为 `d394c924-5bce-473e-8a34-1868e4171410`。该文件 `passed=false`；原始失败不因 r2 成功而覆盖。

**r2：隔离连续三轮 3/3、旧状态副本 3/3，通过后发布。** r2 仅修改提示和 Pi 工具说明，强化“已正确 COUNT 后，用户只重申范围时继续 COUNT”，宿主 focus / 分页实现未改。隔离连续对话首轮“Redis 高频问题有哪些？”得到 LIST / Redis / `top_n=null`，保留下一页；第二轮“一共有多少道题”和第三轮用户原纠正句均保持 COUNT / Redis / 152。三轮均由 Pi 完成，不能与首候选的失败轮混合成成功分母。

r2 连续三轮回执为 `server-evidence-complete/public-stage-v12-r2-20261008-200515-89d9f0ca.json`，会话为 `26cb38cd-7788-4cef-94ab-1c69124391cc`；三轮 run ID 为 `ac777043-c1e1-47aa-8f01-1f45f625c1fc`、`2994d9eb-4701-46b1-9268-27d93e38f7cf`、`667460d2-0941-41f6-a5fc-4502244bc9d2`。该组消费 SSE，与 legacy 脚本的 GET run 轮询分别记录。

r2 另行从上表三个原始状态建立三个独立 legacy fork，三例均得到 COUNT / Redis / 152 / Pi；观察到的工具动作均只有 COUNT。两组 legacy 清单独立留存：

| 版本 / 私有目录（均位于 `server-evidence-complete/`） | manifest SHA-256 | 结果 |
| --- | --- | --- |
| 首候选 `legacy-focus-replay-20261008T120016Z-90a6df9fa666/` | `d4e979851a64145dbe882b880e069436fce28aae8a987b169a690799acf6d623` | 3/3；不替代首候选连续对话的失败。 |
| r2 `legacy-focus-replay-20261008T120605Z-b4fae064b8b1/` | `a52c6916923da0082c2c0770b4715f10e0f3b431260a0f471fd239821785bf98` | 3/3，全部 Pi / COUNT Redis 152。 |

两组清单的 `source_records_unchanged=true`，原会话、五轮状态 / 结果与持久事件的结构 hash 前后均为 `43ad597bfd4b434281f3ee58580857079b4880b756e2ce411e413aa42383e109`。r2 三例 run ID 为 `8854c570-458a-49f6-8f86-48e8a9c9ac54`、`2c13fc80-018c-498f-a6aa-88df2163edc4`、`4262c2ca-5b35-4482-87b7-15a473bcc5d6`。逐尝试 JSONL、独立结果与前后原始快照均保留，摘要见 [证据索引](2026-10-08-conversation-focus.evidence.json)。

## 发布后的正式公网与界面验证

最终公网通过 [SSH 面经助手](https://interview.101-47-18-72.sslip.io/) 的消息提交接口、SSE 和 GET run，在一个新会话 `916da162-c5f2-4a7c-8b8f-742bfa3c5bbd` 中完整执行 12 轮。`public-after-v12-r2-retry-20261008-201152-8d4bf6f2.json` 为 `passed=true`、`failures=[]`，12 轮 provider 均为 `pi`、提示均为 query v12、SSE 检查均通过。

| 轮次 | 原句 | 核对结果 |
| --- | --- | --- |
| 1 | Redis 高频问题有哪些？ | LIST Redis；总集合 152，返回20，`top_n=null`，有下一页。 |
| 2 | 一共有多少道题 | COUNT Redis：152 道题 / 179 次提问 / 63 场面试 / 61 篇面经。 |
| 3 | 我说的是redis的高频率问题啊，这个会话不是才说过 | 保持 COUNT Redis 152，未改成 LIST。 |
| 4 | 那整个题库一共有多少道题？ | COUNT 明确全库：2,452 / 2,771 / 185 / 172。 |
| 5 | 我刚才是问 Redis 的数量，不是整个题库 | 修正范围回 Redis，保持 COUNT 152。 |
| 6 | 其中腾讯呢？ | COUNT 腾讯 / Redis：16 / 17 / 7 / 7。 |
| 7 | 现在换个话题，列出 Java 高频问题 | LIST Java；清除腾讯公司条件，`top_n=null`，第一页20，总集合232。 |
| 8 | 这些一共有多少题？ | COUNT Java，未退回旧 Redis 计数：232 / 285 / 68 / 66，公司仍为空。 |
| 9 | 下一页 | NEXT Java，offset=20，返回20；与第7轮20题的 ID 重叠为0。focus 为 LIST、`source_action=NEXT`，保留原句“现在换个话题，列出 Java 高频问题”。 |
| 10 | 解释一下 Redis 为什么快，三个主要原因，200字以内 | ANSWER / EXPLAIN / GENERAL_KNOWLEDGE。 |
| 11 | RDB 和 AOF 有什么区别，怎么选？300字以内 | ANSWER / COMPARE / GENERAL_KNOWLEDGE。 |
| 12 | 刚才的 AOF 重写为什么能缩小文件？200字以内 | ANSWER / EXPLAIN / GENERAL_KNOWLEDGE，延续上一回答。 |

表中四个数量依次为去重题目、提问记录、面试场次、面经篇数。Java 数量和公司清除、两页 ID 交集及 NEXT 原目标另直接核对完整回执；不把分页当前返回20误作总题数。最终 JSON SHA-256 为 `70b9e948faefcfac65bfc8437fd6473519a4d7b7c6b400e3ad9bd220f2f5f44b`，逐轮 run ID 在证据索引中保留。

**首次公网 after 采集中断单独留存。** `public-after-v12-r2-20261008-201014-df8122e8.json` 仅有前四轮已完成检查；第5轮请求被接受后，Windows 文件保存 / 替换触发 `PermissionError`，采集未闭环。原 `.json` 及 `.tmp` 都保留，后者第5轮 `checks={}`，两者均没有最终 `passed`。这属于采集失败，不能把待完成轮算作完整通过，也不能据此认定模型第5轮失败。保存脚本增加有界重试后，另开上面的新会话重新完整执行12轮；没有把旧四轮拼入最终成功分母。

**真实界面走正式 API 的 SSH 隧道。** 内置浏览器直开公网遇到 `ERR_BLOCKED_BY_CLIENT`，Chrome 自动化不可用，故通过 `localhost:18085 → SSH 127.0.0.1:18082` 访问正式 API 页面。这是正式服务的页面验证，不能写成公网浏览器点击成功，也不是私有 staging UI。

界面新会话 `22dc1e3e-5851-46e1-ad0f-6648c902ad76` 连续输入 Redis 高频首问、裸总量和用户原纠正句，结果为 LIST → COUNT → COUNT，后两轮都显示 Redis 152 / 179 / 63 / 61。刷新并回到“面经问答”后，原会话及两轮正确计数恢复。`formal-ui-v12-r2-correction.jpg` 及 `formal-ui-v12-r2-{correction,history}.dom.txt` 留证；DOM 与截图核对到相同纠正句、范围说明和数量。这组界面检查不替代上面通过公网地址完成的12轮 API / SSE 证据。

## 工程检查与包归属

| 检查 | 已完成结果 | 范围 |
| --- | --- | --- |
| 当前工作区 Python | 767 passed / 1 skipped | 工作区含并行检索修改；不是 SSH 安装包的完整质量证明。 |
| Pi / Web Node | 43 passed | 运行时和前端检查。 |
| 冻结首个 v12 包 Python 专项 | 126 passed | 对实际候选叠加包单独检查；首候选真实连续对话仍失败。 |
| r2 提示 / 工具说明变更专项 | 51 Python + 43 Node passed | 证明约束与工程回归通过；真实语义另以 r2 连续三轮和 legacy 三例记录为准。 |

这些检查范围重叠，不相加为去重测试数。`test_conversation_focus.py` 新增 22 例显式计划回归，覆盖目标、范围、分页、复习操作及旧状态迁移；本轮没有以 fake planner 声称自然语言纠正通过。工程记录为 `engineering-checks-20261008-200251-bf82da43.json`；该文件记录前三组，r2 专项为随后已完成的执行检查，不能将该 JSON 当作四组命令的原始完整日志。工程摘要与文件 hash 见证据索引。

候选以已发布 v11 冻结包为基线，仅叠加六个意图相关文件：harness、query_contract、query_service、config、v12 提示及 Pi runtime。构建元数据中，r1 → r2 只有提示和 runtime hash 改变；其余四个文件相同。首候选提示为 12,745 UTF-8 字节，r2 为 13,376 字节。

正式 SSH 已安装上述六个 r2 文件，发布前备份为 `/home/dylan/services/interview-intelligence/backups/conversation-focus-v12-20261008-200731`，发布检查 `retrieval_preserved=true`。r2 归档 `conversation-intent-v12-r2.tar.gz` 的 SHA-256 为 `bdfde4ac39143f5bc6c5caba22f89f2a6bd472931c7e5b38efec744db64a8cf7`，提示 SHA-256 为 `ccdd630a7eb8967732a9b46ad96b29828fa99dd8be8929648344e0ea8bf4b17c`。

20:14:05 的独立只读核验保存在 `final-deployment-verification-20261008T121405Z-0af86f73.json`，SHA-256 为 `48c0b282e6b2ea24ba9045359bc23c1c73d440b8980ccdd87762dc23b5d14a47`。六个正式安装文件均与发布回执、冻结清单和打包源码 hash 一致；三个 r10 检索文件 hash 保持；七个指定正式服务均 active / running，没有将 staging 服务计入。正式健康为 query v12 / rerank v10，数据库 / 索引 ready，revision 293 / 293，Fast Decision 关闭。该回执 `all_checks_passed=true`；部署一致性与上面的真实问句验证分开判断。本轮没有执行真实回退演练。

SSH 检索模块继续按冻结 r10 基线处理。本地 query v10 / rerank v16 是另一轮检索部署，**没有据本次候选构建宣称本地 r16 已发布到 SSH**。完整检索评测及 V1 六项门禁没有重跑，H01 和原有质量阻断保持开放。

## 证据留存与访问路径

原始会话、完整模型 context / SSE、用户状态副本及执行回执放在被 Git 忽略的私有 `data/reports/intent-retest-20261008/`。本报告只保留必要原句、run ID、摘要和 hash；不提交原始会话 / 语料全文或凭据。认证信息仅在程序内从受保护配置 / 环境读取。

| 证据 | 用途 |
| --- | --- |
| `public-ssh-recent-20261008-194607-9c0b626c.json` | 公网健康、最近会话与截图原句正向匹配。 |
| `local-8000-recent-20261008-194607-9c0b626c.json` | 区分本地版本；仅最近 50 会话检查。 |
| `ssh-exact-conversation-20261008-194830-b99de270.json` | PostgreSQL READ ONLY 导出的原五轮 before / after 状态、结果和持久事件。原始 `model_request` 与另存的重建 context 分开标记。 |
| `read-only-findings-20261008-195520-68e28fad.json` | 五轮摘要、实际原始 model context、来源回执 hash 和检查边界。 |
| `public-before-v11-20261008-195218-8e98281d.json` | 正式公网消息 / SSE 三例失败。 |
| `build-metadata-v12.json` / `release-manifest-v12.json` | 未发布首候选的构建范围。 |
| `build-metadata-v12-r2.json` / `release-manifest-v12-r2.json` | 已完成隔离语义验收并发布的 r2 构建范围。 |
| `replay_legacy_focus.py` | 旧状态隔离复测脚本；每次尝试生成唯一目录并及时持久化，不覆盖失败。 |
| `server-evidence-complete/` | 两个独立 stage 连续三轮、r1 / r2 两组 legacy 的逐次日志、结果、manifest 及原会话前后快照。 |
| `public-after-v12-r2-20261008-201014-df8122e8.json` / `.tmp` | 首次公网 after 的四轮完成及第5轮未闭环，保留采集失败。 |
| `public-after-v12-r2-retry-20261008-201152-8d4bf6f2.json` | 新会话正式公网12轮完整成功，消息 / SSE / GET run 回执。 |
| `formal-ui-v12-r2-correction.jpg` / `formal-ui-v12-r2-{correction,history}.dom.txt` | 正式 API 的 SSH 隧道页面三轮、纠正计数和刷新后历史恢复。 |
| `deployment-receipt-v12-r2.json` / `final-deployment-verification-20261008T121405Z-0af86f73.json` | 发布备份、六个安装文件、三个保留检索文件、七个正式服务及独立健康核验。 |

正式公网 API 消息 / SSE、正式 API 的 SSH 隧道页面、隔离 staging API 和服务器 loopback legacy 脚本是不同入口，用户及会话分开记录。私有原始文件与必要摘要 hash 通过 [随文证据索引](2026-10-08-conversation-focus.evidence.json) 对应；索引不复制凭据和原始会话全文。

## 验收结论与保留边界

本次原句、范围纠正及三种旧状态的定向修复已通过隔离真实验证、正式发布和公网12轮复验。正式 API 隧道界面三轮及刷新恢复也已验证；原截图会话及五轮记录在两组 legacy 复测前后 hash 不变。首候选失败与首次公网采集中断仍独立留存。

I12 仅将本轮 Redis / Java 未指定 N 及 Java 计数后续页场景标为修复；不把历史失败删除，也不据此宣称全部榜单问法、Top N 与复习集合组合或所有分页路径均通过。公网浏览器直连点击未通过本机工具验证，正式页面检查通过隧道完成。

这些问句已用于开发诊断和修订，是小样本定向回归，不是未见泛化评测。生成解释 / 比较只核对动作与依据，没有独立证明技术答案正确率。完整 V1 六项门禁、H01、检索残差、并发压力、全部恢复路径和真实回退演练不据本轮关闭。
