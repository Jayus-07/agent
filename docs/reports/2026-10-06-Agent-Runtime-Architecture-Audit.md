# Agent Runtime 架构审计报告（只读还原，2026-10-06）

> 本报告完全基于当前仓库代码还原，未做任何代码修改。所有结论附文件路径+行号；无法确认处显式标 UNKNOWN。
> 审计方式：6 路并行只读代码探查（主图 / 路由引擎 / 三域实现 / 执行模式与 Skill-Tool 层 / Reporter 与 SSE / State-Trace-Checkpoint-测试耦合），交叉汇总。
> 代码基线：分支 `feat/domain-isolation-closure`（工作区含未提交改动，行号以工作区文件为准）。

---

## 1. Executive Summary

1. **主图确实混合了「业务域」与「执行方式」两个层级**，混合点就在 `route_mode` 这一个字段和 `route_selector` 这一个条件边上：`route_mode` 的取值集合 = {direct, workflow, plan(默认兜底), general_chat, clarify, handoff, customer_service, travel, travel_commerce, travel_booking, selection_funnel}——前六个是执行方式/交互控制，后五个是业务域归宿，两者在同一字段、同一分派函数、同一层调度。
2. **但运行时不是全混**：三个业务域已通过 `DomainGraphRegistry`（`orchestration/domain_registry.py`）实现声明式注册与自动布线，主图 builder 不硬编码域名；域与主图之间有独立 State + adapter 转换，边界是清晰的。混合主要在**路由分派层**而非**图结构层**。
3. **统一 RouteDecision 已存在**（`orchestration/router/types.py:34-58`，Pydantic），RoutingEngine 六阶段已收敛为唯一决策出口；但它只覆盖 `execution_mode/route_mode/candidates/confidence/workflow_name/routing_meta`，domain 等语义放在 `routing_meta` dict 里未强类型化，且 State 里还并存三代决策表示（route_decision dict / 平铺字段 / 四个 *_decision 快照）。
4. **Reporter 没有二次生成问题**：direct/workflow 模式 executor 直出 `final_answer`，runner 对 executor 答案采用覆盖语义（`runner.py:744-764`）；三个域图节点直连 END，自带 reporter 且全为模板组装（零 LLM），主图 reporter 被结构性绕过。
5. **Selection Funnel 是纯 Workflow（固定 DAG 线性漏斗，全链零 LLM）**，不是 Agent；Travel 是「纯规则状态机调度的确定性 workflow」（执行链零 LLM）；CS 才是真正的 agent_subgraph（规则为主 + LLM 兜底的 supervisor + 5 子 Agent）。
6. direct / workflow / plan 本质是 **Execution Mode**（`ExecutionMode` 枚举 direct/plan/workflow），但 `route_mode` 里又混进了 domain_graph/general_chat/clarify/handoff 四种非执行模式归宿——「Execution Mode」和「Route Outcome」没有分离。
7. guide/execute 属于**产品交互策略**（域入口模式，`{CS,TRAVEL,SELECTION}_GLOBAL_ENTRY_MODE`，作用在 prefilter 命中后的分派），不是 Runtime 类型；clarify 属于**会话控制状态**（零 LLM、纯规则、Redis 持久化、下轮重新路由）。
8. Skill/Capability/Tool 层级依赖方向干净（Capability→Skill→Tool→Infra，无反向违规），但存在少量跨层借用（direct_executor 摸私有注册表、借 reporter 内部渲染函数）；MCP 只是 Tool 层的数据源/对外暴露服务，不在主图运行时。
9. 改造约束面：node_id 硬编码测试 ≥33 文件，`route_mode` 依赖测试 ≥36 文件，SSE event 名依赖 ~224 文件，前端两处硬编码域图节点名（有守护测试）——「不改节点 ID、不破坏 SSE/checkpoint 的渐进治理」可行，但 State 字段与 route_mode 取值语义是最大约束点。

---

## 2. Current Main Graph（主图真实还原）

### 2.1 构建入口

- 图构建：`build_graph(checkpointer=None)` — `backend/orchestration/graph/builder.py:119-230`
- State Schema：`StateGraph(OrchestratorState)` — builder.py:132
- 运行入口：`MultiAgentSystem.__init__` → `build_graph(checkpointer=build_main_checkpointer())` — `orchestration/graph/system.py:45-46`；执行经 `GraphRunner`（runner.py）
- 节点包裹：全部节点经 trace_middleware 包裹（span/状态键守卫），router 额外经 `guard_node_update`（builder.py:140）

### 2.2 节点清单

#### 内置节点 9 个（builder.py:140-151）

| node_id | 文件 | 实现 | 读 State | 写 State | LLM | Tool/Skill | 副作用 | 下一跳 | 职责 |
|---|---|---|---|---|---|---|---|---|---|
| `router` | graph/router_node.py:45-348 | `router_node`（决策逻辑在 graph/routing/ 子包） | question, domain_hint, routing_context, session_id, tenant_id | route_decision, route_mode, query_understanding, domain 四件套, candidate_tools/selected_tool/tool_arguments/tool_confidence/tool_route_mode, need_clarification/clarification_reason, 四个 *_decision 快照, router_fallback_reason, legacy_used, _clarify, _handoff, cs_context, travel_context, funnel_context | 是（RoutingEngine LLM fallback；redirect LLM 仲裁默认 OFF） | 否（调 RoutingEngine/prefilter 链） | 是（ConversationContext 回写、澄清标记） | conditional `route_selector` | 唯一路由引擎：预过滤链→延续→分层路由 |
| `tool_selector` | graph/tool_selector.py:567-642 | `tool_selector_node`（FC 决策 `_fc_decide`:386-554） | route_decision, candidate_tools, selected_tool, question, tool_arguments | resolved_params, selection_blocked, _tool_selection, tool_arguments | 是（FC 选工具+填参，预算闸 35%，2 次业务尝试） | 否 | 观测 | fixed → skill_executor | direct 路径 FC 工具选择与参数解析 |
| `skill_executor` | graph/direct_executor.py:162-290 | `skill_executor_node`（`_run_skill_step`:116） | resolved_params, question, route_decision, kb_id | step_results, final_answer, executor_error, executor_mode, messages, alerts | 经 Skill 内部 | 是（单 capability 直调 Skill 节点） | 是 | fixed → reporter | direct 单步执行 |
| `workflow_executor` | direct_executor.py:360-448 | `workflow_executor_node` | route_decision, question, funnel_context | workflow_result, step_results, final_answer, executor_workflow | 经 workflow 内部 | 是 | 是 | fixed → reporter | workflow 整图执行 |
| `planner` | agents/planner/planner.py:103 | `planner_node` | question, kb_id | plan（DAG）, alerts | 是（5min LRU 缓存） | 否 | 否 | fixed → critique | 产出 Capability DAG |
| `critique` | agents/planner/critique.py:206+ | `critique_node` | plan, question | plan（可修正）, _plan_changed, _plan_critiqued | 条件（仅规则修不掉的 anomaly） | 否 | 否 | conditional `route_after_critique` | 计划审查/修正 |
| `supervisor` | orchestration/supervisor/scheduler.py:53-214 | `supervisor_node` | plan, step_results, _degraded_steps, _supervisor_loop_count | step_results, _ready_dispatch, _all_steps_done, _supervisor_loop_count, alerts | 否（纯规则） | 否 | 否 | conditional `route_after_supervisor`（Send 并行） | 就绪步骤调度/循环上限/降级 |
| `reporter` | agents/reporter/reporter.py:40+ | `reporter_node` | route_mode, step_results, question, _handoff | final_answer, _clarify | 是（多路径，多数命中透传/模板快速路径） | 否 | 否 | fixed → END | 结果汇总/短文案/L2 拒答追问 |
| `general_chat` | graph/general_chat_node.py:47-90 | `general_chat_node` | question, messages（最近 6 条） | final_answer | 是（主 LLM 直连，禁 RAG/工具） | 否 | 流式 emit | fixed → END | 寒暄/能力咨询直答 |

