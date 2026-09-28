# Architecture Simplification — STOP B 实施前代码审计

日期：2026-09-28
阶段：STOP B / Step 0
状态：审计完成；本阶段未修改生产代码

## 1. 审计范围与结论

本次只读审计覆盖：

- `backend/orchestration/graph/router_node.py`
- `backend/orchestration/router/types.py`
- `backend/orchestration/router/hierarchical.py`
- `backend/orchestration/router/router.py`
- `backend/orchestration/router/rule_router.py`
- `backend/orchestration/router/vector_router.py`
- `backend/orchestration/router/llm_router.py`
- `backend/orchestration/workflow/router.py`
- `backend/orchestration/graph/*_prefilter.py`
- `backend/orchestration/state.py`
- `backend/orchestration/domain_registry.py` 及当前域图注册入口

结论：当前已经存在两条可运行的路由架构（`hierarchical` 与 `legacy`），`router_node` 还同时承担入口门禁、跨轮延续、域图 prefilter、路由调用、问题理解、状态展平和下一节点选择。STOP B 可以采用适配器收口，但必须以“复用既有实现、保持旧 state 输出、异常回退 legacy”为前提。当前没有 `DomainDecision`、`CapabilityDecision`、`ExecutionModeDecision` 这三个统一决策对象。

工作区审计时发现的既有未提交改动仅为三个前端 `tsconfig.json` 文件；本次没有触碰、暂存或覆盖这些改动。

## 2. `router_node` 当前真实调用链

`router_node(state)` 的实际顺序如下，不能在 STOP B 中隐式改序：

1. 从 `state["question"]` 或 `state["query"]` 取 query；空 query 直接返回 `route_mode="plan"`。
2. 计算客服规则命中数，读取 `domain_hint`，识别客服窗口锁域。
3. 非锁域入口依次尝试：
   - `travel_pending_resolver.resolve_travel_pending`
   - `_try_continuation`（客服/旅游/选品跨轮延续）
   - `_try_general_chat`（问候/能力咨询）
4. 客服路径：
   - 锁域且未 `redirect_main`：`try_cs_prefilter(..., forced=True)`；
   - 全局入口客服规则命中：`try_cs_prefilter(..., forced=False)`。
5. 域锁转出判断：旅游/选品正则，随后可选的 `non_cs_detector` LLM 仲裁。
6. 非锁域或已转出后按既有顺序尝试：
   - `try_travel_prefilter`
   - `try_selection_funnel_prefilter`
   - `try_booking_prefilter`
   - `try_commerce_prefilter`
7. 全局入口客服规则未命中时，再执行一次完整客服 prefilter（保留既有灰度语义）。
8. 入口弱命中澄清：`build_entry_clarify`，命中则 `route_mode="clarify"` 短路。
9. `get_router()` 懒加载，构造 `route_context`，调用 `router.route(query, context)`。
10. 若返回 `routing_meta`，调用 `_handle_hierarchical_meta`：域图 prefilter、general、澄清或 legacy 回退；否则继续旧路径。
11. 调用 `understand_query(query, decision)`，必要时把 `plan` 降级为 `direct`。
12. 写回旧 state 字段与 hierarchical 展平字段，交给 `route_selector`。

因此，STOP B 的新适配器只能包住第 9～12 步的“决策编排”，不能重复实现第 1～8 步的门禁与 prefilter 语义。

## 3. Hierarchical Router 输入、输出与边界

### 输入

`HierarchicalRouter.route(query: str, context: dict | None = None)`：

- `query`：用户问题；
- `context`：当前实现传入部门、用户、active domain、上一轮 intent/action、brief summary、pending question 等可序列化上下文。

内部依赖：

- `RuleRouter`：只保留 workflow 强信号与复合意图 override；
- `CoarseIntentClassifier`：输出 `DomainPrediction`；
- `capabilities.yaml` 派生的 manifest；
- `capability_registry.tool_registry`：过滤已注册且 `routed=true` 的候选；
- 既有 `VectorRouter` 索引做域内细选；
- 不在此处执行 Tool、Skill 或 Workflow。

### 当前输出

输出 `RouteDecision`：

```text
execution_mode: direct | plan | workflow
candidates: list[CapabilityScore(name, score)]
confidence: float
reason: str | None
workflow_name: str | None
routing_meta: dict | None
```
`routing_meta` 在 hierarchical 路径下包含：

```text
architecture, domain, domain_confidence, domain_margin, domain_source,
reason_code, domain_action, candidate_tools, candidate_tool_count,
fine_top1, fine_top1_score, fine_margin, tool_route_mode,
calibration, selected_tool, need_clarification,
clarification_reason, routing_latency_ms
```

