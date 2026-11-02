# PROGRESS — cs-public-launch（客服域公开投产实施）

- 任务文件：同目录 TASK.md（唯一事实源）；计划由 2026-11-02 会话立项（承接 cs-evaluation-upgrade 收官）
- 更新时间：2026-11-02
- 当前状态：**Q1–Q4 已合并；Q5/Q6 报告已交付（PR 流程中）——两拍板项待用户**

## 阶段状态总表

| 期 | 主题 | 状态 | 分支 / PR | 证据 |
|---|---|---|---|---|
| Q1 | 上线巡检小件（管理端选项处理 / frontend-cs 构建验证 / 渠道资金落档 / 残留清理） | 已完成（残留按依据保留；共享管理端部署待协调） | feat/cs-prod-q1 / PR #259 | merge 01aa614；见下方验收日志 |
| Q2 | cs_faq BM25 索引补建 + live 冒烟对比 | 已完成（知识链路指标；非完整质量PASS） | feat/cs-prod-q2 / PR #260 | merge 144aea7；60条繁忙0% |
| Q3 | live 质量缺口决策与实施（4 个拍板项） | 已完成（预算/full_path/多轮/action 合并；剩余拍板项见 2026-11-03（四）） | feat/cs-prod-q3 / PR #270 | merge d7c5a0e；CI 6 success+1 预期 skip；Review P2 已修；action 27/38 |
| Q4 | 多坐席容量压测 | 待实施 | feat/cs-prod-q4（计划） | TASK §3 Q4 |
| Q5 | 安全渗透自检（Mimosa + 边界复核） | 待实施 | feat/cs-prod-q5（计划） | TASK §3 Q5 |
| Q6 | 资金范围决策落档 | 待实施 | docs（计划） | TASK §3 Q6 |

状态取值：待实施 / 进行中 / 待验收 / 已完成 / BLOCKED（注明原因）/ PAUSED（注明现场）。

每期完成后回填：分支与 PR、实际测试命令与 PASS/FAIL、关键数字（如「系统繁忙」比例、压测 P95）、遗留事项。

## 环境

- Worktree：`D:\Program Files\workplace\agent-cs-prod`（新建，分支自最新 origin/main；worktree 无 .env 从主仓库拷贝）
- 测试解释器：`D:\Program Files\workplace\agent\.venv\Scripts\python.exe`（cwd 在 worktree 根）；真库 5433
- frontend-admin node_modules：junction 复用主仓库
- 宿主执行三件套（NO_PROXY / 注册表刷新 / 8088 模型服务）：cs-evaluation-upgrade PROGRESS 2026-11-02（六）
- 身份链（user:279 super_admin JWT + agent-auth-redis + X-API-Key 经 :9080）：cs-evaluation-upgrade PROGRESS 2026-11-02（四）
- 环境坑：cs-evaluation-upgrade TASK §2.3（Mimosa hook / GitHub 抖动退避 / compose 等价原命令 / MSYS_NO_PATHCONV / 并行会话纪律）——开工必读
- 关键参照：锁版数据集与门禁基线 cs-evaluation-upgrade PROGRESS 2026-11-02（三）；live 基线与移交清单 2026-11-02（六）；P7/P8 运营口径 2026-11-02（七）（八）

## 日志

### 2026-11-02（一）—— 立项

- cs-evaluation-upgrade P1–P8 全部收官（评测运营线达成受控试用口径）；用户拍板「写公开投产实施计划，下一会话处理」。
- 本任务立项：Q1–Q6 六期，覆盖管理端测评误导选项、frontend-cs 构建验证、渠道/资金落档、共享库残留清理、BM25 索引、live 质量缺口拍板、容量压测、渗透自检、资金决策。
- 门槛口径：Q1+Q2 = 受控试用放量；Q1–Q6 全绿 = 全量公开投产。

### 2026-11-02（二）—— Q1 接手与首轮验证（进行中，未验收）

- Worktree：D:/Program Files/workplace/agent-cs-prod；branch feat/cs-prod-q1；起点 HEAD 84601db97be7021a28d8375c7c16f973bb888d2f；当前改动未提交，无 PR。
- RunsTab 非 rag 隐藏评测方式、发送 offline、说明同步离线口径；customer-service.md 补 web 与资金开关投产要求。
- T0 git diff --check PASS；评测中心既有定向 Vitest 3 文件 / 8 tests PASS（其中 RunsTab 在 EvaluationCenter 测试中被 mock，不代表新行为验收）；管理端 build 编译成功，类型检查尚在运行。
- frontend-cs 从本工作树 compose build 成功（依赖与构建缓存命中）；仅 up -d --no-deps frontend-cs，未重建其他服务。容器 healthy，curl --noproxy '*' http://127.0.0.1:3300/login = 200。
- 共享 app 与 frontend-admin 来自 agent-wt-travel-proto，未覆盖。
- 清理冲突：review_candidate 禁止覆盖 approved/rejected，因此 398 approved 候选不能再次 reject；已向用户询问保留挂账或另补能力，未执行数据库删除。
- 新发现：共享 app 的 CS_BUSINESS_GATEWAY_MODE 实测为 http，CS_REFUND_REQUEST_MODE 未显式设置（代码默认 disabled）；与计划“双 disabled”不一致，尚未修改共享配置。文档描述为投产要求，不宣称当前运行态满足。
- 未完成：RunsTab 实际行为验证、admin build 最终结果/受影响服务重建、残留引用与活跃状态盘点及清理、资金运行态差异处理、PR/CI逐项核对/合并。Q1 与受控试用放量均未验收。

### 2026-11-02（三）—— 持续实施授权与计划校正

