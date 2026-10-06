# Runtime Semantic Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 按 STOP A→G 完成 Runtime 语义收口，新增 RouteDecisionV2、统一兼容投影、演进现有 DomainGraphRegistry，并以机械验收门证明旧行为、节点、SSE、checkpoint 和前端映射不变。

**Architecture:** 新 V2 决策是路由事实源，旧 route_mode、route_decision 和平铺字段只由唯一 Projection 生成。Runtime 元数据直接扩展现有 DomainGraphRegistry，不增加第二套 Registry；域内部图、State 和回答生成保持原状，RuntimeResult 先在边界归一，再逐步接入域适配器。

**Tech Stack:** Python 3.10、Pydantic v2、LangGraph、TypedDict、pytest、现有 Prometheus/Trace 观测链。

**Spec:** docs/superpowers/specs/2026-10-06-runtime-semantic-closure-design.md

## Global Constraints

- 所有回答与新增代码注释使用中文。
- 不修改既有 node_id、SSE event name、checkpoint key、pending Send、前端 node mapping。
- 不把 Travel 或 Selection 改成 Agent Runtime，不统一各域内部 State。
- 不新建 DomainRegistry、RuntimeRegistry、SubgraphRegistry 或其他第二注册表。
- 新 Runtime/Decision 数据必须可序列化并登记进 LangGraph State schema。
- 新增或修改行为必须先写失败测试、确认 RED，再写最小生产代码、确认 GREEN。
- 局部 pytest 命令必须带 --no-cov。
- 保留工作区现有未提交报告、probe 文件和截图，不纳入本计划提交。
- 任一 STOP 只有阶段测试、Global Regression Gate 和机械聚合器均通过，才可标记完成。

## Review Focus

- V2 的 domain、execution、interaction、runtime 组合必须强类型且不能互相漂移；Task 1 覆盖合法/非法组合。
- prefilter、continuation、general_chat、clarify、handoff 不能绕过 Projection 直接写旧字段；Task 2 覆盖出口矩阵和 AST 单写者守卫。
- DomainGraph 的顶级域、subflow、alias 和 runtime capability 必须来自同一 Registry；Task 3 覆盖 fake domain/subflow。
- RuntimeResult 归一不能丢 final_answer、sources、clarification、handoff 或 tool metadata，也不能触发二次 LLM；Task 4 覆盖三个域 mock。
- 新字段不能在 checkpoint、Travel interrupt 或 Plan Send 中丢失；Task 5 与 Task 7 覆盖 State schema 和兼容门。

## 机械验收总门

新增 backend/scripts/verify_runtime_arch_v2.py。脚本只接受阶段测试真实退出码、基线比较和静态扫描结果，不接受手写 PASS；报告写入 d:/tmp/，不提交生成文件。

计算字段：

~~~text
RUNTIME_ARCH_V2_CONTRACT_PASS
ROUTING_SEMANTIC_SPLIT_PASS
RUNTIME_REGISTRY_PASS
RUNTIME_RESULT_CONTRACT_PASS
STATE_CANONICALIZATION_PASS
DOMAIN_REGISTRATION_GOVERNANCE_PASS
RUNTIME_OBSERVABILITY_PASS
NODE_ID_COMPAT_PASS
SSE_COMPAT_PASS
CHECKPOINT_COMPAT_PASS
FRONTEND_COMPAT_PASS
GLOBAL_REGRESSION_PASS
AGENT_RUNTIME_ARCH_V2_READY
~~~

阶段验收字段必须逐项计算，不允许只输出阶段总称：

