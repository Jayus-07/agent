# Domain Runtime STOP A — Full Domain Runtime Audit（只读审计报告）

> 日期：2026-09-24 ｜ 范围：A1-A14 全链路只读审计，架构零重构
> 方法：6 路并行代码审计（入口链路/路由体系/状态结构/检查点与记忆/身份权限/异步副作用可观测），关键结论逐条人工复核
> 说明：工作区存在其他会话未提交改动（memory 048/conflict/keying、context_budget 三文件等），本报告以审计时点工作区内容为准并逐处标注
> 审计行号基准：当日工作区（未提交文件已注明）

---

## 0. Verdict

```text
STOP_A_PASS      = true    （A1-A14 审计完成；发现的 1 项 P0 已在本 STOP 内最小修复并通过回归）
STOP_B_ALLOWED   = true
```

P0 修复记录（本轮唯一代码改动，详见 §12）：

| # | 缺陷 | 修复 | 回归 |
|---|---|---|---|
| P0-1 | `selection_decision` / `selection_funnel` 两个 HTTP 路由无任何身份门禁：网关 JWT 后任意已认证用户可读/写全部决策任务与导入池（无角色档位、无操作者留痕），后台 workflow 执行无身份绑定 | 两路由统一挂 `deps.resolve_operator_role`（JWT 用户取平台角色 viewer/editor/admin，服务凭据走 X-Internal-Token 映射 admin，与 prompts/model_prices 同档）；create 端点记录 operator.actor 留痕 | `tests/api/test_selection_routes_authz.py` 5 例全绿 + 既有 9 例契约测试适配后全绿 |

「立即停止」条件逐项核查（任务书 §7）：跨租户数据泄漏**未发现**（§7）；Domain A state 被 Domain B 错误消费**未发现**（§5）；恢复任务进入错误 Domain**未发现**（§6.4）；权限上下文在 SubGraph/worker 丢失**主链未发现**（选品 HTTP 侧门即 P0-1，已修；子图与 Celery 边界见 §8/§9）；旧 execution 产生 side effect**未发现**（fencing 已冻结）；用户确认对象被其他 Domain 误消费**未发现**（§5.3）；router failure 进高权限 Domain**未发现**（§3.7）；checkpoint 恢复错误业务动作**未发现**（§6.5）；canonical chat_messages 被改写**未发现**（§5.4）；SSE success 与 DB failure 不一致**主链未发现**（CS 审计轮 fire-and-forget 记 P1-7）。

---

## 1. 真实 Runtime 调用图（A1）

```text
POST /api/chat/stream (APISIX :9080, gateway-auth 验签注入身份头)
  → StripPrefix /api → FastAPI :8000
  → 中间件链 server.py:37-78（CORS→access_log→并发门→改密门→api_key 中间件[app 级 X-API-Key + Bearer 会话闸]）
  → chat_stream  app/api/routes/chat.py:192-450
      ├─ resolve_identity  app/api/identity.py:92-113   ← X-User-Id/X-Tenant-Id/X-User-Roles/X-User-Permissions
      ├─ meta SSE 帧（node_labels+request_id）chat.py:295-298
      └─ producer 线程 → MultiAgentSystem.stream_events → GraphRunner.iter_events
  → GraphRunner  orchestration/graph/runner.py:238-777
      ├─ Input Guard（runner.py:268）BLOCK/CLARIFY 短路
      ├─ CS 业务门禁（锁域时 runner.py:361-384）
      ├─ trace_collector.start（runner.py:387，trace_id=uuid12 在此创建）
      ├─ memory.start_session（runner.py:399）
      ├─ make_initial_state（events.py:429-474）← state 在此创建
      ├─ FollowUpResolver（runner.py:422，CS 锁域跳过）→ routing_context 组装（runner.py:460-470）
      ├─ RequestContext 构建 + request_context 入 state（runner.py:484-533，checkpoint_safe dict）
      ├─ worker 线程 graph.stream（runner.py:535-650，thread_id=agent-{session}-{ms}-{uuid8}）
      │     router_node（判域，见 §2）→ route_selector 条件边
      │        ├─ 域图节点（cs_graph_node/travel_graph_node/selection_funnel_graph_node，自动发现 builder.py:145-149）
      │        ├─ direct：tool_selector→skill_executor / workflow_executor
      │        ├─ plan：planner→critique→supervisor(Send 并行)→skills
      │        └─ general_chat / clarify→reporter
      ├─ 事件汇流 merged_q → status/log/delta/clarification/todo/usage/context
      ├─ end_turn 持久化（runner.py:765-775，CS 轮豁免）+ CS 轮独立落库（runner.py:890-912）
      └─ done 帧（trace_id/usage/pending_action/context_usage）
  → SSE：meta → status/log/delta/… → done | error（15s 空闲 ping）
```

- state 创建点：`make_initial_state`（events.py:429-474）；session_id 来自 body（schemas.py:13，默认 "default"）；tenant/user/roles 来自网关注入头（identity.py）；trace_id 在 runner 创建（不进 state 字段，随 RequestContext 与日志上下文流动）；domain 在 router_node 判定后以 `route_mode` 写入 state（`domain` 键仅 hierarchical 平铺时写，router_node.py:508-522）。
- 域图自动发现：`backend/domains/__init__.py` import 三个 register → `domain_graph_registry`（orchestration/domain_registry.py:12-34）→ builder 遍历注册（builder.py:145-149），不改 builder。

---