当前 `domain_action` 主要有：`prefilter_cs`、`prefilter_travel`、`prefilter_selection`、`general_chat`、`clarify`、`tool_route`、`plan`，以及规则覆盖的 `rule_workflow`、`rule_composite`。

### 重要边界

- 域图粗分类命中时，Hierarchical Router 只返回 plan 占位决策和 `domain_action`，真正是否进入域图仍由 `router_node` 复用既有 prefilter 决定。
- 域图 prefilter 未放行（例如 CS 灰度 control）时，`router_node` 调 `get_router().route_legacy(...)`，避免再次进入 hierarchical 分支。
- 粗分类 degraded 会抛异常，由外层 `Router.route` 回退 legacy。
- `ToolSelection.route_mode` 的 `fast_path` / `llm_selection` 是细路由元数据，不是主图的 `route_mode`。

## 4. Legacy Router 输入、输出与回退

`backend/orchestration/router/router.py::Router.route(query, context)` 是统一入口：

- `ROUTING_ARCHITECTURE=hierarchical`：先查 hierarchical 缓存，再调用 `HierarchicalRouter.route`；异常结束 hierarchical span 后进入 legacy；
- legacy 模式或 hierarchical 未决：执行 `_route_legacy`；
- `ROUTING_SHADOW_MODE=true` 时，在 legacy 拍板后异步语义上计算 hierarchical 对比，但不改变 legacy 结果。

`_route_legacy` 的真实顺序是：

1. 路由缓存（`RouteDecision` 反序列化）；
2. `RuleRouter.route(query)`：强规则命中直接返回，弱命中仅作为下层提示；
3. `VectorRouter.route(query)`：高置信或中置信候选直接采纳；
4. `LLMRouter.route(query)`：最终兜底拍板。

三层都返回同一个 `RouteDecision` 契约；异常向上抛给 `router_node`，由其保守降级为 `route_mode="plan"`。`route_legacy` 是显式绕过 hierarchical 的入口，供域图 prefilter 未放行时使用。

## 5. Domain prefilter 返回结构

所有 prefilter 均采用“命中返回主图 state 更新 dict，未命中/关闭返回 `None`，异常由调用方兜底”的契约，但上下文键并不完全一致：

| Prefilter | 命中时 `route_mode` | 命中返回字段 | 备注 |
|---|---|---|---|
| `try_cs_prefilter` | `customer_service` | `route_decision=None`、`route_mode`、`cs_context` | InputGuard block/clarify 时返回 `route_mode="clarify"` 与 `final_answer`；支持 `forced`、灰度、人工接管 |
| `try_travel_prefilter` | `travel` | `route_decision=None`、`route_mode`、`travel_context={conversation_id, travel_route.source=prefilter}` | 目的地抽取留给域图 |
| `try_selection_funnel_prefilter` | `selection_funnel` | `route_decision=None`、`route_mode`、`funnel_context={conversation_id, source=prefilter}` | 与 `selection_decision` workflow 互斥 |
| `try_booking_prefilter` | `travel_booking` | `route_decision=None`、`route_mode` | 不新增 context 键 |
| `try_commerce_prefilter` | `travel_commerce` | `route_decision=None`、`route_mode` | 不新增 context 键，避免 state schema 未登记时被剥离 |

另有跨轮 `travel_pending_resolver`，命中时也返回 `route_decision=None`、`route_mode="travel"` 及旅游上下文；它不是主 Router 的 capability 选择器，STOP B 不应把它复制到新适配器。

## 6. `route_mode` 当前取值与下一节点映射

### 主图路径值

- `direct` → `skill_executor`
- `workflow` → `workflow_executor`
- `plan` → `planner`
- `clarify` → `clarify`（随后 reporter 输出澄清短文案）
- `general_chat` → `general_chat`
- `customer_service` → `cs_graph_node`
- `travel` → `travel_graph_node`
- `selection_funnel` → `selection_funnel_graph_node`
- `travel_booking` → `travel_booking_graph_node`
- `travel_commerce` → `travel_commerce_graph_node`

`route_selector` 对未注册的其它值统一回退 `planner`。域图值由 `domain_graph_registry` 动态注册；当前注册入口对应上表五个域图。`fast_path`、`llm_selection` 只属于 `tool_route_mode`，不能当成主图 `route_mode`。

## 7. State 写入与序列化边界

### 既有核心字段

`router_node` 直接或通过 prefilter 写入：