~~~text
STOP_A: A1_ROUTE_DECISION_V2_SCHEMA A2_INVALID_COMBINATION A3_RUNTIME_TYPE_ENUM A4_RUNTIME_RESULT A5_PRODUCTION_BEHAVIOR
STOP_B: B1_DIRECT B2_PLAN B3_WORKFLOW B4_CS B5_TRAVEL B6_SELECTION B7_CLARIFY B8_GUIDE B9_GENERAL_CHAT B10_LEGACY_PROJECTION
STOP_C: C1_CS_RUNTIME C2_TRAVEL_RUNTIME C3_SELECTION_RUNTIME C4_SUBFLOW C5_CHECKPOINT_CAPABILITY C6_INTERRUPT_CAPABILITY C7_FAKE_DOMAIN C8_DOMAIN_LITERAL_GUARD
STOP_D: D1_CS_OUTPUT D2_TRAVEL_OUTPUT D3_SELECTION_OUTPUT D4_SOURCES D5_CLARIFICATION D6_HANDOFF D7_TOOL_METADATA D8_NO_DOUBLE_GENERATION
STOP_E: E1_DECISION_SINGLE_SOURCE E2_PARAMS_SINGLE_SOURCE E3_CLARIFICATION_SINGLE_SOURCE E4_LEGACY_PROJECTION E5_UNKNOWN_STATE_KEYS
STOP_F: F1_NEW_DOMAIN_SURFACE F2_PREFILTER_MAP F3_ROUTE_MODE_FAMILY F4_ENTRY_MODE_SWITCHES F5_BUSINESS_RULES_RETAINED
STOP_G: G1_DOMAIN G2_SUBFLOW G3_RUNTIME_TYPE G4_RUNTIME_ID G5_INTERACTION_MODE G6_EXECUTION_MODE G7_WORKFLOW_ID G8_CAPABILITY G9_SKILL_ID G10_TOOL_ID G11_PROMPT_VERSION G12_CONFIDENCE G13_SOURCE
GLOBAL: NODE_ID_UNCHANGED SSE_EVENT_SCHEMA_UNCHANGED CHECKPOINT_COMPAT_PASS TRAVEL_INTERRUPT_RESUME_PASS PLAN_SEND_PAYLOAD_PASS FRONTEND_NODE_MAPPING_PASS DIRECT_PATH_PASS WORKFLOW_PATH_PASS PLAN_PATH_PASS CS_DOMAIN_PASS TRAVEL_DOMAIN_PASS SELECTION_DOMAIN_PASS CLARIFY_PASS HANDOFF_PASS GENERAL_CHAT_PASS
~~~

Global Regression Gate 的任一字段为 false，GLOBAL_REGRESSION_PASS 必须为 false。Travel interrupt 必须真实经历 validator interrupt、pending、resume、继续和完成；Plan Send 必须真实经历 Planner、2~3 个并行 Skill、step_results merge、supervisor 和 reporter。

### 用户确认版总验收门（2026-10-06）

以下别名与阶段明细同时输出，便于发布门禁直接消费：

~~~ini
RUNTIME_ARCH_V2_CONTRACT_PASS=true
ROUTING_SEMANTIC_SPLIT_PASS=true
RUNTIME_REGISTRY_PASS=true
RUNTIME_RESULT_CONTRACT_PASS=true
STATE_CANONICALIZATION_PASS=true
DOMAIN_REGISTRATION_GOVERNANCE_PASS=true
RUNTIME_OBSERVABILITY_PASS=true

NODE_ID_COMPAT_PASS=true
SSE_COMPAT_PASS=true
CHECKPOINT_COMPAT_PASS=true
FRONTEND_COMPAT_PASS=true
GLOBAL_REGRESSION_PASS=true

AGENT_RUNTIME_ARCH_V2_READY=true
~~~

最终值严格按下式计算，禁止人工覆盖：

~~~text
AGENT_RUNTIME_ARCH_V2_READY =
  A && B && C && D && E && F && G
  && NODE_ID_COMPAT
  && SSE_COMPAT
  && CHECKPOINT_COMPAT
  && FRONTEND_COMPAT
  && GLOBAL_REGRESSION
  && STATE_UNKNOWN_KEY_TOTAL == 0
  && PRODUCTION_BEHAVIOR_CHANGED == false
~~~

阶段明细必须保留用户可审计的场景粒度：