## 2. Domain Router 审计（A2 十问）

**1) DomainDetector 几个实现？** 严格意义的 DomainDetector 仅 1 个：`customer_service/router/domain_detector.py:16`（纯规则，向量通道 2026-09-17 已删）。但「域判定」实际 6 套并存：CS DomainDetector、travel_prefilter 正则（travel_prefilter.py:54-76）、selection_funnel_prefilter 正则（:36-48）、legacy 三层 Router（router/router.py:168-357）、hierarchical 粗分类 CoarseIntentClassifier（domain_classifier.py:183-260）、CS 域内 coarse/fine router（customer_service/router/）。

**2) 不同入口各跑一套？** 是，三入口并存：全局入口（router_node 预过滤链+主 Router）、客服锁域（domain_hint→cs_forced，router_node.py:235-236 跳过域检测门/灰度/其他 prefilter）、hierarchical 域图 prefilter（router_node.py:560-589 复用同一批 prefilter 再进一次）。

**3) domain 枚举在哪定义？** 主图 `route_mode` 全部为字符串字面量（travel/customer_service/selection_funnel/clarify/general_chat/direct/plan/workflow），域图注册表键即字符串（domain_registry.py:25）；CSDomain/CSRoutePath 是 str Enum（cs/router/types.py:10-27）；hierarchical 9 粗域声明于 capabilities.yaml:60-220，unknown 为字符串字面量（domain_classifier.py:201）。**无统一 domain Enum**——见 P2-1。

**4) 无法识别进哪？** hierarchical：显式 unknown（reason_code 四种，domain_classifier.py:199-254）→ `COARSE_UNKNOWN_ACTION` 默认 "clarify"（config/llm.py:258）；legacy 无显式 UNKNOWN：LLM 失败 fallback = plan + rag.search(0.5) conf 0.3（llm_router.py:113-122），Router 整体异常 → plan（router_node.py:446-452）。

**5) confidence 如何表达？** RouteDecision.confidence 0-1 float（router/types.py:44）；rule 拍板线 0.8、vector 采纳线 0.85（中置信 0.6-0.85 采纳）、LLM 固定 0.7/fallback 0.3；粗分类双阈值 top1≥0.75+margin≥0.12（config/llm.py:250-268）；domain_confidence 平铺进 state 并进 trace/metrics（router_node.py:512）。

**6) 是否有明确 UNKNOWN？** hierarchical 有（转 clarify）；legacy 无（落 plan 支线，靠 RAG 无证据拒答兜底）。可接受，P2-2 记录。

**7) fallback 会误进其他 Domain？** 不会。LLM/异常 fallback 都落 plan+rag.search；SQL 只能被 rule 强信号或 vector 主动命中，且执行前有 sql.read 权限门 fail-closed（sql/policy.py:172-190）。已知风险：vector 中置信（≥0.6）可把模糊 query 送进 sql.query——权限门兜底，P1-6 记录。

**8) 子图内是否再判域？** 域级不再判（CS supervisor/expert 与 travel supervisor 是域内调度）；`try_cs_prefilter` 在同一请求内可被调用 2-3 次（全局入口 1 次 + hierarchical 域图路径 1 次 + cs_prefilter 内改写后再 detect 1 次，cs_prefilter.py:120-124），有三级缓存缓解。P2-3。

**9) 重复分类？** 见 8)；另有 `cs_rule_hit_count`（无缓存）与 `detect_cached`（300s TTL）对同一 query 双跑正则（domain_detector.py:56-63 vs 72-96）。hierarchical 回退 legacy 时 embedding 双跑。

**10) route_path 可追踪？** CS route_path（cs/types.py:47）进 trace tags cs_target + metadata cs_route（cs_prefilter.py:171-189），执行后对照 cs_expert_final/visited（cs_graph_node.py:176-213）；route_mode 进 runner trace 拓扑快照（runner.py:604-607,1084-1122）与 metrics（routing_domain_total{domain,source}）。**SSE meta 帧不含 domain/trace_id**（P2-4）；done 帧带 trace_id。

**粘滞与切换（C2/C3 相关现状）**：三层粘滞机制——①`mark_domain_turn` 写 active_domain（router_node.py:29-54,540-558,629-643；routing_context.py:85-111）；②TravelPendingResolver（结构化 pending 短路回 travel，开关默认 true）与 ContinuationResolver（延续信号词+无外域强信号→回 active_domain，默认 true）；③CS ContextResolver 在 cs_prefilter 判域**之前**做指代解析（cs_prefilter.py:100-124）。粗分类本身不消费粘滞上下文（classify(query, context) 的 context 未使用，domain_classifier.py:194-260）。延续信号窗口 ≤40 字，外域强信号优先——「退款后说规划东京」会被 travel prefilter 正确夺走（travel 强信号命中即不进 ContinuationResolver 短路）。

---

## 3. Domain State 审计（A3）—— 域私有状态矩阵

