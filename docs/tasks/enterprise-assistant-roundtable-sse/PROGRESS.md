# 进度

## 2026-10-28：持续任务第一轮——基线实测、三异常根因修复与发布管线死锁

- 用户指令：作为持续任务，费用不设限，用最优解让五类任务全部达标；验收加入上下文/记忆/多轮/计费四维度；全部修复后用 browser-use 复测。
- **基线实测（12 题，管理端正式 API）**：普通解释 2.08s✅、支付 FAQ 5.70s✅、无资料正确拒答 3.35s✅、联网 pgvector 4.78s✅（链接精确）、旅游引导 0.72s✅、SQL 月度 4.92s✅、SQL Top3 2.87s✅、记忆写入/回忆 2.68/1.78s✅（"蓝杉42"正确）；首反馈 0.04-0.41s 全部达标。异常 3 项：礼貌改写 8.68s❌（目标2-5）、发货/发票两问答"未找到"❌（该 FAQ 明确存在）。
- **计费审计（新增维度）**：主链路 23 条 llm 明细归属完整（trace/user/tenant/request 全有，成本实算）；**次级链路漏归属**——memory.extract 后台任务每题产生 ~900 tokens doubao 调用+embedding+rerank 全部无任何归属（user/tenant/request/trace 全空）；本地 rerank 全部 0 成本未计价；llm_usage.cost_usd 列恒 0（实际成本在 input_cost/output_cost，展示口径不一致）。
- **异常1根因与修复（改写 8.7s）**：VectorRouter 对账在 MCP 快照抖动（expected 116↔118 每 ~30s 翻转）下连续全量重建，每次 ~116 条串行 embedding 独占 8088 单推理槽 7-8s，用户路由 embedding 被排队（领域判断 5.4s）。修复：`ROUTER_REBUILD_COOLDOWN_S=300` 冷却 + 内容签名门禁（同签名不重建）+ 单飞双检（vector_router.py）。实测改写 8.7→5.7s、领域判断 5.4→3.5s；残余为路由阶段 7 次串行 embedding（未竞争时 ~190ms/次）与后台记忆任务争抢单槽——后续优化项（减少/并行化路由 embedding、记忆任务错峰）。
- **异常2根因与修复（发货/发票未命中）**：分两层。(a) DB chunk 陈旧——2026-10-04 旧版 QA 解析器写入的 chunk 问句行为空（源文件"Q：客户下单后多久发货?"完整，解析修复于 10-13 后该文档从未重建），重排只能靠答句打分；已走正规 reindex 接口重建（18 chunks 问句完整）。(b) 模型语义塌缩——bge-reranker-v2-m3 对语义等价问句打分差 20 倍（"现货商品通常多久发货"0.995 vs "企业订单的发货时效是怎么规定的"0.073；本地 CPU float32 复算与 GPU float16 服务完全一致，排除精度问题，为模型真实行为）；充实 simulated_questions 无效（0.09）；10-27 通过样本本就是词汇重合问法（0.99），此问法从未通过过——非回归，是召回鲁棒性缺口。
- **纠错链修复**：自纠错改写提示词过薄（仅"改写使能命中"）产出书面压缩变体仍被淘汰。已改 default yaml（口语化同义桥接+证据侧重变体，playground 实测首变体"企业订单发货需要多久时间"重排分 0.57、发票"企业申请发票的流程是怎样的"0.64）；`try_rewrite` 跨重试轮次复用变体缓存（免重复 LLM 调用）+ `SELF_CORRECTION_MAX_RETRIES` 1→2。**v1 提示词下两问仍未命中**——v2 上线被发布管线死锁阻断（见下）。
- **发布管线死锁（阻塞项，需用户决策）**：`prompt_release_runner.py:66` 硬编码 `strict_fields=True`，发布评测强制要求 question/expected_answer/ground_truth 全量可用；逐一核查全部 9 个既有 RAG suite 样本均缺 ground_truth → **任何 prompt 都无法走完发布流**。v2 草稿已建（版本 2）。两条出路：(a) 为评测样本注解 ground_truth（从权威 FAQ 源文档派生，增量式修改共享评测数据）；(b) 调整门禁策略（如允许带标注豁免的 smoke 发布）。缓解证据已留：playground 改写质量 + 直接重排分对照。
- **重建与复测**：`devctl.bat rebuild /y` exit 0，app/rag/worker healthy 且归属本 worktree。复测：改写 5.75s（仍略超 2-5s 目标，残余为上述串行 embedding 争抢）、shipping 11.5s/invoice 6.6s 仍未命中（等 v2 提示词上线）、无资料拒答 2.97s 正确（重试上限调整未拖慢拒答）。
- **测试**：新增 `test_vector_router_rebuild_throttle.py` 5 用例 + `test_self_correction_strategy.py` 变体缓存 4 用例，连同相邻组 32 项全部通过。**既有失败定界（非本轮引入）**：`test_router_domain_literal_guard`（execution_mode.py 领域字面量，上游提交遗留）；`test_chain.py` 7 处 + `test_rag_p1_self_correction.py` 1 处断言旧拒答文案（"暂无/未提及"，文案已在先前提交改为"知识库中未找到/未能通过事实一致性校验"，测试未同步）。
- **未完成**：browser-use 完整复测（用户指定收口方式，等 prompt 出路确定）；memory.extract 归属绑定、rerank 计价口径、cost_usd 展示统一；路由 embedding 串行优化；P95/小并发统计与冷热分类；多轮会话/切换浏览器回归。

## 2026-10-28：Zcode接手后完成父子时限传播（P1）

- 接手核验：worktree/branch/HEAD与交接文档一致（d11ab4a，分支codex/enterprise-assistant-reliability-fixes），8个未提交文件A/B分层确认未覆盖；容器栈healthy且compose working_dir为本worktree。8088模型服务交接后掉线，经本worktree `dev-local-models.bat start`恢复（healthz双模型CUDA/float16就绪），未重建索引、未改模型绑定。
- B组WIP已完成全链路接线（此前`provider_deadline_kwargs`无调用方、未测试未重建）：
  - `executor.py`：`scoped_execution_timeout(effective_timeout_s)`包住每次工具尝试，ContextVar经to_thread传入同步工具线程（新增真实executor继承用例）。
  - `deadline.py`：`provider_deadline_kwargs`修正——补查ChatAnthropic的`default_request_timeout`字段，已有更短超时只收窄不放宽；httpx.Timeout逐分量封顶；过期直接抛TimeoutError。已核对宿主安装版本（langchain-openai 1.4.1/langchain-anthropic 1.4.8）源码确认per-invoke kwargs并入SDK create payload，`timeout`为请求级覆盖。
  - `proxy.py`：invoke/ainvoke韧性循环每次尝试前按剩余时限收窄SDK读取超时，过期不再预占、不出站；流式sync/async包装调用点收窄+chunk边界检查（单次read超时≠总时长上限）；主/备两条模型路径同样收窄。
  - `rag/client.py`：ask在执行时限内发送`X-Agent-Deadline-Seconds`（剩余−1s回传余量）并把读超时封顶到剩余值；顺带修正此前per-request浮点超时把connect也放大到300s的旧不一致（对齐长短分离设计）。后台/无作用域调用行为不变。
  - `services/rag_server.py`：/ask校验头值（有限数值、≤3600s、过期归零），扣除接收→绑定间耗时与0.5s服务端回传余量后以本地单调钟还原为执行作用域包住pipeline.ask；无头保持旧行为。新增两端`[RAG client deadline]`/`[RAG /ask deadline]`观测日志。