#### Skill 节点 12 个（自动发现，builder.py:166-174；`capability_registry.get_skill_nodes()`）

`sql_skill / rag_skill / report_skill / web_search_skill / web_crawl_skill / data_collection_skill / data_export_skill / email_skill / map_lookup_skill / travel_poi_skill / business_analysis_skill / competitor_analysis_skill`

各 Skill 包 `__init__.py` 自注册 `register_skill_node("<name>_skill", fn)`；统一经 `_make_sync`（builder.py:98-112）+ trace 包裹；**fixed edge → supervisor**（builder.py:171），经 Send payload（scheduler.py:266-277）读写 `step_results`。

#### 域图节点 5 个（自动发现，builder.py:154-163）

| node_id | route_mode(name) | adapter | 注册 | 出边 |
|---|---|---|---|---|
| `cs_graph_node` | customer_service | graph/cs_graph_node.py:25-68 | customer_service/register.py | → END |
| `travel_graph_node` | travel | graph/travel_graph_node.py:87-148 | travel/register.py | → END |
| `travel_commerce_graph_node` | travel_commerce（subflow=commerce） | travel/commerce/graph_node.py | travel/commerce/register.py | → END |
| `travel_booking_graph_node` | travel_booking（subflow=booking） | travel/booking/graph_node.py | travel/booking/register.py | → END |
| `selection_funnel_graph_node` | selection_funnel | graph/selection_funnel_graph_node.py:25-89 | selection_funnel/register.py | → END |

均 `with_domain_attribution` 包裹（domain_registry.py:90-105，builder.py:157）；commerce/booking/选品开关默认关（注册恒在，prefilter 把门）。

### 2.3 真实主图拓扑

```text
START
 ↓
router  ──(conditional route_selector, 依据 state["route_mode"])──┐
                                                                  ├─ direct            → tool_selector → skill_executor → reporter → END
                                                                  ├─ workflow          → workflow_executor → reporter → END
                                                                  ├─ general_chat      → general_chat → END
                                                                  ├─ clarify/handoff   → reporter → END
                                                                  ├─ customer_service  → cs_graph_node ────────────→ END
                                                                  ├─ travel            → travel_graph_node ─────────→ END
                                                                  ├─ travel_commerce   → travel_commerce_graph_node → END
                                                                  ├─ travel_booking    → travel_booking_graph_node ─→ END
                                                                  ├─ selection_funnel  → selection_funnel_graph_node→ END
                                                                  └─ 其他/默认(=plan)  → planner → critique ─┬→ reporter → END   (plan 空节点)
                                                                                                            └→ supervisor
                                                                                                               ├─ Send(step) → 12×Skill节点 → supervisor（回环，≤10 轮）
                                                                                                               └─ 无 ready → reporter → END
END
```

固定边：START→router、tool_selector→skill_executor、skill_executor→reporter、workflow_executor→reporter、planner→critique、每 Skill→supervisor、每域图→END、general_chat→END、reporter→END（builder.py:177-223）。

三条 conditional edge：`route_selector`（router 之后，读 route_mode，builder.py:197）、`route_after_critique`（plan 空→reporter 否则 supervisor，builder.py:65-71）、`route_after_supervisor`（读 `_ready_dispatch`，Send 并行，scheduler.py:217-288）。

### 2.4 recursion_limit / checkpointer

- recursion_limit：`MAIN_GRAPH_RECURSION_LIMIT` 默认 80（config/__init__.py:111），接线 runner.py:702、task_executor.py:339,368；选品子图独立 `SELECTION_FUNNEL_GRAPH_RECURSION_LIMIT`（selection_funnel_graph_node.py:43-48）。
- checkpointer：`graph/checkpointer.py:27-79`，`MAIN_GRAPH_CHECKPOINTER_ENABLED` 默认关；thread_id 每轮唯一 `agent-{session}-{ms}-{uuid8}`（runner.py:703-710）。

---

## 3. Routing Engine（路由真实决策流程）

### 3.1 router_node 真实流程（router_node.py:45-348）

```text
guard_result(router) 
 └─ router_node(state)
     1. 空 query → route_mode=plan
     2. cs_rule_hits_of（纯正则预判）
     3. is_cs_forced（domain_hint=cs 锁域, lock_domain.py:12-23）
     4. 非锁域：travel_pending → booking_pending → continuation → general_chat
     5. 锁域 redirect_main（CS_WINDOW_STANDALONE 放行, LLM 仲裁默认 OFF）
     6. 锁域→_try_cs_prefilter(forced)；非锁域+CS命中→_try_cs_prefilter（guide→handoff）
     7. run_domain_prefilters：旅游→选品→预订→商务（prefilter_chain.py:357-421）
     8. CS 兜底检测
     9. L1 入口弱命中追问（clarify）
    10. RoutingEngine.route()（缓存→六阶段；异常→安全 clarify）
    11. _handle_hierarchical_meta 分派
    12. understand_query（纯规则，禁止改写 mode）
    13. 写 update{route_decision, route_mode, ...}
 └─ route_selector(state)（router_node.py:350-376）：读 route_mode，纯字符串分派，不做任何分类/选工具/生成参数
```

优先级（代码注释冻结，router_node.py:60-71）：**锁域续跑/pending resolver → CS → 旅游 → 选品 → 预订 → 商务 → CS 兜底 → L1 追问 → 主 Router**。

### 3.2 RoutingEngine 六阶段（engine.py）

入口 `route()`（engine.py:160-203）：先查 Redis 路由缓存（fallback 与 clarify 不缓存，:320-329），未命中走 `_route_uncached`（:205-318）。

| 阶段 | 实现 | 输出对象 | 证据提供者 |
|---|---|---|---|
| entry_gate | 不在本引擎内——由 GraphRunner Input Guard 前置（engine.py docstring 1-6） | guard_result | 纯规则 |
| domain | DomainRouter.route（domain_router.py:63-103）：prefilter 映射 / domain_hint 锁域 / CoarseIntentClassifier（规则 hint→embedding 域心余弦+softmax→双阈值，domain_classifier.py:194-260） | DomainDecision（TypedDict, models.py:13-20） | rule + vector，**无 LLM** |
| intent | IntentRouter.classify（intent_router.py:48-176）：纯归一化，产出 kind ∈ workflow/composite/single/domain_graph/general/clarify/task | IntentDecision（models.py:45-59） | rule 为主，无 LLM |
| capability | CapabilityRouter.route（capability_router.py:25-78）→ HierarchicalRouter.select_tool（hierarchical.py:152-219）：pgvector 分数 + 规则强信号 → Fast Path；灰区标 `route_mode="llm_selection"` 但**此处不调 LLM**（收敛到 tool_selector 节点） | CapabilityDecision（models.py:23-31） | vector + rule；LLM 只标记不调用 |
| policy | ExecutionModeResolver.resolve（execution_mode.py:46-136）：纯规则优先级 override.clarify→override→general→domain_graph→top1→direct→多候选/兜底 plan | ExecutionModeDecision（frozen dataclass, models.py:65-82） | rule（override），无 LLM |
| route_decision | `_assemble`（engine.py:482-552）：组装 RouteDecision + routing_meta | RouteDecision（types.py:34-58） | — |

**全链路唯一路由 LLM**：`_llm_fallback`（engine.py:380-420）→ `LLMRouter.route`（llm_router.py:45-111，真实 invoke）。触发条件：①DomainRouter 抛异常；②domain source ∈ {degraded, fallback}；③CapabilityRouter 抛 VectorRouteError/异常；④capability 有 fallback_reason。LLM 再失败落 plan + rag.search + conf 0.3（llm_router.py:113-127）。

### 3.3 结论