| 域 | State 定义 | 私有字段（Domain Private） | 跨轮持久位置 |
|---|---|---|---|
| CS | `CSGraphState` customer_service/graph_state.py:76-112 | conversation_status/handling_mode/handoff_state/confirmation_state/pending_action/supervisor_decision/expert_history/current_expert/expert_loop_count/last_expert_result/cs_context/cs_audit_entries/cs_action_result | confirmations/handoffs/tickets/conversations 表（DB 权威）；cs_state_loader 每轮从 DB 重载并显式重置执行态（graph_builder.py:40-76） |
| Travel | `TravelGraphState` travel/graph_state.py:46-124 | brief/brief_missing/clarifications/brief_fingerprint/candidates/day_plan/itinerary/validation/repair_*/stage/current_expert/persistence_status | 域图 checkpointer（thread=travel:{conv}）为执行权威；结构化摘要 travel_pending 挂 ConversationContext（键含 tenant/user/conv） |
| Selection | `SelectionFunnelState` selection_funnel/graph_state.py:41-73 | brief/brief_missing/pool/candidates(含 data_quality)/stage_logs/config_snapshot/run_id/funnel_context | ConversationContext.sync_funnel_candidates_to_context；**不接 checkpointer**（graph_builder.py:105） |
| SQL | 无域上下文字段（sql_context 全仓 0 命中） | SQLResult（sql/sql_result.py:33-69）/Skill 层 Pydantic SQLResult（skills/sql/models.py:14-26）/BusinessInsight（skills/business_analysis/models.py:9-16） | step_results（reducer 合并）；无跨轮 |
| RAG | 无独立 TypedDict | AskOutcome{answer,sources,answer_meta}（rag/pipeline.py:54-63）；docs 不进图 state | step_results.output 文本内嵌引用；reporter 解析（reporter.py:378-396） |
| General | — | general_chat_node 直答（:68-72） | 无 |

Shared vs Private 判据：凡写入 `OrchestratorState` 顶层 schema 的即 Shared Runtime State；域图内部 TypedDict 字段即 Domain Private（经域图适配器白名单进出主图，runner.py:585-589）。

---

## 4. Shared Graph State 审计（A4）—— 共享字段矩阵

`AgentState`/`OrchestratorState`：orchestration/state.py:65,144。reducer：step_results（进展优先合并 state.py:24-45）、messages（add_messages）、_degraded_steps（or_）。

| 类别 | 字段 | 定义 |
|---|---|---|
| identity | request_context（RequestContext/checkpoint_safe dict）、user_id、department、session_id、tenant_id | state.py:96,101,102,113,126 |
| routing | route_decision、route_mode、domain_hint、routing_context、query_understanding | state.py:76-78,107,115-119 |
| routing(hierarchical 平铺) | domain/domain_confidence/domain_margin/domain_source/candidate_tools/selected_tool/tool_arguments/tool_confidence/tool_route_mode/need_clarification/clarification_reason | state.py:131-141 |
| 业务 | question/kb_id/plan/step_results/current_step_id/messages/final_answer/resolved_params/executor_*/workflow_result | state.py:67-92 |
| 流程控制 | guard_result/alerts/_supervisor_loop_count/_plan_critiqued/_plan_changed/_degraded_steps/selection_blocked/_clarify | state.py:84-92,160,165 |
| 域上下文 | cs_context（153）、cs_action_result（154）、cs_audit_entries（155）、funnel_context（172）、travel_context（179） | sql_context/selection_context **不存在**（选品域登记名是 funnel_context） |

关键结论：
- `cs_context`/`travel_context`/`funnel_context` 已全部登记 schema（后两者 2026-09-23 补登记，回归测试 tests/orchestration/graph/test_orchestrator_state_persistence.py 静态防线）——LangGraph「updates 流剥离未登记键」的历史坑已闭环。
- **`cs_pending_action` 未登记 schema**（P2-5）：cs_graph_node.py:232,248 写入节点输出，runner.py:616,746 直接从节点原始输出读取（绕过 state）。功能正确但属剥离坑的活证据。
- 无 `state["metadata"]` 巨型 dict；metadata 存在于三处嵌套容器：trace.metadata（cs/travel/funnel 三域键混装单容器）、cs_context.cs_route.metadata（路由归因+实体+治理字段混装）、业务报表 result["metadata"]。扁平混装有，但都在域私有/观测容器内，不在共享 state 顶层。P2-6 记录归因/实体/治理混装。

---

## 5. Context 泄漏审计（A5）—— Domain Context Ownership Matrix

| field | owner | writer | reader | lifetime | cross-domain allowed |
|---|---|---|---|---|---|
| cs_context | CS | cs_prefilter.py:194（每轮重建）；merge cs_graph_node.py:227 | cs_graph_node 适配器、runner trace 快照 | 请求级（每轮覆盖） | **否**（唯一读取点都在 CS 入口） |
| cs_route.metadata.order_id | CS | cs_prefilter.py:126、router_node.py:172、action.py:439 | query.py:201,237、action.py:330 | 随 cs_context 轮级；Redis 承接 TTL 7200s | 否（读取点仅 CS expert） |
| travel_context | Travel | travel_prefilter.py:100-107 / travel_pending_resolver.py:141-217 / router_node.py:98-105 / travel_graph_node.py:294-328 | travel_graph_node:89-92,119-125,244-246 | 轮级覆盖 + ConversationContext 摘要 | 否 |
| funnel_context | Selection | selection_funnel_prefilter.py:69-76 / selection_funnel_graph_node.py:36-39,83-86 | selection_funnel_graph_node、direct_executor.py:340（top 注入 workflow） | 轮级 + ConversationContext | **有一处例外**：direct_executor 读 funnel top 注入 selection_decision workflow——同域（选品系）内，允许 |
| routing_context.active_domain | Runtime | mark_domain_turn（router_node 三处） | assemble_routing_context → router_node（两个 resolver + 缓存键） | 会话级（ConversationContext，TTL 管理内） | 是（设计内：路由粘滞载体） |
| ConversationContext（跨轮载体） | Runtime | sync_travel_*/sync_funnel_*/mark_domain_turn/mark_turn | FollowUpResolver、TravelPendingResolver、routing_context | 会话级 | 摘要级共享（历史语义），结构化 pending 有 active_domain 门控 |