- 传播语义实测：父rag.search 45s → 头43.99s → 服务端作用域43.47s；rag-service内部LLM读取超时从固定120s变为随剩余时限递减，父超时后子不再长时间空转；SDK隐式重试已被A组max_retries=0消除；时限过期不再发起新的出站尝试。
- 测试：新增`backend/tests/tool_runtime/test_execution_deadline_propagation.py`30项（作用域嵌套/线程继承/收窄不放宽/过期不出站/流式边界/头校验/服务端还原，/ask经TestClient打桩验证作用域真实生效）；定向回归：executor边界+RAG超时对齐+server令牌+remote模式48 passed；驱动/预算/绑定+入口流61 passed；任务/移交/额度/一致性36 passed；resilience+SSE契约43 passed。约218项全部通过，存在既有pytest-timeout配置警告与Windows事件循环清理警告，未冒充无警告。
- 既有失败定界（非本次改动引入）：`test_llm_resilience.py::TestCircuitBreakerFallback::test_open_circuit_uses_fallback_model`在本机环境必失败——共享库登记了带价格档位的fallback模型，熔断开路走fallback时`_enforce_model_input_price_tier`对空入参按`price_tier_unrecognized_input`硬抛ContextBudgetExceededError（proxy.py:645→2438，均为未修改的既有代码；该用例无执行作用域，本次插入点全部为no-op）。待后续单独修复该用例的环境假设。
- 重建与真实验证：`devctl.bat rebuild /y`返回0（首次执行曾被本会话主动中断留下半死容器，按避坑纪律重跑完成）；app/rag-service/agent-worker/rag-index/mcp healthy、compose working_dir为本worktree、8000/8090/9080均200、无OOM无重启。真实API两问：普通解释2.09s（首反馈0.065s、首正文1.694s）、显式知识库问答10.40s（首反馈0.072s、首正文10.345s），均在目标区间且与改动前基线一致；两端时限传播日志已核实。证据`D:/tmp/agent-deadline-prop-20261028.json`。
- 边界与未完成：时限传播消除了"父超时后子空转"与SDK重试放大，但122秒生成等待当时的供应商/传输根因无历史现场可复现，现以"同类等待最长不超过父预算"硬约束兜底；冷/热P95与统计性时延、复杂报告来源完整性等整体UNVERIFIED项不变。本轮未提交前保持原状，提交/合并按用户既有授权执行并另行汇报。

## 2026-10-28 01:47：用户要求交接Zcode

- 用户要求把当前任务工作树交由Zcode继续，Codex停止功能修改，原工作树和常驻服务保留。交接入口为根`ZCODE_HANDOFF.md`，包含完整目标、Git、端口/进程、测试/证据、未完成事项及续工顺序；不包含密码/令牌。
- HEAD/main仍d11ab4a727a080ba2ee2f398018d3e9c9b0af1f0，任务分支codex/enterprise-assistant-reliability-fixes。交接前已有8个未提交文件：6个已验证后续优化/任务记录，另2个为刚写的deadline.py/executor.py时限传播WIP。WIP未测试、未重建，provider_deadline_kwargs尚未被调用，不能宣称RAG时限传播完成。
- 最新核验Docker代码服务healthy、Compose工作目录为本worktree，8088双模型CUDA就绪；3100/3200/8000/8081/8088/8090/8091/9080/5433/6379/6380已监听，3300未监听。更多映射和宿主5432区别见交接文档。
- 整体目标未完成，不标complete；本会话执行转为PAUSED交接状态，防止与Zcode并发写入。交接文档写好不等于已通过接口启动Zcode任务。

## 2026-10-28：阶段合并与下一阶段总耗时优化

- 用户新增“作为持续任务优化总任务”的要求，已创建本会话active持续目标：按原目标和最新边界持续定位、修复、实测、阶段性本地合并。当前仍复用d11ab4a上的任务worktree，保留未提交的冷首反馈与SDK重试修复；下一优先项为RAG跨服务时限不一致及完整回答时延分解，不设重复定时任务或擅自推送生产。
- 阶段合并完成：功能提交fe36a22，接入最新main的合并提交d11ab4a；主目录main已通过ff-only更新至d11ab4a，工作区干净，未推送。接入后T2后端83 passed、前端45 passed；本worktree devctl rebuild /y返回0，API/网关健康。无新PR或云端Review；这只是已验证功能阶段合并，不代表剩余性能/业务质量已通过。
- 合并后继续定位：RAG容器实际LLM_REQUEST_TIMEOUT=120秒，远端/ask读取超时300秒，父rag.search工具45秒；自建OpenAI兼容驱动没有设置max_retries，实际SDK默认2次，代理层另有重试。已确认是可放大失败总耗时的因素，尚不能据此断言首次122秒生成等待的唯一根因。下一批在任务工作区关闭OpenAI/Anthropic自建驱动的SDK隐式重试，保留统一代理重试/预算/Trace，不全局砍长文生成时限。真实SDK+MockTransport对408/429/503的invoke/stream六个反例均只发一个请求；驱动/预算/绑定回归44 passed，T0通过。该批暂未提交/合并，正在重建与正常调用验收。
- SDK改动重建后正常接口：53ea5e18d089普通解释4.637秒，9d73eef6d45e知识库6.806秒且FAQ出处正确，D:/tmp/agent-sdk-retry-smoke-20261028.json；冷启动首反馈2.213秒未达标。定位get_multi_agent仍在HTTP处理阶段同步初始化，改为producer工作线程中执行；deps已有双重锁单例，不重复初始化。新增直接消费StreamingResponse的异步测试阻塞图初始化，确认meta与明确反馈先到且最终done正常，连同驱动组31 passed。下一次重建后复验冷首反馈；不把本次首次反馈失败隐去。
- 下一批两项优化已从本worktree再次devctl rebuild /y，返回0，API/网关及worker健康。重建后首问0307060656f9：首反馈0.043秒、首正文1.234秒、完整终帧1.740秒，回答正确，D:/tmp/agent-cold-feedback-fixed-20261028.json。仅为本次首问样本，仍不是P95验收。下一批6文件（驱动/入口/测试/任务文档）保留未提交；本地main仍d11ab4a且干净，阶段合并后总耗时优化继续进行。
- 用户明确授权：达到可合并条件时先提交并合并本地main，然后继续优化不同任务的端到端总耗时。指标分别记录反馈、正文、流终帧以及后台任务最终完成，转后台通知不能冒充完成。
- 合并前检查：本任务HEAD 4a7a0b4，最新main为953c201（工具治理/管理端新增9个提交），变更文件无重叠；主目录工作区干净。保留本任务既有修改，排除Next构建产生的next-env路径噪音，不推送、不创建PR、不擅自发布生产。
- 本地Diff自查重点：可信产品/用户/租户、恢复时当前授权、移交状态先落库后入队、节点检查点与额度拒绝、会话任务锁、成功记忆事务及outbox、单模型推理准入。已有T1/T2及真实页面证据保留，偶发生成长等待和复杂报告来源完整性仍作为未验收项，不宣称本次合并即完成性能目标。接入main后补跑相邻契约与实际服务健康检查。

## 2026-10-28：实际业务计划持久化、性能与记忆/预算测试

