# cs-public-launch 下一会话实施交接

> 更新：2026-11-02（Asia/Shanghai）。当前为 **PAUSED**，用户要求本会话只整理交接。下一会话收到“继续/接手”后再施工；不要自动启动测试、构建或付费调用。原目标 Q1–Q6 保持不变，本文件覆盖 TASK 中已经过时的启动步骤与 Q3 默认建议；历史日志保留。

## 1. 总计划、当前结论与停止条件

| 期 | 原目标 | 当前真实状态 | 下一步及验收 |
|---|---|---|---|
| Q1 | 管理端非 RAG 选项、客服前端构建、渠道/资金落档、残留清理 | 实现及 PR #259 已合并；残留按引用依据保留，非清零 | 不重做；共享管理端 :3200 是否已更新仍需协调、核验版本，隔离 :13200 的 200 不能替代它 |
| Q2 | 补知识链路索引并将繁忙率降至≤5% | PR #260 已合并；实际是宿主误用 local 空索引，生产 remote 索引存在，未盲目重建 | 60条复测46 pass、0 error、繁忙0/60、v1=9/20；知识链路指标通过，整体质量门禁仍 FAIL |
| Q3 | action质量、full_path断言、多轮、live预算 | 用户已拍板，代码施工中，全部未提交；局部测试通过，整体验收未完成 | 按本文件 §4 收口预算保护→真实完整/多轮→action逐例→重建/PR/CI/合并 |
| Q4 | 多坐席容量压测 | 未实施，仅只读检查了已有脚本 | 隔离场景、阶梯负载、并发/吞吐/P95/错误率、状态正确性及资源报告；不能只测200或只跑微基准 |
| Q5 | 深度扫描及客服授权/租户/资源/SSE/WS复核 | 未实施；当前未发现可调用的 Mimosa 工具/命令，不能沿用“工具已在环境”预设 | 开工核对工具与对应 Skill；工具缺失就明确缺口，不把其他扫描冒充Mimosa；完成有效发现处置及报告 |
| Q6 | 首期资金范围与开关核对 | 用户尚未拍板 | 建议首期不开放真实资金动作；开工列建议再问；核对所有资金入口，不能只看退款申请一个开关 |

原门槛仍保留：Q1+Q2是受控试用技术阶段门槛；Q1–Q6全绿且主链路冒烟通过才可全量公开投产。**当前不能宣布已放量或全量投产**：共享入口版本未统一、完整质量门禁FAIL、Q3–Q6未完，预算真实链路尚未验收。

每期流程保持：实现→实际测试→Diff自查→PR→逐项核对CI（包括skip原因）→合并→PROGRESS回填。用户已授权该逐期流程；不要直推main、重复开PR、主动发@codex review/security review。自动Review计入审查，预算/授权等高风险路径须保留对应审查证据。

## 2. 工作树与Git现场（必须复用）