结论：
- **CS order_id 不会注入 Travel/SQL prompt**：ContextResolver 的读取点仅 cs_prefilter.py:105-122 与 CS 图入口 pin 登记（graph_builder.py:106-119）；travel 链路读 travel_context/ConversationContext travel 摘要；SQL 链路只读 state 平铺 question/step_results。`clear_recent_context` 无生产调用者（TTL 2h 自然过期）——P1-8 记录「无显式清域但读取点封闭」。
- **Travel destination 不会成为 SQL 实体**：SQL 链路无任何域上下文消费点。
- **跨域泛化改写唯一通道**是主图 FollowUpResolver（runner.py:58-146，读 ConversationContext 摘要 + L1 末条）——共享历史语义的合法通道，CS 锁域时跳过（runner.py:423）。
- 切域后旧域 pending 不强制回域：travel pending 被 `active_domain=="travel"` 门控（travel_pending_resolver.py:121-126）；CS pending 只在同 session 再进 CS 时经 cs_state_loader 从 DB 重载，不被其他域消费（confirmations 键 (user_id, session_id)，读取点仅 CS 图）。

---

## 6. Session / Checkpointer 审计（A6）—— Checkpoint Matrix

| 图 | thread_id | 实现位置 | 开关（默认） | 表 | TTL |
|---|---|---|---|---|---|
| 主图 chat | `agent-{session_id}-{ms}-{uuid8}` 每轮唯一 | runner.py:553-563 | MAIN_GRAPH_CHECKPOINTER_ENABLED（**false**） | agent_memory 库 checkpoints/checkpoint_blobs/checkpoint_writes | 7d |
| 主图 task | `task-{task_id}`（跨 retry 不变） | task_service.py:80 | 任务强制 PostgresSaver（task_executor.py:46-63，**禁降级 MemorySaver**） | 同上 + tasks/agent_checkpoints | 7d |
| CS | 裸 `conversation_id` | cs_graph_node.py:252-264 | CS_CHECKPOINTER_ENABLED（false） | 同上三表 | 7d |
| Travel | `travel:{conversation_id}`（无会话 travel-{uuid4}） | travel_graph_node.py:385-407 | TRAVEL_CHECKPOINTER_ENABLED（false） | 同上三表 | 7d |

- 三域共用同一组 PostgresSaver 表；`travel:` 前缀是防 CS/travel 同会话互撞的 namespace 隔离（travel_graph_node.py:399-403 注释载明动机）。
- TTL 清理全进程单例守护（checkpointer_cleanup.py:152-164，首调方 TTL 生效），advisory lock 抢主，advisory_lock_key=20260921。三处 TTL 常量必须一致（7/7/7）。
- **tenant 不进 checkpoint key**（P1-4）：隔离完全依赖 thread_id 唯一性。conversation_id 源自 session_id（body 用户可控）——两个用户若人为共用 session_id，CS/travel checkpoint thread 相同互写；业务状态每轮从 DB 按 (user_id, session_id) 重载+执行态显式重置，实际污染面被压到执行态，但属于结构性弱点（P1-5）。
- **resume 域判定**：任务执行器固定构建主图（task_executor.py:66-83）；`tasks.graph_name` 预留但当前仅 main（tasks/schema.sql:11）；rag_index 任务被守卫拒绝进 agent 图（agent_tasks.py:260-266）。域图不在任务 resume 路径上 → 「resume 恢复错误 Domain」结构性不存在。resume 时授权以执行时解析覆盖 checkpoint 旧权限（task_executor.py:328-363，update_state 补丁）。
- **跨域旧 pending 恢复**：CS pending 在 DB（confirmations，原子认领 pending→confirmed）；travel pending 在 ConversationContext（结构化、active_domain 门控）；均不依赖 checkpoint 存活。

---

## 7. Memory 边界审计（A7）

五类职责切分（现状）：

| 载体 | 职责 | 写入方 | 读取方 |
|---|---|---|---|
| Chat History（chat_messages，L2） | 主链每轮原文历史 | MemoryService.end_turn（runner.py:771-775）；**CS 轮豁免**（runner.py:768-775，防客服会话泄漏进主侧栏） | start_session 装载、L2 摘要源 |
| L1 短期 | 进程内环形缓冲 ≤20 条 | MemoryManager.start_session | prompt 组装 |
| L3 长期（memory_records，pgvector） | 跨会话用户事实/偏好 | end_turn 后台管线（service.py:296-403）：证据门→hedge 封顶→分类→重要性→PII→冲突裁决；**CS 轮不进**（随 end_turn 豁免） | HybridRetriever（sim .5+imp .3+rec .2）、memory_search_tool |
| Domain State | 域私有执行态/业务态 | 域图节点/DB store | 域图自身 |
| Checkpoint | 崩溃恢复/审计/interrupt | LangGraph checkpointer | 任务恢复 |
| PinnedContext | 请求级关键值防压缩丢失 | CS 图入口 _register_request_pins（pending target_id/last_order_id，ContextVar 请求级） | context_budget L5 保护 + LLM 裁剪豁免 |