- **Router 负责**：domain（规则+向量）、intent（规则）、capability/候选工具（向量+规则）、execution mode（规则）、confidence、evidence（source/reasoning）、clarify、guide/execute 分派（prefilter 命中后）。**不负责**：workflow 编排（只定 mode）、tool 最终选择（Fast Path 有 selected_tool hint，FC 决策在 tool_selector 节点）、参数生成。
- **route_selector 是纯 Evidence→Final Decision 的分派边**，不做二次分类/选工具/生成参数（router_node.py:350-376 全部逻辑是查表返回字符串）。
- **Tool Selection 与 Route Selection 有部分重叠但已分层**：RoutingEngine capability 阶段产出 selected_tool hint（Fast Path 三条件直通，hierarchical.py:195-207），灰区交给 tool_selector FC；两者共用 candidate_tools 通道，边界有注释约束（hierarchical.py:213-215）。

---

## 4. Route Decision State

### 4.1 统一 RouteDecision（存在，唯一决策对象）

`backend/orchestration/router/types.py:34-58`，Pydantic BaseModel：

| 字段 | 类型 | 说明 |
|---|---|---|
| execution_mode | Enum: direct/plan/workflow（types.py:16-25） | HOW |
| route_mode | Optional[str] | 最终归宿：direct/workflow/plan/clarify/domain_graph/general_chat（handoff 由 state 层写） |
| candidates | List[CapabilityScore]（name+score） | 候选 hint |
| confidence | float [0,1] | 整体置信度 |
| reason | Optional[str] | 判断依据 |
| workflow_name | Optional[str] | workflow 模式的 workflow 名 |
| routing_meta | Optional[dict] | 分层决策上下文（engine.py:514-543 组装：domain/domain_confidence/domain_margin/domain_second/domain_source/domain_action/route_mode/intent*/candidate_tools/fine_top1/tool_route_mode/selected_tool/need_clarification/clarification_reason 等） |

辅助契约：DomainDecision/IntentDecision/CapabilityDecision（TypedDict，models.py:13-59）、ExecutionModeDecision（dataclass，models.py:65-82）。

**RouteDecision 顶层没有** domain/subflow/capability/source/interaction_mode——它们在 `routing_meta` dict 里，由 router_node 展平进 State（graph/routing/hierarchical.py:25-38 `_hierarchical_state_fields`）。`interaction_mode` 字段**不存在**；subflow 只在 DomainDecision（models.py:17）。

### 4.2 路由相关 State 字段读写矩阵（state.py:65-213）

| State 字段 | 写者（代表） | 读者（代表） | 重复/备注 |
|---|---|---|---|
| route_decision（dict） | router_node:292-297；tool_selector 读改写候选；各 prefilter 置 None | tool_selector、capability_router.from_route_decision | 与平铺字段、四个快照构成**三代决策并存** |
| route_mode | router_node 各分支、prefilter/handoff/continuation 更新 | route_selector、edge_map、conversation_context | **域归宿与执行方式混在同一字段**（见 §17-A） |
| domain / domain_confidence / domain_margin / domain_source | 统一写入口 hierarchical.py:25-38（routing_meta 展平）；源头 engine.py:402-467 | domain_router 反派生、intent_router、trace | 单一展平口，无重复来源 |
| candidate_tools | engine.py:406,463,533；hierarchical.py:349 | tool_selector、capability_router | |
| selected_tool | engine.py:408,465,539；tool_selector.py:540 | hierarchical.py:181、conversation_context | |
| tool_arguments | tool_selector.py:541；hierarchical.py:33 恒置 None | （FC 成功后真正生效的是 resolved_params） | **准死字段**，与 resolved_params 语义重复 |
| resolved_params | tool_selector FC 成功 | skill_executor | |
| tool_route_mode | engine.py:538、tool_selector.py:542 | tool_selector fast_path 判断 | |
| need_clarification / clarification_reason | engine.py:540-541、hierarchical.py:146 | 澄清分支 | 与 `_clarify`（state.py:178）构成**双澄清通道** |
| executor_mode / executor_workflow / workflow_result | direct_executor:427,447 | runner 结果消费 | 与 route_mode 语义重叠（执行模式两处可写） |
| 四个 *_decision 快照 | engine + prefilter_chain:170-179 | router_trace、兼容消费方 | state.py:148 注释自认「旧字段继续为兼容事实源」 |
| _clarify / _handoff | router_node:230、handoff_update_for | events.py:104-112（SSE） | 必须入 schema 否则 LangGraph 剥离 |
| cs_context / travel_context / funnel_context / cs_pending_action / selection_blocked | 各域 prefilter/adapter | 对应域图节点 | |
| routing_context | runner 图外 assemble（routing_context.py:32-83，源 Redis） | router_node、continuation resolvers | |

---

## 5. Domain Inventory

| Domain | 注册位置 | 主入口 | Runtime 实现 | 独立 State | 独立 Graph | 内部 Supervisor | Workflow | Skill |
|---|---|---|---|---|---|---|---|---|
| customer_service | customer_service/register.py:10-15 | cs_graph_node（adapter）+ 前端锁域 domain_hint | build_cs_graph（graph_builder.py:126-171），9 节点 | CSGraphState（graph_state.py:76） | 有（独立 checkpointer，CS_CHECKPOINTER_ENABLED） | 有（v2 七层规则 + L7 LLM 兜底） | 无 | 经 experts 调 RAG 服务/工单服务（非主图 Skill 层） |
| travel | travel/register.py | travel_graph_node + 独立 REST/SSE `/api/travel/plan/stream`（routes/travel.py:323，绕过主图） | build_travel_graph（graph_builder.py:135-186），10 节点 | TravelGraphState（graph_state.py:46） | 有（独立 checkpointer + 三级状态可见性） | 有（**纯规则纯函数** decide，supervisor.py:129-210） | 无（本体即确定性 workflow） | 经 run_travel_tool 调 Tool（poi/transit/weather/budget/risk） |
| selection_funnel | selection_funnel/register.py:17-25 | selection_funnel_graph_node | build_selection_funnel_graph（graph_builder.py:66-100），7 节点线性漏斗 | SelectionFunnelState（graph_state.py:41） | 有，**无 checkpointer**（单轮任务） | 无 | 本体 = 固定 DAG workflow | 无（直接读 PG import_pool/watchlist） |
| travel_commerce / travel_booking | travel/commerce/register.py、travel/booking/register.py | 独立 graph_node，subflow 归 travel | 子流域图（默认关） | 各自 | 有 | — | — | — |
| general | 无注册——general_chat 是主图节点非域 | general_chat 节点 | 单节点 LLM 直答 | 共享 AgentState | 无 | 无 | 无 | 无 |
| rag / sql / report 等 | **不是 Domain，是平台能力**（capabilities.yaml domains 段的 9 个「粗域」是路由分类学，不是业务域运行时） | rag_skill/sql_skill 等 | Skill 层 | — | — | — | — | 12 Skill 承载 18 capability |

**平台能力 vs 业务域的判定**：capabilities.yaml `domains` 段（L60-219）的 knowledge/data/business/communication 等 9 个是**路由分类学粗域**（embedding prototypes + keywords），不产生域图运行时；真正的业务域运行时只有 domain_graph_registry 里注册的 5 个 graph node。目录名叫 `backend/agents/` 的 planner/reporter 不是 Domain，是主图节点实现。

### 客服真实调用链

```text
主图 router（domain_hint=cs 锁域 / CS prefilter 命中）
 → cs_graph_node（adapter：OrchestratorState → CSGraphState 转换 + 子图 invoke，异常兜底回复）
 → START → cs_state_loader（DB 快照 + Context Budget pin）
         → cs_pending_handler（确认/取消/超时，Command）
         → cs_supervisor（七层规则：handoff→pending→风险→循环→L4.5 分诊直出→意图路由→低置信→L7 LLM 兜底）
             ⇄ cs_knowledge_expert（RAG 问答，LLM）
             ⇄ cs_query_expert（规则预判 + LLM 意图分解）
             ⇄ cs_action_expert（纯规则动作提案）
             ⇄ cs_complaint_expert（规则 + LLM 兜底检测）
             ⇄ cs_handoff_expert（转人工，零 LLM）
         → cs_reporter（零 LLM 纯组装 + OutputGuard，direct_reply 直出）
 → END（直连，不经主图 reporter）
```

