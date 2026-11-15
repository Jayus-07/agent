# 进度

## 2026-11-15：接续实施

- 工作树：C:\Users\wh\.codex\worktrees\assistant-review-fixes\agent
- 分支：codex/assistant-main-followup-20261114
- 基线 HEAD：19eecf23302103f8431b9f2d59df43f6d9a0d202
- Git 状态中保留未跟踪的 .playwright-mcp/；不操作 evaluation-center-ui 工作树或共享知识库。

### 已完成的代码阶段

- SQL 结果卡片已接入 SSE、会话历史和企业助手页面，覆盖指标/趋势/排名/明细的展示与展开、CSV 导出；对完全对应的 SQL Markdown 表格去重。
- SQL 结果载荷有列数、行数、单元格和整体大小上限；失败结果、SQL 文本和大对象引用不作为结果卡下发。
- 本次确认项目 SQL Golden SC-117 将商品级退款率定义为退款金额除以商品销售额。修正旧“高退款率=退款次数”的示例，新增分别按商品聚合后关联的退款率示例，补齐退款率的候选事实表，并同步 Generator Prompt 与 schema 描述。
- 分类销售额示例仅匹配含明确明细计算和取消订单条件的问法，且必须具备其全部表；移除会把“退款最多”误当退款次数的含糊别名。
- .github/workflows/rag_smoke.yml 已纳入 backend/tests/sql/test_sql_router_examples.py。
- SQL 图表遇到缺失数值时改为显示明细表，避免折线跨过空档造成连续趋势的错觉。
- 前端 SQL 结果卡已显示行数时，答案区折叠完全重复的“查询成功，共 N 行/无符合数据（0 行）”模板句；保留自然语言结论、标题和其他上下文。
- 企业助手引用来源卡改为展示可对应正文 `[n]` 的编号、章节和页码，隐藏面向调试的相关度分数；PDF 有文档 ID 和页码时复用已有鉴权预览抽屉。
- Reporter 文本协议解析保留来源引用编号并按编号排序，避免前端引用卡不能准确对应正文编号。

### 已有验证证据

- 本阶段定向 SQL 示例/路由测试：12 passed（python -m pytest --noconftest -q backend/tests/sql/test_sql_router_examples.py）。
- 本阶段变更 Python 文件 py_compile 通过。
- 合并复跑 SQL 路由/示例、SQL 离线评测和 SQL Golden validator：27 passed（backend/tests/sql/test_sql_router_examples.py、backend/tests/evaluation/test_sql_eval.py、backend/tests/sql/test_golden_validator_gate.py）。
- 最新合并复跑 SQL 路由/示例、SQL 离线评测、SQL Golden validator 与 Conversation-first 关键路由/会话 ID 用例：80 passed。
- 补强退款率/退款次数分离断言后，SQL 示例与路由测试复跑：12 passed。
- Conversation-first 定向检查：普通对话与工具路由、会话 ID、产品能力边界、知识追问路由共 53 passed。
- RAG 来源定位/编号与反序列化定向检查：`backend/tests/rag/test_chunk_pages_locality.py` 21 passed。
- 跨后端 RAG done.sources、来源定位、SQL SSE 结果、Reporter 降级披露和消息元数据/历史载荷定向组合：5 个文件 61 passed；其中新增检查确认引用编号、页码和 doc_id 从 RAG step output 到 SSE `done.sources`。
- 引用来源卡和 SQL 图表/结果卡定向检查：三个 Vitest 套件 13 passed；覆盖引用卡预览触发、引用编号/页码展示、图表选型、缺值回退、明细切换和截断提示。
- SSE/历史状态、引用和 SQL 结果卡相关前端 6 个套件：63 passed（包含修正夹具后的复跑）；SQL 回答区重复行数收敛的定向覆盖也在其中。
- 修正 `chat.test.ts` 两处过期的澄清事件测试夹具（补齐协议必需字段；保留原有断言）。最新 `npx tsc --noEmit --pretty false` 只剩一项客服页面测试既有类型错误，未修改客服模块；本次实现和会话 store 无类型错误。类型检查整体仍失败，不能记为通过。
- RAG smoke workflow 合同检查：`tests/evaluation/test_rag_ci_workflow_contracts.py` 6 passed；`git diff --check` 通过。
- git diff --check 通过；smoke 清单已加入结果卡、消息历史编解码和 SQL 示例/路由测试。
- 此前结果卡阶段的定向后端测试 50 passed；前端五个相关套件 57 passed；git diff --check 通过。
- 完整 TypeScript 检查曾报告 3 个不在本次改动范围的既有错误；不代表本次类型检查通过。

### 未完成/下一步

1. SQL Golden 静态与离线校验已过；真实数据库结果级准确性待共享运行安全窗口。
2. Conversation-first 单测与产品隔离定向测试已过；API/SSE/历史连续性和真实页面仍待验。
3. 对 RAG 检索与引用做只读核验：确认有效索引代次/文件、候选证据与用户可见引用；不改资料。
4. 完成企业助手回复的用户体验验收，区分组件测试与真实页面/API 验收。
5. 共享 Docker、真实业务数据和真实 Provider 链路未验收前，整体状态保持 UNVERIFIED。

### 本次续做状态

- 引用编号和来源卡的本地往返链路已验证；仍未证明实际在线 RAG 本轮引用来自当前有效索引中的正确段落。此前只确认过一次检索降级 Trace（重排器约 4 秒超时）；Trace 中实际文档身份尚未核验。
- SQL 语义示例/Golden 关联和自适应卡片已覆盖离线行为；真实 SQL 结果级正确性、实际 SSE 与历史会话恢复仍需安全运行窗口。
- 页面/API 全流程、真实多轮会话连续性尚未验收；这不是组件测试通过即可替代的项。
- 当前代码与离线测试可以继续推进；另一个会话占用共享资料链路验收期间，不启动或重建共享 Docker。

## 2026-11-15：继续实施与引用回看补齐