- chat_messages 写入方是 memory service（非 context_budget 改写）；**canonical chat_messages 无任何域改写路径**。
- L2 摘要（≥50 条触发）+ L5 AutoCompact（预算 ≥90%）写 chat_sessions.summary，pin 值作为 critical fact 受 ProtectedFactRegistry 保真（manager.py:384-391）——**设计上让确认态实体在摘要中保真**（会话级，非跨会话）。
- **transient 永久化风险（P1-9，重点）**：runner 只豁免 CS 轮；**travel 轮原文照写 chat_messages 并进 L3 候选管线**。用户补槽短答（「住难波」「3 天」）是用户原话、可通过证据门；分类只有 user_fact/preference 两类无条件 STORE（trigger.py:28-30），importance 类型保底 0.7/0.65 必过 0.6 闸（importance.py:13-23）——**travel 槽位值可被永久化为跨会话记忆，管线无 pending/域维度过滤**。`memory_store_tool`（origin=explicit）同样无 transient 过滤。此风险与另一会话正在推进的 Memory STOP C（memory_key+冲突裁决）同域，登记在案，本轮不动 memory 冻结层。
- memory_key scope=(tenant_id, user_id, memory_key)，partial unique index 保证同 key 单 active（models/memory.py:35-36）；检索强制 (tenant,user) 双维；**无 domain 维度**（同用户跨域记忆互检索是设计内）。`normalize_tenant_id` 把空/非法折叠为 "default"（keying.py:32-42，未提交文件）——网关路径 tenant 恒有值（72be04d 起登录强制可信租户头），折叠仅 guest/直连死路径可达，P1-10 记录。chat_sessions/chat_messages 无 tenant 列（user_id 属主隔离，单租户现实，P1-4）。

---

## 8. Identity Propagation 审计（A8）—— 身份传播矩阵

| 层 | user_id | tenant_id | session_id | trace_id | roles/permissions/data_scope | 边界丢失点 |
|---|---|---|---|---|---|---|
| APISIX | JWT→X-User-Id（gateway-auth.lua:489-498） | claim→X-Tenant-Id（:518-521） | 不传 | X-Trace-Id 透传 | X-User-Roles/Permissions（:500-517）；九伪造头剥离（:48-49） | shadow/open 模式不注入 |
| FastAPI | resolve_identity（identity.py:64-89） | X-Tenant-Id 正则规范化（:55-61） | body（chat.py:170） | 访问日志头 | Identity.roles/permissions | legacy 模式 body 可伪造（生产 header/strict） |
| runner | 显式传参→RequestContext（runner.py:484-495） | 同 | RequestContext.session_id | trace 对象随 ctx（不序列化） | roles→widest_data_scope 折算（:487） | checkpoint_safe 剔除 trace/sink（设计内） |
| state | 平铺四键已登记（state.py:96-126） | 同 | 同 | — | request_context 内 | 未登记键被剥离（已全部登记闭环） |
| 节点/Send | bind_from_state 重绑（trace_middleware.py:81-82） | 同 | 同 | trace_collector.bind | bind() 写 ContextVar（core/request_context.py:199-207） | 漏绑线程即静默丢（现有节点已绑） |
| CS 子图 | user_id（cs_graph_node.py:36） | tenant_id（:40；缺省 "default"，P1-11） | session_id | 不传 | **不传**（P1-12：CS 授权模型=归属+确认状态机，不消费角色，记录为设计事实） | — |
| Travel 子图 | user_id（travel_graph_node.py:102） | **不传**（P1-13，只读无实害） | session_id | 不传 | 不传 | — |
| Tool | get_tool_user_id() ContextVar（core/request_context.py:75-77） | get_tool_tenant_id | session ContextVar | — | roles/permissions ContextVar | MCP 直调/脚本未绑→空串，消费方 fail-closed（rag guest 化、SQL 拒绝） |
| Celery 消息 | **不传**（消息只带 task_id） | 不传 | 不传 | 不传 | 不传 | 全部靠 worker 查 tasks 行重建 |
| Worker | tasks 行→_bind_task_identity（agent_tasks.py:207-231，空值也显式绑防 prefork 泄漏） | 同 | RequestContext.session_id=task id | 任务级 trace，session_id=thread_id（task_executor.py:232-236） | **执行时查 auth.users 重建**（task_executor.py:153-180，不信任创建时 JWT 快照） | 仅 tenant/user 进工具 ContextVar；roles 只进 state |
| selection_decision 后台 asyncio | **无任何绑定**（routes/selection_decision.py:44-55） | 无 | 无 | 无 | 无 | **P0-1 已修门禁+留痕；执行体身份绑定记 P1-14** |

结论：主链（网关→图→子图→Tool→Celery）身份闭环，fail-closed 面完备；缺口集中在选品 HTTP 侧门（已修）与 WorkflowExecutor 无身份装配（P1-14）。

---

## 9. Permission Boundary 审计（A9）—— 权限矩阵