~~~text
A: RouteDecisionV2 Schema / 非法组合 / RuntimeType / RuntimeResult / 生产零行为变化
B: direct / plan / workflow / CS / Travel / Selection / clarify / guide-handoff / general_chat / Legacy Projection 唯一写入口
C: CS / Travel / Selection Runtime / subflow / checkpoint / interrupt / fake_domain / Domain Literal Guard
D: CS / Travel / Selection 输出 / sources / clarification / handoff / tool metadata / no double generation
E: canonical decision / params / clarification / legacy projection / unknown state key
F: 新域注册面≤3处 / prefilter family / entry mode / 业务规则保留 / Router 域字面量扫描
G: domain / subflow / runtime_type / runtime_id / interaction_mode / execution_mode / workflow_id / capability / skill_id / tool_id / prompt_version / confidence / source
~~~

Global Regression Gate 固定覆盖：node_id、SSE event schema、checkpoint、Travel interrupt/resume、Plan Send payload、前端节点映射、direct/workflow/plan、CS/Travel/Selection、clarify、handoff、general_chat。每个 STOP 的聚合器都执行该门；`--stage G --final` 再一次性回放 A–G 全部测试并写入 `d:/tmp/runtime_arch_v2_gate.json`。

最后一项只能由真实结果计算：

~~~python
agent_runtime_arch_v2_ready = all(stage_results.values()) and all(global_results.values())
~~~

每个 STOP 完成时运行该阶段与 Global Regression Gate；最终运行全量聚合器。任一项失败，脚本退出码非零。

---

### Task 1: STOP A — Contract Freeze

**Files:**
- Modify: backend/orchestration/router/types.py
- Create: backend/orchestration/runtime_types.py
- Modify: backend/orchestration/domain_graph.py
- Create: backend/tests/orchestration/router/test_runtime_contracts.py
- Create: backend/tests/orchestration/test_runtime_architecture_gate.py
- Create: backend/tests/orchestration/runtime_architecture_baseline.py
- Create: backend/scripts/verify_runtime_arch_v2.py

**Interfaces:**
- Produces RuntimeType、InteractionMode、RuntimeTarget、RuntimeResult、RuntimeContext、RouteDecisionV2、normalize_runtime_result()。
- Extends DomainGraph with runtime_id、runtime_type、aliases、capabilities、supports_checkpoint、supports_interrupt、supports_streaming、result_contract_version、entry_modes、continuation_policy; old constructors remain valid.
- Produces python -m backend.scripts.verify_runtime_arch_v2 --stage A.

- [ ] Step 1: 先写 RED 测试。实例化 direct/plan/workflow、CS/Travel/Selection、clarify/guide/handoff/general_chat 的合法组合；扫描五个 RuntimeType；验证非法 runtime/interaction 抛出 Pydantic ValidationError；用 CS 字符串、Travel 行程 dict、Selection 报告 dict 验证 normalize_runtime_result()；验证 model_dump(mode="json") 可序列化。
- [ ] Step 2: 运行失败测试。

~~~powershell
Set-Location backend
& D:/Python/python.exe -m pytest tests/orchestration/router/test_runtime_contracts.py -q --no-cov
~~~

Expected: FAIL，原因是 V2 模型、RuntimeType 和归一函数尚不存在。
- [ ] Step 3: 写最小实现。V2 模型放入现有 router/types.py；RuntimeResult 只做纯数据协议；normalize_runtime_result() 不调用 LLM；DomainGraph 增加默认字段；基线模块冻结当前核心 node id、SSE 事件、State 字段和前端 node mapping。
- [ ] Step 4: 运行 GREEN 与 A 门。

~~~powershell
Set-Location backend
& D:/Python/python.exe -m pytest tests/orchestration/router/test_runtime_contracts.py tests/orchestration/test_domain_registry.py -q --no-cov
& D:/Python/python.exe -m backend.scripts.verify_runtime_arch_v2 --stage A
~~~

Expected: CONTRACT_V2_PASS=true、PRODUCTION_BEHAVIOR_CHANGED=false。
- [ ] Step 5: 提交。