- Worktree：`D:/Program Files/workplace/agent-cs-prod`
- Branch：`feat/cs-prod-q3`
- HEAD：`144aea7729b4e573b87e5ba34421c42e329c98d2`（Q2合并提交）
- Q1：[PR #259](https://github.com/Jayus-07/agent/pull/259)，merge `01aa6149f69d942553da80189d27e0d3d16d90dd`；PR head `e1268cbb2b5a1fced5ab4b3211cc518c14e23e19`。CI 8 success/2预期skip；自动Review P2已修复。
- Q2：[PR #260](https://github.com/Jayus-07/agent/pull/260)，merge即当前HEAD；PR head `c33947b510a6a9aef7922b8e5506db45dc98eb57`。CI 6 success/1预期skip；自动Review completed，无发现。
- Q3：**没有commit，没有PR，没有Q3云端CI或Review证据**。不要误把HEAD当成当前脏代码版本。
- 主目录和agent-gov属于其他/已收官任务，不能在那里构建本任务代码。不要新建Q1工作树或覆盖当前Q3；不要stash/reset/clean、强制清理、替他会话合并。

暂停时功能/测试改动（以实时git status为准）：

- `.github/workflows/rag_smoke.yml`
- `backend/evaluation/{cli.py,config.py,provenance.py,runner.py,service.py,runners/cs.py}`
- `backend/infra/async_utils.py`、`backend/infra/llm/{budget.py,quota.py}`
- `backend/observability/llm_usage_store.py`、`backend/orchestration/graph/runner.py`
- `backend/rag/client.py`、`backend/services/rag_server.py`
- `backend/tests/customer_service/test_cs_eval_runner_live.py`、`backend/tests/test_sse_event_schema.py`
- `scripts/run_cs_live.py`、`tests/evaluation/test_cs_live_entrypoint.py`
- 新文件：`backend/tests/evaluation/test_cs_execution_mode.py`、`backend/tests/infra/test_run_budget_pg.py`、`backend/tests/infra/test_sync_timeout_context.py`、`backend/tests/rag/test_run_budget_transport.py`
- 文档：TASK/PROGRESS、`Q3-PREFLIGHT.md`，以及本HANDOFF。Q3-PREFLIGHT末尾“未拍板”属于历史描述，以本文件已批准范围为准。

## 3. 用户已经批准的Q3范围（不重复问）

1. action先逐例复核再实施，合理扩词表须守门禁；仅对证实的错标按锁版流程修订，不能迁就实现改期望。
2. **本期补齐full_path与multi_turn逐轮重放并实际验收**，不允许采用原TASK“另立项/降口径”的建议来收官。
3. 先修复并验证预算保护，再启用**周度人工一次、单次≤10元**的常规live；**不创建自动排程**。此前是实施批准，不代表预算保护已通过。
4. 398条approved候选保留历史审核事实，候选发布过滤另挂小PR；不再次reject覆盖审核结论。

## 4. Q3当前实现与下一施工顺序

### 4.1 已写代码，但仅局部验证

- `--cs-mode` / EvalConfig / run_all / service / CS runner贯通；full_path仅允许显式cs live；模式和金额进入CS快照。`cs_max_cost_cny`为配置字段（>0且≤10）；**目前没有同名CLI金额选项，不要凭空使用参数**。
- graph_only与full_path都按metadata.turns顺序执行user轮次，不注入标注assistant回复；同case复用会话，不同case/run隔离。末轮断言独立，错误事件与空回答不再被当成功。
- 已复现公共`sync_call_with_timeout`丢ContextVar，修复为每次提交copy_context；GraphRunner新线程也复制上下文，仍保留原RequestContext绑定。
- `scoped_run_budget`保存批次ID/金额/失败信号；节点重绑Trace不丢批次。用量request_id优先批次归因；子线程/远端额度拒绝不能被降级掩盖。
- PG `reserve`新增可选request_limit_cny，事务advisory lock串行同批次预占；复用budget_reservations并按reservation ID去重，累计已结算与reserved/needs_review。无该参数的原调用保持旧流程。
- CS live要求enforce；RAG client先检查令牌与ready能力版本，再传批次头。服务端校验内部令牌、eval批次ID、0<上限≤10、enforce，恢复作用域。旧版本/无令牌不静默旁路。
- 宿主脚本新增令牌/enforce/能力预检；`CS_EVAL_RAG_URL`、`CS_EVAL_BUSINESS_URL`可指定本机HTTP隔离端口，拒绝外部主机。共享默认RAG尚未支持新能力时，**拒绝运行是预期保护行为**。

### 4.2 第一优先：完成预算真实链路，不先烧模型预算

1. 先审当前Diff和接口契约，核对新增测试及普通请求兼容性，避免继续扩大改动。
2. 检查构建句柄/底层build进程（§6），确认是否已有本任务镜像；验证镜像内关键源码hash与本工作树一致。不要因旧会话无句柄就重启构建。
3. 使用隔离PG、独立RAG端口、内部令牌、enforce及现有模型登记。统一把所有PG用途指向隔离库，Redis使用独立实例/前缀；禁止改共享.env或替换共享容器。
4. **真实HTTP+PG，先无付费验证**：坏令牌/缺令牌/非法ID/NaN/超额金额拒绝；旧版本拒绝发生在ask之前；同一批次跨服务/用户/并发不能超卖；拒绝后run终态failed/INVALID，不能completed/pass；usage/预占按同一run ID可对账。
5. 再做一次最小真实模型预占/结算和低额度阻断，保留provider是否真正调用、reserved/settled/needs_review、币种、剩余额度与日志证据。核对**预占上界和Provider最大输出约束**，不能仅凭事后计数保证≤10元；未知价格/未知成本须按既有规则保守处理。
6. 重点补回归：普通无run RAG问答/检索鉴权、嵌套/并发作用域隔离、取消/重试/超时、金额精度和非法值、绑定失败、服务不可达与版本不匹配、预算失败被expert捕获后的终态。

**已知待审风险**：新评测分支会发送X-Internal-Token，但普通问答客户端鉴权尚需整体核对。不能直接给共享RAG开启令牌，否则普通请求可能被拒绝。先在隔离环境把正常与评测请求一并验收，再协调发布。

### 4.3 第二优先：full_path、多轮实际验收

- 模拟事件流只证明接线，不能证明真实主图、安全断言、会话记忆或身份链。
- 最小真实集应覆盖域进入、拒答、must_contain、must_not_contain、forbidden_facts、澄清；逐轮记录实际输入/输出/trace和最后一轮断言；至少覆盖两种上下文依赖及跨case/run不串话。
- 审核facts字段语义：当前full_path对forbidden_facts按字符串匹配，allowed_facts未见消费；若值是KB/事实标识，不能把“标识没出现在答案中”冒充事实安全验证。先对齐现有数据契约与真实证据机制，不伪造完整断言通过。
- graph_only不包含主图理解软层，不能用它的多轮通过替代full_path上下文验收。
- full_path当前用合成eval_bot/default，须核对product/tenant/权限与真实web链路；内部图通过不等于网关JWT链通过。新会话ID不代表已建立正确授权身份。

### 4.4 第三优先：action逐例修复

- P6 b1–b4共304条；action共62：24 pass/38 fail。失败expert分布：32 action、4 query、1 knowledge、1 complaint+knowledge；不是38条都由词表不足造成。
- 32条路由已对的失败中，抽查C3-021/030/040/055为must_contain未命中订单号/确认词。逐条检查可信身份、业务地址、提案必填槽、确认状态、回答及断言，形成原因/修复/验收表。
- 来源：`D:/Program Files/workplace/agent-gov/data/eval_runs/cs-live-1102-b1`至`b4`的report.json（只读）；当前锁版`backend/evaluation/datasets/cs/cases.jsonl`及manifest v2.2。
- 词表只改真实路由缺口；错标修订必须有业务依据、升版本与hash/notes同步；不得降阈值、扩mock、skip掩盖失败。
- Q2命名集只抽8条action，不能代替62条action或完整多轮验收。预算保护通过后再执行所需最小live集合并与基线逐例比对。

### 4.5 Q3交付门禁

定向验证→本任务代码受影响服务隔离重建并健康/行为检查→Diff自查→Q3独立PR→自动Review及预算/并发/授权证据→CI全部逐项终态和skip归因→合并→回填。高风险问题未闭环保持UNVERIFIED/BLOCKED，不能只凭局部PASS建议投产。

## 5. 测试证据与待执行验证

下表是**各实现时点真实执行**，有重叠，不相加；最新全部改动尚未统一回归。没有Q3 T3/发布级PASS。

| 验证 | 实际结果 | 能证明什么 / 不能证明什么 |
|---|---|---|
| Q1评测中心目录Vitest、tsc、Docker生产构建 | 10 tests PASS、tsc PASS；最新镜像隔离/login内外200 | 表单和构建；不证明共享:3200已部署 |
| Q2修正后live | 60条46pass/0error/繁忙0，v1=9/20 | 知识链路指标；整体smoke50%/core80.8%，门禁FAIL、CLI exit1 |
| CS mode+runner相邻首轮 | 6 passed /26.18s | 初期配置接线，不是最终真实链路 |
| full_path接口桩/模式测试 | 7 passed /30.13s | 逐轮调用、末轮断言/禁词/error、跨run会话隔离 |
| timeout上下文修复前 | 2 FAILED /25.41s | 确认身份和预算对象丢失，可复现根因 |
| timeout+LLM预算+CS supervisor+travel renderer | 47 passed /22.53s | 公共线程修复及相关调用方；不是跨服务金额上限 |
| graph_only+full_path定向合跑 | 14 passed /26.29s | 多轮接线；非实际记忆/主链路 |
| 独立PG整批预占 | 2 passed /27.50s | 真实并发、结算差额、needs_review占额与批次隔离 |
| scope/预算/模式组合 | 28 passed /33.96s | 作用域传递与quota参数；非完整HTTP模型链 |
| GraphRunner事件流+原SSE | 2 passed /32.77s | 实际worker线程保留scope，事件序列不回归 |
| CS模式/强制保护 | 13 passed /26.77s | off/observe不跑样本，配置上限拒绝等 |
| transport+上下文+既有内部令牌API | 25 passed /53.29s | HTTP mock/函数与原鉴权回归；有Windows事件循环清理及依赖警告 |
| 最新transport边界 | 11 passed /23.45s | 缺令牌无HTTP、开发绕过不能声明预算、版本/金额/ID/402拒绝 |
| 最新宿主入口 | 9 passed /1.83s | 隔离本机端口、外部URL拒绝、缺令牌前置拒绝、选样构成 |
| py_compile与git diff --check | 对当时改动执行PASS | 静态结果，不替代真实运行；交接文档仅做静态核对 |

恢复后从工作树根使用`D:/Program Files/workplace/agent/.venv/Scripts/python.exe`（遵循Python Skill）。先配置隔离PG/Redis，不直接跑全仓回归：

```powershell
Set-Location 'D:/Program Files/workplace/agent-cs-prod'
$py = 'D:/Program Files/workplace/agent/.venv/Scripts/python.exe'
& $py -m pytest backend/tests/evaluation/test_cs_execution_mode.py backend/tests/customer_service/test_cs_eval_runner_live.py backend/tests/evaluation/test_cs_runner_v2.py -q --no-cov
& $py -m pytest backend/tests/infra/test_sync_timeout_context.py backend/tests/test_llm_budget.py backend/tests/rag/test_run_budget_transport.py backend/tests/api/test_rag_server_internal_token.py -q --no-cov
& $py -m pytest backend/tests/test_sse_event_schema.py::test_runner_event_stream_passes_contract backend/tests/test_sse_event_schema.py::test_graph_worker_keeps_run_budget_scope tests/evaluation/test_cs_live_entrypoint.py tests/evaluation/test_rag_ci_workflow_contracts.py -q --no-cov
```

- PG集成另跑`backend/tests/infra/test_run_budget_pg.py`；`CS_Q3_PG_DSN`从隔离环境安全读取，连接15433/cs_q3，不回显口令。测试只接受cs_q3或_test数据库，禁止为了通过移除此保护。
- 预算/并发属于T2，补真HTTP/真PG与必要Provider证据；路由改动补`test_cs_eval_gate.py`、`test_cs_decision_replay.py`及相关词表/理解测试。
- 全局conftest曾打印TaskService schema ensured等日志；不能声称历史单测全程无DB触达。后续应把所有数据库用途和缓存统一隔离，不只设置集成测试的独立DSN。
- rag_smoke定向清单已新增Q3相关测试，尚未有云端执行证据。CI失败按实际Diff修，不skip/删断言。默认不跑全量回归。

## 6. 暂停时资源与临时文件（恢复须重新查归属）

| 项 | 已知现场 | 处理纪律 |
|---|---|---|
| 主库5433与主Redis/网关 | 共享栈；归属会变 | 不删除数据、不清缓存、不覆盖其他会话服务 |
| agent-cs-prod-q3-pg | 本任务独立PG，:15433，cs_q3，256MiB/0.5CPU | 两个真实PG测试已运行；其后载入其他schema、模型/价格配置及默认预算模板；仅隔离模板改为100/1000 hard作测试，不是生产策略 |
| agent-cs-prod-q3-net | 本任务新网络，PG已连接 | 未启动新RAG；恢复后检查网络/端口，不盲目重复创建 |
| cs-prod-q3后端构建 | `docker compose -p cs-prod-q3 -f docker-compose.yml -f 部署/prod-4c8g/docker-compose.local.yml build app`，GIT_COMMIT=144aea7-q3-dirty；compose同时构建依赖镜像标签 | 句柄13377最后仍running、exporting layers；读取cs-prod-q3-app尚无镜像ID。先poll句柄或查底层docker/buildx进程，观察失败不等于构建终止；不要重复下载/构建 |
| agent-cs-prod-q1-admin-check | :13200，Q1隔离镜像 | 保留；不冒充共享管理端:3200 |
| frontend-cs :3300 | Q1时healthy、login200 | 是历史证据，恢复后按需要核对当前版本 |
| 共享rag-service | 最近inspect归agent-wt-travel-proto，之前多次被其他会话重建 | 构建和启停前重新查，不沿用旧归属 |

临时文件均在D:/tmp：

- `cs-q3-isolated.env`：由工作树.env加隔离覆盖生成，含内部令牌及配置密钥，**不得打印、提交或附到报告**；PG在容器网络指向agent-cs-prod-q3-pg，其他PG用途cs_q3；Redis/Auth Redis关闭；enforce；退款申请disabled。尚未用于启动新服务。
- `cs_q3_env.py`：生成上述env；不应盲目重跑覆盖已有nonce。
- `cs_q3_budget_schema.sql`、`cs_q3_schema.sql`：共享库只读schema快照；`cs_q3_schema_load.log`、`cs_q3_model_load.log`：隔离载入日志。模型配置数据经本机管道直接导入，不显示凭据。
- `cs-q2-smoke*.jsonl/log`、`cs_q2_usage.py`：Q2选样/日志/只读对账；旧`cs-live/run_eval.py`已由仓库宿主入口取代。
- 原报告`data/eval_runs/cs-prod-q2-1102-r2/report.json`（gitignore），SHA256 `cd54c077a0167851422b1af8cf528e8a598bbceff00b0506072086d3318dae5d`。不要把忽略目录当可删缓存。

暂停前已存在的构建仍在导出层；本会话未取消它，也未启动后续服务。暂停指令到达后曾误执行一次隔离模板/env/网络准备，已停止并如实记录，**只影响本任务隔离资源**；不据此假称做过实际服务验收。

## 7. Q4–Q6具体推进建议

### Q4：先修可复用压测，再实际执行

复用`scripts/cs_dispatch_loadtest.py`，其现状持续负载是串行请求，且异常耗时与成功混在同一列表，不能证明目标RPS或错误率。先修真实调度、并发/请求计数、独立错误统计、P95口径、预检与非200行为；补定向测试。测试场景包括：坐席状态/接单开关、认领/转接/人工接管状态一致性、SSE/WS长连接/恢复、主agent与客服并行，以及DB/Redis/APISIX资源。写压测只在独立库/实例和测试身份下执行，不能对主栈直接200并发。参考4C8G资源档位，报告说明实测容量、限制、瓶颈与是否阻塞，不承诺未测容量。

### Q5：工具能力与授权边界都要实证

先重新发现Skill/MCP/本机Mimosa命令。当前工具清单及Get-Command未找到Mimosa，Codex Security工具可见，但不能直接把其报告当Mimosa；若需要替代，说明差异并取得相应决定。按authorization-standard逐API做角色/同租户他人资源/跨租户/网关伪造头/WS票据与SSE授权核对，包含Q3新增内部预算头。实际启动扫描时遵守对应Skill和不可变目标；无未处置高危才可验收，外部渗透是否引入仍是用户决定。

### Q6：先建议再拍板，检查全资金面

建议web首期不开放真实资金动作；`CS_REFUND_REQUEST_MODE=disabled`仅关闭退款申请写入，不能证明商城支付、退款审核等入口全部关闭。`CS_BUSINESS_GATEWAY_MODE`是http/sandbox查询与业务网关模式，代码默认sandbox，不支持把disabled当资金关闭。`部署/prod-4c8g/README.md`仍有相反旧描述，核实现行配置/代码后同步。最终由用户明确首期范围；若包含真实资金后端，另立项，不在本任务伪造完成。

## 8. 下一会话启动清单与最终验收矩阵

收到接手/恢复授权后：

1. 读本HANDOFF、TASK、PROGRESS、Q1-RESIDUALS、Q2-VALIDATION、Q3-PREFLIGHT；历史日志与当前状态冲突以本交接+代码/运行证据核实，Q3不再重复拍板。
2. 核对cwd/branch/HEAD/status/diff，保留脏代码及忽略文件；fetch最新origin/main（GitHub抖动用127.0.0.1:7897代理和退避）。有上游冲突先报告，不在脏工作树强制pull/rebase。
3. 核对已有构建、镜像源码、PG/网络/env、端口与共享归属；不要把暂停误当崩溃重启整个栈。
4. 从§4.2预算隔离验收开始，失败先分传输/执行/业务/终态；不降低guard或换无凭据mock来宣称真实通过。
5. 依次完成Q3各单元与PR合并，再Q4→Q5→Q6；任务未完成保持相应状态，遇到真实外部阻塞记录下一步，暂停不继续施工。

最终必须有：Q1共享入口版本与残留处置证据；Q2知识链路指标；Q3实际full_path/多轮/action与跨服务预算阻断/对账；Q4完整容量报告；Q5扫描及授权复核和风险处置；Q6明确决策与实际入口一致；当前主链路真实冒烟及回滚依据。每项列PR/HEAD、T0–T3实际范围/结果/警告、Review处理和未验证P0/P1。任何缺项都不能标记全量公开投产PASS。