| 域/面 | 授权入口 | 写操作 | 审批门 ensure_approved | worker/执行侧再校验 |
|---|---|---|---|---|
| SQL | authorization.py:31-39（sql.read 仅 editor/admin）+ sql/policy.py:172-190 权限门 + 表域×scope（:299-323）+ tenant/department/self 参数化注入（:337-371，tenant 缺参 fail-closed） | 无（SELECT-only 六层 + agent_readonly 只读账号 + 只读事务） | 无需 | Guard 纵深复查；Skill 缺可信 request_context 拒绝（skills/sql/skill.py:239-256） |
| CS 查询 | PermissionChecker.validate_user_identity（experts/query.py:49）+ 订单归属 SQL 强制 customer_id=user_id（order_service.py:182-191） | 无 | 无 | — |
| CS 动作 | validate_user_identity（action.py:70）+ 确认状态机原子认领 CAS（confirmation_flow.py:114-155，幂等 key 含 confirmation_id） | 退款/售后/账户（当前 simulate_execute 模拟） | 无（确认即授权） | 归属+幂等认领防重放 |
| CS 工单/坐席管理 | require_cs_supervisor 双档闸（cs_ops.py:21-32） | 状态流转/认领 | 无 | 平台 admin 或 cs role=supervisor |
| RAG | require_principal + KB 级 ABAC（authorized_kbs，retrievers.py:472-473）+ 文档级 permission_scope（rag/permissions.py:43-94，None 即拒绝受限文档；chain.py:350-372） | 上传/审核（rag.upload/rag.review） | 无 | Tool 通道 resolve_tool_principal fail-safe customer |
| Email/Export/采集/竞品写 | — | 写 | email.py:67/export.py:29/data_collection.py:62/competitor.py:164+HTTP:40；指纹掺 user/tenant（tool_approval.py:330-342）+ 副作用预算门（:356-382） | 审批单 consume 核对归属 |
| 异步 Agent 任务 | resolve_task_authorization 执行时重查 auth.users（task_executor.py:328-354，失败 fail-closed FAILED + metrics） | 任务状态写（fenced） | 无 | 每次 execution 重新授权；resume 覆盖旧权限 |
| **Selection（修前）** | **无** | 导入池写/决策落库 | 无 | 无 |
| **Selection（修后）** | resolve_operator_role（viewer/editor/admin/内部令牌） | 同上 | 无 | 门禁+operator.actor 日志留痕；**执行体身份绑定仍缺（P1-14）** |
| 治理端点 | resolve_operator_role 唯一入口 + require_user_actor（kind=user only）+ require_admin_user | 视端点 | — | 角色只认网关 roles 或内部令牌 |

租户隔离面：SQL 数仓 18 表无租户列（结构隔离决策 D1，Guard 保留注入能力位）；RAG 无 tenant 元数据维度（KB 矩阵隔离）；CS 业务表 ORM 带 tenant_id；tasks 表 user_id NOT NULL+tenant 索引+owner 读取（get_task_for_user，404 防 枚举）；幂等 key 全局含 tenant（tasks/side_effect.py:5,48-85）。**「前台允许≠worker 允许」有实证防线**（执行时授权+fail-closed+task_authorization_denied_total 指标）。

---

## 10. Sync/Async Matrix + Side Effect Inventory（A10/A11）

**Sync/Async**：六个域的「回答生产」全部在同步图内完成（CS/travel/selection 子图为图内节点 invoke；SQL/RAG 为图内 skill）。Celery 异步仅两条业务链：rag_index（上传索引，rag_upload.py:948 等三处投递）与 interactive_agent（任务运行时，task_manager.py:174）+metadata_shadow。**域图不提交 Celery 任务**。异步 payload 一律只带 task_id（Celery 消息不含身份），身份/trace 权威在 PG tasks 行，worker 重建——这是冻结层 Phase2 的既定契约，Domain Runtime 作为调用方遵守。

**Side Effect Inventory**（18 项，节选，全部复用既有幂等设施）：

| 写操作 | 幂等保护 | 审批门 |
|---|---|---|
| email.send | 全局幂等账本+进程指纹窗 | ✅ email.py:67（无租户上下文降级直发绕账本，P1-15） |
| export CSV / data_collection / competitor watchlist | run_idempotent_operation | ✅ :29/:62/:164 |
| CS 确认单 | DB 条件 UPDATE CAS（pending→confirmed 仅一方成功） | 确认即授权 |
| CS 动作执行+审计落库 | 幂等账本 cs.action.persist（audit_repo.py:122-136 ON CONFLICT） | — |
| CS 工单/handoff | 同 turn 幂等+活跃 handoff 复用 | — |
| CS outbox/实时事件 | 事务性 outbox+SKIP LOCKED / event_id 唯一 | — |
| tasks 行全状态写 | fencing：execution_id CAS，旧 owner TaskLeaseLost（task_service.py:280-291） | — |
| RAG indexing 五路写 | file_hash duplicate 跳过 + rag.index.submit 账本 | — |
| travel run 取消 | expected_run_id CAS（travel_graph_node.py:24-46） | — |
| memory L3 写 | 048 版本冲突裁决（另一会话推进中） | — |
| 选品决策落库 | decision_version 递增 | —（门禁已补） |

---

## 11. Reporter / SSE / Observability（A12/A13/A14）

**Reporter**：主图 reporter（Markdown+结构化模板渲染，reporter.py:431,491,517）；direct/workflow 由 executor 自产 final_answer；CS/travel/selection 子图各有 reporter 直连 END（builder.py:191-192）；general LLM 直答。**失败/挂起表达不一致（P2-7）**：CS 用结构化 pending_action（done 帧）+固定失败话术映射；Travel/Funnel 把 need_info/需确认内嵌 Markdown 文本（不发 clarification 事件）；clarification SSE 事件仅主图入口与 CS L2 拒答使用；兜底文案三套措辞。