LLM 纪律：全部经 `backend.infra.llm` 代理（supervisor.py:665 注释）；component=customer_service 记账。

### 旅游真实调用链

```text
主图 router（travel prefilter 命中）→ travel_graph_node
 或 前端旅游页 POST /api/travel/plan/stream → routes/travel.py:323 → worker 内直接 get_travel_graph().invoke（绕过主图）
 → START → travel_slot_filler（纯规则抽槽；可选 LLM 补判默认关 TRAVEL_LLM_INTENT_ENABLED）
         → travel_supervisor（纯函数决策：意图门禁→brief 缺槽→poi→transit→weather→budget→risk→validate→repair）
             ⇄ poi_expert（真 Tool：LBS live 检索；PlanningAgent/ResearchAgent 名为 Agent 实无 LLM，planning_agent.py:4/research_agent.py:4）
             ⇄ transit/weather/budget/risk（真 Tool）
             ⇄ travel_validator（纯规则八轴；支持 interrupt() 人机决策）
             ⇄ travel_repair（纯规则确定性修复算子）
         → travel_reporter（纯模板组装）
 → END
```

判定：**没有真 LLM Agent，全部是 LangGraph Node + Tool 调用**；「Research/Planning Agent」是无 LLM 的纯函数组件。plan_version 版本链（brief_version/plan_version/parent_plan_version，repair.py:131）。

### 选品真实节点

`START → funnel_brief → funnel_pool → funnel_screen → funnel_verify → funnel_econ → funnel_rank → funnel_report → END`，条件边 `_route_after_stage`（graph_builder.py:60-66）做 need_info/empty_pool 短路直跳 report。全部节点零 LLM（stages/__init__.py:6「本包零 LLM——漏斗必须可复现」）：brief 纯规则抽槽、pool 读 PG、screen/verify/econ/rank 纯规则打分核算、report 纯渲染。

```text
selection_runtime_type = workflow（固定 DAG 线性漏斗 + 短路条件边）
```

（不用 supervisor、不用 planner、无 Agent autonomy、无 LLM 选步、非状态机——状态转移是单向线性的。）

---

## 6. Execution Modes（direct / workflow / plan）

### 6.1 direct

```text
route_selector（route_mode=direct）
 → edge_map 映射 → tool_selector（FC 选工具+参数解析；fast_path 直通；灰区 LLM FC；失败降级 clarify/单候选 passthrough）
 → skill_executor（capability → CAPABILITY_MAP → <skill>_skill 节点；伪造成 plan 单步契约调 BaseSkill.execute；final_answer=skill 原文/渲染直出）
 → reporter（runner 对 executor final_answer 覆盖语义，reporter 空跑）
```

- capability 确定：RoutingEngine capability 阶段（candidates）+ tool_selector 收敛。
- skill 确定：`tool_registry.get_node(capability)` → CAPABILITY_MAP（skills/registry.py:113-119），失败回退旧拼接 `f"{prefix}_skill"`（direct_executor.py:20-34）。
- tool 确定：**Skill 内部 `_select_tool`（skills/base.py:229-238）**，一 Skill 多 Tool 时按 capability/params.action 分发——plan 模式下 tool 选择者也在这里，LLM 不直接选 Tool。
- 参数生成：direct 模式 = tool_selector 的 **LLM function calling**（resolved_params，tool_schema.py:55-97 契约转换）；缺省回退 `{"question": ...}` 透传给 skill 内部 NL2SQL 等。
- 副作用审批：ensure_approved 接线 4 处（email.py:67 / export.py:29 / data_collection.py:62 / competitor.py:164）。
- `_USER_CAP_LABELS`（direct_executor.py:60-78）：17 capability 中文标签白名单，未知能力泛称「信息查询」防内部标识泄漏。

### 6.2 workflow

注册 4 个（workflows/__init__.py::register_all，L27-46；capabilities.yaml workflows 段 L1204-1233）：

| workflow_id | 用途 | Domain 归属 | 节点数 | 固定 DAG | LLM | 调 Skill |
|---|---|---|---|---|---|---|
| daily_report | 经营日报 | business | 6 Step / 4 并行层 | 是（@step depends_on） | 否 | 是（skill_adapter.call_sql/rag/report/email） |
| inventory_alert | 库存预警 | data | 8 Step | 是 | 否 | 是 |
| market_research | 品类调研证据管线 | business | collect→清洗→3 分组→fact_lock→报告 | 是 | 是（llm.invoke:72） | 经业务管线模块 |
| selection_decision | 选品决策（评估/决策分离） | selection | 9 Step 固定 DAG | 是 | 是（:136） | 读 competitor store，不走 capability 路由 |

Workflow 与 Skill 关系：经 `orchestration/workflow/skill_adapter.py` 直调 Skill 层，**不进主图 supervisor**。

**Selection Funnel 是否应归此类？** 它当前是**域图形态**（DomainGraph 注册 + 独立 State + 主图节点），但内容物与上面 4 个 workflow 同构（固定 DAG、零 LLM、纯规则）。差别：①funnel 有对话交互（need_info 追问、clarify 短路），4 个 workflow 无交互；②funnel 有跨轮上下文（ConversationContext 回写）；③funnel 有独立 State/Graph/前端专属页。所以它是「**业务域载体里的固定工作流范式**」——归 workflow 类是合理的，但它当前作为 Domain 注册不是错误，因为它承担了对话态管理。选品另有 selection_decision workflow（9 步 AI 评审团版）走 workflow_executor 路径——**两个「选品」并存，语义不同**（漏斗=可复现筛选管道；decision=LLM 评估决策），这是一个命名混淆点（P2）。

### 6.3 plan

```text
planner（LLM，输出 plan={nodes:{step_id:{capability,params}},edges:{step_id:[deps]}} = Capability DAG，非 Task List；只出 capability 不选 tool；5min LRU 缓存；后置规则：_filter_plan/_ensure_knowledge_step/_fallback_plan）
 → critique（规则 5 条 0ms → _auto_fix_plan → 复查仍有 issues 才调 LLM（prompt planner.critique）→ 深度超限 backstop 拒绝计划）
 → supervisor（纯规则：循环上限 MAX_SUPERVISOR_LOOPS=10、stale 300s 强判 failed、dep_failed→skipped、deps_met 且 get_worker 有节点→_ready_dispatch）
 → route_after_supervisor：Send(item["worker"], payload) 并行派发到 12 个 Skill 节点（worker 名=f"{skill.name}_skill"）
     previous_outputs 注入 = 前驱 step_results 经 compact_previous_outputs + 依赖感知压缩（scheduler.py:240-263）
 → Skill 节点执行 → 回 supervisor → 全 done → reporter
```

- 重试：Tool 级重试在 `BaseSkill.execute_with_retry`（skills/base.py:786）；步骤级降级在 supervisor + degradation.py；循环上限与 stale 在 supervisor。
- 失败降级：`execute_degradation`（scheduler.py:175-178，无 ready 且未全完成时）。

**归属判断**：planner/critique/supervisor 目前是**主图三个顶层节点 + 一条回环子拓扑**（builder.py:146-148 + 210 + Skill→supervisor 回环），在图结构上是主图核心架构；从职责语义上它们只服务 route_mode=plan 一条路径、其余模式全部绕过——即「Plan Runtime 的内部实现被展开在了主图顶层」。两者同时为真：**图结构上是主图节点，语义上是 Plan Runtime 内部**。这正是「Planner 实现泄漏到主图」的准确表述。

---

## 7. Domain Runtime 与主图耦合