- 复工确认worktree为assistant-roundtable-sse/agent，分支codex/enterprise-assistant-reliability-fixes，HEAD 4a7a0b4；保留既有未提交修改。本轮未提交、未合并、未创建PR，未运行全量T3或云端Review。
- 按用户最新决定删除HTTP字数/段落/关键词长任务判定。Critique后的实际计划有至少两个业务能力节点才移交现有TaskService/TaskManager/TaskGraphExecutor/PostgresSaver；完整values状态先持久化，之后入队，不重跑已完成Router/Planner/Critique。单次长文章仍普通SSE。主图内嵌工作流目前仍是原子节点，未宣称内部工具函数任意位置都可恢复。
- 新增真实LangGraph/MemorySaver移交测试，保留历史和累计步骤结果，校验前台关闭后不执行后续业务节点、后台从下一节点恢复。真实接口发现内部移交事件被旧节点白名单忽略，导致已入队却前台显示无结果；改为白名单解析前处理，并补GraphRunner终帧/不写成功问答记忆的集成回归。
- Chat入口在执行池排队之前发送可重放的明确反馈；耗时起点前移到handler入口，分别记录首反馈、首正文、流终帧。后台done.task_id代表移交，不代表报告完成。页面任务查询异常显示状态暂不可用，不伪装成缓存的已暂停；未成功任务有自身任务记录时可恢复，精确的会话不存在提示被消除。
- 会话临时代号被误路由，现沿既有general_chat处理，查询企业私有事实仍保持工具授权；Planner后处理不再删除用户明确要求的知识库/退款政策来源。显式联网不被“一句话解释/用途”格式抢成普通直答。GitHub同名仓库优先项目自身同名命名空间，补Redis/Windows移植仓库反例；说明用途进入已有汇总链路，纯链接仍走快速路径。
- MemoryService先事务提交L2、幂等收据和L3 outbox，再后台投递UUID，不等待Broker。新增真实PG故障注入验证Broker阻塞时仍可读取已提交消息/outbox；原子幂等、失败重试、恢复投递和删除取消均保留。测试发现旧清理依赖级联外键，造成同一测试前缀消息残留；改为ORM按本轮随机前缀显式清理子记录，未降低断言。
- 本地BGE与rerank共享单推理槽，最多一个额外等待者，embedding batch=1，输入规模准入，OOM后释放槽位并冷却；等待者拿锁后再次检查冷却。真实8088 health确认双模型CUDA/float16已就绪，embedding返回1024维，65项请求413，证据D:/tmp/agent-model-guard-live-20261028.json。没有故意耗尽真实显存，不承诺任意模型/输入绝不OOM。生产模型外置；Agent worker改为单并发768MiB，报表worker512MiB，常驻总预算仍7168MiB。新worker实测约479/768MiB。
- T0 py_compile与git diff --check通过。定向测试分组：计划/任务API/传输/Trace首组39 passed；记忆/上下文硬门禁/隔离/预算/额度暂停/模型guard/首反馈指标92 passed；前端useSSE/useAgentTask/agentTasks/store 45 passed；更新后的路由/身份/模型guard组40 passed；SSE契约和模型guard组34 passed；GraphRunner移交及真实图18 passed；最新计划/路由组49 passed（以上有重叠，不相加）。SSE旧测试漏paused的14种硬编码改为明确15事件清单。Web/Reporter组原57 passed，新增用途汇总参数用例后Reporter定向8 passed；Outbox清理修正后6 passed。Windows事件循环关闭时资源清理警告及缺pytest-timeout插件配置警告仍存在，未冒充无警告。
- 实际UI：1012原任务cdc19a05…暂停后点击继续已完成，刷新正文仍在且无会话不存在提示。新会话uJVbEjLMarxnwP4PtOwW2的多步骤任务在2个已保存节点后PAUSED，执行本worktree重建、刷新后仍PAUSED，点击同一任务继续到9步完成；证据D:/tmp/agent-multistep-paused-20261028.png、after-rebuild-paused-20261028.txt、resumed-20261028.png/txt。业务回答指出退款知识库缺资料，不能称该账号的完整经营风险报告业务质量通过。后一次替换任务验收遇到任务自然完成竞态，未算取消成功。
- 管理员正式接口样本：普通解释91ba5f6ffd2d约2.073秒；SQL b8415e9f371f约4.379秒；RAG 2226f077ec71约10.389秒且FAQ出处正确；最新联网952440df6d00约11.2秒，一句话+redis/redis链接且无同名移植仓库；最新记忆d71cb21b7ec3/6fe801196a73约2.438/3.923秒，正确记下和回忆蓝杉42。首反馈约0.03–0.12秒（另一个冷样本0.264秒），证据agent-latency-20261028.json、agent-latency-final-before-outbox-20261028.json、agent-latency-retest-20261028.json。
- 未隐去失败：首次RAG c8878634de2b在48秒父工具超时；子Trace98531b95b702检索重排约1.6秒，生成等待122秒后失败，具体原因尚未定位。第二次正确但13.52秒，随后10.389秒。不是统计性P95或生产压测通过。管理员多步骤e9488f9a…已入队并最终SUCCESS，轮询期间一次非JSON网关响应使验收脚本中断；后续只读正式API确认终态，未将脚本中断当暂停恢复成功证据。
- 当前功能定向验证通过，整体性能/业务质量仍UNVERIFIED；后续优先定位生成阶段偶发长等待、核对复杂报告来源完整性和统计性时延，再处理此前Trace展示、历史needs_review预占等遗留项。最后代码服务重建和健康检查已完成，文档补充不代表云端Review或生产发布。

## 已完成

- 任务分支：`codex/enterprise-assistant-roundtable-sse`；基线 HEAD：`d886f7c`。合并前发现本地 `main` 已前进到 `c3e1f97`，已确认其提交不改本任务文件，并将任务分支快进至该 HEAD 后再构建。
- `RequestBudget` 继续累计调用数，但不再因 `max_calls` 拒绝模型调用；token、金额、重试和备用模型限制保持现有行为。
- 仅 `QuotaExceeded.subject_id` 为 `user:*` 时从 GraphRunner 发出 `paused` 终态。暂停帧保留 SSE 已发出的部分文本；租户额度、配置错误等仍走错误协议。
- 前端将 `paused` 视为终态，保留过程快照与部分回答，并提供补足余额后重新发送。会话历史、模型选择、预算展示、停止/重试和功能导航继续使用企业助手现有 API/状态能力。
- `/agent` 已改为独立的 `AgentWorkspace` 展示实现；不接圆桌 API，不导入原 `/agent` 展示组件。圆桌原型只借用过程时间线、回复气泡、处理中指示和暂停提示的表现方式。
- 同步 SSE 生命周期、前端 SSE 与模型预算文档。

## 验证

- `frontend`: `npm ci --offline` 成功；`npm run build` 成功（Next.js 15.5.28，类型检查与 16 个静态页面构建通过）。构建自动改写的 `frontend/next-env.d.ts` 已恢复。
- 以本工作树的生产构建在 `localhost:3101/agent` 实际打开页面；新建页可渲染，输入文字后发送按钮启用，清空后禁用。未提交真实模型请求，避免产生业务调用或费用。浏览器控制台无错误；历史/额度 API 由于隔离运行缺少 `X-API-Key` 未能验证。
- 对 5 个改动的 Python 文件执行 `D:\Python\python.exe -m py_compile`，退出码 0。
- 从快进后的任务工作树执行 `devctl.bat rebuild /y` 成功；11 个后端镜像构建成功，数据库迁移器退出码 0。`agent-app-1`、worker、RAG 和 business-mock 为 healthy；`/health` 与 business-mock `/health` 返回 200。
- 在新 app 容器内做无 Provider 调用的运行 smoke：`max_calls=1` 时 reserve 9 次均通过且无 exceeded；`user:*` 判为暂停、`tenant:*` 不判为暂停；`paused` 帧通过契约校验。
- 未执行测试套件，也未调用真实模型，因此真实用户欠额到浏览器暂停 UI 的完整 SSE 链路仍未端到端演练。
- `git diff --check` 通过。

## 后端运行验证状态

- 用户授权后，已从本任务工作树重建共享后端；服务健康检查通过。后端新代码已部署到本机服务。
- Provider 级余额耗尽的完整 SSE 真实请求未触发，以免实际调用模型或改变额度；验证范围仅到预算拒绝、作用域分类、暂停帧契约和服务健康。

## Checkpoint 语义

- 企业助手聊天记录有服务端会话持久化；主图执行 checkpoint 当前默认关闭且没有主图恢复入口。`/chat/agent/stream/resume` 仅重放进程内 SSE 事件，不会从中断节点续跑。
- 因此余额补足后重新发送原问题；当前暂停能力不宣称恢复之前的 graph 执行。

## 2026-10-26：4C8G 容器配置与全量重建准备

- Worktree：`C:\Users\wh\.codex\worktrees\assistant-roundtable-sse\agent`；分支 `codex/enterprise-assistant-roundtable-sse`；HEAD `41f69a7bf39f3fa024a2ebcef565e8b0fd33c6e2`。本轮配置和文档改动尚未提交。
- 数据库只读确认 embedding=`bge-m3`、rerank=`bge-reranker-v2-m3`，供应商均为 `custom-gpu`、billing=`local`、地址 `http://host.docker.internal:8088`。宿主机 Python PID 3688 健康接口返回 embedder/reranker 已加载，当前进程工作集约 1.2GiB；GPU 为 GTX 1660 Ti 6GiB，总显存使用约 2.3GiB。这些不是重建时峰值。
- 普通索引 worker 显式注入 `EMBED_BATCH_CONCURRENCY=1`；全量 `run_rebuild` 源码实际是最多 8 条/批、串行生成，不受此参数控制。Agent worker 并发 2、索引 worker 并发 1、维护/报表各 1。
- dev-svc.bat 在未显式指定 COMPOSE_FILE 时自动加载根 Compose 与本机覆盖层。保留脚本 ASCII 编码。维护 worker 从覆盖层原 224m 调整为已运行的 512m（常驻实测约 280MiB）；索引 worker 为 1024m。
- 从本 worktree 执行 `devctl.bat rebuild /y` 成功。实际容器标签确认加载两个 Compose 文件；app=1000MiB/1.5CPU、Agent=768MiB/1CPU、索引=1024MiB/1CPU、维护=512MiB/0.3CPU、报表=768MiB/0.5CPU、RAG=512MiB/0.5CPU。检查的容器全部 healthy、OOMKilled=false、RestartCount=0；8000/health、8090/readyz、8091/health、9080/health 均 200。`git diff --check` 通过。
- 当前非候选向量 7114 条、19 个集合（包括保留的 eval 集合）。没有 biz_type=embedding_rebuild 的任务。未删除任何集合，未提交全量重建，也未改写向量。
- **BLOCKED：管理端按钮及重建负载验收。** 3200 的旧 Next dev 进程 PID 30344 页面请求超时；自动审批分别拒绝停止旧前端并启动当前 worktree 前端、以及在 3201 启动备用前端，理由均仅为 blocked by policy。没有绕过审批；浏览器读取也未能取得页面。需恢复进程操作能力或由用户恢复管理端后，继续按钮预检/确认、监测完整重建、核实模型指纹与退款问答。未完成 T2 真实重建/问答验收，不把当前配置认定为已通过完整负载调优。