- SQL 示例匹配增加否定范围识别，避免“不要查库存低于安全库存”等反向约束仍误命中相似示例；同时覆盖“不要查退款次数，改查最近 30 天订单量”的后续正向意图。
- 增加 SC-117 SQL 示例与 Golden SQL 的归一化一致性检查，验证商品退款率按退款金额 / 商品销售额计算，并先聚合后关联。SQL 示例、离线评测和 Golden validator 合并定向复跑：29 passed；相关 Python 文件 `py_compile` 通过。
- 发现非 PDF 引用即使有 `doc_id` 也无法从用户界面回看原文。复用既有双重鉴权原文件接口，新增 Markdown、TXT、CSV、JSON、XML、HTML 的纯文本预览；PDF 仍走 pdfjs 页码预览。文本内容按 React 文本节点显示，不执行 HTML；超过 20 万字符时截断并提示。未给 DOCX/XLSX/PPTX 增加未经验证的预览行为。
- 来源卡片和预览抽屉定向测试：`npx vitest run src/components/chat/SourceCard.test.tsx src/components/chat/SourcePreviewDrawer.test.tsx`，6 passed；覆盖文本引用可打开、无文档 ID/不支持的二进制来源不可点、授权请求头与纯文本安全呈现。
- `git diff --check` 通过。全前端 `npx tsc --noEmit --pretty false` 仍被既有 `src/cs2/customer-service-page.test.tsx:118` 类型错误阻断；本轮企业助手变更没有新增类型诊断，整体类型检查不能标记通过。
- 最近只读环境检查：另一个资料链路线程仍为 active；共享 `agent` Compose 项目有 20 个 running、1 个 restarting 容器，Windows 可用内存约 3.09 GB，Redis DB 1 的 `rag_index=0`、`agent=0`。遵守不并行重启/重建共享服务的要求，本次没有发起共享 API、SQL、RAG 或浏览器业务验收，没有修改资料、索引或其他工作树。
- `.playwright-mcp/` 证据目录继续保留。整体仍为 UNVERIFIED：真实数据库结果、在线检索引用和 Trace、Conversation-first API/SSE/历史连续性、企业助手真实页面回复体验都需在资料验收线程释放共享运行环境后继续核验。
- 继续审查 SQL 结果图表选择时发现，英文列名子串 `time` 会把 `lifetime_value` 误判为日期轴，可能将数值误画成趋势图。已改为识别明确日期字段边界或日期值格式，不确定时回退表格，并增加 `lifetime_value` 与 `orderDate` 紧凑年月回归用例；`npx vitest run src/components/enterprise-assistant/sqlResultPresentation.test.ts src/components/enterprise-assistant/SQLResultCard.test.tsx`：12 passed。
- Conversation-first 复核发现切换会话或离开页面后旧 SSE 可能继续运行；并且切换时只清空流状态会丢掉已生成片段。现在按活动 session/request 立即中止前端流并请求 `/chat/agent/abort`，把部分回答与 Trace 固化到原会话；终态答案不再被部分文本覆盖。`npx vitest run src/hooks/useSSE.test.tsx src/store/chat.test.ts`：46 passed，覆盖切换、页面卸载、停止和迟到事件隔离。全前端 TypeScript 检查仍只有客服测试文件 `src/cs2/customer-service-page.test.tsx:118` 的既有错误；`git diff --check` 通过。
- 扩展完成态竞争用例后，SSE/store 合并复跑为 47 passed。后端取消路由定向检查首次复现 401 的真实原因是本机配置了 `GATEWAY_TRUST_SECRET`，旧测试夹具只发用户头、没有模拟网关信任头；已让夹具显式使用测试密钥并补上信任头，不改生产身份校验。`D:\Python\python.exe -m pytest backend/tests/api/test_chat_stream_hardening.py -k abort -vv --tb=short -x --timeout=60 --timeout-method=thread`：4 passed，13 deselected。
- 本轮最新环境复核：`验收资料覆盖、切分与 Trace` 仍是 active；共享 Compose 项目仍为 20 running、1 restarting，Windows 可用内存降至约 1.75 GB（低于 2 GB）。Redis 队列只读查询返回 `NOAUTH`，没有取用或暴露凭据，因此队列状态未知。未启动/重建共享服务；真实 SQL、RAG 与页面验收继续待共享安全窗口。
- 补充验证前端实际调用的 Agent 专属取消路由：新增用例构造正确产品、租户、用户、session、request 作用域，确认 `POST /chat/agent/abort` 只触发对应 stop event；单测 1 passed。此前 `/chat/abort` 前缀兼容与身份隔离用例 4 passed。
- SQL 结果载荷再次对照 `execute_sql_tool` 的统一成功封套，发现其业务计数键为 `data.total`，而结果卡只读 `row_count`。现兼容两种后端契约，新增“只带部分 rows、total 更大”回归，避免把展示行数冒充总行数；`D:\Python\python.exe -m pytest --noconftest -q backend/tests/orchestration/graph/test_sql_result_cards.py`：6 passed。
- 本轮重新检查资料验收线程仍 active；共享 Compose 为 20 running、1 restarting，Windows 可用内存约 2.25 GB。未启动服务或访问共享业务链路，在线验收继续等待安全窗口。

## 2026-11-15：SQL 回复卡片与文本去重

- 将 SQL 回复体验收敛为分层呈现：先保留自然语言结论，再展示自适应结果卡；只有能与结构化结果确认一致的 Markdown 表格、行数和截断说明才折叠。结果载荷不完整时继续披露实际展示范围。
- 对照 Reporter 与结果卡发现两个重复/误导点：超过 20 行时 Markdown “仅显示前 20 行”脚注与卡片重复；上游报告的总行数大于已返回行数时，固定“查询成功，共 X 行”可能把预览行数说成总数。现在按卡片总数/展示数匹配折叠固定提示；截断脚注还要求卡片确实含有同样数量的预览行，否则保留原文。
- SQL 回复展示定向验证：`npx vitest run src/components/enterprise-assistant/assistantAnswerPresentation.test.ts src/components/enterprise-assistant/sqlResultPresentation.test.ts src/components/enterprise-assistant/SQLResultCard.test.tsx`，3 个文件、21 tests passed；覆盖结论保留、重复表格/计数/截断脚注折叠及无法确认时保留提示。`git diff --check` 通过。
- 该验证是离线组件级验收，不替代真实 SQL 结果、SSE/历史恢复和浏览器中的完整用户体验验收；共享资料链路会话仍 active，本轮未触碰共享服务或数据。
- 继续核验 RAG 低置信提示时发现，它无条件建议“点击下方来源核实”，而页面在没有结构化引用卡时没有可点击目标。现在企业助手和普通聊天都按本条消息是否含来源卡选择提示：有来源时引导打开原文，无来源时建议补充资料/业务/时间范围；检索降级等稳定状态仍显示自己的专属处理建议。单独组件测试 5 passed；与 SQL 卡片/回复展示合并定向复跑：`npx vitest run src/components/enterprise-assistant/assistantAnswerPresentation.test.ts src/components/enterprise-assistant/sqlResultPresentation.test.ts src/components/enterprise-assistant/SQLResultCard.test.tsx src/components/shared/RagAnswerHint.test.tsx src/lib/ragAnswer.test.ts`，5 个文件、26 tests passed。全前端 `npx tsc --noEmit --pretty false` 仍只报告既有 `src/cs2/customer-service-page.test.tsx:118` 类型错误；`git diff --check` 通过。
- SQL 趋势/排名图使用明确带 `%` 的字符串数据时，数值解析原先会去掉百分号，坐标轴和悬浮提示也没有补回。现只依据该列值是否都显式带 `%` 决定展示后缀；没有猜测其他单位。SQL 回复与 RAG 提示定向套件复跑为 5 个文件、27 tests passed；`git diff --check` 通过。类型检查仍只有既有客服测试 `src/cs2/customer-service-page.test.tsx:118` 错误。
- 多指标时间趋势原来共用一条纵轴；销售金额和退款率等不同单位会被放在同一尺度比较。现在每个数值指标单独绘制趋势小图，标题带字段名，坐标轴按该列显式单位格式化；时间分类仍取自同一查询结果。加入“销售额（元）+退款率（%）”组件用例；SQL 回复与 RAG 提示定向套件 5 个文件、28 tests passed，`git diff --check` 通过。全前端类型检查仍被同一个既有客服测试错误阻断。

## 2026-11-15：历史恢复保留答案展示状态

- 对照 SSE 与历史恢复确认：结束会话时原先只持久化 SQL 结果卡；RAG 来源卡、拒答状态、置信度和回复归因仅在当前 SSE 中，刷新/恢复会话后丢失。
- 新增 SSE/持久化共用的答案展示元数据提取函数。Runner 保存同一份 SQL 结果、来源、RAG 状态/置信度和回复归因；历史 API 仅对白名单 assistant 字段输出，来源字段做数量、长度、页码、分数校验，不泄露其他内部元数据；前端恢复后重建来源卡与提示。
- 定向验证：`D:\Python\python.exe -m pytest --noconftest -q backend/tests/rag/test_rag_answer_status.py backend/tests/memory/test_message_codec.py backend/tests/orchestration/graph/test_runner_trace_finalize.py`：40 passed；`npx vitest run src/store/chat.test.ts src/components/shared/RagAnswerHint.test.tsx src/components/chat/SourceCard.test.tsx`：3 个文件、44 passed。`git diff --check` 通过。
- 全前端类型检查仍只报告既有 `src/cs2/customer-service-page.test.tsx:118` 错误。此项是离线 Runner/序列化/API payload/前端 store 验收；真实刷新页面、来源预览权限和在线 RAG Trace 仍待共享环境安全窗口。

## 2026-11-15：历史重复回答合并展示元数据

- 回看历史去重逻辑发现，旧前后端双写会生成内容相同的连续 assistant 消息；恢复时直接丢弃后一个副本，可能连同 SQL 结果卡、RAG 引用和置信提示一起丢失。
- 调整为仍只显示一条相同回答，但把后续非空的来源/SQL 结果和已记录的状态、置信度、回复归因并入保留消息，不改变用户消息去重行为。
- `frontend/src/store/chat.test.ts` 增加首个副本缺少元数据、后续副本包含 SQL/RAG 展示信息的回归用例；`npx vitest run src/store/chat.test.ts`：1 个文件、38 tests passed；`git diff --check` 通过。
- `npx tsc --noEmit --pretty false` 仍只报告既有客服测试错误 `src/cs2/customer-service-page.test.tsx:118`；未出现本次企业助手改动的新类型诊断。全量类型检查整体仍为失败状态。
- 该修复扩大了历史恢复的离线覆盖；在线会话历史与页面刷新仍待共享环境安全窗口核验。