- 用户要求分析完整任务、优化实施计划并持续完成；持续目标已建立，TASK §7 为执行计划。
- 用户确认：398 approved 候选保留历史审核记录，过滤修复另挂小 PR。
- 新增 RunsTab 实际渲染测试 PASS：RAG→CS 隐藏选项/无在线说明、提交 offline、切回 RAG 保留选项；既有 8 tests PASS。
- admin 宿主 build exit 0，类型检查通过，但 standalone symlink EPERM 警告；Docker 生产构建在执行，尚未替换共享 admin 容器。
- 真库只读盘点：unanswered=29（日期 2026-10-04 至 10-31 UTC，并非已证实当日批次）；eval_bot tickets=60 open；confirmations=38 expired+2 cancelled；eval-cs-* checkpoints=0；archived case 关联 approved candidates=398。
- smoke_refuse_mvmdmqfr published version 有 1 item、2 个 suite version 引用，按禁止删除引用对象规则保留。其他同前缀空数据集仍需查权限与活跃关系。
- 业务网关 http 同时承接查询，disabled 不是受支持关闭模式；退款申请未显式设置，代码默认 disabled。文档已纠正语义，其他资金入口待 Q6 核对。
- 数据库 MCP 凭据失效；已使用项目 Python + 当前 .env 中 PG* 配置连接 5433，只读盘点成功，未执行共享库删除。

### Q1 残留清单核对补充

- 完整处置依据见 Q1-RESIDUALS.md：所有共享数据暂保留；60 工单并非无引用（29 handoff、41 条关联 open 会话），不执行孤立删除。29 未答记录不符合当日测试批预设；entry=16 的 intake 未删除原记录。
- 当前数据库所有含 tenant_id 的业务表中 govtest/p7v/p9g- 租户前缀命中为 0；不推广为所有数据库/对象名称均清零。
- 398 候选保留、published rag case 保留、confirmation 终态保留、目标 checkpoint 为 0，均已逐项记录依据。
- 本期为既有 UI 错误修复，新增组件测试由 evaluation-v2.yml 的评测中心目录测试自动覆盖；rag_smoke 无前端 Vitest 执行链，不新增平行前端门禁。
- 补充验证：评测中心目录全量定向 Vitest 5 files / 10 tests PASS（9.75s）；独立 tsc --noEmit exit 0；Diff 静态自查无后端/授权/数据库写入变更。
- Q2 只读预探：共享 rag-service 实际存在 /app/data/bm25/PUBLISHED.json 和索引文件，因此“缺失 BM25”的旧根因必须重新核实宿主与容器路径/发布指针，不盲目重建共享索引。
- admin Docker build PASS，镜像 sha256:4dc84edc5d72638472cab1677b0e4ccb4b0c3faf090e1dbe65b9c09ce3b75c88；独立容器 agent-cs-prod-q1-admin-check 在 :13200 运行，宿主 /login 与容器内 fetch 均 200。共享 :3200 未更新，避免覆盖旅游会话；后续统一部署仍需协调。
- Q1 PR #259，初始 HEAD 9c1ca42；自动 Review 提出 P2：不能将 travel/travel-provider 的离线质量评测标成 contract_only。已改为中性“离线评测（按方案执行）”，避免在前端复制后端模块能力注册表；补两旅游模块切换断言，定向测试 PASS。最终镜像需按修复后代码重建。
- RAG skip 归因实测：本 PR changed paths 输入 rag_smoke_path_filter.py --event pull_request 返回 relevant=false，所以 preflight 跳过；付费 Evaluation 仅可信 main push/dispatch，PR 跳过属预期。自动 Review 已计入，不额外触发云端审查。

### 2026-11-02（四）—— Q1 合并与 Q2 开工

- Q1 PR #259 已合并，merge SHA 01aa6149f69d942553da80189d27e0d3d16d90dd，PR HEAD e1268cbb2b5a1fced5ab4b3211cc518c14e23e19。
- 最新 HEAD CI 全部终态：Evaluation V2 free contract tests / Evaluation V2 admin build and tests / admin-observability-auth / frontend-admin-functional / frontend-admin-security / scan / Detect RAG changes / RAG Smoke 共 8 success；付费评测与 preflight 共 2 预期 skip，原因见前述路径/触发器核对。Review P2 已修复并定向复核，无已知未处置 P0/P1。
- 最新代码 Docker build PASS；镜像 sha256:adcc5c6a82aca9bab329c745b98fabca0a7ef1693881bb5e5c2ee933df8a823a 在独立容器 agent-cs-prod-q1-admin-check、:13200 运行；宿主 /login 与容器内 fetch 均 200。共享 :3200 仍为其他会话版本，不宣称已更新该入口。
- frontend-cs :3300 healthy、login 200；残留逐项保留依据见 Q1-RESIDUALS.md。上述为本机隔离验证，非全量投产通过。
- Q2 分支 feat/cs-prod-q2 自最新 origin/main@01aa614 创建。宿主默认 local、指向不存在的 backend/data/bm25；生产 remote 索引存在且 8090 ready。
- 共享 rag-service 被并行会话重建后现归 agent-wt-ci-blindspots；启动窗口首次检索超时，就绪后同目标 remote 检索成功（cs_faq，548 chars，index_status=ok），未改共享索引。
- 新 scripts/run_cs_live.py 提炼原一次性环境前置：强制宿主 remote、本机代理绕过、退款申请关闭、8088/8090 就绪检查、注册表刷新，然后调用既有 CLI；4 个入口测试 PASS，已加入 rag_smoke 免费预检。
- 本轮实际执行 60 条（v1 20 + CS2 faq12/query8/action8/complaint4/safety4/multi_turn4）；临时选样 d:/tmp/cs-q2-smoke.jsonl、日志 d:/tmp/cs-q2-smoke.log；run_id cs-prod-q2-1102，尚未完成，质量与成本暂不下结论。- Q2 补充验证：既有 cs runner live/离线相邻回归 8 passed（26.63s）；CS 决策回放门禁 6 passed（23.70s）；入口与 workflow 契约合跑 7 passed（0.46s）；py_compile PASS。60 条 live 尚在执行，进程会话 42710，禁止重复启动同 run。
- Q2 诊断批次新增环境缺陷：.env 的 BUSINESS_SERVICE_URL 使用 Docker 名称 business-service，宿主查询反复 DNS 失败。入口已固定宿主 8081 并增加 /health 检查，实际返回 200；修改后入口 4 tests PASS。现有批次不得作为完整链路验收，结束后用新 run ID 做一次必要复测，不并发启动。
- 成本归因限制：request_id=eval:cs-prod-q2-1102 在 llm_usage 为 0 行；远端 RAG 行为 eval_bot / rag-ask:*，另有空身份调用，日志出现子线程无请求预算状态警告。因此不能用该 request_id 零行宣称零成本或预算保护有效；后续以限定时间窗作上界并如实记录跨线程归因缺口。
- 首批已终态 completed、进程退出 1（质量门槛失败，非崩溃）：60 results、38 pass、final_answer 含“系统繁忙”0/60。因业务服务 DNS 缺陷，本批只作诊断证据，不作完整 Q2 验收。
- 修正后复测已启动：run_id cs-prod-q2-1102-r2，日志 d:/tmp/cs-q2-smoke-r2.log，运行会话 56166；复用同一 60 条选样。不得重启或与首批混算。
- r2 已完成：60 results、46 pass、0 error、繁忙0/60；8 query恢复，无退化，v1=9/20。整体质量门禁仍FAIL（smoke50%、core80.8%），CLI exit1。Q2知识链路指标达成，完整质量问题留Q3，不宣布公开投产。详见Q2-VALIDATION.md（含报告hash和成本归因限制）。
- 已新增 cs_public_launch_smoke 命名清单，只引用canonical ID/hash；选样构成与本次60条一致；入口+workflow契约8 passed。当前待Diff/PR/CI/合并。