~~~powershell
git add backend/orchestration/router/types.py backend/orchestration/domain_graph.py backend/tests/orchestration/router/test_runtime_contracts.py backend/tests/orchestration/runtime_architecture_baseline.py backend/scripts/verify_runtime_arch_v2.py
git commit -m "feat(router): freeze runtime v2 contracts"
~~~

### Task 2: STOP B — Routing Semantic Split

**Files:**
- Create: backend/orchestration/router/projection.py
- Modify: backend/orchestration/state.py
- Modify: backend/orchestration/graph/router_node.py
- Modify: backend/orchestration/graph/routing/hierarchical.py
- Modify: backend/orchestration/graph/routing/prefilter_chain.py
- Modify: backend/orchestration/graph/routing/continuation.py
- Modify: backend/orchestration/graph/cs_prefilter.py
- Modify: backend/orchestration/graph/travel_prefilter.py
- Modify: backend/orchestration/graph/selection_funnel_prefilter.py
- Modify: backend/orchestration/graph/booking_prefilter.py
- Modify: backend/orchestration/graph/commerce_prefilter.py
- Create: backend/tests/orchestration/router/test_route_projection.py
- Create: backend/tests/orchestration/graph/test_router_semantic_split.py
- Create: backend/tests/orchestration/graph/test_legacy_route_writer.py

**Interfaces:**
- Consumes Task 1 的 RouteDecisionV2。
- Produces project_route_decision_to_legacy_state(decision, *, legacy_route_mode=None) -> dict。
- Produces build_route_decision_v2_from_route(...) -> RouteDecisionV2。
- Adds route_decision_v2: dict to OrchestratorState; old fields remain.

- [ ] Step 1: 先写 RED 测试。矩阵覆盖 B1 direct、B2 plan、B3 workflow、B4 CS、B5 Travel、B6 Selection、B7 clarify、B8 guide/handoff、B9 general_chat；断言 B10 中旧 route_mode 值完全保留。AST 测试扫描 Router/pre-filter 生产写入口，除 router/projection.py 外禁止新增对 route_mode 的 state-update 写入。
- [ ] Step 2: 运行失败测试。

~~~powershell
Set-Location backend
& D:/Python/python.exe -m pytest tests/orchestration/router/test_route_projection.py tests/orchestration/graph/test_router_semantic_split.py tests/orchestration/graph/test_legacy_route_writer.py -q --no-cov
~~~

Expected: FAIL，原因是 route_decision_v2 和统一 Projection 尚不存在，旧分支仍有多个生产写入口。
- [ ] Step 3: 写最小实现。所有 Router 出口先构造 V2，再调用 Projection。Projection 统一生成 route_decision_v2、兼容 route_decision、route_mode、domain 平铺字段、candidate/selected/tool route 字段、clarification 字段和既有 decision 快照；域上下文、_clarify、_handoff 等业务 payload 由调用方合并但不得再写路由 legacy 字段。route_selector 继续读取 route_mode，不改 node_id、边或 SSE。
- [ ] Step 4: 运行 B 回归与全局门。

~~~powershell
Set-Location backend
& D:/Python/python.exe -m pytest tests/orchestration/router/test_route_projection.py tests/orchestration/graph/test_router_semantic_split.py tests/orchestration/graph/test_legacy_route_writer.py tests/orchestration/graph/test_router_prefilter_order.py tests/orchestration/graph/test_entry_mode_handoff.py tests/orchestration/router/test_router_consolidation_adapters.py -q --no-cov
& D:/Python/python.exe -m backend.scripts.verify_runtime_arch_v2 --stage B
~~~

Expected: ROUTE_DECISION_V2_PASS=true、ROUTE_MODE_BACKWARD_COMPAT_PASS=true、ROUTE_SINGLE_WRITER_PASS=true，Global Regression Gate 无失败。
- [ ] Step 5: 提交。

~~~powershell
git add backend/orchestration/router/projection.py backend/orchestration/state.py backend/orchestration/graph backend/tests/orchestration/router/test_route_projection.py backend/tests/orchestration/graph/test_router_semantic_split.py backend/tests/orchestration/graph/test_legacy_route_writer.py
git commit -m "feat(router): project v2 decisions to legacy state"
~~~