- **主图是否显式知道每个域名？** builder.py **不硬编码域名**——遍历 `domain_graph_registry.get_all()` 注册（:154-163）+ edge_map（:179-183）+ 连 END（:189-191）。但 **router 层知道**：`_PREFILTER_DOMAIN_MAP`（domain_router.py:148-169）、`_ROUTE_MODE_FAMILY`（prefilter_chain.py:240-246）、`_ENTRY_MODE_SWITCHES`（:248-252）、`_ONE_SHOT_TRAVEL_RE`（:257-260）、redirect 逻辑（lock_domain.py）、`_hierarchical_state_fields` 对 prefilter 域的展平——**域名以映射表形式散落在 router/prefilter 层 5+ 处**，新增域要动这些表（有 G1/G2 派生约束缓解，但不是零注册面）。
- **存在 Domain Registry**：`DomainGraphRegistry`（domain_registry.py:109-208，单例 :208）；数据类 `DomainGraph(name/node_name/label/adapter/subflow/decision_subflow/domain)`（domain_graph.py:13-44）；归属派生方法（route_mode_to_active_domain 等 :136-205）。**不存在**名为 DomainRuntimeRegistry/Subgraph Registry 的抽象——DomainGraph.adapter 函数就是全部的 runtime 契约（一个签名 `(state) -> dict` 的节点函数）。
- **Runtime 契约现状**：域图接入的契约 = ①adapter 节点函数 ②独立 TypedDict State ③route_mode 命名约定 ④`_clarify/_handoff` 等 AUX 键约定。**没有统一的域执行结果协议**（各域 reporter 输出形态各自定义），也没有统一的域生命周期/失败降级接口（各 adapter 自带 try/except 兜底回复）。

---

## 8. Skill / Capability / Tool Architecture

### 真实依赖图（按 import 实证）

```text
Planner / ToolSelector / Supervisor
        │ 读
        ▼
orchestration/capability_registry ──延迟读──> skills/registry._instances（12 Skill）
        │                                          │ bind_manifest_metadata ← capabilities.yaml（唯一事实源）
        ▼                                          ▼
   manifest(capabilities.yaml)              backend/skills/*（BaseSkill.execute → _select_tool → execute_with_retry）
                                                   │ 仅 import backend.tools.*（无反向，grep 零命中）
                                                   ▼
                                            backend/tools/*（39 @tool，tool_registry 注册，_base.ok/fail/not_configured）
                                                   │
                              ┌────────────────────┴────────────────────┐
                              ▼                                          ▼
                  backend/infrastructure（llm/db/http/lbs/redis）    infra/mcp_client（仅 train/zhihu 两 Tool）
```

1. **Capability 是业务能力声明**，不是 Tool alias：`<域>.<动作>` 全域唯一，承载 description/params_schema/planner_examples/routed/risk_level/fast_path/rule_keywords/calibrate_signals（capabilities.yaml:222-1202，18 项），是 Planner prompt、critique、tool_selector 校验、tool_schema 转换的共同事实源。
2. **Skill 是执行逻辑封装**（含流程状态、多 Tool 分发、重试、输出归一），不是纯 Tool group；一个 Skill 可承载多个 capability（如 email 四个）。
3. **Tool 是最小执行单元**：无状态、`@tool`、返回 JSON 字符串、统一封套、副作用过审批门。
4. **SkillExecutor 执行的是 Skill**（`_run_skill_step` 伪造 plan 单步契约调 BaseSkill.execute），不是直接执行 Tool；Tool 在 Skill 内部被选中执行。
5. **MCP 是 Tool 的第二出口/数据源，不在核心 Runtime**：对外 = mcp_servers/ 的 RAG/SQL FastMCP server（独立服务）；对内 = infra/mcp_client.py 薄客户端只被 train/zhihu 两个 Tool import；skill_executor/supervisor 零 import。
6. **ToolSelector 存在**：`orchestration/graph/tool_selector.py`（FC 决策节点，direct 专属）。与三者的边界：RoutingEngine 出候选+hint（Fast Path 可直通 selected_tool）；tool_selector 做最终 FC 选定+参数生成（仅 direct 模式，非 direct 直通）；SkillExecutor 只消费结果；plan 模式的 tool 选择在 Skill._select_tool（规则分发，非 LLM）。
7. **已知跨层借用**（P2 记录）：direct_executor.py:93 摸 `tool_registry._get_skill_registry()` 私有方法；direct_executor.py:315 import reporter 的 `_render_table_section`；workflows 经 skill_adapter 绕过 capability 路由（设计如此，有注释）。

---

## 9. Reporter / Response Layer

### 各响应出口全景

| 出口 | LLM | 机制 |
|---|---|---|
| 主图 reporter（agents/reporter/reporter.py） | 是，但多路径省 LLM：结构化路径 LLM 只写 64 token 摘要（L285-304）；RAG 透传快速路径（L243-261）；单步骤透传（L266-271）；全失败拒答模板（L211-237）；完整 LLM 路径 = reporter.system prompt（L307-353，ENABLE_TOKEN_STREAMING 时流式） | step_results 合并渲染 + 引用提取（_extract_rag_references:418-457）+ done 帧 sources/answer_status/confidence/reply_source |
| skill_executor 直出 | 否 | `_coerce_final_answer`：skill 原文/SQLResult Markdown 渲染（复用 reporter._render_table_section，无 LLM），runner 覆盖语义 |
| workflow_executor 直出 | 否 | 报告类直取 report_md 全文；显式注释「防 reporter 覆盖」（direct_executor.py:405） |
| general_chat | 是 | 直答，reporter 绕过（直连 END） |
| cs_reporter | 否 | _assemble_answer 优先级直出（direct_reply→handoff→pending→draft→action_result）+ OutputGuard |
| travel_reporter | 否 | 纯模板拼装（_render_itinerary 等） |
| funnel_report | 否 | 纯渲染 |

### 二次生成判定

**不存在 Domain Reporter → Main Reporter 二次生成**：域图节点直连 END（builder.py:206-207 注释「域图自带 reporter，直接到 END」），主图 reporter 被结构性绕过；runner 从域图输出直接取 final_answer（runner.py:744-764，注释含「曾漏掉 travel_graph_node」事故记录）。direct/workflow 模式 executor 答案就位后 reporter 空跑（图边仍在但 LLM 不触发）。

**残余风险（P2/P3 级）**：①引用合并只在主图 reporter 做，域图/skill 直出路径的引用处理各自为政（RAG 引用靠 skill 文本内嵌 + done 帧兜底 parse_sources_from_text）；②reply_source 白名单对域图步骤返回 None 不标注（events.py:488-527）；③reporter L2 拒答追问（_refusal_clarify_marker）是隐藏交互行为，在 Response Layer 里做会话控制（越界但可控）。

---

## 10. Guide / Clarify

### guide/execute

- 存储形式：**不是 route_mode/execution_mode 的值，也不是 LangGraph 分叉**，而是三个独立 sys_config 开关 `{CS,TRAVEL,SELECTION}_GLOBAL_ENTRY_MODE`（execute|guide，默认 execute，prefilter_chain.py:248-272），作用于 prefilter 命中后的分派判定 `entry_mode_verdict`（:275-293）。
- guide → `handoff_update_for`（:296-323）→ route_mode="handoff" + `_handoff=HandoffPayloadV1`（contracts/handoff.py:70-88，extra=forbid，含 v/target_domain/reason/params/text）→ route_selector → reporter 短路输出引导文案 → SSE `handoff` 帧（events.py:198-231，指标 agent_handoff_total{phase=shown}）→ 前端 HandoffCard 三入口带参跳转（HandoffCard.tsx:55-78，埋点 POST /api/observability/handoff/click）。
- execute → 照常进域图 route_mode → 域图节点 → END。
- 一次性查询 passthrough（_ONE_SHOT_TRAVEL_RE）不受 guide 影响；客服窗口锁域不受模式开关影响。
- **结论：guide/execute 属于产品交互策略**（入口引导 vs 就地执行），不是 Runtime 类型；它复用了 route_mode="handoff" 这个通道，是 route_mode 语义过载的又一来源。

### clarify