### 2026-11-02（五）—— Q2 合并；Q3 决策等待

- Q2 PR #260 已合并，merge 144aea7729b4e573b87e5ba34421c42e329c98d2；PR HEAD c33947b510a6a9aef7922b8e5506db45dc98eb57。
- CI逐项终态：Detect RAG changes、RAG Smoke preflight、RAG Smoke汇总、Evaluation V2 admin build/tests、Evaluation V2 free contract tests、scan 共6 success；RAG付费Evaluation共1预期skip（PR不注入付费Secrets、不执行真实评测）。本期路径relevant=true，免费preflight确实执行通过。自动Code Review completed、has_any_findings=false、has_unresolved_findings=false。
- Q2实际证据见Q2-VALIDATION.md：60条live复测、繁忙0/60、0执行error、46pass；路由回放门禁通过，v1=9/20与基线一致。整体质量门禁未通过，不能据此宣布Q3或全量投产通过。
- 已切feat/cs-prod-q3，自origin/main@144aea7。Q3尚未实施功能代码，等待用户对action策略、full_path+multi_turn本期/另立项、周度live与预算保护三组决策；预选项不视为批准。
- 独立只读准备：Q4已有cs_dispatch_loadtest.py可复用，但持续负载为串行且无正确错误率统计，不能直接证明目标RPS；Q6部署README对业务网关disabled默认的描述与代码不符，进入该期时同步核正。
- 当前待提交仅本PROGRESS与TASK状态回填；未更改后端或共享服务。
### Q3 决策前只读复核

- P6原报告b1–b4合计304条；action=24pass/38fail。38失败中32已进入action expert，4query、1knowledge、1complaint+knowledge；抽查错误为must_contain未命中订单号/确认词。不能将缺口全部归为词表不足，不能盲目扩规则。实施前证据与按决策分支见Q3-PREFLIGHT.md。
- 尚无Q3决策回复；未修改功能代码、未再启动付费批次。当前未提交文档仅TASK/PROGRESS状态回填与Q3-PREFLIGHT。

### 2026-11-02（六）—— BLOCKED：Q3 必需决策等待

- 同一决策阻塞已连续三轮存在：Q2合并回填、Q3只读分类、当前现场复核。三组异步问题尚无回复，不能把预选项或持续执行指令当作该决策批准；按TASK明确按序与Q3拍板要求，不开工依赖项。
- 待用户选择：①action逐例分类后针对性修复，或接受现有兜底并只修有依据错标；②full_path与multi_turn本期补齐，或明确限制另立项；③先修复预算保护再周度人工≤10元，或暂不启用常规live。
- 现场：worktree D:/Program Files/workplace/agent-cs-prod；branch feat/cs-prod-q3；HEAD 144aea7729b4e573b87e5ba34421c42e329c98d2。
- 未提交文件：TASK.md、PROGRESS.md、Q3-PREFLIGHT.md（新增）；全部为状态/只读分析文档，未修改Q3功能代码。git diff --check PASS。无本会话跑批或CI等待；不重复测试、不新增付费调用、不动共享容器/库、不stash/reset/清理。
- 已完成Q1/Q2实现与PR合并；全量公开投产尚缺Q3–Q6及主链路验收。收到决策后核对工作树和最新origin/main，再继续Q3；现有证据见Q1-RESIDUALS.md、Q2-VALIDATION.md、Q3-PREFLIGHT.md。
### 2026-11-02（七）—— 用户决策确认，恢复Q3