## 2026-10-26：生产全服务资源档与真实按钮重建（续）

- 用户明确模型服务外置。统一使用 local 命名的共享资源层；生产层仅替换后端 env_file 为 .env.production。常驻服务上限合计 7168MiB（含三个前端、单 dispatcher、备份），为 8GiB 系统预留 1024MiB。开发 mock 额外 128MiB，不在生产清单内；重型可选 profiles 不启动。
- 所有 20 个本机运行容器已落实 cpuset=0-3、对应内存/CPU配额；DB 池1–2、全局请求闸门12、PG120连接/256MB shared_buffers/4MB work_mem、Redis按缓存/队列/认证分配并预留AOF空间。Redis加载AOF实测36秒，已增加60秒启动宽限。
- devctl rebuild 完成后端与共享数据库/缓存重建；其余基础设施用本 worktree Compose重建。三个前端镜像分别构建成功（管理端47页、用户端16页、客服10页），改用Node22与standalone容器运行；各256MiB。旧宿主前端已通过用户重新授权的devctl停止，管理端API Key缺失问题通过BFF运行时注入恢复。Docker前端start/stop入口增加容器识别，避免误杀Docker端口转发进程。
- 管理端已恢复既有超级管理员会话并显示13个角色。当前模型为 bge-m3/custom-gpu，预检确认1024维、19集合7114条向量。通过页面“检查并重建全部向量”及“确认并开始重建”真实提交任务，未直接调用API或改数据库状态。
- 任务ID：`a1f8912b-d120-48e0-a1dd-13523f86b1d5`；Celery ID `f98aad99-df79-4eeb-b5a2-b69d5f07846b`；2026-10-26 12:23:30 UTC创建。状态IN_PROGRESS：RUNNING，已见1536/7114生成，无错误。持续采集D:/tmp下本轮agent-4c8g-probe JSONL资源数据；目前采样总内存峰值约3.0GiB。候选未全量发布前不认定索引成功。
- 备份首轮agent_memory约71MB、agent_business约156KB，日志均OK，pg_restore -l目录可读；尚未做隔离恢复演练。PgBouncer SELECT1、三个前端/login及后端健康检查通过；截至提交任务无OOM或重启。仍须跟踪任务终态、核实所有集合身份与向量数量并验证企业助手问答。
- 首次任务在2800条后FAILED/provider_timeout，正式向量未发布，2800条候选保留。错误栈确认OpenAI embedding HTTP读超时；失败批包含一条16002字符文档，现有客户端沿用在线8秒超时并重试一次。修复为仅run_rebuild固定客户端采用独立60秒超时，在线默认保持不变，非法无限/非正超时拒绝；队列可见性3900秒覆盖3660秒全量任务硬时限。定向超时用例6 passed，Python语法检查通过；需重建后从管理端任务中心重试原任务验证长文档和候选复用。
- 完整定向验证：embedding_singleton、embedding_switch、devctl migration runner共20 passed。旧provider测试的SimpleNamespace缺少身份字段，替换为正式SpecializedBinding并增加非空指纹断言，未删断言或跳过测试。环境缺pytest-timeout插件产生2条配置警告，测试本身均执行通过。
- 60秒离线超时与3900秒队列可见性已从当前worktree重建并在容器环境确认；冷启动超过devctl短等待窗口，随后实际8000/health、8090/readyz、9080/health均200，索引worker healthy，候选仍2800条。企业助手用户已在页面重新登录，管理员会话随后过期，已请求用户在3200重新登录。
- **最新状态BLOCKED（覆盖上文运行态描述）：** 2026-10-26约20:56本地时间，Docker Desktop被重启；恢复后另一操作从`D:\Program Files\workplace\agent`主目录重建共享栈，并启动新的主目录Next管理端进程。实际app/index-worker工作目录标签均变为主目录，内存又为4GiB/3GiB，cpuset为空，本任务新修复不再是当前后端运行版本。主目录还有未提交DatasetsTab与tsconfig等改动，未覆盖或提交它们。已向用户询问并行操作安排，未再次覆盖新栈、未直接改任务状态。
- 原任务`a1f8912b-d120-48e0-a1dd-13523f86b1d5`仍FAILED/provider_timeout，持久候选2800条确认保留，正式向量尚未发布。首轮资源采样116次，总内存采样峰值3153.173MiB；这是失败尝试的测量，不能报告为全量成功验收。采样进程已正常结束。
- 下一步：确认其他共享栈重建已停止并恢复管理员登录；重新从本worktree部署资源层及60秒离线客户端，核对容器标签/配额后，通过管理端任务中心重试原任务（复用2800候选），跟踪7114条全部发布，再核对19集合模型/供应商/指纹并完成已登录企业助手退款问答。本轮配置、Python修复、测试与文档均未提交；HEAD仍41f69a7。

## 凭据登录与继续执行（2026-10-26，覆盖前述BLOCKED状态）

- 用户提供临时管理员凭据文件并授权自行登录。凭据只在浏览器内存用于登录，不打印、不复制到代码或日志；原localhost开发页CDP无响应，改用同一Edge浏览器的127.0.0.1:3200生产前端入口，确认模型页和任务中心可访问。凭据文本、分行缓存和字段变量均已清除。
- 主目录栈稳定运行24分钟且无活动索引任务后，按既有授权从本worktree重新部署资源层/修复；保留主目录未提交文件及其宿主管理端进程。另将已运行的可选12306适配器限制为128MiB/0.2CPU/cpuset0-3，生产默认不启用该profile，不计入7168MiB基线。
- 通过任务中心筛选原任务并确认重试。期间新增评测数据，最新正式向量范围变为7613条；2800候选复用，模型/供应商指纹仍匹配。60秒请求策略仍在同一混合长文本批失败，GPU实测100%/5888MiB(总6144MiB)，确认应用容器无OOM；SDK自动重试会叠加客户端已超时而供应商仍计算的请求。
- 离线客户端进一步改为模型HTTP批大小1、SDK max_retries=0；外层8条候选检查点不变。在线客户端默认超时/批大小/重试不变。24个定向用例全部通过，语法检查通过。从本worktree再次devctl rebuild后，8000/health、8090/readyz均200，索引worker healthy且环境为batch1/timeout60/visibility3900。
- 已校验并重启本机8088的原模型进程以清除残留推理，使用原脚本路径和127.0.0.1绑定，未修改其他worktree源码；新PID26464，embedding=cuda、reranker=cpu，模型均重新加载、healthz正常。通过管理端再次确认原任务重试，实际已越过原失败点，进度2896、2952/7613；模型HTTP持续200，显存约2958MiB，仍在运行。当前状态IN_PROGRESS，尚未认定全量发布成功。
- 后续进度已到4000/7613，错误消息为空，未再出现超时；原任务ID始终不变。当前工作区部署已恢复，索引worker=768MiB/1CPU/cpuset0-3，API/PG/RAG等均healthy、无OOM或重启。新一轮资源采样118次时采样总内存峰值3082.663MiB。管理端在127.0.0.1:3200/tasks按完整任务ID筛选，便于继续观察和最终截图。

## 2026-10-27：全量发布成功与重建交互补强