- 5 个产生点（router_node.py:194-234 L1 弱命中 / :276-286 引擎异常安全澄清 / intent kind=clarify / hierarchical meta action=clarify / CS InputGuard）。
- **零 LLM**：clarify 内容全部纯规则/策展选项（clarify_content.py 模块 docstring「零 LLM」）。
- 持久化：`_clarify` 入 OrchestratorState schema（state.py:174-178）发 SSE clarification 帧；追问问题写 Redis ConversationContextRepository（set_pending_question）；防循环计数在缓存（30min 同问去重 + 10min 会话 2 次封顶，clarify_allowed:244-261）。
- 用户回答后**完整重跑 router_node**（重新路由），pending_question 进 routing_context 供 ContinuationResolver 拉回活跃域；选项点击原样重发（consume_clarify_click）。
- 不进 LangGraph checkpoint（主图 thread_id 每轮唯一，跨轮靠 Redis routing_context 不靠 checkpoint）。
- **结论：clarify 属于 Conversation Control State**（会话控制），不是 Runtime——它是路由失败/低置信的一种归宿，且独立于 Runtime 存在（域图内部 CS 也有自己的 clarify 通道 `_clarify`）。

---

## 11. State Contract（主 State）

主 State = `OrchestratorState(AgentState)`（state.py:65-213，TypedDict；AgentState 基座 + OrchestratorState 扩展）。全部字段见 §2.2 与 §4.2 矩阵。要点：

- **三代路由决策表示并存**：route_decision(dict) → 平铺字段（domain/route_mode/candidate_tools/…）→ 四个 *_decision 快照。state.py:148 注释自认兼容现状。
- **语义重复组**：tool_arguments vs resolved_params（前者准死）；executor_mode/executor_workflow vs route_mode；need_clarification vs _clarify 双通道；route_mode 一字段承载执行方式+域归宿+交互控制三种语义。
- **键守卫**：KNOWN_STATE_KEYS + validate_state_update（state.py:219-238），P1-1 状态键守卫接线 trace_middleware（state_unknown_key_total 指标）；schema 外键被 LangGraph updates 剥离（多处事故注释）。
- CS 子图独立 State（CSGraphState），经 adapter 显式转换；Travel/Funnel 同型。

---

## 12. Trace / Observability

- **routing_meta 不整体入 trace**，仅低基数摘要投影：`router_trace.py:19-62 record_router_decision` 把 domain/subflow/capability/mode/confidence/source/intent_* 写 `trace.metadata["router"]`。
- **已投影**：prompt_versions（tracer.py:537-539 metadata + trace_middleware bind_prompt_versions）；Tool span contract_hash（skills/base.py:403-412）；llm_usage 三列 skill_id/tool_id/agent_domain（LLMAttribution ContextVar，llm_context.py:28-65；写入点 skills/base.py:130-132 / tool_runtime/executor.py:56-58 / domain_registry.with_domain_attribution builder.py:157）。
- **未投影（记录待补）**：runtime_type、workflow_id（有 workflow_name 进 trace_store 列）、tool_version（有 contract_hash 可反查）、confidence 在 span 级。
- Trace 存 PG：单表 trace_id PK + data 整条 JSON + 6 个提升列（trace_store_pg.py:90-110，migration 012）。
- Trace metadata 示例（按代码构造逻辑推导）：`trace.metadata = {"router": {domain, subflow, capability, mode, confidence, source, intent_kind, ...}, "prompt_versions": {...}}`；`trace.tags = {tenant_id, user_id, prompt_versions}`。
- 成本：账本（llm_usage PG）→ cost_gauge.py 单向投影成 `llm_usage_cost_cny_24h/month{domain,model}` gauge（600s），Prometheus 不独立记账。
- 路由指标族：routing_domain_total / routing_hierarchy_verdict_total / routing_latency_seconds / metadata_route_total / agent_handoff_total / cs_handoff_total / state_unknown_key_total（metrics.py 各处）。

---

## 13. SSE Contract

14 种 event（event_schema.py，CORE 6 + AUX 7 + ping）：

| event | 关键字段 | 备注 |
|---|---|---|
| meta | request_id, node_labels | 首帧唯一，HTTP 层注入 |
| status / log | node, ts | **log.node = LangGraph node_id** |
| delta | content | 流式 |
| done | elapsed, sources, answer_status?, confidence?, reply_source? | 尾帧（与 error 互斥） |
| error | message | 尾帧 |
| todo / usage / file / clarification / context / thinking | — | AUX 任意位置 |
| handoff | v, target_domain | HandoffPayloadV1 校验 |
| ping | ts | 不计帧序 |

**前端对 node_id/event 的依赖**（真实清单）：
- `frontend/src/components/cs/constants.ts:5-43`：硬编码 `cs_graph_node` + CS 子图节点名（有后端守护测试 test_frontend_node_ids_consistency.py）。
- `frontend/src/components/travel/travelDisplay.ts:129-168`：硬编码 travel 子图 10 节点序列及 capability 标签。
- `frontend/src/store/chat.ts:226-274`：按 `evt.event` 字符串分派 13 类；handoff 帧入 store。
- `frontend/src/hooks/useSSE.ts:111-115`：消费 done 的 answer_status/confidence/reply_source。
- `frontend/src/hooks/useCSChat.ts:46`：domain_hint=customer_service。
- HandoffCard.tsx:55-78：三入口带参跳转（travel query / selection-funnel query / CS sessionStorage+事件）。
- frontend-admin：无 node 名硬编码（agents 页从 /agents 动态渲染）。

改 node_id/域图节点名/节点序列的前端破坏面集中在上述两文件 + 守护测试；改 event 名破坏面大（~224 测试文件含 event 名，schema 测试硬编码帧名）。

---

## 14. Checkpoint Coupling

三处 checkpointer（主图 checkpointer.py:30-83 / CS graph_builder.py:193-256 / Travel graph_builder.py:188-262）：同 PG 表、主图与 CS production fail-loud（degrade_or_raise），**Travel 不 fail-loud**（warning + MemorySaver 降级，与另两处不一致，AGENTS.md 已知挂账）。

LangGraph checkpoint（langgraph 1.1.10 / checkpoint 4.2.0）序列化内容**包含 pending sends（task 列表）与节点名**——worker node 名与 Send payload 均进 checkpoint blob。interrupt/Command 使用点：CS 全 Command(goto) 无 interrupt（pending_handler.py、supervisor.py:774-810）；Travel supervisor Command(goto) + validator `interrupt()`/`Command(resume)`（validator.py:607-730，要求 checkpointer 可用）。

**若未来包裹 planner/critique/supervisor 或改域调度，风险清单**（只列不实施）：
1. Send payload 是手抄平铺键集（scheduler.py:266-285）——包裹后新增键不进 payload 即 worker 侧 None。
2. 节点名/step_id 已编入 checkpoint task 与 span_id（trace_middleware.py:150、skills/base.py:399-401 硬编码 parent 候选）——改名/包裹使旧 checkpoint 无法 resume、trace 父子链断裂。
3. Travel validator interrupt 依赖节点路径稳定（validator.py:628 注释 resume 后从头重跑）——外层再包裹导致旧 pending 线程 resume 失败。
4. `_handoff/_clarify/cs_pending_action/_ready_dispatch` 等「从节点输出读取」的消费方（events.py:111）——若改为从 state 读，未登记 schema 即 None。
5. 主图 thread_id 每轮唯一——跨轮 resume 需改 thread 构造。
6. history thread 复用旧 checkpoint 的场景（Celery 断点续跑 task_executor.py:339,368）对节点拓扑变化最敏感。

---

## 15. Test Coupling

backend/tests/ 统计（grep 实测）：