## 2026-11-15：SQL 自然语言摘要带入实际结果

- 核对结构化 SQL 的摘要调用发现，原摘要上下文只有表名和行数，没有查询问题、时间字段或指标值；因此卡片虽有数据，正文的一句话结论却无法具体回答用户。
- 摘要现在读取最多 3 行、6 列的实际返回值，兼容 SQL Agent 直出和 `execute_sql_tool` 的 `data.total` 封套，并附上原问题。提示词要求只回答数据支持的内容，不猜单位、时间范围和趋势，空结果或信息不足时如实说明。
- 定向验证：`D:\Python\python.exe -m pytest --noconftest -q backend/tests/test_reporter_prompt_contract.py backend/tests/agents/test_reporter_result_kind.py`：19 passed；覆盖 SQL 工具封套与 Agent 直出值、真实列值、0 行、缺明细和“总数大于预览”时的准确描述。Reporter 与 Prompt Registry `py_compile` 通过。
- 这是提示词输入与摘要上下文的离线验证，尚未通过真实模型确认实际措辞；真实 SQL/用户端展示仍待共享运行环境释放后验收。
- PromptService 可读取数据库中的已发布模板覆盖 YAML 默认值；未修改共享提示词数据，线上/共享实例是否使用新文案需在安全窗口核对当前 prompt 版本并走项目发布流程。

## 2026-11-15：SQL 卡片拦截不安全的大整数

- 检查图表解析时发现，数字字符串中的超长整数会回退表格，但已被 JSON 解析成 JavaScript `number` 的不安全整数仍可能进入图表并被四舍五入；表格和 CSV 也会显示这个无法确认原值的数字。
- 不安全整数现在不参与图表；表格隐藏对应值并说明精度问题，同时禁用可能导出错误数值的 CSV。正常后端大整数以字符串传递时仍保留原值。
- 定向验证：`npx vitest run src/components/enterprise-assistant/sqlResultPresentation.test.ts src/components/enterprise-assistant/SQLResultCard.test.tsx`：2 个文件、16 tests passed；覆盖大整数图表回退、表格提示与 CSV 禁用。
- 全前端 `npx tsc --noEmit --pretty false` 仍仅有既有错误 `src/cs2/customer-service-page.test.tsx:118`；未出现此次 SQL 卡片改动的新类型诊断。
- 这是前端防止精度错误扩散的离线检查；真实页面与数据库返回格式仍需在线验收。

## 2026-11-15：续验环境状态

- 重新读取资料验收会话 `验收资料覆盖、切分与 Trace`：状态为 notLoaded，最近一轮标记 failed，原因是 Codex 用量限制；没有向该会话发送消息，也未操作它的工作树。
- 共享 Compose 仍为 20 个 running、1 个 restarting，主机可用内存约 1.84 GB（低于 2 GB）；本轮未发起 API/RAG/SQL 页面验收，未启动、重建或重启服务，也未读写共享知识库。
- `.playwright-mcp/` 继续保留；待共享 Redis/资源状态恢复且资料链路验收可协调后，再做真实 SQL 结果、RAG 引用/Trace 和 Conversation-first 页面验收。

## 2026-11-15：非拒答 RAG 状态仍提示低置信度

- 核对 RAG 状态码发现前端按“answerStatus 是否为空”判断是否提示低置信度；非拒答状态（如 `ok`）会压掉 0.6 以下的核实提示。
- 现仅在存在已知拒答行动建议时优先展示该建议；普通/未知状态仍按置信度显示核实提示，并根据是否有引用给出可执行的下一步。
- `npx vitest run src/components/shared/RagAnswerHint.test.tsx src/lib/ragAnswer.test.ts`：2 个文件、6 tests passed。
- 复跑全前端 `npx tsc --noEmit --pretty false` 后仍仅有既有客服测试 `src/cs2/customer-service-page.test.tsx:118` 类型错误；SQL 卡片和 RAG 提示没有新增类型诊断。

## 2026-11-15：SQL 非零总数不能误报为空结果

- 核对后端成功结果封套发现，`total` 可大于 0 而预览 `rows` 为空；前端旧逻辑只检查预览行数，会把这种情况显示成“没有匹配记录”。
- 现在卡片区分三种情况：总数为 0 才说无匹配；总数非零但无预览行时说明有结果、明细暂不可展示；载荷因消息上限被截断时说明展示受限。
- 后端结果卡提取测试：`D:\Python\python.exe -m pytest --noconftest -q backend/tests/orchestration/graph/test_sql_result_cards.py`，7 passed。前端卡片和图表测试：`npx vitest run src/components/enterprise-assistant/SQLResultCard.test.tsx src/components/enterprise-assistant/sqlResultPresentation.test.ts`，2 个文件、17 tests passed。
- 全前端 `npx tsc --noEmit --pretty false` 仍只有既有客服测试错误 `src/cs2/customer-service-page.test.tsx:118`；本轮 SQL 卡片改动没有新类型诊断。

## 2026-11-15：会话历史 SQL 结果限容与白名单

- 对照 RAG 来源历史 payload 的白名单检查，发现 SQL 结果却直接透传数据库里的 `sql_results`；旧/异常 metadata 可额外带内部 SQL 字段、嵌套对象或超大数据。
- 历史接口现在仅输出卡片字段，最多 3 个结果集、12 列、20 行、单元格 160 字符和 48 KiB；大整数转为文本，异常嵌套单元格清空并标记截断，截去的列数也会如实写入。
- 定向验证：`D:\Python\python.exe -m pytest --noconftest -q backend/tests/memory/test_message_codec.py`：7 passed，覆盖 SQL 内部字段剔除、结果维度/总字节数上限和异常单元格处理。

## 2026-11-15：多步 RAG 引用编号统一

- 离线复现 Reporter 的引用错配：每个 RAG 步骤都从 `[E1]` 重新编号，报告直接拼接参考文献，而 SSE `done.sources` 只提取第一步；多步问答可能让正文、来源清单和企业助手来源卡展示不同资料。
- 多步汇总前为来源分配整轮唯一编号，按文档和页码/章节定位复用相同证据；同步重写 RAG 正文标记、合并参考文献和来源卡数据。单步 RAG 快速透传保持不变；多步 RAG 改走统一汇总，涵盖 LLM 与无 LLM 两种路径。
- 新增回归覆盖两步输出都含 `[E1]`、局部 `[E2]` 映射、最终引用清单、来源卡编号，以及 LLM 提示输入中的统一引用编号。
- 加入“旧索引无 doc_id、不同步骤同名同页文件”回归：身份不确定时按步骤分别保留，避免把两个来源合并。`D:\Python\python.exe -m py_compile backend/agents/reporter/reporter.py backend/tests/agents/test_reporter_multi_rag_citations.py` 通过；Reporter、提示词契约、RAG 页码引用和回复归因组合定向测试 `D:\Python\python.exe -m pytest --noconftest -q backend/tests/agents/test_reporter_multi_rag_citations.py backend/tests/agents/test_reporter_result_kind.py backend/tests/test_reporter_prompt_contract.py backend/tests/rag/test_chunk_pages_locality.py backend/tests/orchestration/graph/test_reply_source.py`：62 passed；`git diff --check` 通过。
- 经 `make_done_event` 验证浏览器最终消费的 SSE 完成载荷：断言 `done.answer` 与 `done.sources` 同时保留统一编号；覆盖 LLM 汇总、无 LLM 回复和旧索引身份缺失情形。
- 此为离线引用映射验收，不证明真实检索命中了正确资料或有效索引。共享资料链路仍由另一会话占用；在线 RAG/Trace、真实 SQL 和完整 Conversation-first 页面验收继续标为 UNVERIFIED。

## 2026-11-15：SQL 高精度小数保真