- 覆盖此前IN_PROGRESS：原任务 `a1f8912b-d120-48e0-a1dd-13523f86b1d5` 已 `SUCCESS`，progress=`索引完成（done）`，output.vectors=7613，Trace=`b5c41cca156a`。21个正式集合全部ready，模型bge-m3、供应商custom-gpu、64位配置指纹一致。正式表7613行全部记录本任务ID及同一模型指纹，候选向量0行；不是只依据页面或HTTP成功推断。
- 重建以候选区完成全量校验后在同一事务覆盖正式行embedding及模型身份，再清理候选；原文、ID、集合归属保留。成功后不保留独立旧向量版本；失败保留正式向量，不能把保留等同于可用或成功后可一键回滚。已同步现行rag.md。
- 成功轮资源采样288次，总内存采样峰值3180.806MiB（包括开发mock/可选适配器，不含宿主外置模型）；采样已结束。API/索引worker/RAG服务均healthy、OOM=false、restart=0、cpuset0-3。未做全业务并发压测或备份隔离恢复演练；不能据此承诺任意负载最优。
- 用户要求管理端不能干等：模型页补充真实条数/百分比及原生进度条、后台执行/可离页返回说明、100%仍待发布说明、失败保留与任务中心重试入口。沿用正式状态API及每4秒查询，不新增执行链路。29个RoleBindingsTab定向测试通过（包含返回恢复、100%未发布、失败状态），管理端47页生产构建通过，已从本worktree重建frontend-admin容器并healthy。
- **企业助手问答验收未通过，不能宣称整体恢复。** 原问题“退款流程是什么？”Trace=`60a02d77c4e9`要求在rag.search/web.search间澄清；点击rag.search后未成功执行。明确知识库请求Trace=`5255dd03e8ab`进入general_chat，元数据domain=general/source=product_scope，候选为空、block_reason=no_authorized_candidates；本轮未调用检索，因此不能归因于重建向量失败。下一步定向核对主助手产品域分流和授权候选过滤；不绕过权限或擅自扩大用户权限。本轮未修改这段路由代码。
- 本轮改动保持未提交，HEAD仍41f69a7；无新PR/Review，远端生产服务器未实际部署。配置/索引发布PASS；企业助手问答FAIL，整体仍未完成该项验收。

## 2026-10-27：退款问答路由及父子超时对齐（续）

- 用户授权继续修复。已复现两处失败：引擎`_assemble`把能力候选缩成仅name/score，`CapabilityScore`也不声明风险/白名单/权限字段，经过JSON和V2投影后selector二次门禁必然误拒绝；显式“企业知识库中检索退款处理流程”缺少knowledge域的“知识库/检索”词声明，仅命中一个“流程”弱信号，语义分类可能落入客服域再被产品隔离映射为general。
- 修复：在正式候选模型及引擎组装保留risk/fast_path_enabled/permission_ready（缺失默认UNKNOWN/false/false）；路由缓存v4→v5避免旧缺字段缓存；manifest知识域增加“知识库/检索”，未修改产品隔离、用户权限、Tool Governance或执行门禁。JSON→V2→selector真实组装回归在修复前失败，修复后可用候选通过、缺字段/权限false/白名单false/HIGH风险仍阻断。显式知识库问句回归修复前失败、后通过；退款申请+订单仍识别客服域。
- 定向回归：selector/域分类/投影/引擎/适配器/产品隔离128 passed；补充missing字段及Runtime契约后selector+runtime 49 passed（与前一组有重叠）；缓存版本变更后引擎15 passed。语法和diff检查通过。现行ai-runtime.md同步候选安全字段传递契约。
- 首次真实页面修复验证Trace=`d7d2e90f3b88`：selected=rag.search、source=passthrough、reason=validated_hierarchical_fast_path，门禁不再误拒绝；但父请求仍19.2秒timeout。其RAG子Trace=`c6101be87fee`约25.95秒实际返回带出处的答案，确认父任务25秒图预算减预留仅剩约19秒，短于既有45秒RAG策略，导致成功答案被丢弃；子服务另有CPU重排供应商4秒超时并降级，未伪装为重排成功。
- 4C8G共享资源层与生产模板对齐BUSINESS_DEADLINE_S=60、WORKFLOW_DEADLINE_S=55、REPORTER_RESERVED_S=5；现有rag.search45秒/零重试/并发12及证据校验不变。读取真实Compose资源档的定向预算用例和既有超时回归7 passed，覆盖26秒响应能装入预算、45秒上限和预算过期拒绝。README同步依据和边界。从本worktree devctl rebuild成功，实际API/RAG均healthy，app环境60/55/5，候选字段及缓存v5已在容器确认。正在重新通过页面验证最终回答，尚未提前认定整体通过。
- **本轮验收PASS（覆盖上句待验收）：** 页面重新创建会话，原问题“退款流程是什么？”Trace=`4341488e6554`，status=success，总耗时36.829秒、直接执行26.5秒，返回FAQ退款申请步骤及来源；tool_selection=validated_hierarchical_fast_path/rag.search，无selection_block_reason。截图`D:/tmp/agent-refund-routing-fixed.png`。
- 再新建会话验证此前误入general的显式知识库请求，Trace=`a3c67a61efe9`，status=success，总耗时13.518秒，domain=knowledge，FC在rag.search/web.search两候选中选中rag.search，返回退款规则及制度/FAQ出处。截图`D:/tmp/agent-explicit-knowledge-routing-fixed.png`。两轮均完成SSE终态并恢复发送输入框，不只依据底层RAG返回成功。
- 本次T0语法/Diff检查通过，T1/T2定向路由/候选边界/产品隔离/超时回归及真实页面请求通过；未执行T3全量回归或并发压测。局部Diff自查保留保守默认、分数/margin/风险/白名单/注册/权限二次门禁及Tool Governance；没有修改用户授权，也没有放开专属产品域。已知外置本机CPU重排有供应商超时降级，仍是性能限制，本次未修改其他worktree模型服务源码或伪造重排成功。当前API/RAG健康、无OOM或重启，向量重建任务仍SUCCESS/7613。未新建PR、未提交或合并；HEAD仍41f69a7。

## 2026-10-27：问答延迟优化实施与运行时恢复阻塞

- 用户授权实施建议。本worktree参考模型服务增加GPU float16重排、小批次默认2、全部候选保序回填后排序、非有限分数拒绝；embedding精度/模型/归一化和512 token重排截断不变。共享推理锁限制双模型并发激活显存。默认auto有CUDA使用GPU，无CUDA使用CPU；可显式RERANKER_DEVICE=cpu，加载失败不会伪装就绪。未修改其他worktree源码。已确认原8088进程后替换为本worktree由dev-local-models管理的新进程PID3920，实际health显示cuda:0/torch.float16、双模型预热成功。
- 六条同输入长文本基准：原CPU14.237秒；GPU3.369/2.852/2.867秒，全部6条候选排序一致，最大分数差0.000079。结果保存D:/tmp/agent-rerank-cpu-baseline.json及agent-rerank-gpu-optimized.json；观测显存3423MiB/6144MiB。这只是小样本数值/延迟验证，不代表全部问答或并发质量通过。
- DashScopeReranker传输超时/连接失败不再自动重发；RagRequestState按供应商/端点/模型记录本轮失败，后续纠错不再重复提交同一服务，下一请求重新尝试。保留既有降级、证据与Claim校验。增加超时/连接错误及请求隔离回归。
- SSE复用原有log/replay缓冲：入口显示准备/检查请求，远端RAG结果实际到达后显示整理回答；等待文案改为处理请求，不伪称已经开始生成。Runner metadata.entry_timing记录before_trace_ms和worker_launch_ms，用于入口耗时分解。未在证据核验前输出模型答案。现行rag.md和commands.md同步。
- 定向测试首次暴露提前Runner yield会改变关闭生命周期，已将入口事件移到API producer并保留原Runner收尾。剩余旧断连测试要求首帧只能status/delta，但当前假图合法首帧为reporter/summary日志，已按具体来源和char_count校验后继续断连，未删除取消、无记忆写入、恰好收尾一次断言。重排/降级/Runner终态/HTTP流组71 passed+1旧首帧断言失败；修正该契约后Runner收尾+HTTP流25 passed（覆盖原失败），其他47项此前已通过。语法/diff检查通过。
- 后端从本worktree devctl rebuild已完成，用户前端16页生产构建完成并重建容器；API/RAG/用户前端及主栈其他代码服务健康。索引任务仍SUCCESS/7613，没有重新写向量。构建期间Docker响应变慢，结束后大部分服务恢复，但APISIX运行状态异常：Docker标记running/PID63664，docker-desktop内该PID已不存在，9080健康请求超时；restart及单容器重建恢复操作均报tried to kill container, but did not receive an exit event。未删除卷或改数据，未擅自重启Docker Desktop。
- 当前BLOCKED：真实页面最终性能验收。页面已回登录，保存的原用户凭据可用于正常登录，但网关故障阻断。已向用户发出重启Docker Desktop确认，因会影响其他会话容器（另见新增agent-mcp-preview-app），需确认后恢复。下一步：用户允许后重启Desktop、devctl start/健康检查、原用户正常登录、重新跑两条退款问题，读取父子Trace确认耗时/重排成功/纠错次数与入口反馈，再给最终性能结论。当前只确认模型基准优化与工程回归，不能宣称整轮问答已加速到目标。未提交，HEAD仍41f69a7。

## 2026-10-27：Docker Desktop / WSL 完全退出后恢复（覆盖前述环境阻塞）