### Task 3: STOP C — Runtime Registry

**Files:**
- Modify: backend/orchestration/domain_registry.py
- Modify: backend/customer_service/register.py
- Modify: backend/travel/register.py
- Modify: backend/travel/commerce/register.py
- Modify: backend/travel/booking/register.py
- Modify: backend/selection_funnel/register.py
- Create: backend/tests/orchestration/test_runtime_registry.py
- Modify: backend/tests/orchestration/test_domain_semantic_consistency.py

**Interfaces:**
- Produces活视图：resolve_alias()、route_mode_to_runtime_target()、route_mode_to_family()、route_mode_to_entry_mode()。
- route_selector 仍由已有 registry.get(route_mode) 选择节点，不新增 dispatcher node。

- [ ] Step 1: 先写 RED 测试。锁定 C1 CS=agent_runtime、C2 Travel=workflow_runtime、C3 Selection=workflow_runtime、C4 booking/commerce subflow、C5 checkpoint capability、C6 interrupt capability；注册 fake top/subflow 后所有派生视图自动包含它，且不改 Router 文件。
- [ ] Step 2: 运行失败测试。

~~~powershell
Set-Location backend
& D:/Python/python.exe -m pytest tests/orchestration/test_runtime_registry.py tests/orchestration/test_domain_semantic_consistency.py -q --no-cov
~~~

Expected: FAIL，原因是 Runtime descriptor 字段和派生方法尚不存在。
- [ ] Step 3: 写最小实现。Registry 注册期校验 runtime_id 唯一、alias 不冲突、子流父域存在、父域不自指；注册模块补真实 runtime 元数据：CS checkpoint=true/interrupt=false；Travel checkpoint=true/interrupt=true；Selection Funnel checkpoint=false/interrupt=false；entry mode 和 runtime_id 明确登记。
- [ ] Step 4: 运行 C 回归与全局门。

~~~powershell
Set-Location backend
& D:/Python/python.exe -m pytest tests/orchestration/test_runtime_registry.py tests/orchestration/test_domain_semantic_consistency.py tests/orchestration/test_domain_registry.py tests/test_domain_registration.py -q --no-cov
& D:/Python/python.exe -m backend.scripts.verify_runtime_arch_v2 --stage C
~~~

Expected: RUNTIME_REGISTRY_PASS=true、DOMAIN_REGISTRATION_SINGLE_SOURCE_PASS=true，Global Regression Gate 通过。
- [ ] Step 5: 提交。

~~~powershell
git add backend/orchestration/domain_registry.py backend/customer_service/register.py backend/travel/register.py backend/travel/commerce/register.py backend/travel/booking/register.py backend/selection_funnel/register.py backend/tests/orchestration/test_runtime_registry.py backend/tests/orchestration/test_domain_semantic_consistency.py
git commit -m "feat(runtime): register domain runtime descriptors"
~~~

### Task 4: STOP D — RuntimeResult

**Files:**
- Create: backend/orchestration/runtime_result_adapter.py
- Modify: backend/orchestration/graph/cs_graph_node.py
- Modify: backend/orchestration/graph/travel_graph_node.py
- Modify: backend/orchestration/graph/selection_funnel_graph_node.py
- Create: backend/tests/orchestration/graph/test_runtime_result_adapters.py
- Create: backend/tests/orchestration/graph/test_domain_output_regression.py

**Interfaces:**
- Consumes Task 1 的 RuntimeResult/normalize_runtime_result() 和 Task 3 descriptor。
- Produces runtime_result 可序列化状态字段，同时保留 final_answer、sources、clarification、handoff 和 domain-specific payload。
- 不改变域内部节点、子图和 Reporter 生成逻辑。

- [ ] Step 1: 先写 RED 测试。CS 字符串、Travel 行程 dict、Selection 报告 dict 分别归一；断言 answer、answer_type、sources、clarification、handoff、tool_calls、metadata 不丢；spy 确认归一函数不调用 LLM。
- [ ] Step 2: 运行失败测试。