- 核对 SQL SSE 载荷到前端图表选型的精度边界：`Decimal` 作为原始十进制文本传输；超过 15 位有效数字的金额不转成近似图表数值，退回表格展示。
- 新增后端 Decimal SSE 单测，锁定 `1234567890123456.7800` 的尾随零和全部有效位；新增前端表格回归，确认精度不够时不画图且明细仍逐字显示原值。
- `D:\Python\python.exe -m pytest --noconftest -q backend/tests/orchestration/graph/test_sql_result_cards.py`：8 passed；`npx vitest run src/components/enterprise-assistant/sqlResultPresentation.test.ts src/components/enterprise-assistant/SQLResultCard.test.tsx`：2 个文件、19 passed；相关 Python `py_compile` 和 `git diff --check` 通过。
- 这是载荷和前端组件级保真验证，仍未覆盖真实数据库驱动返回类型和真实页面；真实 SQL 结果级正确性继续为 UNVERIFIED。

## 2026-11-15：摘要不再把长数值截断后当成事实

- Reporter 原来把 SQL 样例值截到前 80 个字符后仍交给模型；长金额/高精度数值会变成错误前缀。现在这类值从摘要样例中省略并明确标记“值过长，未纳入摘要；请查看结果明细”，不提供不完整数字作为事实。
- 同时收敛摘要总长度：只保留完整事实段，超过 4000 字时明确说明后续结果未纳入；BusinessInsight 长摘要也整段省略，不再截断成可能失真的半句话。
- 摘要提示词明确要求模型不得引用被省略的值或内容，问题依赖时引导用户看明细；新增高精度 Decimal、总长度整段裁切和长 BusinessInsight 回归。
- Reporter 提示词契约、结果渲染和 SQL SSE 结果定向测试：`D:\Python\python.exe -m pytest --noconftest -q backend/tests/test_reporter_prompt_contract.py backend/tests/agents/test_reporter_result_kind.py backend/tests/orchestration/graph/test_sql_result_cards.py`：30 passed；Reporter 和测试 `py_compile` 通过；`git diff --check` 通过。
- PromptService 可能优先读取数据库中已发布模板；没有修改共享 Prompt 数据。代码侧已经不再把长值前缀传给模型，但部署环境的实际提示词版本及模型最终措辞仍需在安全窗口验证。

## 2026-11-15：SQL 摘要标清预览范围

- Reporter 只把最多 3 行交给一句话总结；此前没有说明它们是样例，模型可能把前三行当全量数据回答“最高/最低/趋势”。现在只有行数契约与已交付明细完全一致且不超过 3 行时标为“全部结果”，其余情况标为预览，并禁止据此下全量排名/趋势结论。
- 行数元数据小于实际返回明细时显式标记不一致；没有可信总行数的空明细只说明“无法确认是否有匹配记录”，不误报“无匹配”。提示词和回归覆盖完整结果、预览、行数不一致及未知总数空结果。
- Reporter 提示词契约、结果渲染和 SQL SSE 结果定向测试：`D:\Python\python.exe -m pytest --noconftest -q backend/tests/test_reporter_prompt_contract.py backend/tests/agents/test_reporter_result_kind.py backend/tests/orchestration/graph/test_sql_result_cards.py`：33 passed；Reporter 和测试 `py_compile` 通过；`git diff --check` 通过。
- PromptService 可能优先读取数据库中已发布模板；没有修改共享 Prompt 数据。代码侧已经不再把长值前缀传给模型，但部署环境的实际提示词版本及模型最终措辞仍需在安全窗口验证。

## 2026-11-15：SQL 结果总数未知时不再误报无匹配

- 复现结果卡误报：SQL 成功结果没有 `row_count`/`total` 且明细为空时，SSE 提取器把空数组长度当成可信总数 0，前端因此显示“本次查询没有匹配到记录”。
- 结果卡现在携带 `total_rows_known`；后端只有拿到非负总数且该总数不小于实际返回行数时才标记可信。缺少总数或行数矛盾时，前端显示“总数未知 / 无法确认是否有匹配记录”；只有可信总数为 0 才显示无匹配。
- 历史 API 白名单保留该标记。旧历史记录没有保存来源信息，统一按总数未知处理；会话恢复原样保留标记。这样避免把旧卡片里推算出的 0 当成数据库事实。
- 定向后端测试：`D:\Python\python.exe -m pytest --noconftest -q backend/tests/orchestration/graph/test_sql_result_cards.py backend/tests/memory/test_message_codec.py`：18 passed。前端结果卡和图表测试：`npx vitest run src/components/enterprise-assistant/SQLResultCard.test.tsx src/components/enterprise-assistant/sqlResultPresentation.test.ts`：2 个文件、21 passed。Python `py_compile` 通过。
- 新增会话恢复状态回归后，前端会话恢复/结果卡/图表定向测试 `npx vitest run src/store/chat.test.ts src/components/enterprise-assistant/SQLResultCard.test.tsx src/components/enterprise-assistant/sqlResultPresentation.test.ts`：3 个文件、59 passed；`git diff --check` 和相关 Python `py_compile` 通过。
- 此为离线契约和组件验收，真实数据库是否稳定提供总行数、在线页面实际文案仍待共享运行环境空闲后验证。

## 2026-11-15：同一文档多页引用不再串号

- 新增端到端离线回归后复现错配：同一 PDF 的第 1 页和第 4 页分别对应 `[E1]`、`[E2]`，解析器却按文件名去重丢掉 `[E2]`；后续 RAG 步骤的 FAQ 因而占用全局 `[E2]`，用户正文指向了错误资料。
- 来源解析器改为按引用编号保留条目；整轮映射仍按文档 ID、页码和章节决定是否复用来源。不同页会有独立来源卡，相同位置仍可复用同一个全局编号。
- 红测确认旧行为会把 FAQ 错标为 `[E2]`；修复后 Reporter 多步引用、RAG 原文定位/文本解析、提示词契约和回复归因定向测试：`D:\Python\python.exe -m pytest --noconftest -q backend/tests/agents/test_reporter_multi_rag_citations.py backend/tests/rag/test_chunk_pages_locality.py backend/tests/test_reporter_prompt_contract.py backend/tests/agents/test_reporter_result_kind.py backend/tests/orchestration/graph/test_reply_source.py`：70 passed。Python `py_compile` 和 `git diff --check` 通过。
- 该证据覆盖来源解析、正文编号、末尾参考文献和 SSE 来源卡的离线往返；真实在线检索段落与当前索引内容仍需授权运行时核验。

## 2026-11-15：悬空引用不再误指向后续来源

- 新增回归发现正文中无来源条目的 `[E#]` 会原样残留，并可能被后续步骤的全局来源编号占用；单步直出也有相同问题。Reporter 现在将无法对应的显式引用显示为“引用来源未能对应”，同时保留可解析且有效的编号；多步中无参考文献的 RAG 步骤也会进入校验。
- 覆盖“部分来源缺失但后续编号复用”“整步无参考文献”“单步悬空引用”及单步有效引用保留。进一步复现 LLM 综合答案新增不存在的编号；Reporter 现在校验非流式和流式最终答案，流式校验会缓存跨 token 的半截引用标记，并确保发出的 delta 与 `done.answer` 正文一致。Reporter、RAG 来源定位、提示词契约和回复归因定向复跑：`D:\Python\python.exe -m pytest --noconftest -q backend/tests/agents/test_reporter_multi_rag_citations.py backend/tests/rag/test_chunk_pages_locality.py backend/tests/test_reporter_prompt_contract.py backend/tests/agents/test_reporter_result_kind.py backend/tests/orchestration/graph/test_reply_source.py`：76 passed；`py_compile` 与 `git diff --check` 通过。
- 这是本地正文、参考文献和 `done.sources` 的离线一致性验证；真实检索命中和来源资料正确性仍未在线核验。
- Reporter 系统 Prompt 追加约束：模型只能保留输入中有来源对应的引用号，不能新增或借用编号；遇到“引用来源未能对应”不能改指另一来源。`backend/tests/test_reporter_prompt_contract.py` 与多步引用测试复跑：26 passed。数据库已发布 Prompt 可能覆盖 YAML 默认值，实际生效版本仍待安全窗口只读核对。

## 2026-11-15：过期历史请求不再抢当前会话