- 用户确认已 Quit，并要求完全退出 WSL。已停止本次 Docker GUI/backend/启动等待进程，执行 wsl --shutdown，确认当时无运行发行版。日志先后定位 Docker/run/dockerInference 与 docker-secrets-engine/engine.sock 的残留 AF_UNIX 端点无法移除；不能据此归因为内存不足。
- 校验目录范围和重解析属性后仅重命名备份运行时目录：LocalAppData/Docker/run.stale-20261027-061423、run.stale-20261027-061627、LocalAppData/docker-secrets-engine.stale-20261027-061627。后者仅含0字节 engine.sock。没有恢复出厂、注销 WSL、删除数据库卷或清理其他工作区源码。
- Docker Desktop 重新启动后 docker info 返回 ServerVersion=29.2.1。从本任务 worktree 执行 devctl.bat rebuild /y 返回0，依赖层命中缓存；容器标签确认 agent-app-1 的 working_dir 为本 worktree。API /health、APISIX、用户页面 /agent 返回200；PostgreSQL accepting connections 且只读查询 public 表数60。此查询不等于全部数据完整性校验。
- API、RAG、MCP、Agent/索引/报告 worker、三端前端、Redis、PostgreSQL 健康；维护worker及business-mock最初仍启动中，business-mock健康接口已200。外置模型 healthz 为ok，embedder/reranker已加载，重排cuda:0/float16。
- 恢复后重新执行 test_chat_stream_hardening.py + test_stream_latency_tracker.py：25 passed；两个既有pytest-timeout配置警告。此前数据库不可达导致的用例初始化阻塞已消除。未再次重建向量；整轮问答质量和全部耗时类别验收仍待完成，不能由容器恢复推定通过。保持未提交。

## 2026-10-27：恢复后分项验收、生成观测纠错与测试资料归档

- 本轮范围：真实用户页面验证首反馈/完整回答、RAG/普通解释/联网；管理员账号单独验证 SQL 服务端路径，保持原业务账号权限不变。没有创建 PR 或提交，HEAD=41f69a7。
- 普通解释「用一句话解释向量检索，不需要联网」Trace=505d5659ff99，完整1.803秒；页面在点击后379ms观测到真实准备日志。原退款首轮恢复后Trace=d376e09ad927，总20.335秒（HTTP20.801秒），before_trace4044ms/worker_launch4614ms；RAG子f416536da492成功6.543秒，但返回种子文件里的矛盾金额，不能算业务质量通过。
- 显式知识库问题Trace=6ab92c6722e9，总14.354秒；子c5e921203bc6检索/重排通过而生成拒答。发现原生成span读取父ContextVar，误把前一阶段78个输入token当作生成用量。新增LCEL真实链回归在修复前失败；模型步在请求态携带本次代理元数据和generation_input_chars，清空/恢复本地ContextVar，未改统一账本结算。覆盖stream/invoke、缺usage不能沿用旧值、新请求空态。真实复测父47361ad36a8f/子5798602952c5证明实际生成输入2679/2022字符、1718/1270 token；模型仍自报无证据，不能再归因为提示词未带资料。此前78token推测已被纠正。
- 明确联网问题修复前Trace=7b78a7cd73fe，粗分类unknown直接澄清。manifest补肯定联网与搜索动作信号，否定联网不构成强信号；web.search描述允许用户明确指定联网，路由缓存v6；没有绕过主体/产品权限。新增正例修复前失败、修复后通过。真实149e3fdff02e选中web.search，但833token结果被默认768上限压为无法展示的预览。4C8G共享档TOOL_INLINE_MAX_TOKENS=2048，保持8192总上下文硬门禁和原并发；最终ead8106ef911总4.398秒，页面展示5条完整链接结果。该结果仍是原始搜索列表，未按用户要求限定官方文档或两句整理，联网质量验收未通过。
- 原业务账号SQL Trace=749f2793213f正确permission_denied，未扩大权限。用户提供管理员凭据文件后，管理端已有有效登录；独立企业助手UI拒绝非11位数字管理员账号，已清空密码并关闭临时页，不修改登录校验。使用正式管理端BFF登录API取得真实管理员JWT，经固定/chat/agent/stream路径单独验证SQL，未伪造身份。首次04f83d1858da完整HTTP16.963秒、首反馈0.217秒（入口before_trace11.819秒）；稳定后3c8c3e63fc1e完整HTTP2.124秒、首反馈0.128秒，实际返回统计合计与月份1行。管理员API结果不能代替原业务账号页面权限验收。临时会话均正常logout，不保存/输出密码或JWT。
- 用户明确确认p4_kb_seed_doc.md为测试资料，并授权归档退出业务检索。通过正式登录与/rag/knowledge/71a1ad2d5f/lifecycle接口将policy_general文档active→deprecated，未直接UPDATE共享库，未删除原文/向量/片段。API返回ok=true，归档列表确认deprecated，原82条chunk仍存在；ai.knowledge_lifecycle_events已留actor/reason审计，后续可走既有deprecated→active恢复。
- 归档时实际发现生命周期审计查询500：can_read_row正式返回bool，而路由及旧mock误当tuple。修正测试桩匹配正式契约后复现两处失败；修复路由直接检查bool，拒绝读仍403且不返回事件、缺文档404。实际重建后审计GET返回200、存在deprecated事件。通用操作日志枚举只接受upload/reindex/delete，另报lifecycle:deprecated不支持；专用生命周期审计成功，该通用列表展示缺口未扩大修改。
- 归档后原问题父d3200ac7fcb9/子8495ab900827成功返回FAQ，完整26.635秒（before_trace9.397秒，worker_launch10.456秒，RAG4.923秒），冷路径未达标。显式问题父a612ec665715/子227ff5ef3060成功返回FAQ，总16.599秒，生成阶段9.083秒，是另一个超标样本。稳定后原问题父5659f0c8b0fe/子95810e8c8674成功5.623秒（RAG4.009秒），final_context无测试文件。引用对应e07b25dc7b；最后一次扩展来源文件名变成内部hash名，出处显示可读性仍待处理，不能据单个暖机样本宣称稳定P95达标。
- 全部代码/配置改动已从本worktree devctl.bat rebuild /y重建并返回0；最后主API/RAG/MCP/各worker健康，TOOL_INLINE_MAX_TOKENS实际2048；数据库/三端前端正常。模型未变、没有再次全量重建向量。资源一次采样app423.1MiB、RAG359.9MiB、PG414.8MiB，非峰值。
- 本轮定向测试：LCEL/重排/域分类/路由引擎/产品隔离/selector119 passed；工具输出Guard/超时策略18 passed；生命周期API/状态机26 passed。各组有2个既有pytest-timeout插件配置警告。T0 py_compile/diff通过；Docker恢复后SSE/反馈25项此前通过。本轮未跑全仓T3、并发压测或请求云端Review。
- 证据截图：D:/tmp/agent-refund-after-seed-archive.png、agent-explicit-knowledge-after-seed-archive.png、agent-refund-warm-verified.png、agent-web-results-budget-fixed.png。D:/tmp/agent-p4-seed-before-archive.json保留归档前文档元数据；SQL性能JSON仅保留业务结果与耗时，不含令牌。
- 阶段状态：工程修复与测试资料归档PASS；全目标UNVERIFIED。下一步优先定位入口冷路径、联网来源约束与整理（现有direct只透传搜索列表），再落实超过20–30秒报告走持久任务及可信产品身份隔离。不能以SSE续播或超时重投冒充后台执行续跑。原业务SQL仍需现有有权业务账号；未申请临时提权。当前不建议把全部性能目标视作通过或直接合并。

## 2026-10-27：用户要求“多测几个问题”追加10题实测