**SSE 事件全集**（/chat/stream）：meta{node_labels,request_id} → status/log/delta/thinking/clarification/todo/usage/context/file → done{elapsed,sources,usage,trace_id,pending_action,context_usage} | error（统一错误协议壳）| ping。无独立 source 事件（sources 在 done 内）。**meta 不含 domain/route/trace_id（P2-4）**。断连：GeneratorExit→stop_event+trace 以 client_disconnect 收尾（幂等 _finalize_trace）；背压→error 截断帧。任务流 /tasks/{id}/stream 独立通道（snapshot+Redis pub/sub 事件+done/error 别名帧）。子图切换/handoff/pause-resume 不破坏生命周期：done 每轮恰一次；error 无「只发一次」护栏（中止路径只 error 无 done，设计如此）。

**Observability**：
- metrics：routing_domain_total{domain,source}、router_decision_total{mode}、router_layer_total{layer}、router_confidence、cs_intent、llm_tokens/requests/failures/fallback、task_*（admission/terminal/lease/fenced_write/recovery/authorization_denied）、context_* 九族、chat_request_total{status}。**用户全文/user_id/trace_id 进 label 的禁令多处明文**（metrics.py:63-64,94-96 等），错误原因必须走 error_taxonomy 词表。
- trace：自研 TraceCollector（SQLite+PG ai.trace_records 镜像）；trace_id 贯穿 HTTP→Graph→Domain→Skill/Tool→LLM（usage store 归因）→Task（session_id=thread_id 贯穿，tags 记 task_id/execution_id/queue）；span 创建点 30+ 处，泄漏强制收尾+finish 覆盖率指标。主链 trace_id 与网关 X-Trace-Id 未同一性关联（P2-8）。
- audit：CS 动作 audit_logs/agent_actions（幂等落库）、SQL audit（hash+表名，不存原文）、审批单状态机、幂等决策 trace tags、Input Guard 结构化日志（HIGH 只落长度+sha256 前 12 位）。

---

## 12. 缺陷台账：P0 / P1 / P2 / KEEP

### P0（本轮已修，STOP A 内闭环）
| # | 缺陷 | 位置 | 修复 |
|---|---|---|---|
| P0-1 | 选品决策/漏斗 HTTP 路由无身份门禁：任意已认证用户可读/写全部任务（水平越权）+ 后台执行无操作者留痕 | app/api/routes/selection_decision.py、selection_funnel.py | 挂 resolve_operator_role + create 留痕 operator.actor；tests/api/test_selection_routes_authz.py（无凭据 401 / JWT viewer 放行 / 内部令牌放行）+ 既有 9 例契约测试适配，14 passed |

### P1（登记在案，按归属排期，不在本轮扩改）
| # | 缺陷 | 位置 | 归属 |
|---|---|---|---|
| P1-1 | 选品共享数据集无 user/tenant 维度（import_candidates/decision_log/selection_* 全部无属主列）——门禁修复后为「角色内共享」语义，与竞品 watchlist 同档；若未来多团队使用需加属主列 | sql/migrations/017、021 | STOP B 复核（域接入规范要求声明数据边界） |
| P1-2 | WorkflowExecutor 全链无身份装配（selection_decision 后台 asyncio 执行体） | orchestration/workflow/executor.py | STOP D（异步身份）评估最小接入 |
| P1-3 | 主链外异步入口 rag_index payload 不含身份字段（靠 tasks 行重建——契约既定，但 domain 维度缺失） | rag_upload.py:1026-1040 | STOP D 验证任务行完整性 |
| P1-4 | chat_sessions/chat_messages/checkpoints 三表无 tenant 维度（user_id 属主/thread 唯一性隔离；单租户现实下可接受） | memory/models/session.py、tasks/schema.sql | 记录为架构决策，多租户化前必须补 |
| P1-5 | session_id 来自 body 用户可控且为 CS/travel checkpoint thread 根——人为共用 session_id 可致 checkpoint 互写（执行态面，业务态有 DB 重载防线） | chat.py:170、cs_graph_node.py:263 | STOP B（checkpoint namespace 语义） |
| P1-6 | vector 中置信（≥0.6）采纳可把模糊 query 送进 sql.query（sql.read 门 fail-closed 兜底，非数据面泄漏） | router.py:246-288 | KEEP+监控（router_confidence 分位） |
| P1-7 | CS 轮持久化 record_cs_turn fire-and-forget（失败仅 debug 日志）——SSE 成功与审计落库可能不一致（业务动作本身有幂等账本，非业务态不一致） | runner.py:890-912 | STOP D 评估改重试队列 |
| P1-8 | CS 订单承接 TTL 2h 无显式清域（读取点封闭所以无泄漏，但过期前同会话回流可能引用旧单） | context_resolver.py:135-140 无调用者 | STOP B 决策：保留 TTL 语义 or 补清域调用 |
| P1-9 | travel 补槽短答可被 L3 管线永久化为 user_fact（无 pending/域过滤）；memory_store_tool 无 transient 过滤 | memory/trigger.py:28-30、importance.py:13-23 | **Memory STOP C（另一会话）**，本轮只登记 |
| P1-10 | normalize_tenant_id 把未声明租户折叠进 "default"（网关路径 tenant 恒有值，死路径防御） | memory/keying.py:32-42 | Memory STOP C |
| P1-11 | new_cs_graph_input tenant_id 缺省 "default" | customer_service/graph_state.py:121 | STOP B（域契约显式化） |
| P1-12 | CS 子图 state 无 roles/permissions/data_scope（CS 授权=归属+确认，不消费角色——设计事实；若 CS 未来接角色能力需补） | graph_state.py:86-92 | F3 冻结记录 |
| P1-13 | Travel 子图 state 无 tenant_id（只读 POI 无实害） | travel/graph_state.py:46-123 | F4 新域规范禁止先例扩散 |
| P1-14 | selection_decision 后台 workflow 执行体无身份（与 P1-2 同根） | routes/selection_decision.py:44-55 | STOP D |
| P1-15 | email/export/data_collection 无租户上下文直调时降级绕过幂等账本（仍过审批门） | email.py:92-95 等 | KEEP（工具直调语义）+STOP D 抽查 |