- Conversation-first 会话恢复复核发现一个导航竞态：请求恢复历史 A 后，用户切到 B 再切回原会话 C，旧请求只比较当前会话 ID，会误以为用户没有导航，随后把页面切到 A；旧请求失败时也可能把 A 的错误显示在 C 上。
- `chat` store 为新建、切换、删除当前会话和历史自动选中维护导航版本。历史响应仍可写回对应会话，但仅在发起后未发生导航时自动选中；过期失败不污染当前会话提示。
- 新增“切走再切回”成功和失败回归。主动停止、切换会话或离开页面时，前端保留已生成片段并明确显示“以上内容可能不完整”；首个回答片段到达前停止也不会留下空白气泡。
- `npx vitest run src/store/chat.test.ts src/hooks/useSSE.test.tsx`：2 个文件、52 passed；`git diff --check` 通过。
- 此为会话状态的离线验证；浏览器路由、真实 History API/SSE/Trace 连续性仍未在共享运行环境验收，Conversation-first 整体保持 UNVERIFIED。

## 2026-11-15：SQL 结果卡提高关键状态可读性

- 对照前端 UI 规范复核 SQL 结果卡，发现行数/截断状态、表头和主要操作只有 10–11px，关键查询信息在小屏上不易阅读。
- 沿用现有白底、灰色分隔和靛蓝选中态；将卡片标题提到 14px，将行数、截断说明、操作、表头和精度提示提到 12px，并把图表刻度调至 12px、为数值轴留出空间。没有增加新的装饰或样式系统。
- 新增字号契约断言。`npx vitest run src/components/enterprise-assistant/SQLResultCard.test.tsx src/components/enterprise-assistant/sqlResultPresentation.test.ts`：2 个文件、22 passed；`git diff --check` 通过。
- `npx tsc --noEmit --pretty false` 仍被既有的 `src/cs2/customer-service-page.test.tsx:118` 类型错误阻断；当前只报告这一项，未指向本轮企业助手文件。ESLint 本地二进制不可用，本轮未安装依赖。
- 现有 `.playwright-mcp` 截图不包含 SQL 结果卡；共享服务仍不允许并行验收，因此这轮只证明 SSR/组件输出与选择逻辑，桌面/手机真实视觉效果仍待安全窗口验收，状态为 UNVERIFIED。

## 2026-11-15：部分 RAG 检索显式传递降级状态

- 复核发现部分召回仍能回答时，正文会注明“部分检索路径未完成”，但此前没有写 `answer_status`；前端的降级行动提示、History 和 Trace 因而只能从自然语言猜测终态。
- 现在在保留可用证据答案及其不完整提示的同时，将 `rag_retrieval_degraded` 写入请求元数据和 Trace。现有 Pipeline 快照、RAGMETA、SSE 与 History 路径继续复用这份状态，不新增并行协议。
- 定向验证：`D:\Python\python.exe -m pytest --noconftest -q --no-cov backend/tests/rag/test_chain.py::test_partial_retrieval_answer_discloses_incomplete_retrieval backend/tests/rag/test_rag_answer_status.py backend/tests/api/test_rag_routes_regression.py::test_snapshot_answer_meta_merges_ctx_answer_status backend/tests/agents/test_reporter_result_kind.py`：34 passed；相关 Python `py_compile` 和 `git diff --check` 通过。
- 旧浏览器证据中“检索未能完成可靠核验/系统已尝试重新检索”的完整文案不在当前代码中，现有记录也无关联 Trace ID，来源待安全窗口核对；没有改数据库 Prompt 或共享资料。在线 RAG 召回与真实用户文案仍为 UNVERIFIED。

## 2026-11-15：单行多指标 SQL 结果改为指标卡

- 将 SQLBot 风格的自适应展示设计补入总任务验收：单行多指标按字段拆成独立指标卡，保留时间/分组上下文；不同指标各自显示，避免用一张表或共用坐标轴让用户自行比较不同单位。
- 复现原行为：一行返回订单数和销售额等多个数值时，原先退回普通表格。现在按每个字段显示一张指标卡，保留原始数值文本，并把可读视图标签从“图表”改成“指标”；用户仍可切换查看明细和导出 CSV。
- 定向验证：`npx vitest run src/components/enterprise-assistant/sqlResultPresentation.test.ts src/components/enterprise-assistant/SQLResultCard.test.tsx`：2 个文件、24 tests passed。覆盖指标选择、多指标独立展示、上下文保留与原值显示。
- `npx tsc --noEmit --pretty false` 仍只报既有客服测试类型错误 `src/cs2/customer-service-page.test.tsx:118`；本轮新增前端文件未出现诊断，但整体类型检查不能标为通过。
- 这是组件及展示选择的离线验证；在线 SQL 实际结果、模型摘要与历史恢复仍待安全运行窗口验收。

## 2026-11-15：趋势图不再隐藏分组或乱序展示

- 复现月份 + 区域 + 销售额会被折线图压成月份 + 销售额、静默丢掉区域；多于 4 个指标时也会漏画后续指标。现在趋势图要求只有一个唯一时间维度、没有额外非数值分组且所有指标都能展示；重复月份、缺失日期或指标过多时回退完整明细表。
- 图表数据按真实年月/日期升序排序，支持紧凑年月、常见 ISO/斜线/中文日期格式。新增分组维度、重复时间、指标超限、乱序日期和中文年月回归。
- 首轮回归发现重复时间桶落回通用柱状图；已收紧为只要识别到时间列但不满足趋势图条件，就回退明细表。`npx vitest run src/components/enterprise-assistant/sqlResultPresentation.test.ts src/components/enterprise-assistant/SQLResultCard.test.tsx`：2 个文件、29 tests passed。
- 这是图表选择和组件的离线验证；实际浏览器图表与真实 SQL 结果仍待共享环境安全窗口验收。

## 2026-11-15：助手错误提示去重且保留部分回答

- 沿着 SSE 错误与中断路径核对用户可见内容：服务端错误被同时写入 assistant 正文和红色错误提示；无部分回答的传输错误也重复一次。中断前已有回答时，正文包含内联中断说明，错误提示又显示相同原因。
- 增加精确的展示选择：错误正文是固定错误模板时只显示红色提示；正文有部分回答和内联中断说明时保留整段回答并隐藏重复提示；答案内容不同于错误时继续同时显示，避免吞掉有效内容。错误提示补上 `role="alert"`。
- 定向验证：`npx vitest run src/components/enterprise-assistant/assistantAnswerPresentation.test.ts src/hooks/useSSE.test.tsx`：2 个文件、25 tests passed，覆盖 SSE 错误去重、网络失败、部分回答和不同错误信息。
- `npx tsc --noEmit --pretty false` 仍只报既有客服测试类型错误 `src/cs2/customer-service-page.test.tsx:118`，本轮企业助手文件无新增诊断。真实页面错误/中断终态和历史恢复仍待共享运行环境安全窗口验收。

## 2026-11-15：缺少总数标记时不再声称查询为空

- 复核 SQL 卡片的兼容字段时发现，`total_rows_known` 缺失会被前端当成可信；旧历史或不完整 payload 的空行/`total_rows: 0` 因而可能显示“没有匹配记录”。
- 改为只有 `total_rows_known: true` 才展示可信总数或确认零行；`false` 和字段缺失都明确显示“总数未知/无法确认”。更新普通已知总数夹具，新增缺字段空结果和非空结果回归。
- 定向验证：`npx vitest run src/components/enterprise-assistant/SQLResultCard.test.tsx src/components/enterprise-assistant/sqlResultPresentation.test.ts`：2 个文件、31 tests passed。类型检查仍只报告既有客服测试错误 `src/cs2/customer-service-page.test.tsx:118`；当前企业助手文件没有新增诊断。
- 后端历史白名单和 SSE 契约已在既有离线测试覆盖；真实历史页面仍待共享环境安全窗口核验。

## 2026-11-15：SQL Skill 到 SSE 结果卡契约复核

- 按实际 SQLSkill 实现核对 `SQLResult.model_dump()`：输出包含 SQL/表名等内部字段，`rows` 为字典行，结果数键为 `row_count`；`execute_sql_tool` 则是 `data.rows` 字典行与 `data.total`。现有 SSE 提取器兼容两者，并只下发结果卡白名单字段。
- 新增直接由 SQL Skill Pydantic 模型构造输出、再进入 SSE 结果卡提取器的回归，覆盖 Decimal 原值保留和 SQL/表名不泄露。
- 定向验证：`D:\Python\python.exe -m pytest --noconftest -q backend/tests/orchestration/graph/test_sql_result_cards.py`：11 passed。
- 这是 SQL Skill→SSE 结果卡契约的离线验证；真实模型生成和数据库执行仍待共享环境安全窗口验收。