- 测试范围：8题真实业务账号页面（改写、解释、发货/支付/发票知识、无资料、联网项目来源、旅游跨产品引导）+2题正式管理员API SQL，未修改业务代码、配置、账号权限或共享业务数据，未重建容器。管理员会话正常logout，不输出/保存凭据和JWT。报告D:/tmp/agent-more-question-results-20261027.md，逐题页面JSON和SQL结果均在D:/tmp/agent-more-*。
- 6 PASS：解释2.059秒（b5b19b8ac0e2）；发货FAQ7.120秒（a0862e72ee46）；发票FAQ3.867秒（42bcea6796b0）；无资料正确拒答3.598秒（d33074473728）；管理员月订单/销售3.999秒（69870e30ba31，API首反馈0.031秒）；旅游正确引导0.449秒（31b7b75d85e0）。发货和发票均与权威chunk_store FAQ原文核对；发货虽最终正确却多走Planner。单轮性能不是P95或生产验收。
- 3 FAIL：礼貌改写2.990秒（8d483cad3b57）错误unknown→clarify；支付问答1.943秒（e6713172f163）被prefilter直通general_chat，未调RAG却声称无权限，而FAQ实际有支付方式；联网pgvector官方GitHub8.169秒（81d5a692229b）query正确且执行web.search，但返回漫画人物百科等完全无关内容。不能将这些Trace的success或SSE done算作答案通过；联网上游/解析根因尚未定位。
- 1 PARTIAL：管理员商品销量Top3 API3.075秒（566fbd7dd20e），实际返回3行且销量递减；首次SQL校验column_undefined，第二次校验/执行成功，父Trace却因历史sql.guard error仍error。首反馈0.037秒。此题答案与监控状态不一致，不能标干净PASS。
- 保存支付失败证据时额外复现：旅游会话切回支付历史后，支付标题/正文已加载，上一会话旅游卡仍在，稳定后再次观测仍存在。截图agent-more-payment-routing-failure.png和agent-more-stale-travel-card.png。同用户会话本地展示残留，未推定跨用户泄露。
- 本轮局部T2真实页面/API完成；无新增功能代码，未重复T1/全量T3/云端Review，保持未提交。总体FAIL。下一步优先支付prefilter与联网相关性/来源校验，再修改写识别、已恢复SQL顶层Trace聚合和会话卡片状态清理。

## 2026-10-27：已有基线合并、新 main 同步与正确性修复

- 已按用户授权提交旧46文件改动为1eeca23fcb5901ab0305036be5f1420ae45bf3cd，并在干净本地main正常合并为7c394503887d1961f7954af51da52b282fa40bd6。随后建立codex/enterprise-assistant-reliability-fixes修复分支。用户提醒main更新后快进同步至561eae8332f1903bfd66c4edb969d9dce40cf836，保留最新动态工具effective_manifest及产品/租户隔离。本轮新修复保持未提交。
- 已修复：Guard裸“支持”误判能力咨询；明确私有知识请求优先于greeting直答；给定原文的改写/翻译进入普通对话。定向121项通过。前端切换/新建/删除当前会话清理流态及引导卡；晚到历史和SSE异常不得覆盖新会话，定向40项通过。SQL增加操作父Span及可信最终执行结果，历史失败保留recovered_by，兄弟失败不被覆盖，SQL/Trace定向66项通过。
- 联网根因证据：同容器Bing RSS在query正确时返回搬屋/转账等无关页面；DDG/Bing HTML不稳定，GitHub官方公开API可访问。现有web.search增加站点/英文主题硬校验、复用既有rerank的中英文相关性判断；明确GitHub项目查询使用官方公开目录，链接只取响应。用户明确要求整理时复用Reporter汇总并保留来源。不得把搜索源无关结果概括成业务答案。
- 第一次实际联网验收仍失败：内部HTTP允许8秒，外层web.search策略只有6秒，返回超时。随后对齐外层10秒零重试、普通两源各3秒且各有1.5秒相关性校验；GitHub目录最多8秒。该修复已再次经本worktree devctl rebuild /y重建，API/RAG/MCP/workers健康。联网最终复验尚在进行，不标PASS。
- 后端Web/Reporter 86项、后续Web/策略/Reporter58项、入口Trace9项分别通过；有既有pytest-timeout插件配置警告。前端Docker生产构建/类型检查通过并更新frontend容器。入口Trace新增clarify_cache_ms/prompt_refresh_ms/input_guard_ms，继续定位冷路径，不凭暖机样本宣称冷启动修好。
- 实际页面：礼貌改写正确答复（生成约2.4秒）；支付知识问答实际rag.search成功，返回支付宝/微信/银联/Visa/Mastercard及FAQ出处，执行链约8秒。旅游引导生成后切回支付会话，卡片消失且正文/引用正确，截图D:/tmp/agent-fixes-payment-verified.png、agent-fixes-session-isolation.png。
- 管理员SQL正式API复验：fc1d0a2a6e03约4.691秒、54b02b56b8bc约3.535秒，首反馈0.040/0.075秒，结果分别1行统计与3行商品，Trace列表均success；本次未自然触发纠错，纠错恢复仍以定向运行测试为证，不称真实错误重试已验收。结果D:/tmp/agent-fixes-sql-results.json。
- 剩余：联网最终页面与相关性真实模型验证、原10题整体验收、冷启动归因/优化、持久后台长任务实现及身份/恢复边界。整体UNVERIFIED，未跑全仓T3或云端Review，不建议宣称全部修复完成。

### 本批最终验收补充

- SQL Trace下钻更正：54b02b56b8bc实际触发了sql.validate.attempt_1 rejected/sql.guard error，随后attempt_2执行success；sql.operation与父Trace均success，原guard保留error且有恢复关联。并非仅靠单测推定恢复正确。fc1d0a2a6e03一次执行成功。
- 冷路径6d684fdd5cc7新增分段确认input_guard_ms=9720.8，clarify_cache_ms=1.6、prompt_refresh_ms=0.3；容器/tmp/data-gym-cache词表文件创建时间与首次Guard对应。Dockerfile在独立构建缓存层预取并校验cl100k_base资源，运行目录/opt/tiktoken-cache。断网临时容器实际加载0.525秒；重建后的首问8e0befc595b3输入检查412.9ms、before_trace415.3ms、Trace duration2147ms，回答正确。仅为首轮实测，不称P95稳定达标。
- 构建时曾因新增ENV位置导致运行系统包层失效，碰到Debian HTTP502。已把ENV移到原依赖层后方，恢复原apt/pip层缓存；随后重建成功，未升级或反复安装依赖。之后构建日志确认tokenizer层也命中缓存。
- 实际BGE相关性验证：中文“容器内存限制”候选含搬屋价格与Docker内存约束，现有bge-reranker-v2-m3保留Docker、拒绝搬屋，约1.396秒，未改模型/权限/向量空间。GitHub目录精确仓库名优先，避免官方项目查询混入生态项目。
- 联网概括中发现原公共代理对每个chunk strip，吞掉Markdown空格换行并污染裸链接。已仅对stream/astream保留边界空白，完整invoke仍去除首尾空白；LLM绑定/流式/RAG/MultiQuery/Reporter 54项通过。最终页面概括为两句话，来源链接精确为https://github.com/pgvector/pgvector，选择4.0秒+查询1.1秒+汇总0.9秒；证据agent-fixes-summary-verified.png/txt。纯链接请求仍无二次LLM汇总。
- 发票实际回归正确（个人/企业抬头、企业税号/开户银行，FAQ出处，RAG3秒）。发货问题此前仍多走Planner，定位“并给出处”被复合意图规则当成额外SQL动作；增加格式约束剔除与manifest知识来源提示，39项路由回归通过，真正SQL+RAG复合任务保持PLAN。该项新容器实际复验尚待完成。
- 后台报告接入草案已保存D:/tmp/agent-background-report-draft.patch，不在当前代码差异/部署中。现有TaskExecutor缺省internal产品，不能未经产品身份、撤权/恢复、模型预算暂停、队列幂等与离页结果验收就把Chat自动转入它。本批不将SSE断线重放冒充持久任务，后台任务仍未完成。
- 当前修复分支与main的基线HEAD仍561eae8，新修复未提交。T0语法/Diff检查通过；T1各定向组通过（非去重总数），T2页面/API为分项实测；无全量T3、生产压测、云端Review或新PR。整体仍UNVERIFIED，剩余后台任务与最终原10题汇总不能省略。

### 本批收尾证据

- 最后一次devctl rebuild /y返回0；API/RAG/MCP与各worker健康，frontend此前生产构建/更新且健康，容器工作目录确认是本任务worktree。新任务草案未混入本次部署。
- 发货修复实际PASS：3b376760f74a，Trace5.781秒，直接rag.search（无Planner），正确返回24小时内发货与FAQ出处；证据D:/tmp/agent-fixes-shipping-direct.txt。无资料复验d1e5b5b252b4约6.139秒，正文明确未找到，子Trace cd238d436c50 rejected，不编造火星制度。第一次该题碰到重建窗口的临时请求失败，未算业务验收，服务健康后已重测。
- 普通解释2c1fc96661a5答复正确两句话、无联网/工具、换行正常；Trace5.686秒，生成5.1秒，略高2–5秒目标，不能称性能全通过。发票bfeb4bedce5a 4.558秒；最终联网概括8807010b454e 7.406秒，真实主项目链接可点击且无章节/链接粘连。
- 本轮原10题的答案正确性与SQL恢复/会话隔离均有对应局部T2证据；没有做统计性压测或全部换问法穷举。持久后台报告仍未实施/验收，后续先补可信产品身份及错误/余额暂停终态，再复用TaskService/TaskManager/PostgresSaver接企业助手任务卡，不能直接借现有任务默认internal权限上下文上线。