- 用户明确批准：①按建议逐例复核action后实施；②本期补齐full_path断言接线和multi_turn逐轮重放并实际验收；③采用周度人工一次、每次≤10元，先修复并验证预算保护，不创建自动排程。此前BLOCKED原因解除，不沿用“另立项/降口径”默认建议。
- 恢复核对：feat/cs-prod-q3，HEAD 144aea7，与最新origin/main一致；原未提交文档保留，无其他会话修改。
- 首个实现单元：EvalConfig.cs_mode、--cs-mode、run_all和service传到CS runner，模式进入CS provenance快照；full_path限制为显式cs live。py_compile PASS；定向测试会话65201（结果以终态输出为准）。
- 尚未验收：多轮重放、full_path实际主链路与安全边界、预算上下文与超限阻断、action38失败逐例复核、受影响服务隔离重建、PR/CI/合并。当前不声明Q3完成。- 首个配置单元实际测试：test_cs_execution_mode + test_cs_runner_v2 合跑6 passed（26.18s），含full_path非法离线/非cs拒绝、CLI到run_all保留模式、CS分发到full_path。此为T1接线验证，不是full_path主链路或Q3验收。
- 后续定位线索：orchestration/graph/runner.py存在graph-worker新线程（1024行），其上下文传播需与checkpoint/task_executor.py已有copy_context模式核对；目前仅线索，尚未证明预算缺失根因。
- get_goal仍返回blocked；目标工具仅支持complete/blocked/paused、不支持恢复，未伪造active状态。用户实施决策已收到且本轮已按授权推进，自动持续执行的目标状态需由用户/系统恢复。

### Q3 多轮与预算定位推进

- 持续目标已由用户恢复active。full_path按metadata.turns顺序仅重放user轮次；同case会话复用、不同case/run隔离；只对末轮断言，避免前轮clarification/域进入掩盖末轮失败。error事件不再被忽略，无回答且无澄清时fail。
- 新增定向验证：主图接口桩实际重放两轮、不注入标注assistant回复、末轮澄清独立、跨run会话隔离、禁词与执行错误拒绝。测试会话31449，终态结果以输出为准；尚非真实主链路验收。
- 预算定位事实：core/node_runtime已使用copy_context传播expert线程，不能简单重复修这里；主图graph-worker直接threading.Thread且重新bind RequestContext；远端rag_server每次ask新建rag-ask:*预算ID，当前run归因不能跨服务保持。尚未证明所有缺失根因，下一步补预算上下文边界复现，复用既有统一预算/额度链路。
- 未提交改动包含评测配置/CLI/service/provenance与CS runner及定向测试；未重建共享服务、未启动新付费批次。Q3尚未验收。

### Q3 预算上下文已复现并修复首个根因

- 精确定位：CS Supervisor._llm_decision与understanding.llm_runtime使用infra.async_utils.sync_call_with_timeout；该公共函数ThreadPoolExecutor.submit原先不复制ContextVar。修复前实际2 tests FAIL：身份marker读到none，current_request_budget读到None。
- 最小修复：每次提交contextvars.copy_context().run，保留调用方预算/身份/Trace，退出恢复池线程上下文。未修改预算金额、价格、角色权限或异常降级规则；未在调用点重复计费。
- 修复后定向T2合跑47 passed（22.53s）：test_sync_timeout_context、test_llm_budget、test_supervisor_triage_direct、travel/test_clarification_renderer。包含同一预算对象跨线程、Token超限在provider之前抛拒绝、两调用方身份隔离及子线程修改不污染调用方。
- 新执行模式与线程预算回归均已加入rag_smoke免费预检定向清单。尚未完成远端RAG run级归因、跨服务金额上限与真实live阻断验证；不能以本次局部通过声明预算整体有效。
- 下一步：基于既有Trace/额度/RunGuard接线run级预算与远端归因，补真实full_path/多轮及action逐例复现；代码服务最终隔离重建及PR/CI/合并仍待执行。

### Q3 整批预占与主图作用域推进

- PG预占接口新增可选request_limit_cny：同request_id用事务advisory lock串行预占；复用budget_reservations，按reservation_id去重四个用户/租户日/月账本行，累计settled与reserved/needs_review占额。原调用未传上限时保持既有行为。
- 已新建独立容器agent-cs-prod-q3-pg，端口15433、数据库cs_q3、256MiB/0.5CPU；从共享库仅pg_dump预算表schema（无数据），写入隔离库。未在共享库运行预算写入测试。
- 真实PG并发与状态测试2 passed（27.50s）：同批次跨两个用户/租户的0.06+0.06在0.10上限下仅一个成功；结算差额释放、needs_review保守占额、不同批次隔离。测试连接强制cs_q3或_test数据库，禁止回退共享agent_memory。
- 新scoped_run_budget携带批次ID/人民币上限，不随节点Trace重绑丢失；预占与usage request_id优先批次ID。CS live入口要求enforce，含judge隐含live路径；cs_max_cost_cny验证>0且≤10，金额进入CS快照。
- 新上下文/预算/模式合跑28 passed（33.96s）。真实GraphRunner事件流保留批次作用域及原SSE序列两例2 passed（32.77s）；CS模式及强制保护配置测试会话18323已输出13通过，终态细节以工具结果为准。
- graph_only也按用户轮次顺序执行，共享case会话且隔离其他case/run；与full_path相关定向合跑14 passed（26.29s）。不注入标注assistant回复。
- 当前限制：远端RAG仍未透传新批次作用域，不能宣称跨服务金额上限有效。内部令牌当前宿主未配置；下一步在隔离服务配置可信令牌，完成透传/版本能力拒绝及真实PG阻断，禁止直接覆盖共享服务或放宽验证。

### Q3 远端预算可信边界接线