~~~powershell
Set-Location backend
& D:/Python/python.exe -m pytest tests/orchestration/graph/test_runtime_result_adapters.py tests/orchestration/graph/test_domain_output_regression.py -q --no-cov
~~~

Expected: FAIL，原因是域节点没有统一 runtime_result 出口。
- [ ] Step 3: 写最小实现。域适配器返回既有 state update 前调用归一函数，写入 runtime_result；final_answer 和原结构化字段原样保留。主 reporter 不读取域 runtime_result 做二次 LLM 改写，域图仍直连 END。
- [ ] Step 4: 运行 D 回归与全局门。

~~~powershell
Set-Location backend
& D:/Python/python.exe -m pytest tests/orchestration/graph/test_runtime_result_adapters.py tests/orchestration/graph/test_domain_output_regression.py tests/orchestration/graph/test_cs_graph_node_contract.py tests/travel/test_travel_graph.py tests/selection_funnel/test_selection_funnel_graph.py -q --no-cov
& D:/Python/python.exe -m backend.scripts.verify_runtime_arch_v2 --stage D
~~~

Expected: RUNTIME_RESULT_PASS=true、DOMAIN_OUTPUT_REGRESSION_PASS=true、NO_DOUBLE_GENERATION_PASS=true。
- [ ] Step 5: 提交。

~~~powershell
git add backend/orchestration/runtime_result_adapter.py backend/orchestration/graph/cs_graph_node.py backend/orchestration/graph/travel_graph_node.py backend/orchestration/graph/selection_funnel_graph_node.py backend/tests/orchestration/graph/test_runtime_result_adapters.py backend/tests/orchestration/graph/test_domain_output_regression.py
git commit -m "feat(runtime): normalize domain results"
~~~

### Task 5: STOP E — State Canonicalization

**Files:**
- Modify: backend/orchestration/state.py
- Modify: backend/orchestration/graph/events.py
- Modify: backend/orchestration/graph/tool_selector.py
- Modify: backend/orchestration/graph/direct_executor.py
- Modify: backend/agents/reporter/reporter.py
- Create: backend/orchestration/state_projection.py
- Create: backend/tests/orchestration/test_state_canonicalization.py
- Modify: backend/tests/test_state_key_guard.py

**Interfaces:**
- Consumes Task 2 的 V2 decision 和 Task 4 的 runtime_result。
- Produces canonical decision reader、legacy projection reader 和静态单写者守卫。
- resolved_params 成为参数执行事实源；tool_arguments 只保留兼容读取，不再新增生产写入。
- ClarificationRequest 成为澄清事实源；旧澄清字段由适配器产生。

- [ ] Step 1: 先写 RED 测试。锁定三类重复字段：V2/平铺/decision snapshots、tool_arguments/resolved_params、need_clarification/clarification_reason/_clarify；断言 canonical decision 优先、旧字段缺失时可由 V2 投影读取；AST 守卫统计未知 State key 为零并拒绝新增 legacy writer。
- [ ] Step 2: 运行失败测试。

~~~powershell
Set-Location backend
& D:/Python/python.exe -m pytest tests/orchestration/test_state_canonicalization.py tests/test_state_key_guard.py -q --no-cov
~~~

Expected: FAIL，原因是当前 State 仍存在多事实源和 Tool Selector 的 tool_arguments 写入。
- [ ] Step 3: 写最小实现。登记 route_decision_v2、runtime_result、结构化 ClarificationRequest State 键；统一读取辅助函数；删除 Tool Selector 的新增 tool_arguments 写入，保留兼容读取/投影；事件层继续发既有 clarification/handoff SSE。
- [ ] Step 4: 运行 E 回归与全局门。

~~~powershell
Set-Location backend
& D:/Python/python.exe -m pytest tests/orchestration/test_state_canonicalization.py tests/test_state_key_guard.py tests/orchestration/graph/test_tool_selector.py tests/orchestration/graph/test_clarify_flow.py tests/orchestration/graph/test_reply_source.py -q --no-cov
& D:/Python/python.exe -m backend.scripts.verify_runtime_arch_v2 --stage E
~~~