- `route_decision`：`RouteDecision.model_dump()` 或域图/澄清路径的 `None`；
- `route_mode`：见上节；
- `query_understanding`：`understand_query` 输出，问候路径可直接写入；
- `domain_hint`：输入字段，只读消费，不由 Router 改写；
- 域图专有：`cs_context`、`travel_context`、`funnel_context`、`_clarify`、`final_answer`（仅部分短路路径）。

### Hierarchical 展平字段

有 `routing_meta` 时由 `_hierarchical_state_fields` 写入：

```text
domain, domain_confidence, domain_margin, domain_source,
candidate_tools, selected_tool, tool_arguments=None,
tool_confidence, tool_route_mode, need_clarification,
clarification_reason
```

这些值被设计为标量、字符串列表、普通 dict 或 `None`，可进入 checkpoint。当前没有 `domain_decision`、`capability_decision`、`execution_decision` 字段；若 STOP B 新增，必须保持可序列化并以增量字段方式写入，不能替换旧字段。

## 8. `TaskRouter` 存量核查

`backend/orchestration/workflow/router.py` 仍定义旧的 `TaskRouter` / `RouteResult`，其内部是 workflow registry 上的“业务对象 + 动作 + workflow match”评分和 agent fallback。

本次仓内引用检索结果：

- 生产代码引用：除 `workflow/router.py` 自身定义/示例外为 0；
- 测试引用：
  - `backend/tests/orchestration/workflow/test_router.py`
  - `backend/tests/inventory/test_workflow_inventory_alert.py`
  - `backend/tests/orchestration/workflow/test_daily_report_smoke.py`

因此当前只确认“无生产调用链”，不在 STOP B Step 0 删除文件或迁移测试。后续若决定收口，需先把这些测试迁移到统一的 workflow/capability 适配器，再单独提交删除变更。

## 9. STOP B 实施边界与风险清单

### 可以做

- 新增只依赖 router/classifier/registry 的 `DomainDecision`、`CapabilityDecision`、`ExecutionModeDecision` 类型；
- 通过薄适配器调用现有 prefilter、`HierarchicalRouter`、`RuleRouter`、`VectorRouter`、workflow/domain registry；
- 保留 `RouteDecision`、`route_mode`、`query_understanding` 及 hierarchical 平铺字段；
- 增加 `router_fallback_reason`、`legacy_used` 等可序列化观测字段或 trace metadata，但必须默认不影响既有消费方。

### 不可以做

- 重写或复制 CS/旅游/选品/商务/预订 prefilter 算法；
- 把 Tool、Skill、Planner、Workflow 执行塞进 DomainRouter 或 CapabilityRouter；
- 在 STOP B 改主图节点 ID、`route_selector` 出边、SSE、checkpoint key、AgentState 既有字段语义；
- 删除 legacy/hierarchical Router 或改变 `route_legacy` 的 control 组语义；
- 把 `domain_graph_registry` 的动态域图发现改成 builder 手写列表。

### 主要风险

1. `router_node` 前置域门禁与 hierarchical 的 `domain_action` 不是同一层；误合并会改变客服锁域、灰度和旅游/选品优先级。
2. `RouteDecision.execution_mode` 目前只有 `direct/plan/workflow`，`domain_graph/general/clarify` 实际通过 `routing_meta` 或主图 `route_mode` 表达，不能直接扩展枚举替代旧状态。
3. `query_understanding` 会把部分 `plan` 降级为 `direct`；新 ExecutionModeResolver 若忽略该步骤，会造成 capability 与执行模式漂移。
4. 预过滤返回 dict 中的 context 键不统一，新增统一模型时必须保留旧键，不能假设所有域图都有相同 context。

## 10. 建议的后续实施顺序

按 STOP B0 冻结设计，下一步应分两批：

1. 先只新增适配器与单测，不接 `router_node` 主链；覆盖客服锁域、旅游、选品、商务、未知、`sql.query`、`rag.search`、`report.generate`、email 及 direct/workflow/plan/domain_graph/general/clarify 映射。
2. 适配器测试通过后，再以最小 diff 接入 `router_node`，保留旧字段和 legacy fallback；用固定 query 集逐项比较旧链路与新链路。
3. 完成 CS、Travel、SQL、RAG evaluation 与结构性回归后，才生成 STOP B 总结报告并判断是否进入 STOP C。

本审计文档不宣称 STOP B 已通过；通过条件仍以冻结设计中的以下不变量为准：

```text
ROUTER_NODE_ID_CHANGED=false
STATE_CONTRACT_CHANGED=false
SSE_CHANGED=false
CHECKPOINT_CHANGED=false
LEGACY_FALLBACK_AVAILABLE=true
ROUTER_CONSOLIDATION_PASS=true
```