| 耦合类型 | 文件数 | 代表文件 |
|---|---|---|
| node_id 字符串精确匹配 | **33** | test_trace_middleware.py、test_observability_topology.py、test_task_phase2_recovery.py、api/test_registry_overview_api.py |
| node 名宽松出现 | 199 | tests/customer_service/*、tests/orchestration/** |
| 依赖 route_mode 字段 | 36 | orchestration/graph/test_clarify_flow.py 等 |
| 依赖 route_decision | 16 | — |
| 依赖 RouteDecision 类 | 7 | router 单测 |
| 依赖 SSE event 名 | 224 | test_sse_event_schema.py、test_entry_mode_handoff.py、test_cs_supervisor.py 等 |

结论：node_id 改名破坏面 ≥33 文件；state 路由字段 ≥36；SSE event 名最大（~224，多数经共享常量可缓冲，schema 测试硬编码是硬门）。**「不改节点 ID、不破坏 SSE/checkpoint 的渐进治理」具备现实可行性**，主要抓手是 route_mode 语义分层与 State 字段收敛，两者都有大量测试锚点但可通过新增字段+兼容读实现。

---

## 16. Component Classification（按真实代码，不按命名）

| 当前组件 | 当前命名 | 实际分类 | 证据 |
|---|---|---|---|
| customer_service | 域图 / agent | **Business Domain + Agent Subgraph** | register.py + graph_builder.py（supervisor 规则+LLM 兜底 + 5 子 Agent） |
| travel | 域图 / agent | **Business Domain + 确定性 Workflow（状态机调度）** | supervisor 纯函数、执行链零 LLM、真 Tool、interrupt 人机回路 |
| selection_funnel | 域图 | **Business Domain + Workflow（固定 DAG）** | 7 节点线性漏斗、全零 LLM、无 supervisor |
| travel_commerce / travel_booking | 子流域图 | Business Domain subflow + Workflow/Agent Subgraph（默认关） | register.py |
| general_chat | 节点 | Response Layer（直答）+ Interaction Control 分支 | general_chat_node.py |
| router + route_selector | 节点/边 | **Decision Engine（Router）** | engine.py 六阶段 |
| tool_selector | 节点 | Decision Engine（Tool 选择，FC）+ Executor 前置 | tool_selector.py |
| skill_executor | 节点 | **Executor**（direct 执行体） | direct_executor.py |
| workflow_executor | 节点 | **Executor**（workflow 执行体） | direct_executor.py:360 |
| planner / critique / supervisor | 节点 | **Plan Runtime Internal**（图结构上是主图节点） | builder.py:146-148 + §6.3 |
| 12 个 Skill 节点 | 节点 | Executor（plan 模式并行执行单元） | builder.py:171 |
| reporter | 节点 | **Response Layer**（LLM Reporter，多路径省 LLM） | reporter.py |
| RAG / SQL 等 12 Skill | skill | **Platform Capability（Skill 层）** | skills/registry.py |
| 39 Tool | tool | Tool（最小执行单元） | tools/* |
| mcp_servers（RAG/SQL） | server | Platform Service（对外暴露，不进主图） | servers/__init__.py |
| mcp_client（12306/知乎） | client | Tool 数据源适配层 | infra/mcp_client.py |
| 4 workflow | workflow | Workflow（固定 DAG，经 workflow_executor） | workflows/__init__.py |
| Input Guard / guard_node_update | — | Interaction Control / 准入门 | builder.py:140 |
| clarify / handoff | route_mode 值 | **Interaction Control**（会话控制/引导策略） | §10 |
| RoutingEngine | engine | Decision Engine | engine.py |
| DomainGraphRegistry | registry | Domain Registry（声明式，但 runtime 契约薄弱） | domain_registry.py |

---

## 17. Architecture Findings（P0-P3 诊断，不含实施方案）

### P0（已存在执行歧义/错误风险）

- **A. Domain 与 Execution Runtime 同层混合（确认存在）**：`route_mode` 单字段承载 {direct, workflow, plan, general_chat, clarify, handoff} ∪ {customer_service, travel, travel_commerce, travel_booking, selection_funnel}；`route_selector` 对两类语义做同构分派。后果：新增执行形态必须挤进同一个字段/同一个条件边；`_ROUTE_MODE_FAMILY`、`_PREFILTER_DOMAIN_MAP` 等映射表需要在 router 层逐一认识每个域。这是本次审计确认的最核心结构债。（证据：builder.py:182-195、router_node.py:350-376、prefilter_chain.py:240-252）
- **F. 多个 Route/Mode 字段表达同一概念（确认存在）**：route_decision dict vs 平铺字段 vs 四个 *_decision 快照三代并存（state.py:76-154）；tool_arguments vs resolved_params；executor_mode/executor_workflow 与 route_mode 双写；need_clarification 与 _clarify 双澄清通道。当前有展平函数和守卫压着，但每新增一个路由语义要写 3 处。

### P1（长期扩展会明显恶化）

- **C. Planner 实现泄漏到主图（确认存在）**：planner/critique/supervisor 是主图三个顶层节点 + Skill→supervisor 回环，但语义上只是 plan 模式的内部实现；主图拓扑被迫为单一执行模式承担 3 节点 + 12 Skill 节点 + 回环边，主图「顶层复杂度 = 最复杂路径的复杂度」。
- **H. Runtime 没有统一 Contract（确认存在）**：域图接入契约 = adapter 函数签名 + State TypedDict + route_mode 命名约定 + AUX 键约定，四者全是隐式约定；无统一的域执行结果协议/失败降级接口/生命周期接口（各 adapter 自带 try/except 兜底）；workflow 与域图两种「固定流程」无共同抽象。
- **B. Domain 与 Agent 强绑定（部分存在）**：注册模型是「一个 Domain 一个 Graph Node」，无 Graph 的域（如 general）无法用同一模型表达；travel 有 3 个 graph node（travel/commerce/booking）靠 subflow 字段区分，说明模型已经开始为一个域挂多个节点打补丁。
- **D. Tool Selection 与 Route Selection 重叠（部分存在，已分层缓解）**：RoutingEngine capability 阶段产出 selected_tool hint 且 Fast Path 可直接定死，tool_selector 再做 FC；两条决策链共用 candidate_tools/selected_tool/tool_route_mode 字段通道，语义耦合在 State 层。
- **G. 主图硬编码 Domain（结构性存在，非字面硬编码）**：builder 不写域名字面量，但 router/prefilter 层 5+ 处映射表（_ROUTE_MODE_FAMILY/_PREFILTER_DOMAIN_MAP/_ENTRY_MODE_SWITCHES/_ONE_SHOT_TRAVEL_RE/lock_domain redirect）需要感知每个域名，新增域的改动面不在注册处而在路由层。

### P2（命名/边界问题）

- **I. Workflow / Agent / Skill 没有统一执行结果协议**：域图 final_answer 自由文本、workflow report_md、skill JSON 封套三种形态；reply_source 对域图步骤为 None；引用合并仅主图 reporter 一处。
- 两个「选品」并存：selection_funnel 域图（规则漏斗）与 selection_decision workflow（LLM 评审团）同名异义。
- travel PlanningAgent/ResearchAgent 名为 Agent 实为无 LLM 纯函数组件（planning_agent.py:4 注释自述）——符合「不因文件名判断」的反例。
- 跨层借用：direct_executor 摸私有注册表（:93）、借 reporter 内部函数（:315）。
- Travel checkpointer 不 fail-loud，与主图/CS 策略不一致（AGENTS.md 已挂账）。
- route_mode 值 "direct" 在 edge_map 里映射到 "tool_selector" 节点（builder.py:184）——mode 名与节点名不一致，历史痕迹。

### P3（文档表达问题）

- selection_funnel/register.py:11-16 注释自述 prefilter「暂未接线」，实际 prefilter_chain.py:357 已接线——注释滞后。
- 「主图 9 核心节点」口径不含 12 Skill 节点与 5 域图节点，对外表述易被误解为全图规模。

---

## 18. Runtime Matrix（最终表）

| Domain | Runtime Type | Execution Mode | Main Entry | Internal Orchestration |
|---|---|---|---|---|
| general | generic_runtime（单节点直答） | general_chat（route_mode 直达） | router→general_chat 节点 | 无（单节点 LLM） |
| customer_service | **agent_subgraph**（hybrid 偏 agent） | domain_graph（route_mode=customer_service；锁域 domain_hint 优先） | cs_graph_node adapter | 规则七层 supervisor + LLM 兜底 ⇄ 5 子 Agent → cs_reporter |
| travel | **workflow**（状态机调度的确定性 workflow，hybrid：interrupt 人机回路） | domain_graph（route_mode=travel；另绕过主图的 /api/travel/plan/stream） | travel_graph_node adapter / REST | 纯函数 supervisor ⇄ 5 Tool 子节点 + validator/repair 循环 → travel_reporter |
| selection | **workflow**（固定 DAG 线性漏斗） | domain_graph（route_mode=selection_funnel；/selection-funnel 专属页；另 selection_decision workflow 走 workflow_executor） | selection_funnel_graph_node adapter | 无 supervisor，brief→pool→screen→verify→econ→rank→report 线性 + 短路条件边 |

---

## 19. 12 Final Answers

1. **当前主图是否混合了 Domain 和 Execution Runtime？** 是。混合点在 `route_mode` 字段与 `route_selector` 条件边（域归宿与执行方式同字段同层分派）；图结构层（builder 自动布线）与运行时层（独立 State/Graph/adapter）已分离。
2. **CS 是否应被视为 Business Domain + Agent Runtime？** 是。它是唯一具备「supervisor 动态决策 + 子 Agent 委派」特征的域，agent_subgraph 判定成立（规则为主、LLM 为兜底）。
3. **Travel 是否应被视为 Business Domain + Agent Runtime？** Business Domain 成立；**Agent Runtime 不成立**——执行链零 LLM、supervisor 纯函数、无 LLM 自主决策，是状态机调度的确定性 workflow（interrupt 人机回路是交互特性不是 agent 特性）。
4. **Selection 是否实际上是 Business Domain + Workflow Runtime？** 是。7 节点固定 DAG 线性漏斗、全零 LLM、无 supervisor/planner，`selection_runtime_type = workflow`。
5. **direct/workflow/plan 是否本质属于 Execution Mode？** 是。`ExecutionMode` 枚举（types.py:16-25）明确为此设计；问题是 route_mode 里还混进了 domain_graph/general_chat/clarify/handoff 四种非执行模式归宿，说明「Execution Mode」与「Route Outcome」未分离。
6. **planner/critique/supervisor 是否只是 Plan Runtime 内部实现？** 语义上是；图结构上不是（主图顶层节点+回环）。准确表述：Plan Runtime 内部实现被展开在了主图顶层（泄漏）。
7. **skill_executor 是否属于 Executor Layer，而不应和 Domain 平级？** 是。它与 tool_selector/workflow_executor 同属 Executor 层，与五个域图节点在 route_selector 的候选集里平级是混合层的表象；归位后 route_selector 的候选集应只剩「执行模式」与「域归宿」两个正交维度。
8. **reporter 是否存在二次生成问题？** 不存在。域图直连 END 绕过主图 reporter；direct/workflow executor 直出且 runner 覆盖语义；reporter 自身有多条省 LLM 透传路径。残余的是引用合并与 reply_source 覆盖不一致（P2）。
9. **guide/execute 是否主要属于 Interaction Mode？** 是。三个入口模式开关作用在 prefilter 分派层，handoff 是产品引导行为（SSE 卡片+前端跳转），不改变 Runtime 类型；它借道 route_mode="handoff" 是实现细节而非语义归属。
10. **clarify 是否主要属于 Conversation Control？** 是。零 LLM、Redis 持久化、下轮重路由、防循环计数，全是会话控制语义；且与 Runtime 无关（路由失败归宿 + CS 域内也有自己的 clarify 通道）。
11. **当前是否已有足够基础抽象出统一 Runtime Contract？** 基础存在但不完整：已有 RouteDecision 统一决策对象、DomainGraphRegistry 声明式注册、独立 State/adapter 边界、SSE 契约校验门；缺的是①route_mode 双语义拆分、②域执行结果/失败降级统一协议、③routing_meta 强类型化、④State 三代决策表示收敛。
12. **能否在「不改节点 ID、不破坏 SSE/checkpoint」前提下渐进实施？** 可以。约束面已量化：node_id 测试锚 ≥33 文件（集中在 frontend constants 有守护测试的两处）、SSE event 名 ~224、checkpoint 里编入节点名与 Send payload。渐进路径的现实抓手是：State 新增字段+兼容读（不删旧字段）、route_mode 保持原值但增加正交字段表达另一维度、域 runtime 契约先文档化再接口化——全部不需要动节点 ID/SSE 帧/checkpoint 格式。风险集中在 Send payload 手抄键集与 travel validator interrupt 路径（§14 清单）。

---

## 20. Evidence Index（关键证据速查）

| 判断 | 证据 |
|---|---|
| 主图 9 内置节点 | backend/orchestration/graph/builder.py:140-151 |
| 12 Skill 节点自动发现 | builder.py:166-174；orchestration/capability_registry.py:78-95 |
| 5 域图节点自动布线+直连 END | builder.py:154-163, 179-191, 205-208 |
| route_selector 纯分派 | orchestration/graph/router_node.py:350-376；builder.py:182-195 edge_map |
| RoutingEngine 六阶段+唯一 LLM fallback | orchestration/router/engine.py:36-43, 160-203, 205-318, 380-420 |
| RouteDecision schema | orchestration/router/types.py:34-58（枚举 :16-25）；models.py:13-82 |
| 三代决策字段并存 | orchestration/state.py:76-154（:148 注释自认） |
| guide/execute 开关与 handoff 契约 | orchestration/graph/routing/prefilter_chain.py:240-323；orchestration/contracts/handoff.py:70-88 |
| clarify 零 LLM+Redis 持久化 | orchestration/graph/clarify_content.py（模块 docstring）；router_node.py:216-226 |
| CS 9 节点+规则七层 supervisor | backend/customer_service/graph_builder.py:126-171；supervisor.py:309-504, 631-698 |
| Travel 10 节点+纯函数 supervisor+零 LLM 执行链 | backend/travel/graph_builder.py:135-186；supervisor.py:129-210；planning_agent.py:4 |
| 独立入口绕过主图 | backend/app/api/routes/travel.py:323 |
| Selection 7 节点固定 DAG 零 LLM | backend/selection_funnel/graph_builder.py:60-100；stages/__init__.py:6 |
| plan 链 DAG/Send/循环上限 | backend/agents/planner/planner.py:103-111；supervisor/scheduler.py:53-288（MAX_SUPERVISOR_LOOPS:22） |
| tool_selector FC 决策 | orchestration/graph/tool_selector.py:386-554, 567-642 |
| Skill._select_tool Tool 分发 | backend/skills/base.py:229-238；execute_with_retry:786 |
| MCP 不进主图 | backend/servers/__init__.py；infra/mcp_client.py 头注；tools/travel/train.py、tools/search/zhihu.py |
| 域图绕过主图 reporter（无二次生成） | builder.py:205-208；runner.py:744-764；direct_executor.py:405 |
| reporter 多路径省 LLM | backend/agents/reporter/reporter.py:211-271, 285-353 |
| SSE 14 事件+帧序门 | orchestration/graph/event_schema.py:30-139, 186-235 |
| 前端 node 硬编码两处 | frontend/src/components/cs/constants.ts:5-43；travel/travelDisplay.ts:129-168 |
| checkpoint 三处差异+Travel 不 fail-loud | graph/checkpointer.py:30-83；customer_service/graph_builder.py:193-256；travel/graph_builder.py:188-262 |
| interrupt/Command 使用点 | customer_service/pending_handler.py:39,94-106；travel/validator.py:607-730 |
| Send payload 手抄键集 | supervisor/scheduler.py:266-285 |
| 测试耦合统计 | §15（grep -l 实测数字） |
| trace 投影边界 | orchestration/router/router_trace.py:19-62；observability/tracer.py:537-539；skills/base.py:403-412 |
| llm_usage 三列归因 | observability/llm_context.py:28-65；domain_registry.py:90-105；builder.py:157 |
| cost_gauge 投影 | backend/observability/cost_gauge.py:35-51 |

（UNKNOWN 项：无。所有问题均有代码证据；前端 admin 侧无 node 依赖为 grep 全量确认。）