Expected: STATE_CANONICAL_SOURCE_PASS=true、LEGACY_PROJECTION_PASS=true、STATE_UNKNOWN_KEY_TOTAL=0。
- [ ] Step 5: 提交。

~~~powershell
git add backend/orchestration/state.py backend/orchestration/state_projection.py backend/orchestration/graph/events.py backend/orchestration/graph/tool_selector.py backend/orchestration/graph/direct_executor.py backend/agents/reporter/reporter.py backend/tests/orchestration/test_state_canonicalization.py backend/tests/test_state_key_guard.py
git commit -m "refactor(state): make runtime decision canonical"
~~~

### Task 6: STOP F — Router Domain Metadata Consolidation

**Files:**
- Modify: backend/orchestration/graph/routing/prefilter_chain.py
- Modify: backend/orchestration/router/domain_router.py
- Modify: backend/orchestration/router/execution_mode.py
- Modify: backend/orchestration/graph/router_node.py
- Create: backend/tests/orchestration/test_domain_registration_surface.py
- Create: backend/tests/orchestration/router/test_router_domain_literal_guard.py

**Interfaces:**
- Consumes Task 3 Registry 派生视图。
- Produces Router domain family、entry mode、prefilter domain 和 subflow 的统一 Registry 查询。
- general_chat 等非 DomainGraph 主图伪模式保留为显式、最小、带测试的例外。

- [ ] Step 1: 先写 RED 测试。fake_domain 注册描述符、实现一个 prefilter 插件后，统计核心生产修改位置；静态扫描拒绝新增可注册域硬编码表和新增域名映射字面量。
- [ ] Step 2: 运行失败测试。

~~~powershell
Set-Location backend
& D:/Python/python.exe -m pytest tests/orchestration/test_domain_registration_surface.py tests/orchestration/router/test_router_domain_literal_guard.py -q --no-cov
~~~

Expected: FAIL，原因是入口模式和域族仍由 Router 手写字典提供。
- [ ] Step 3: 写最小实现。将可注册部分替换为 Registry 活视图；保留旅游 regex、CS continuation、booking resolver 等业务逻辑在原模块。
- [ ] Step 4: 运行 F 回归与全局门。

~~~powershell
Set-Location backend
& D:/Python/python.exe -m pytest tests/orchestration/test_domain_registration_surface.py tests/orchestration/router/test_router_domain_literal_guard.py tests/orchestration/graph/test_entry_mode_handoff.py tests/orchestration/graph/test_router_prefilter_order.py tests/orchestration/test_domain_semantic_consistency.py -q --no-cov
& D:/Python/python.exe -m backend.scripts.verify_runtime_arch_v2 --stage F
~~~

Expected: DOMAIN_REGISTRATION_GOVERNANCE_PASS=true，fake_domain 注册面受控，Global Regression Gate 通过。
- [ ] Step 5: 提交。

~~~powershell
git add backend/orchestration/graph/routing/prefilter_chain.py backend/orchestration/router/domain_router.py backend/orchestration/router/execution_mode.py backend/orchestration/graph/router_node.py backend/tests/orchestration/test_domain_registration_surface.py backend/tests/orchestration/router/test_router_domain_literal_guard.py
git commit -m "refactor(router): derive domain metadata from registry"
~~~

### Task 7: STOP G — Observability Closure 与最终总门

**Files:**
- Modify: backend/orchestration/router/router_trace.py
- Modify: backend/observability/tracer.py
- Modify: backend/observability/trace_middleware.py
- Modify: backend/orchestration/graph/runner.py
- Modify: backend/evaluation/report/builder.py
- Modify: backend/scripts/verify_runtime_arch_v2.py
- Create: backend/tests/runtime/test_runtime_arch_v2_trace.py
- Use: backend/scripts/verify_runtime_arch_v2.py::GLOBAL_REGRESSION_TESTS
- Modify: backend/tests/runtime/test_runtime_trace.py