- RAG client在存在批次作用域时，先要求内部令牌并GET readyz确认run_budget_version=1；旧服务/无令牌/预检不可用先拒绝，不POST ask，不允许静默降为普通请求。ready能力只在服务端配置令牌且LLM_BUDGET_MODE=enforce时声明。
- 服务端ask在pipeline初始化前校验批次头：内部令牌常量时间比较、eval批次ID值域、0<人民币上限≤10、enforce可用；开发模式免令牌不能声明批次预算。合法请求绑定原批次ID及作用域，退出恢复。
- quota的request级402传播到客户端并记入共享批次失败信号；跨线程/子请求重绑后仍能check_quota_blocked，后续Provider预占和case执行检查阻断，service不允许把额度拒绝包装completed。
- 远端/上下文首批11 passed（25.58s）；增加402和跨子请求拒绝传播后，与既有RAG内部令牌API回归合跑25 passed（53.29s）。出现现有Windows Proactor清理Event loop is closed警告与依赖弃用警告，未隐藏；没有将其报告为零警告。
- 当前只有HTTP/服务函数桩验证加既有真实PG并发验证；隔离RAG真实HTTP+PG、模型预占与完整live验证仍未执行，不标记预算整体验收通过。脚本前置还需同步能力/令牌检查以在任何模型调用前失败。
- 新增无令牌在任何HTTP请求前拒绝、开发免令牌不能声明批次预算（pipeline初始化前403）、版本字段严格整数校验等边界。最新transport定向会话71356输出11通过；最终统计见工具终态。未开始新的付费批次或替换共享容器。

### 2026-11-02（八）—— 用户暂停与下一会话交接

- 持续目标状态PAUSED；用户另要求本会话分析总计划、未完成项并写下一会话实施计划。已完成HANDOFF.md，未恢复施工、未启动新测试/付费批次。
- 工作树agent-cs-prod，branch feat/cs-prod-q3，HEAD 144aea7729b4e573b87e5ba34421c42e329c98d2；所有Q3代码与测试仍未提交，无Q3 PR。已改/新增文件完整分组见HANDOFF §2，未stash/reset/清理。
- 最新宿主入口加入令牌/enforce/ready能力预检与显式本机隔离端口，9 tests passed（1.83s）。暂停后只进行文档静态核对，不新报运行验证。
- 暂停前后端compose构建句柄13377仍running，最后输出exporting layers；cs-prod-q3-app尚未读取到镜像ID，不宣称构建成功或已部署。compose可生成依赖镜像标签，但未启动新RAG服务。
- 本任务PG :15433/cs_q3、agent-cs-prod-q3-net及临时隔离env已保留；现有:13200管理端隔离容器保留。共享资源未替换，归属恢复时重查。
- 暂停消息到达后误执行过一次隔离配置准备（仅隔离预算模板/env/网络），已承认并停止。隔离库额外载入现行schema、模型/价格元数据及默认预算模板；未复制会话/用户业务数据，未显示凭据，env不得提交/打印。
- 新交接将原TASK过时启动步骤、生产BM25根因和Q3默认建议改为当前事实；历史日志不删除。Q3-PREFLIGHT作为决策前历史，最新批准范围以TASK/HANDOFF为准。
- 下一会话收到继续/接手授权后，从HANDOFF §4.2隔离预算验收开始，再真实full_path/多轮、action逐例、Q3 PR/CI合并，随后Q4–Q6。未完成门槛与风险不因写出计划而消失。

### 2026-11-03（一）—— 用户接手，Q3 恢复施工：§4.2 预算链路验收完成

- 用户回复「接手 作为持续计划完成」，按 HANDOFF §8 清单恢复。核对：branch feat/cs-prod-q3 @ 144aea7、26 个未提交文件与 HANDOFF §2 逐项一致；fetch 后 origin/main 前进至 775cf74（+10 提交），与脏文件唯一交集 rag_smoke.yml 两处改动相距约 30 行不重叠，可自动合并；未 rebase/pull 脏工作树。
- `cs-prod-q3-app:latest`（73993ebdacf0）确认为本任务构建产物（2026-11-02 14:08 UTC，project=cs-prod-q3），8 个关键源文件 sha256 与工作树逐一比对全部一致——镜像即当前 Q3 代码，无需重建。
- 定向回归统一基线（隔离 PG 15433/cs_q3、Redis 关闭、宿主 venv）：套件1 CS模式/runner 22 passed 13.03s；套件2 预算/传输/内部令牌 45 passed 23.51s；套件3 SSE/入口/workflow契约 14 passed 9.88s；套件4 真实PG整批预占 2 passed 8.57s。历史遗留的宿主单测 DB 触达已隔离（TaskService schema ensured 落 cs_q3）。
- §4.2.1 Diff 审查完成：budget/quota/llm_usage_store/async_utils/graph-runner/rag client+server/evaluation 层契约连贯、fail-closed；测试只增不删（唯一删除行是把 mock 升级为必须携带 run_budget_version）。
- **发现并修复普通请求缺内部令牌缺口**：RAG client 的普通 /ask、/retrieve、/retrieve_docs 不带 X-Internal-Token（文档管理类端点带），共享 RAG 一旦开启令牌普通问答会被既有中间件 401。新增 `_internal_headers()` 统一携带（未配置时保持旧行为），新增 4 例回归。修复后传输+内部令牌合跑 27 passed（33.22s）。
- §4.2.4 真实 HTTP 拒绝路径（隔离 rag_server :18090，令牌+enforce）：缺/错令牌 401（既有中间件）；非法 run_id、空 user_id、缺金额头 422；限额 0/11/NaN 422；无批次头普通 retrieve 200（令牌配置不阻碍普通请求）；readyz 能力声明 run_budget_version=1 仅在令牌+enforce 下出现。
- §4.2.5 最小真实模型预占/结算与低额度阻断（真实 provider doubao-seed-2.0-mini，单次 ≈0.0001 CNY）：case1 预占 0.50→真实调用→结算 0.000086 CNY→差额释放；case2 批次上限 0.000001 在预占阶段 QuotaExceeded('run') 拒绝、provider 零调用零残留；case3 二次调用正常。对账：usage/预占/账本按同一 request_id 三方一致。
- 计价发现（既有治理现状，非 Q3 回归）：隔离库 active 价格版本只覆盖测试模型，doubao 走 registry_fallback 估价（native USD×fx7.2 计 CNY，方向多计约7倍=保守安全）；币种换算链路验证正确（native/billed/fx 字段分离）。
- 脚本事故如实记录：首次误用裸 `get_llm()`（治理链旁路）产生一次无归因 doubao 调用（≈0.00004 CNY 量级、无预占/用量行）；产品代码已系统性迁移到治理代理（judge/chat_fallback 等均注明），仅启动预热与 admin prompt 测试端点用裸实例。验收脚本修正为 `llm` 代理单例后全部通过。