## 2026-11-15：SQL 结果卡防止不完整数据误导

- 将回复设计原则写入总任务：先给有证据的自然语言结论，再用自适应结果卡展示数据；单位/时间范围不明确时不猜；不完整或行数矛盾时说明边界；失败、降级、空结果与成功清楚区分。
- 复核用户展示边界后发现两处风险：已知总行数小于可见明细时卡片仍会照信该总数；已截断的时间序列仍可能画成完整趋势；图表坐标和提示把所有小数固定舍入到两位，可能把 0.004 显示为 0。现在行数矛盾会降为无法确认，不完整结果默认显示明细表，图表数值保留最多 15 位有效数字。
- 定向验证：在 `frontend` 目录运行 `npx vitest run src/components/enterprise-assistant/SQLResultCard.test.tsx src/components/enterprise-assistant/sqlResultPresentation.test.ts`：2 个文件、34 tests passed；`git diff --check` 通过。
- `npx tsc --noEmit --pretty false` 复核仍仅报既有客服测试类型错误 `src/cs2/customer-service-page.test.tsx:118`；整体类型检查不通过，但没有新增企业助手文件诊断。
- 以上是组件和展示逻辑的离线验收；浏览器真实页面、SQL 数据库返回和会话历史恢复仍待共享运行环境安全窗口核验。

## 2026-11-15：空明细不能被摘要提示误说成零条

- Reporter 的数据摘要已把“空明细且总数未知”表达为“无法确认是否有匹配记录”，但快速总结模板同时要求“结果为空时明确说没有匹配记录”，存在会诱导模型把未知当成 0 行的冲突。
- 修正 `reporter.summary` 默认提示：只有已核实总数为 0 才能说没有匹配记录；空明细、总数未确认时必须说明无法确认。增加对真实渲染提示的契约断言。
- 定向验证：最初单测 1 passed；加入“已确认 0 行仍明确报告无匹配”回归后，`D:\Python\python.exe -m pytest --noconftest -q backend/tests/test_reporter_prompt_contract.py`：19 passed。
- 此修复更新的是 YAML 默认 Prompt；数据库中已发布的 Prompt 可能覆盖默认值，实际运行时生效版本待共享环境安全窗口只读核对。

## 2026-11-15：区分 SQL 返回行数与底层匹配总数

- 沿 SQL executor 和 LIMIT 校验确认：`row_count/total` 是这次 SQL 实际返回的行数，可能已经受到 SQL LIMIT 限制，不代表底层符合条件的所有业务记录数。结果卡原先显示“共 N 行”，容易让用户把两者混为一谈。
- 结果卡改为“查询返回 N 行/展示 N 行”，未知时显示“查询返回数未确认”；异常行数不一致也只说本次查询返回量无法确认。Reporter 摘要中“全部结果”也改为“本次查询返回”，避免自然语言和卡片都把 SQL 返回行数说成底层匹配总量。同步更新类型说明和总任务验收口径，不改 SSE 字段以保持历史兼容。
- 定向验证：前端 `SQLResultCard` 与展示选择 2 个文件、34 tests passed；`D:\Python\python.exe -m pytest --noconftest -q backend/tests/test_reporter_prompt_contract.py`：19 passed；Reporter 与测试文件 `py_compile` 通过；`git diff --check` 通过。

## 2026-11-15：同一文件不同页保留各自 RAG 引用

- 沿 CitationFormatter 到 Reporter 来源卡的离线路径发现：同一 PDF 的不同页会分配不同引用编号，但参考文献、结构化来源和 citation_evidence 原先分别按文件名或文件/章节合并，可能让正文 `[E2]` 丢失页码或对应来源。
- 对正文已引用的来源改为按引用编号保留参考文献和来源卡；无引用时继续按文件名折叠候选卡。私有证据记录按文档、页码、章节和版本区分同一文件的不同页，同页同章节的重复 chunk 仍可合并。
- 新增同名 PDF 两个页码、同一章节、答案分别引用 `[E1]`/`[E2]` 的回归，串过 formatter→参考文献文本→Reporter 来源解析→结构化来源，并检查私有证据页码；也验证无引用兜底仍去重。两个定向测试均通过：`backend/tests/rag/test_chunk_pages_locality.py::test_same_file_different_pages_keep_cited_references_and_sources` 与 `backend/tests/rag/test_rag_answer_status.py::test_make_done_event_keeps_same_file_citations_for_different_pages` 各 1 passed；相关文件 `py_compile` 和 `git diff --check` 通过。
- 随后复跑 Reporter 单步/多步引用编号、同 PDF 双页引用和 SSE `done.sources` 的 4 个跨模块定向用例：4 passed。
- 这是离线 formatter/解析链路验证；共享库中的真实文档页码、在线检索命中和浏览器来源卡仍待资料验收会话结束后核对。

## 2026-11-15：企业助手来源卡补齐资料版本与生效期

- 追踪 RAG 来源字段后确认：检索元数据和直接来源提取包含 `version_id`，但主助手把引用转成参考文献文本、再由 Reporter 解析时，只保留文件/页码/章节/doc_id；来源卡与历史 API 因此拿不到版本和生效日期。
- 在引用行现有隐藏注释协议中加入经过 URL 编码的版本、生效/失效日期；Reporter 仅解析这三个白名单字段。结构化来源提取和历史消息白名单同步透传日期，历史接口不暴露内部的 `supersedes_version_id`。来源卡新增版本及有效期文字标签，沿用原有标签样式；字段缺失的旧索引与历史消息保持兼容。
- 更新总任务验收项，要求有登记信息时，在线来源卡和历史恢复都能展示资料版本/日期。
- 定向验证：`D:\Python\python.exe -m pytest --noconftest -q backend/tests/rag/test_chunk_pages_locality.py backend/tests/agents/test_reporter_multi_rag_citations.py backend/tests/memory/test_message_codec.py`：42 passed，包含中文和注释结束符编码往返；`npx vitest run src/components/chat/SourceCard.test.tsx`：1 个文件、5 passed；涉及 Python 源码和测试 `py_compile` 通过；`git diff --check` 通过。`npx tsc --noEmit --pretty false` 仍仅报已有的 `src/cs2/customer-service-page.test.tsx:118` 类型错误，未指向本轮来源卡或类型文件。
- 为 Conversation-first 补上版本字段的跨层往返断言：Runner 保存的来源与 SSE `done.sources` 相同；历史 API 白名单输出与 SSE 来源、状态/置信度相同；前端切换会话恢复时保留版本与有效期。
- 该跨层定向验证：`D:\Python\python.exe -m pytest --noconftest -q backend/tests/rag/test_rag_answer_status.py::test_answer_presentation_metadata_matches_sse_for_history_restore backend/tests/orchestration/graph/test_runner_trace_finalize.py::test_normal_completion_persists_the_same_rag_display_metadata_as_sse backend/tests/memory/test_message_codec.py::test_history_api_payload_only_exposes_allowlisted_assistant_metadata`：3 passed；`npx vitest run src/store/chat.test.ts`：1 个文件、40 passed。
- 进一步对照索引发布代码，未声明 `version_id` 时它会回退为 `uuid4().hex[:16]` 的随机索引代次。来源卡现在不把这类代次误标为业务版本；chunk 同时带有 `index_generation` 时按两字段相同识别，旧 chunk 再按已确认的 16 位十六进制生成格式兜底过滤。已登记的 `v2` 和生效日期仍展示。
- 版本过滤修改后的定向复跑：`D:\Python\python.exe -m pytest --noconftest -q backend/tests/rag/test_chunk_pages_locality.py backend/tests/agents/test_reporter_multi_rag_citations.py backend/tests/rag/test_rag_answer_status.py backend/tests/orchestration/graph/test_runner_trace_finalize.py backend/tests/memory/test_message_codec.py`：79 passed；Python `py_compile` 与 `git diff --check` 通过。前端来源卡 5 tests、会话 store 40 tests 的本轮证据仍有效（本次只改了后端来源选择逻辑和 Python 用例）。
- 这是离线元数据传递、API payload 组装和组件/store 验收。真实索引中的版本字段、在线检索实际选中的版本、真实 History API/页面刷新及浏览器视觉仍未核验；资料验收会话仍在使用运行环境，本轮没有重建或重启服务。