**Interfaces:**
- Consumes Task 2 V2 decision、Task 3 descriptor、Task 4 RuntimeResult、Task 5 canonical State。
- Produces Trace/Eval/Cost 字段：domain、subflow、runtime_type、runtime_id、interaction_mode、execution_mode、workflow_id、capability、skill_id、tool_id、prompt_version、confidence、source。
- Produces Travel interrupt、Plan Send、节点/SSE/checkpoint/frontend/direct/workflow/plan/域图兼容门的真实测试结果。

- [ ] Step 1: 先写 RED 测试。Trace 断言字段全量可追溯；兼容测试机械比较 node id、SSE schema、State key 和 frontend mapping；真实构造 Travel validator interrupt→pending→resume→complete，以及 Planner→2~3 并行 Skill→Send→step_results merge→supervisor→reporter；覆盖 direct/workflow/plan/CS/Travel/Selection/clarify/handoff/general_chat。
- [ ] Step 2: 运行失败测试。

~~~powershell
Set-Location backend
& D:/Python/python.exe -m pytest tests/runtime/test_runtime_trace.py tests/orchestration/test_runtime_observability.py tests/orchestration/test_global_regression_gate.py -q --no-cov
~~~

Expected: FAIL，原因是 Runtime 字段和最终兼容聚合尚未完整接线。
- [ ] Step 3: 写最小实现。Router span 和请求 Trace tags 写入 V2/Runtime 字段；Skill/Tool/Prompt 原有归因继续沿用；runner 从 canonical State 投影兼容 trace；聚合脚本按真实结果做逻辑 AND，不允许环境变量或手改文件伪造 PASS。
- [ ] Step 4: 运行 GREEN 与最终总门。

~~~powershell
Set-Location backend
& D:/Python/python.exe -m pytest tests/runtime/test_runtime_trace.py tests/orchestration/test_runtime_observability.py tests/orchestration/test_global_regression_gate.py -q --no-cov
& D:/Python/python.exe -m backend.scripts.verify_runtime_arch_v2 --stage G --final
~~~

Expected: A–G、NODE_ID_COMPAT_PASS、SSE_COMPAT_PASS、CHECKPOINT_COMPAT_PASS、FRONTEND_COMPAT_PASS、GLOBAL_REGRESSION_PASS 全为 true，且 AGENT_RUNTIME_ARCH_V2_READY 由脚本计算为 true。
- [ ] Step 5: 提交。

~~~powershell
git add backend/orchestration/router/router_trace.py backend/observability/tracer.py backend/observability/trace_middleware.py backend/orchestration/graph/runner.py backend/evaluation/report/builder.py backend/scripts/verify_runtime_arch_v2.py backend/tests/runtime/test_runtime_trace.py backend/tests/orchestration/test_runtime_observability.py backend/tests/orchestration/test_global_regression_gate.py
git commit -m "feat(observability): close runtime architecture gates"
~~~

## 当前执行状态（自动模式，2026-10-06）

~~~text
STOP A  PASS
STOP B  PASS
STOP C  PASS
STOP D  PASS
STOP E  PASS
STOP F  PASS
STOP G  PASS
GLOBAL  PASS
READY   PASS
~~~

最终回放证据：`447 passed`；`STATE_UNKNOWN_KEY_TOTAL=0`；`PRODUCTION_BEHAVIOR_CHANGED=false`；`AGENT_RUNTIME_ARCH_V2_READY=true`。阶段门与兼容门均由 `verify_runtime_arch_v2.py` 从测试退出码计算，未手工填 PASS。

## 完成报告格式

自动执行期间只汇报阶段结果，不报告未经测试的完成状态。最终报告必须包含：
1. A–G 每个 PASS/FAIL 的真实测试命令和输出；
2. Global Regression Gate 每一项结果；
3. AGENT_RUNTIME_ARCH_V2_READY 的机械计算结果；
4. 实际提交列表；
5. 失败、阻塞和未完成项；
6. 为解决计划冲突作出的每条 Ruling 及错误成本。