### P2（记录，不阻塞）
P2-1 无统一 domain Enum（route_mode 字符串+注册表键）；P2-2 legacy 无显式 UNKNOWN（落 plan+rag 拒答）；P2-3 同请求 CS prefilter 重复分类 2-3 次（缓存缓解）；P2-4 SSE meta 无 domain/trace_id（done 有 trace_id）；P2-5 cs_pending_action 未登记 schema（读节点原始输出，功能正确）；P2-6 cs_route.metadata 混装归因/实体/治理字段；P2-7 各域失败/挂起表达不一致（CS 结构化 vs Travel/Funnel 文本内嵌）；P2-8 主链 trace_id 与网关 X-Trace-Id 未关联；P2-9 兜底文案三套措辞。

### KEEP（设计事实，冻结确认）
①route_mode+domain_graph_registry 字符串键机制；②主图每轮 thread 唯一、跨轮记忆归 MemoryService；③CS 轮豁免 chat_messages/memory 主链（防侧栏泄漏）；④域图不提交 Celery 任务、异步只经冻结 Task Runtime 且消息只带 task_id；⑤checkpoint 三表共享+travel: 前缀隔离+单例 TTL 守护；⑥hierarchical unknown→clarify 显式表达；⑦LLM/异常 router fallback 落 plan+rag.search 不误入高权限域；⑧CS 子图授权=归属校验+确认状态机（不消费平台角色）；⑨SQL 数仓无租户列的结构隔离决策 D1；⑩工具层 ContextVar fail-closed（空身份→guest/拒绝）。

---

## 13. 最终验收问题预答（对照任务书 §13）

| 问题 | 答案（出处） |
|---|---|
| 这轮为什么路由到这个域？ | router_node 判定层序 + trace 拓扑快照 + routing_domain_total{source}（§2） |
| 上轮 Domain 是否影响这轮？ | 仅三条受控通道：active_domain 粘滞（延续信号≤40字+无外域强信号）、travel 结构化 pending 短路、CS 指代解析（判域前）；粗分类不消费粘滞（§2.11） |
| 域私有状态在哪、何时失效？ | §3 矩阵：域图 TypedDict（轮级）+ DB store（业务态机）+ ConversationContext（会话级摘要） |
| 切域会泄漏吗？ | 读取点封闭：CS 实体只进 CS 入口、travel pending 有 active_domain 门控、SQL 零域上下文消费（§5） |
| tenant/user 贯穿子图？ | CS：user+tenant ✅ roles 不传（设计事实）；Travel：user ✅ tenant 不传（只读）；SQL/Tool：ContextVar+request_context fail-closed（§8） |
| 进 worker 身份还在？ | tasks 行权威+worker 重建 ContextVar+执行时重查 auth.users（§8 Celery 行） |
| 权限在哪层再校验？ | SQL 六层+Guard；CS 归属+确认 CAS；RAG KB+文档级；任务执行时授权 fail-closed；写工具审批门（§9） |
| worker 死后恢复知道原域吗？ | 任务固定主图+graph_name 预留；域图不在任务路径；thread_id=task-{id} 跨 retry 不变（§6.4） |
| 旧 execution 能写吗？ | 不能——execution_id fencing CAS，TaskLeaseLost+指标（§10） |
| 重复请求 side effect 几次？ | 幂等账本/确认 CAS/decision_version 逐项收敛（§10） |
| Router 挂了进哪？ | plan+rag.search 或异常→plan，绝不随机进高权限域（§2.7） |
| 模型挂了谁 fallback？ | 冻结 Model Governance（llm_fallback_total），域不自选模型 |
| Redis 挂了域会怎样？ | 会话上下文有 memory fallback 仓、限流/锁各有降级（E7 实机验证） |
| PG 挂了会「前端成功 DB 失败」吗？ | 主链业务态写失败会 error 出帧；CS 审计轮 fire-and-forget 是唯一弱面（P1-7，非业务态）（§11） |
| SSE 断开后台状态？ | 同步图 stop_event 取消+trace client_disconnect 收尾；异步任务不受影响继续跑（§13/E12 验证） |
| Trace 能一路找到吗？ | trace_id 贯穿五层+task session_id=thread_id；网关头未关联是 P2-8（§11） |

---

## 14. STOP A 结论

```text
STOP_A_PASS    = true
STOP_B_ALLOWED = true

带入 STOP B 的输入：
1. P1-5/P1-11：session_id/tenant 的域契约显式化（checkpoint namespace + 缺省值语义）
2. P1-8：CS 订单承接清域语义决策
3. P1-1：选品数据边界声明（域接入规范 F3/F4 的第一个应用样例）
4. P2-5：cs_pending_action 登记 schema（最小补登记，防剥离坑复发）
5. B8 泄漏测试的靶点已定位（§5 Ownership Matrix）
```