## 2026-11-15：退款率排名排除未定义分母

- 复核退款率最高商品的 SQL 示例和 SC-117 后发现：销售额可为 0 或 NULL，`NULLIF` 会产生 NULL 退款率；PostgreSQL 的 `DESC` 默认将 NULL 排在有效值之前，所以 `LIMIT 1` 可能选中无法计算退款率的商品。[PostgreSQL 排序规则](https://www.postgresql.org/docs/current/queries-order.html)
- 最小修复是在候选条件中要求商品销售额大于 0，并显式写 `NULLS LAST`；同时将这条退款率口径写入 SQL 生成约束。没有假设业务表会自动保证正销售额，迁移表也没有这样的 CHECK 约束。
- 按数据集不可变规则保留 `sql_v2`，创建 `sql_v3` 45 条版本化候选，只修正 SC-117，manifest hash 为 `84c9822e0bf23956`。默认 `sql` 评测和业务库执行尚未接入/运行，待 SQL 结果级验证后决定是否准入。
- 定向验证：`D:\Python\python.exe -m pytest --no-cov --noconftest -q backend/tests/sql/test_sql_router_examples.py backend/tests/sql/test_golden_validator_gate.py backend/tests/prompts/test_registry_defaults.py`：30 passed；Python `py_compile` 与 `git diff --check` 通过。离线核对确认 v3 共 45 条、仅 SC-117 相对锁定 v2 有变化，manifest hash `84c9822e0bf23956`。
- 以上验证证明 SQL 示例、生成约束、Prompt 默认模板和新版数据锁一致；没有在共享 PostgreSQL 执行，也没有验证数据库中生效的 Prompt 覆盖，因此结果级 SQL 验收仍为 UNVERIFIED。共享服务未启动或重建。

## 2026-11-15：Gold SQL 失败不再被表路由命中掩盖

- 复核 SQL Live Runner 发现，参考 `gold_sql` 校验或执行失败时会退回 `router_hit == 1.0` 并记录 PASS；即使没有可比较的真实结果，也可能给模型质量评测贡献通过样本。
- 移除这条降级通过路径。Gold SQL 校验失败记为数据集错误；执行失败按权限/环境、数据库基础设施分类，写入 `error_stage=sql_gold`。只要 SQL 评测中有此类错误，整轮被判为相应 `INVALID_*`，不能作为结果准确性的 PASS 证据。同步更新评测治理文档和 RAG Smoke 定向测试清单。
- 定向验证：`D:\Python\python.exe -m pytest --no-cov --noconftest -q backend/tests/evaluation/test_sql_eval.py backend/tests/evaluation/test_release_gate_module_aware.py backend/tests/sql/test_sql_router_examples.py backend/tests/sql/test_golden_validator_gate.py backend/tests/prompts/test_registry_defaults.py tests/evaluation/test_rag_ci_workflow_contracts.py tests/test_rag_smoke_path_filter.py`：113 passed；相关 Python `py_compile` 与 `git diff --check` 通过。
- 资料验收会话仍在运行，当前共享 Redis 为 restarting；按项目规则没有重建本机服务。当前证明是离线 Runner/有效性分类回归，真实 SQL 结果仍为 UNVERIFIED，后续需在共享服务稳定且无并发资料验收时进行运行时重建和结果级验收。

## 2026-11-15：SQL 版本化候选可显式进入结果级评测

- 追查发现通用 `--dataset` 路径固定交给 RAG runner，SQL v3 虽有 Gold SQL runner，却无法通过 CLI 对候选集做实际结果比较。
- 保持现有默认用法兼容，仅当用户同时指定 `--module sql --dataset ...` 时按 SQL runner 执行；E0 schema preflight 校验 SQL 样本，报告记录候选文件和版本。显式加载 `cases.jsonl` 时同步验证旁边 manifest 的 content hash，避免绕过版本锁。
- 候选结果级命令记录在 `backend/evaluation/datasets/README.md`：`D:\Python\python.exe -m backend.evaluation.cli --module sql --dataset sql_v3/cases.jsonl --live --no-ragas`。该命令会调用真实 SQL Agent/数据库，本轮未运行；共享资料验收尚未结束、Redis restarting，真实结果仍为 UNVERIFIED。
- 定向验证：`D:\Python\python.exe -m pytest --no-cov --noconftest -q backend/tests/evaluation/test_sql_eval.py backend/tests/evaluation/test_eval_preflight.py backend/tests/evaluation/test_release_gate_module_aware.py backend/tests/sql/test_sql_router_examples.py backend/tests/sql/test_golden_validator_gate.py backend/tests/prompts/test_registry_defaults.py tests/evaluation/test_rag_ci_workflow_contracts.py tests/test_rag_smoke_path_filter.py`：129 passed。覆盖 SQL v3 schema preflight、候选 hash 漂移拦截、显式 SQL runner 路由/报告版本记录，以及 Gold SQL 失败有效性门禁；相关 Python `py_compile` 和 `git diff --check` 通过。未连接数据库。

## 2026-11-15：SQL Golden 自然月与空集合语义

- 对照 SQL v3 的全部金额/比率/时间查询时发现两处可复现的 Golden 口径缺陷：SC-121 的下界从上月开始但没有上界，不能保证仅比较上月与本月；SC-127 的 `SUM` 在本月无订单时返回 NULL，而且没有排除今天之后的数据。
- 沿不可变数据集规则创建 sql_v4，不覆盖 v3：SC-121 用两个月桶并限定 `[上月月初, 下月月初)`，缺少销售额的月份保留但退款率为 NULL；SC-127 将无销量聚合明确为 0，并将订单时间限制到今天结束。同步补充 SQL 生成约束，区分“没有记录即 0”和“分母无效即未知”。
- 定向验证：同上一节命令，131 passed；覆盖 v4 的 manifest/hash、全套 SQL schema preflight、两条时间/空集语义断言及生成提示约束；Python `py_compile` 与 `git diff --check` 通过。业务库结果与真实 SQL Agent 的一致性尚未运行，仍为 UNVERIFIED。

## 2026-11-15：SQL 执行结果比较不再吞掉精度和 NULL 差异

- 检查结果集比较器发现两处会造成假 PASS 的归一化：所有 Decimal/float 都被转成 float 并四舍五入到 4 位；`None` 和空字符串都被归一成 `""`。这会掩盖小额财务差异、大数精度差异，以及“无值”和“空文本”的区别。
- 改为保留 Decimal 系数/指数的精确数值身份，整数/Decimal/float 只有数值相等才匹配；同时给 NULL、字符串、布尔值、日期和数字使用区分开的类型标记。该比较仍兼容 Decimal `1.5000` 与 float `1.5` 的数值相等情形。
- 新增小数精度、大额小数 0.01 差异、Decimal/float 等值和 NULL/空字符串回归。定向验证：同 SQL 定向命令 133 passed；相关 Python `py_compile` 和 `git diff --check` 通过。真实业务库数据仍未触及。
- 用户展示层定向复验：在 `frontend/` 运行 SQL 结果卡、回答排版、来源/预览卡、无资料提示、会话 store 和 SSE 相关 8 个 Vitest 文件，108 tests passed。该证据覆盖组件和状态契约，不替代共享服务上的浏览器视觉、真实 SSE/History/Trace 验收。
- 同轮发现并修复 SQL 卡片的状态更新边界：结果从完整趋势更新为截断/行数矛盾时，现强制切回明细表；新增同一组件实例 props 更新回归。复跑相同 8 个 Vitest 文件，109 tests passed；`git diff --check` 通过。浏览器真实视觉仍待共享环境释放。

## 2026-11-15：会话、引用定向复验与 API 测试夹具修正

- 复跑企业助手 SSE、History、Runner Trace、RAG 引用和 Reporter 结果类型定向测试时，首次出现 3 个产品入口 API 用例 401、其余 101 项通过。检查发现测试夹具固定设置了 `GATEWAY_TRUST_SECRET`，但该三条模拟请求缺少对应的 `X-Gateway-Trust`；这是测试请求未满足网关契约，不是业务路由回归。
- 为测试请求补上固定信任头，没有放宽生产身份校验。重跑相同定向组：`D:\Python\python.exe -m pytest --no-cov --noconftest -q backend/tests/api/test_chat_stream_hardening.py backend/tests/memory/test_message_codec.py backend/tests/orchestration/graph/test_runner_trace_finalize.py backend/tests/rag/test_rag_answer_status.py backend/tests/rag/test_chunk_pages_locality.py backend/tests/agents/test_reporter_multi_rag_citations.py backend/tests/agents/test_reporter_result_kind.py`：104 passed。仍是隔离测试证据，不代表在线 Trace、真实知识库命中或浏览器历史恢复已验收。

## 2026-11-15：SQL Golden 聚合改用实体主键

- 检查 SQL v4 数据所依赖的建表定义后确认：`customer.customers.name`、`product.products.product_name`、`product.categories.name` 和竞品名称都没有唯一约束。v4 中 15 条查询仅按展示名分组/去重，遇到同名客户、商品、分类或竞品记录会合并成一个统计对象。
- 沿版本锁规则新增 sql_v5，不改 v2/v3/v4：客户、商品、分类聚合使用主键加名称；竞品聚合增加竞品记录 ID；分类 `DISTINCT ON` 改用 category ID，并加商品 ID 作为稳定并列排序条件。SQL 生成提示同步要求实体聚合包含稳定主键，防止同类误合并。
- 定向验证：SQL 评测/数据预检/Golden/prompt 合并组 134 passed；覆盖 sql_v5 全集 schema preflight、manifest hash、15 条实体分组与分类去重断言；Python `py_compile` 通过。`npx tsc --noEmit --pretty false` 仍只报既有客服测试文件 `src/cs2/customer-service-page.test.tsx:118` 错误；未连接共享业务数据库。
- 最新线程状态复核显示资料验收会话仍 active；其 9081 隔离预览已停止，尚不代表共享服务可供并行使用。没有操作另一工作树、共享浏览器、Docker 或知识库数据。
- 进一步验证 SQL 生成真正调用 `prompt_service.render_sync` 时，日期边界、空 `SUM` 与稳定主键约束会进入传给 Prompt Registry 的 `question`；定向 `backend/tests/sql/test_sql_router_examples.py`：18 passed。首次断言漏写 `SUM` 的协议反引号，修正测试文本后通过；`git diff --check` 通过。

## 2026-11-15：完整验收缺口与共享环境状态复核

- 复核 Reporter SQL 摘要路径：`{status, data}` 形式会递归解包，缺失行数不会被说成 0；没有发现新的离线代码缺陷，也没有新增代码改动。
- 只读查看资料验收会话：仍为 active，当前正核对资料上传错误与 Trace 数量差异；最新状态称共享 Redis 仍在重启、BGE 服务不可达。没有进入该会话的工作树、操作 Docker/浏览器或写入共享知识库。
- 完整验收仍待：①在共享服务可用窗口执行锁定的 `sql_v5` 45 条结果级 Golden，并确认候选 manifest hash 与报告；②真实页面验证 SQL 指标/多指标/趋势/排名/明细、精度和截断提示、导出及历史恢复；③用授权账号核对 RAG 实际召回、引用段落/页码/版本、来源卡与 Trace；④端到端核验会话新建/切换/追问/停止/迟到 SSE 归属和 History/Trace 一致；⑤人工检查成功、追问、无资料、降级、SQL 空结果/失败等用户可见回复是否准确、清楚且不重复。
- 以上在线项目当前均为 UNVERIFIED；等资料验收会话结束且共享依赖恢复后，再安排一次有序的共享运行验收与受影响服务健康检查。离线单测通过不等同于完整验收。

## 2026-11-15：不完整 SQL 展示载荷禁用自动图表

- 前端 SQL 卡片审查发现：行数未确认但刚好有可画图的行、结果列被裁掉或单元格值被截短时，卡片仍可能自动渲染趋势/排名图，图表会隐藏完整性不足的风险。
- 新增 4 个回归条件，先确认现状全部失败；随后将不完整列/单元格载荷切换为明细表，并要求趋势/排名图的行数已确认。单值/多指标在其他完整性条件满足时仍可走指标卡。
- 定向验证：在 `frontend/` 执行 `node_modules/.bin/vitest.cmd run src/components/enterprise-assistant/SQLResultCard.test.tsx src/components/enterprise-assistant/sqlResultPresentation.test.ts`：2 个文件、39 tests passed。当前工作树的 `git diff --check` 通过；共享页面运行验收仍因资料验收会话占用而未执行。

## 2026-11-15：Reporter 兼容旧版已发布摘要模板

- 组合测试暴露出 Prompt Registry 会优先使用数据库里的已发布版本；只更新 YAML 默认模板不能保证运行中的旧版摘要模板包含用户问题及 SQL 事实约束。旧模板模拟此前未覆盖这一情况。
- Reporter 现在在调用层保证用户问题进入摘要提示，并追加不会被已发布模板覆盖的事实边界：只根据核实数据作答、不推算数值或范围、预览不用于全量排名、空明细不能冒充零条记录、省略数据不得猜测。没有写入或更新共享 Prompt 数据。
- 新增旧模板兼容回归；Reporter 定向测试 20 passed，SQL 评测/预检/Golden/Prompt 组合 134 passed，SQL 结果卡片 39 passed。离线回归证明调用契约；共享数据库里当前启用的 Prompt 版本、真实 SQL 结果和浏览器回复仍为 UNVERIFIED。

## 2026-11-15：PR #471 合并后对齐验收分支

- 资料链路会话的 PR #471（`codex/file-quality-main-followup-20261114`）已合并，merge SHA 为 `08413877d0b44fa3701fe06066261f59871df380`。复核该会话的工作树仍停在 `cad34bb`，仅保留未跟踪 `.playwright-mcp/`；当前会话未修改或同步该工作树。其最新记录说明 CI 和安全检查通过，但没有部署、真实 API 验收或写共享知识库。
- 将最新 `origin/main`（`0841387`）合入本分支，生成合并提交 `316d4c0`。两分支只共同修改 `.github/workflows/rag_smoke.yml`，合并树预检无冲突；保留了双方不同的测试项。本工作树的 `.playwright-mcp/` 仍未跟踪、未提交。
- 在整合后的分支复跑：SQL 评测/预检/Golden/Prompt 134 passed；Reporter 提示契约 20 passed；会话、RAG、引用和 Trace 后端 104 passed；前端 8 个定向文件 113 passed；`git diff --check` 通过。
- 当前企业助手分支自己的 PR 尚未创建/合并。在线 SQL v5、真实 RAG 召回与引用、浏览器体验、完整 Conversation-first 流程仍为 UNVERIFIED；PR #471 的合并不替代这些验收。

## 2026-11-16：修复 PR #472 Review 意见

- Review 指出 Reporter 多步引用和 SQL 摘要契约测试没有进入 RAG Smoke 定向清单；已加入 `test_reporter_multi_rag_citations.py` 和 `test_reporter_prompt_contract.py`。
- Review 复现了单步 RAG 正文保留 `[E3]`、SSE `done.sources` 却重编号为 1 的错位。Reporter 现在复用单步快速路径判定，并让来源提取保留原编号；新增回归断言正文、来源卡及 `done.sources` 都对应编号 3。
- 修复后的 Reporter 相关测试 38 passed，RAG Smoke 路径/工作流契约测试 28 passed，Python `py_compile` 与 `git diff --check` 通过。修复提交推送后仍需复核 PR #472 最新 HEAD 的 CI 和 Review；真实 API/运行验收仍为 UNVERIFIED。

## 2026-11-16：处理 PR #472 离线 CI 回归

- PR #472 的 `acb9df9` 离线预检失败于两个既有契约：同一文档的不同章节应合并成一张 SourceCard，而不同文档版本仍需分别显示。问题来自引用时按引用编号去重，与来源卡片按文档聚合的既有行为冲突；章节级引用证据仍由独立证据提取逻辑保留。
- `extract_sources()` 已改为按文档 ID（缺失时按文件名）和版本 ID 去重；增加同一文档两个版本分别保留来源卡的回归测试。来源卡片、章节引用证据的职责保持分开。
- 本地运行 `test_citation_formatter.py` 与 `test_cs_eval_kb_contract.py`：59 passed。该修复尚未推送，需提交至 PR #472 并等待最新 HEAD 的离线 CI 通过；真实 API/运行验收仍为 UNVERIFIED。