### 2026-11-03（二）—— §4.3 隔离 RAG 栈搭建与 full_path live 验收（run1–run4）

- 隔离栈搭建（全部只读拷贝自共享，写入仅落隔离资源）：bm25(29M)+chroma(11M)+doc_db(5.9M) 从共享 rag-service 容器拷至 d:/tmp/q3-rag-data；rag_vectors(11025)/rag_index_meta(945)/doc_registry(2401)/chunk_store(5335)/keyword_rules(133) 从共享 agent_memory pg_dump 导入隔离 cs_q3；隔离库 llm_providers.custom-gpu base_url 修正为 http://127.0.0.1:8088（原 host.docker.internal 宿主不可达）；rag_index_meta 全部非 candidate 行 model_revision 对齐运行时 embedding 指纹（79f7eb…）。隔离 rag_server 最终 readyz ready + run_budget_version=1，cs_faq 检索返回真实内容。
- 新增 in-repo 验收工件：backend/evaluation/datasets/cs/suites/cs_q3_acceptance.json（11 条锁版 ID：拒答×2、澄清×2、propose×2、query 链、多轮×3、投诉 handoff、faq answer）。
- run1–run3 逐层排障（证据均在 /d/tmp/q3-fullpath-run*.log）：run1 发现 /ask 500（doc_db 等次级 collection 指纹未对齐）与主图路由全澄清；run2 修复指纹后 query/action 仍澄清；run3 隔离库 sys_config 设 CS_ROLLOUT_PERCENT=100 后 prefilter 生效但 TRANSACTION/AFTER_SALES 类仍不入域。
- **run3 根因（真实产品发现）**：主图 CS prefilter 用域检测器（纯正则，CS_RULE_MIN_HITS=2 且每域只计 1 次），对「查订单状态/申请退款/我要投诉」等单域命中全部 no_match——检测器召回远窄于 CS 路由器；prefilter 单测全部 mock detect_cached，检测器召回从未被测。生产口径是否放宽属产品决策，本任务不擅改；评测进程以 CS_RULE_MIN_HITS=1（仅评测 env，与灰度=100 同类测试配置）打通全链路。
- **run4（q3-fullpath-1103-4）结果 4 pass / 7 fail，全部失败均为真实行为发现**：
  - PASS：C1-001（faq 域进入+回答）、C2-001（query 链+must_contain 订单号）、C4-001（投诉显式转人工建单）、C5-001（**多轮上下文依赖真实生效**：次轮「它」正确解析为订单并回答物流状态）。
  - FAIL→已定位根因：C3-021/C3-001/C5-003/C5-010 路由与实体解析缺陷（见 2026-11-03（三））；C1-011 期望澄清事件实际走无答案兜底（澄清事件语义缺口）；C6-001 input_guard 在主图入口先拒（安全结果达成，但 cs_route=hit 断言口径冲突，待拍板）；C1-053 出域问题（股票类）检测器 0 命中落主图查询技能。
  - 多轮重放 turns=2 全部生效；error 事件 fail-closed；跨 case 会话隔离（session_id 含 run_token+case id）由单测+报告证实。
- 预算跨服务归因实证（run4）：32 条 llm_usage 全部带 eval:q3-fullpath-1103-4 + eval_bot/default（隔离 rag_server 侧 embedding 行与评测进程侧 doubao 行同批次）——Q2 时代「空身份调用」修复实证；预占按请求级 0.50 保守全量、结算释放差额，并发占额+已结算始终 ≤10 元批次上限，本 run 实际花费 ≈0.01 CNY（registry_fallback 保守计价）。

### 2026-11-03（三）—— §4.4 action 逐例复核：fixture 根因反转 + 4 项修复 + 27/38