## 2026-10-27：新增问答、管理端Trace/价格/额度、中止与恢复专项验收

- 用户要求再问几个问题，并实际核对管理端Trace、价格记录、用户扣额及中途中断/暂停能否点击继续。本轮无功能代码改动、未修改限额/价格/历史账单。容器仍本worktree部署；数据库采样采用readonly事务，任务控制只操作本轮正式API新建的两个自有测试任务。
- 新问题：给定成本80/售价100计算毛利误澄清（7f9329a0b232，3.237秒，FAIL）；企业发票资料正确引用FAQ（父26ccde460ed0/子9673f1835f9c，13.569秒，正确但超目标）；幂等一句话解释正确（4dfa63059516，3.521秒）；Redis官方仓库查询包含正确redis/redis但混入非官方同名仓库与多余描述（acde6de1e39f，9.002秒，PARTIAL）。20段模板正常完成24.967秒；首次停止测试碰到自然完成竞态，未作为中途中止证据，随后另起50段模板在同一UI操作批次内于已产出第一段时点击停止。
- 计费快照0.2/2/0.04 CNY每百万token、版本2026-10-cny-1与普通调用计算一致；4dfa63059516的171输入/42输出为0.000118，用户账本一致。本轮用户日/月used分别增加同一0.004280 CNY（RAG生成0.000214+短解释0.000118+20段模板0.003948），不是日/月相加双扣。无重复结算证据，但不能据此判全链计费正确。
- P1归因/漏预算：RAG生成明细67587实际结算到用户1012，却user_id/tenant_id空；RAG选择明细67583（0.000280）trace/request/session/user/tenant全部空。同时间日志明确mode=enforce下缺request_id，本次不预占不结算，随后的ToolSelector日志归属本次1012/rag.search。此类调用漏入个人额度链；本地rerank仍unpriced/unavailable而非local_inference零云费用。
- 管理端实际下钻：正常有213token与0.000118费用却显示LLM调用0/无LLM调用，general_chat被归工具且有leaked占位节点。RAG父显示费用0/LLM0，合并时间线有LLM生成，子Trace实为0.000214；自身费用与合并子Span口径不同。活跃态/HTTP/业务结果等仍BLOCKED或未采集。正常微额费用显示0.0001，不泛化为所有费用四舍五入成0。
- 中途停止FAIL：2effe87fd8bd已显示开头与第一段，停止后正文消失且无继续入口；11:03:42 abort，11:06:11才落estimated用量0.004594，最终Trace cancelled。预占50dd8dd7-0341-497a-90e3-82f85bf1e6f1为needs_review/stream_failed_possibly_billed，用户日/月各保留同一0.50元预占（不是已扣0.50），不可未知账单直接释放。真实余额耗尽未通过改用户限额制造；当前paused按钮明确为补足后重新发送，非节点续跑。
- SSE真实断线重放PASS（进程存活范围）：只收meta(seq1)后关闭连接，两次resume均200/done、同Trace29a136987e7e、完全相同事件；未重发问题，不代表重启或Checkpoint业务续跑。
- 任务中心队列内暂停恢复PASS：新建9b897bc5-c123-448b-ae43-f35f0b9e6a11，PENDING→PAUSED，经管理端重试/确认重试→SUCCESS并有正确正文。运行中暂停恢复FAIL：新建946bd8d5-2351-4d3b-94d2-c80f59a26f44，在RUNNING/router完成时申请暂停，general_chat完成后PAUSED，有2条Checkpoint且保存final_answer 1631字；管理端恢复后SUCCESS，却发布“无结果/未能获取任何有效数据”。原Checkpoint仍在。Trace1716a7b9d98c→c4beacfc897c，两个测试任务现均终态，无其他任务被重试或撤销。
- 完整报告D:/tmp/agent-billing-lifecycle-audit-20261027.md；原始账务agent-billing-before/after-normal/after-final/window-final.json、Trace详情agent-billing-trace-details.json、停止前后agent-stop-active-before/after.txt/png、SSE重放agent-sse-resume-real.json、任务agent-managed-task-pause/resumed.json和agent-node-pause-results/node-task-resumed.json。管理端截图agent-billing-admin-parent-cost-gap.png、agent-billing-admin-normal-trace.png、agent-node-resume-ui-detail.png。均无密码/JWT。
- 状态FAIL；优先修请求/预算/Trace归属传播，其次取消与预占对账、Checkpoint累计输出恢复，再统一Trace展示及修正算术/官方仓库路由。不将本轮验收宣称修复完成；无新PR/提交/合并/部署、全仓T3或云端Review。

## 2026-10-27：关机后恢复及首批账务/执行恢复修复

- 复工核对：worktree/branch及HEAD仍为本任务codex/enterprise-assistant-reliability-fixes、561eae8；此前26文件改动完整保留。Docker栈已自行恢复健康，但宿主8088模型服务未运行。通过本worktree dev-local-models.bat start恢复，healthz确认embedding及rerank已加载，reranker cuda:0/float16；没有重新建索引或更改模型绑定。
- 修复TaskGraphExecutor：恢复前仍重新解析当前授权，读取LangGraph权威检查点的累计final_answer/step_results，缺失或无效检查点拒绝恢复；之后照常刷新权限并只消费新节点增量。新增真实MemorySaver图用例在修复前2失败，修复后通过，覆盖END处零新事件、部分节点恢复、已完成节点不重跑及当前权限注入。连同既有异步任务授权回归13 passed。
- 正式任务接口实测：2e688c76-b4d4-43a4-b7fb-dac7171d822d在general_chat完成后PAUSED，检查点保存1549字，resume后SUCCESS，result.answer与检查点逐字相等，Trace319075d79669。验收脚本首次误取output而非公开API的result字段，修正取值后对同一份原始响应重算通过，没有重复创建任务来掩盖失败。首次环境/路由样本2c9d11d1…自然澄清终态；中间暂停样本a658afa4…在tool_selector边界无回答，已正常恢复到SUCCESS，两者不作为有正文恢复证据，未留下暂停任务。
- 工具选择漏预算根因：safe_call_with_timeout的原生线程不继承ContextVar，Linux非主线程也走该路径。现用copy_context为每次超时线程传入请求身份、预算和Trace。Windows/Linux并发隔离与异常传播回归、selector及预算旁路诊断组48 passed；未改变超时线程无法强杀底层调用的既有限制。
- RAG明细归属根因：独立/ask仅bind_request_budget但未绑定用量存储读取的tool user/tenant。现复用scoped_tool_identity及scoped_current_user_id，退出/异常均恢复旧值；内部令牌及授权字段契约保持不变。RAG内部令牌、明细作用域、恢复及权限组28 passed（与前组有重叠）；有既有pytest-timeout配置警告及Windows事件循环关闭时资源清理警告，不等同于断言失败。
- 前端停止前固化delta正文、思考及执行过程，晚到事件不覆盖消息。useSSE/store定向41 passed。实际页面在收到首段开头时同批点击停止，输入框恢复且部分正文保留；证据D:/tmp/agent-stop-fixed-active-before.txt、after.txt/png。此前两次自然完成竞态未算停止通过。仅证明当前页面正文保留，不宣称服务端保存中止正文、立即物理取消供应商或聊天已有断点继续。
- 最新代码均从本worktree重建：devctl rebuild /y返回0，frontend生产构建16页/类型检查通过并更新容器；依赖层缓存复用。当前没有新提交/PR/合并。ai-runtime.md同步后台任务累计结果恢复契约，不把SSE续播混同执行续跑。
- 新知识库页面实测正确返回企业税号/开户银行与FAQ来源；父Trace b7774e25e754、子86837bbca0b5。selector明细67717正确user1012/tenantdefault/request/trace，981输入+42输出=0.000280；RAG生成67721同用户/租户，621输入+42输出=0.000208，exact及既有价格快照。两笔预占均settled；日/月账本各增加同一0.000488，未观察到本样本重复扣费。原始只读证据D:/tmp/agent-billing-reboot-before/after/window.json。
- 本批已验证恢复输出、停止正文及新调用归因/扣费，整体仍UNVERIFIED：Trace父子汇总/LLM分类、rerank本地成本口径、历史中止needs_review预占与取消物理传播、聊天入口持久恢复/余额暂停续跑、算术和官方仓库筛选尚未修完；未改历史账本或直接释放未知预占，未执行全仓T3/云端Review。