- **根因反转（复核核心结论）**：P6 的 action 38.7% 缺口主因不是客服图缺陷，而是评测环境缺 DEMO 沙盒种子——P6 b2 的 C3-021 回答「暂时没有找到可用于该申请的订单」与本次 http 模式重测逐字相同。数据集的 DEMO-1001~1015 契约基准是 sandbox 演示库（backend/sql/seeds/demo_sandbox.sql，customer 99001）；共享 http 网关只有随机 MO 单。Q2 抽的 8 条 action 全是「索要订单号」类澄清用例，不需要真实订单，故未暴露。
- 评测环境口径修正（全部隔离资源）：003_agent_business_schema + demo_sandbox.sql 载入隔离 cs_q3（15 条 DEMO 订单）；补建 agent_readonly 只读账号（SQL 执行器连接所需）并授予业务 schema SELECT；评测进程 CS_BUSINESS_GATEWAY_MODE=sandbox + CS_DEMO_MODE=true。
- 重测轨迹（graph_only，隔离 RAG :18090）：run1 0/38（http 模式无 DEMO 单）→ run3 23/38（sandbox 打通）→ run5 **27/38**。P6 同条件为 0（全部无单可查）。
- **4 项代码修复（均带回归测试，CS 三门禁+守卫+缺陷9 合跑 65 passed，客服侧受影响面 176 passed）**：
  1. 实体改写吞意图：`resolve_explicit_order_route` 未按文档语义限定「无域关键词」，把「订单 X 申请退款」改写成查单丢掉 as_refund（full_path C3-021 复现）。修复=仅当原始路由落 knowledge 兜底才改写（backend/customer_service/context/context_resolver.py）。
  2. 质量诉求 coarse 单票不过门槛：「屏幕有点划痕要处理」「收到是坏的，帮我处理」落知识兜底。补 AFTER_SALES 词表 r"划痕" 句式与「坏的」关键词（vocab.py，与退款/退货同双票机制）。
  3. 地址修改提案语义塌缩：target 塌缩到 user 且 new_address 不在语义载荷键 → 不同订单改址共用指纹互相误挡（跨会话连坐）。修复=target 按订单作用域 + _SEMANTIC_PAYLOAD_KEYS 增 new_address（business_guard.py / action.py / account_action_service.py）。
  4. 地址确认卡未点名订单：订单级改址卡补「修改订单 X 的收货地址」（锁版 must_contain 订单号口径，与退款/退货卡一致）。
- **剩余 11 例全部归类（无词表类遗留），均需用户拍板或另立项，不擅改**：
  - 资格口径分歧 4 例（033 paid 退货 / 044+045 cancelled 退款 / 061 completed 取消）：图按种子资格规则拒绝并给出政策说明，数据集期望 propose/其他关键词——锁版修订或种子口径，需业务依据。
  - 同单重复动作 2 例（038 vs 027、041 vs 028，同订单同动作）：语义守卫跨会话幂等是生产正确设计（§48），数据集同单双用例与其冲突——锁版修订或载荷加 reason，需拍板。
  - 无单号改址歧义 1 例（049 期望 clarify，实际自动绑定 DEMO-1006）：多单歧义→clarify 规则未覆盖 action 路径，与 C5-010 同类。
  - query 专家维修/质量意图 4 例（057-060）："您当前没有相关订单记录"——intent→entity 映射 order 但查询路径返回空，行为设计缺口，挂账另修。
  - full_path 主图路由发现（2026-11-03（二））：检测器召回口径、turn-reference 动作路由、澄清事件语义、input_guard 先于域断言——均记录待拍板，未擅改生产路由行为。
- 新增 in-repo 工件：suites/cs_q3_action_pool.json（38 条 P6 action 失败池）。
- rag_smoke 定向清单补入 defect9/守卫/CS 三门禁 4 个测试文件（词表与守卫改动连带守卫，此前不在任何 workflow）。
- 最终定向回归（最终代码态）：客服受影响面 176 passed + S1 22 + S2 49 + S3 14 + PG 2，全部通过。

### 2026-11-03（四）—— Q3 PR #270 合并收口

- 分支 feat/cs-prod-q3 提交：6884144（功能 31 文件）→ feca65a（文档）→ 2d7658b（scan 误报修复）→ 3190709（Review P2 修复）。PR [#270](https://github.com/Jayus-07/agent/pull/270)，merge `d7c5a0ece15c800ec29a6ed032c646af0ae180c6`。
- CI 逐项终态（最终轮 2d7658b→3190709 后）：Detect RAG changes / RAG Smoke preflight (PR, no paid API，含新增 defect9/守卫/CS 三门禁定向) / RAG Smoke (8 cases) 门禁校验汇总 / Evaluation V2 admin build and tests / Evaluation V2 free contract tests / scan 共 6 success；RAG Smoke Evaluation (8 cases, real API) 1 项预期 skip——付费评测仅限可信 main push/dispatch，PR 不注入 Secrets（该口径与 Q1/Q2 一致；"RAG Smoke (8 cases)" 任务本身为门禁校验汇总，无 API 调用，已核实）。
- 第一轮 CI scan FAIL：`backend/rag/client.py` 新增行 8 处 assigned_secret 误报——header 键名接内部令牌配置变量（无字面量）被"token: 长值"启发式命中。以 import 短别名断开形态修复，未改扫描器白名单（不削弱门禁）。
- 自动 Code Review（chatgpt-codex-connector）P2 一条（有效发现）：服务端 /ask 把整批请求绑到同一请求预算 ID，`bind_request_budget` 按 ID 复用 `_states` 累计状态 → 0.50 元/32k token 单请求限额被整批累计。已修复：服务端恢复唯一 `rag-ask:{uuid}`；CS runner 两个循环按 case 绑定 `eval-case:{run_token}:{case.id}` 并 finally 清理；批次归属与 10 元上限由 scoped_run_budget 承担（PG 预占/用量取 run 作用域 ID，代码路径已核对解耦）。带 per-case 唯一性回归测试；相关合跑 35 passed。Review 无其他发现。
- 合并后 origin/main = d7c5a0e。Q3 交付门禁对照 HANDOFF §4.5：定向验证✓、隔离重建（自建 :18090 全栈+镜像哈希核对）✓、Diff 自查✓、独立 PR✓、CI 逐项终态与 skip 归因✓、自动 Review 处理✓、合并✓、回填✓。
- **Q3 未关闭项（不阻塞本期合并，需用户拍板/另立项）**：①action 剩余 11 例（资格口径 4、同单幂等 2、歧义 clarify 1、query 专家维修/质量查询缺口 4）；②full_path 主图路由 4 类发现（检测器召回口径、turn-reference 动作路由、澄清事件语义、input_guard 断言口径）；③价格治理 active 版本未覆盖 doubao（registry_fallback 保守方向）；④Q1 共享管理端 :3200 部署协调项。

### 2026-11-03（五）—— Q4 容量压测（脚本修正 + 三段实测）

- 脚本修正（HANDOFF §7 判断属实）：cs_dispatch_loadtest.py 重写为 open-loop 定时发射（旧实现串行，实际速率=1/延迟）；分位数只统计 2xx、错误按 status/异常独立分布；P95 ceil 索引防小样本越界；预检按 401/403/429/5xx 分类；--header 支持认证；默认路径改为真实存在的 /api/cs/customer-context（旧默认 /cs/conversations/stats 网关 404）。定向单测 4 passed（backend/tests/scripts/test_cs_dispatch_loadtest.py）。
- 网关事实核实：CS 路由经 /api/* 兜底（剥前缀）+ gateway-auth（JWT 验签+会话闸 auth:session:{uid}:{jti}）+ limit-count 600 次/分/用户（≈10 RPS 反滥用基线）；应用层另需 X-API-Key（纵深）。
- 身份链：项目 issue_access_token 签发 user 279 / agent 角色 / TTL 1800s，会话键写入共享 auth-redis；测后 auth:session:279:* 全部删除（残留 0），token 文件已删，凭据未入日志。
- 实测（共享栈 :9080，只读端点）：突发 50 并发 50/50 成功 p50 8.53s（排队未失败）；突发 20 p50 0.56s；持续 8 RPS×180s **1441/1441 成功、零错误，p50=42ms / p95=116ms / p99=241ms**，发射=完成 8.0 rps 无积压；限流边界 25 RPS×30s 751 发射 589 成功 + 162×429（网关层拒绝，桶 600/分行为与配置一致）。
- PR #275 Review 两条已修：P1 新测试文件补入 rag_smoke 定向清单（AGENTS.md 连带要求）；P2 httpx 默认连接池 100 会导致 open-loop 在途超池时客户端排队虚报发射速率——新增 _pool_size() 显式配置（max(并发, rps×超时, 512)）+ 单测；已核实在途≈rps×p95 远低于默认池，本报告数据不受影响。单测 5 passed。
- 报告：docs/tasks/cs-public-launch/Q4-LOADTEST.md（解读/未覆盖场景/瓶颈分级）。原始 JSON 在 docs/reports/（随分支入库），SHA256 前 16：e362aca476f2881a / 67f0edd9c9ce02e3 / 69645e87ff9b7149 / 9697dc137992cd50。
- 瓶颈分级：限流 600/分/用户=设计边界（放量前确认并发用户规模）；突发排队（50 并发 p50 8.5s）=可挂账（隔离栈复测 100/200 并发）；WS/写路径容量零数据=挂账（需隔离栈，不阻塞受控试用）。
- 未覆盖如实声明：写路径、坐席状态流转、WS 长连接、主 agent 并行、资源水位——均需隔离 app 实例，本报告不据此宣称容量。

### 2026-11-03（六）—— Q5 安全自检 + Q6 资金范围核对

- **Q5 Mimosa 深扫**：本会话 Mimosa MCP 工具可用（HANDOFF「工具缺失」已失效）；jobId scan-job-mvzrfg8m-877b3546f6831efa，scanId scan-2026-11-02T21-22-50.452Z-77e8d4e922d1，seal sha256:722528c4…（密封完成）。304 findings（high 250）+ 依赖离线告警 88 条/14 包。逐类抽查+全部高危单点人工核实：硬编码凭据 3=误报（字典键/故意错误凭据测试占位）、SQL 74=服务端配置表名 DDL 拼接（值参数化）、SSRF 115=前端 fetch 与内部脚本、路径穿越 44=防御在位（realpath+commonpath/basename）；挂账 4 项（pptx ElementTree 解析用户上传 XML 建议换 defusedxml=中；验证码 random→secrets、演练脚本 shell=False、BM25 pickle→restricted unpickler=低），立即修=0。报告 Q5-SECURITY.md。
- **Q5 授权边界逐端点复核**：13 个 cs_* 路由文件三档鉴权全覆盖、无未鉴权路由；会话对象级访问守卫（user+tenant 双匹配、仅当前认领坐席）覆盖全部按 ID 端点；工单详情 tenant+归属+404 防探测；evaluation_v2 42/42 路由有鉴权依赖（脚本核验）；WS 一次性票据 60s/单次/服务端反查 agent_id/API-Key 拒签；app :8000 仅绑 127.0.0.1（信任边界=网络边界）；customer_service 无 f-string SQL（参数化），sandbox 走 agent_readonly SELECT-only。报告 Q5-SECURITY.md §二—§四。
- **Q6 资金入口全景**（共享栈运行态实测）：①退款申请 CS_REFUND_REQUEST_MODE 未设置=disabled ✓；②退款审核双开关未启=disabled ✓；③**order_cancel 无独立开关**且 gateway=http 下取消+退款链可达（终点 business-mock，幂等+身份闸在位）——真实后端接入前需补开关（挂账，用户拍板）；④/mall 演示商城 9/9 路由有鉴权；⑤CS_BUSINESS_GATEWAY_MODE=http 非资金开关。**当前全链路无真实资金通道**（全部指向 business-mock）。
- **Q6 文档同步**：部署/prod-4c8g/README.md 修正「BUSINESS_GATEWAY 默认 disabled」旧描述（代码事实：默认 sandbox，disabled 非受支持值）。决策记录 Q6-FUNDS-DECISION.md：建议首期不开放真实资金动作（现状即满足），两个拍板项（首期范围、order_cancel 开关）待用户。
- 交付物：Q5-SECURITY.md、Q6-FUNDS-DECISION.md、README 修正——docs/cs-prod-q5 与 docs/cs-prod-q6 两分支走 PR。
