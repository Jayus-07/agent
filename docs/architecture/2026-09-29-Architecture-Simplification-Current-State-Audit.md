# Architecture Simplification — Current State Audit

日期：2026-09-29  
范围：只读复核 Architecture Simplification 的 STOP A–H 当前状态；不进入 STOP E–H 实施。  
审计基准：`main@7fae31d`，并单独标注未提交工作区证据。  

## 审计结论

STOP A 已有可追溯的只读审计；STOP D 有提交、自动化测试和真实网关/SSE 运行证据。STOP B 的兼容收口主体已经在 `main`，但 Router 决策持久化 Trace metadata 的最后缺口只存在于**未提交**工作区，不能视为冻结完成。STOP C 只完成了“路由 manifest 的单源派生”，没有完成任务要求的“完整 Capability metadata 单一事实源”。

因此不得进入 STOP E。下一步应先将 STOP B 的 Trace 变更独立确认/提交，并为 STOP C 决定并完成 metadata 的唯一归属；二者闭环后才进入 STOP E。

## 1. Current Architecture Status

### 当前真实调用链

```text
POST /chat/stream
  -> FastAPI Chat Runtime / GraphRunner
  -> Input Guard -> memory/context/follow-up 组装 -> main graph 的 router node
  -> router_node 前置门禁（travel pending / continuation / general_chat / CS 锁域）
  -> CS -> travel -> selection -> booking -> commerce prefilter（既有顺序与开关）
  -> Router.route()
       -> hierarchical（启用时：CoarseIntentClassifier + manifest 域内候选/细选）
       -> 失败或兼容场景 route_legacy（RuleRouter -> VectorRouter -> LLMRouter）
  -> router_node 把已有路由结果规范化为 DomainDecision / CapabilityDecision /
     ExecutionModeDecision，并继续写回旧 route_mode / route_decision
  -> route_selector（仍以兼容 route_mode 为权威分支）
       -> direct: tool_selector -> skill_executor -> reporter
       -> workflow: workflow_executor -> reporter
       -> plan: planner -> critique -> supervisor -> Skill nodes -> reporter
       -> general_chat: direct LLM response
       -> domain graph: customer_service / travel / selection_funnel /
          travel_commerce / travel_booking -> domain-owned response -> END
```

这说明当前的 `DomainRouter` / `CapabilityRouter` 是**兼容性决策规范化层**：常规 hierarchical 请求由 `CoarseIntentClassifier` 先产生域结果，再由 `DomainRouter.from_hierarchical_meta()` 投影为统一决策；它尚不是唯一的、直接被主请求链调用的 `DomainRouter.route(query)` 粗分层入口。`ExecutionModeResolver` 同样只将既有结果与 override 归一，`route_selector` 仍消费既有 `route_mode`。这是为了 checkpoint/SSE/旧 state 兼容的刻意过渡，但也是 STOP B 尚未彻底“单一架构出口”的债务。

### STOP 状态

| STOP | 状态 | 证据 | 是否需要补修 |
|---|---|---|---|
| A Router audit | PASS | `c3d0a01`；`2026-09-28-Architecture-Simplification-STOPA-Audit.md`；本次静态复核与其关键结论一致 | 否 |
| B Router consolidation | PARTIAL | `7a6d1c6`、`90ec58e`、`4ca0b18`；当前定向回归 57 passed；Trace metadata 修复仍未提交 | 是，先冻结 trace 缺口 |
| C Capability SSOT | PARTIAL | `01a3329` / `a2b50d9` 和 manifest 派生/守护测试；Skill 仍维护重复业务 metadata | 是，确定完整 metadata SSOT |
| D Runtime/Plan | PASS | `8158cc4`、`2399603`、`7fae31d`；运行报告；本次定向 runtime 52 passed | 否（保留观测债务） |
| E Domain | PARTIAL | B 已将 booking/commerce 的决策语义映射为 `travel + subflow`；DomainRegistry/README/AI Runtime 仍注册/展示五个平级域图 | 是，但不得在 A–D 未闭环时实施 |
| F Node Runtime | NOT_STARTED | 未发现 `core/node_runtime` 或 `execute_node_safely`；CS/Travel Base Expert 仍各自实现生命周期 | 是，后续 STOP F |
| G Tool Contract | PARTIAL | 已有 `core/tool_runtime.ToolResult`、`SafeToolExecutor`、`shared.tool_envelope`；未发现旧 Tool 的统一 compatibility normalizer，且 Markdown 存量仍在 | 是，后续 STOP G |
| H Documentation | PARTIAL | README/system-overview/ai-runtime 存在；顶层仍突出三层 Router、Skill、Critique/Supervisor 和五个平级域图 | 是，后续 STOP H |

状态含义：`PARTIAL` 表示有实证实现但未覆盖本任务的全部验收；不把计划文本或未提交代码当成 PASS 证据。

## 2. Completed STOP

### STOP A — PASS

提交 `c3d0a01` 与 STOP A 报告已盘点主调用链、Router、Registry、Capability/Skill/Tool、Workflow、Domain、Expert Runtime 和 MCP 边界。本次复核确认：`TaskRouter` 仍无生产引用、Capability/Skill metadata 仍存在双源、CS/Travel Expert lifecycle 仍有重复，原审计没有把历史债务伪装成已解决。

### STOP D — PASS

`builder.py` 当前仍注册固定 node id：`router`、`planner`、`critique`、`supervisor`；本次也未发现这些 node id 或 `route_selector` 的图边被改名。既有 STOP D 最终报告证明了 checkpoint、trace、SSE、真实 APISIX 登录聊天与 token 归因；本次新跑的 runtime 子集为：

```text
tests/runtime/test_checkpoint_recovery.py
tests/runtime/test_error_fallback.py
tests/runtime/test_runtime_trace.py
tests/runtime/test_token_accounting.py
52 passed in 9.56s
```

本次没有重跑真实网关压测、PostgreSQL checkpoint 或完整 evaluation；报告中的生产运行数据属于历史可追溯证据，不冒充本次新结果。

## 3. Incomplete STOP

### STOP B — Router trace 仍非冻结状态

当前工作区（非 HEAD）新增 `backend/orchestration/router/router_trace.py`，并在 `router_node._with_router_decisions()` 调用它。其将无 query、无候选大对象的投影写入当前 `TraceRecord.metadata["router"]`。未提交的测试在真实 `trace_collector.current()` context 中验证这个结构。

但 `main@7fae31d` 不包含该模块或调用；`2026-09-29-STOP_B_Trace_Metadata_Audit.md` 与 `2026-09-29-STOP_BC_Final_Closure.md` 也均未跟踪。因此：

- 当前工作区：`TraceRecord.metadata["router"]` 可被测试验证；
- 已提交基线：Router 决策只写 LangGraph state，不能作为持久化 Trace metadata 查询；
- 结论：STOP B 不应在提交前标记为 PASS。

### STOP C — Capability SSOT 只覆盖 routing metadata

`backend/orchestration/router/capabilities.yaml` 是以下内容的 SSOT，并通过 `manifest.py` 启动期派生、校验：capability name、Skill binding、domain、routed、risk_level、fast_path、rule keywords、routing examples 和 workflow routing examples。

但每个 `BaseSkill` 子类仍强制且自行声明 `capabilities`、`description`、`params_schema`、`examples`；例如 `SQLSkill`、`RAGSkill`、`ReportSkill` 等。Skill Registry 用这些字段实例化，Planner/Critique 仍消费它们。因此存在两份作者维护的业务能力信息：

| 元数据 | 现状 |
|---|---|
| capability name / Skill binding / domain / risk / routing / routing examples | `capabilities.yaml` 作者源 |
| Skill capabilities / description / params_schema / examples | Skill class 作者源 |
| `ALL_CAPABILITIES` / `ROUTE_EXAMPLES` / routing registry | manifest 派生，不是独立作者源 |
| Tool name / args_schema / description | `@tool` 作者源，属于 Tool 契约 |
| MCP Tool 参数 | 对 `search_knowledge`、`sql_query` 从 Tool args_schema 派生；RAG 的 `list_documents/get_stats`、SQL 的 `list_tables` 是无 Tool 等价的 MCP 专有声明 |
| DB capability metadata | 本次静态检索未发现作为 production capability 定义源的 DB 表/读取路径 |

结论：不得把“manifest 路由 SSOT”扩大表述为“Capability 全 metadata SSOT”。

### STOP E–H 的提前侵入

- **E**：`DomainRouter` 已将 `travel_commerce` / `travel_booking` 决策投影为 `domain=travel`、`subflow=commerce|booking`，这是 STOP B 的语义兼容工作；但 `DomainGraphRegistry` 仍只认识五个顶层名字，README 与 `ai-runtime.md` 仍称“五个域图”。因此是提前出现的**部分语义收敛**，不是 STOP E 完成。
- **F**：未发现 `core/node_runtime`、`execute_node_safely` 或等价公共节点运行时。现存 `core/tool_runtime` 只处理 Tool，不能算 Node Runtime。
- **G**：`ToolResult` / `SafeToolExecutor` / `tool_envelope` 均早于本轮（历史提交 `f8d9590`、`21a63ca`、`38b6c1a`）。它们是可复用基础，不等于 STOP G 的“新 Tool 强制结构化 + 旧 Tool adapter”已完成。
- **H**：已有文档更新，但目标架构术语并未收敛，不能提前结案。

## 4. Hidden Debt

### Router inventory 与实际生产状态

| 组件 | 生产角色 | 结论 |
|---|---|---|
| `graph/router_node.py` | 唯一 LangGraph 主入口；门禁、prefilter、兼容 state、调用 Router、选择下一节点 | 生产中；职责仍偏多 |
| `router/router.py::Router` | hierarchical 开关入口、legacy fallback、shadow | 生产中 |
| `router/hierarchical.py` + `domain_classifier.py` | 粗域分类、域内候选/细路由 | 生产中（按配置） |
| `rule_router.py` / `vector_router.py` / `llm_router.py` | legacy fallback 的内部策略 | 生产中；不是额外架构层 |
| `DomainRouter` / `CapabilityRouter` / `ExecutionModeResolver` | 决策对象兼容规范化 | 生产中，但非唯一主动路由入口 |
| CS/Travel/Selection/Booking/Commerce prefilter | 域门禁与开关/灰度/锁域，非 Capability Router | 生产中 |
| `workflow/router.py::TaskRouter` | 旧 workflow-vs-agent 评分器 | **无生产引用**；仅 `test_router.py`、inventory alert 与 daily report smoke 测试引用；可删除候选，但本轮不删 |
| `customer_service/router/*` | 客服域内部 coarse/fine/domain 检测 | 域内部 Router，不能并入主 Router |
| RAG 查询 Router、SQL Schema Router、Celery queue router | 各子系统内部策略 | 生产中，不属于平台两级 Router |

`TaskRouter` 的生产引用检索命令排除了 `tests/`，只命中其自身定义；测试引用仍存在。它没有生产价值，但删除前应先迁移这些测试到主 Capability/Workflow 适配器，避免“删实现即删覆盖率”。

### Plan Runtime

`planner_node` 负责 LLM 拆 Capability DAG；`critique_node` 已是规则校验、自动修复和 anomaly-only LLM fallback；`supervisor_node` 是确定性 DAG/Send/依赖/previous_outputs 调度。行为上已经符合 Planner → Validator → Executor，但尚未提供 `PlanValidator` / `PlanExecutor` 语义别名，README/AI Runtime 仍以 Critique/Supervisor 为架构术语。node id 必须继续保持不变。

### Expert Runtime

`customer_service/experts/base.py::run_expert_safely` 与 `travel/experts/base.py::run_expert_safely` 都重复：计时、status envelope、异常捕获、日志与 trace/metrics。CS 另含显式线程 timeout、contextvars 与 CS 专属 metrics；Travel 另含 expert span。这些业务 hook 不应强行合并 Result 类型，但可成为 STOP F 抽取公共 lifecycle 的边界。

### Tool / Reporter debt

已确认返回 Markdown 或 Markdown 终态/内容的 Tool 包括：

- `sql_query_tool`（`SQLResult.to_markdown()`）；
- `generate_report_tool` / `run_report`；
- `competitor_analyze_tool`、`competitor_watch_tool`、`competitor_history_tool`、`competitor_watchlist_tool`；
- `data_collection_tool`；
- `web_search_tool` 与 `web_crawl_tool`（后者是正文 Markdown，不一定是用户最终答复）。

`skills/base.py` 还明确记录 18 个 Markdown 存量例外。它们不得在 STOP G 之前被批量改写。现有 `ToolResult` 是运行时治理对象，`shared.tool_envelope` 是部分 JSON 结果封套；未发现覆盖全部旧 Tool 的 compatibility normalizer。

### 资产清单

| 类型 | 当前定义/数量 |
|---|---|
| Skill | 12：BusinessAnalysis、CompetitorAnalysis、DataCollection、DataExport、Email、MapLookup、RAG、Report、SQL、TravelPoi、WebCrawl、WebSearch |
| Capability | 17（3 个 `routed:false`），manifest 是路由事实源 |
| Tool | 34 个 `@tool`；覆盖 competitor(4)、calculator、email(4)、data collection、export、rag、memory(2)、report、sql(2)、map(14)、web(2)、travel POI |
| Workflow | 4：`daily_report`、`inventory_alert`、`market_research`、`selection_decision` |
| Domain Graph | 5：`customer_service`、`travel`、`selection_funnel`、`travel_commerce`、`travel_booking` |
| MCP Adapter | 2 server / 5 tool：RAG（search_knowledge/list_documents/get_stats）与 SQL（sql_query/list_tables） |
| Registries | manifest-derived routing、Skill、Tool、Workflow、Domain Graph、MCP Manager；各自边界合理，不建议创建 UniversalRegistry |

### 真正的 Agent 与普通 Executor

需要 LLM 进行动态任务拆解的是 Planner；`general_chat` 是 no-tool LLM response；Reporter 是呈现生成器；CS Supervisor 仅在兜底路径进行 LLM 判断。Router 中的 LLM fallback 是分类策略，不是 Agent。Critique/Plan Validator、Supervisor/Plan Executor、Skill、Tool、Workflow executor、Expert runtime 与 MCP Server 都是确定性或受控执行器，不应统称 Agent。

## 5. Risk Assessment

### P0 必须补

- **STOP B Trace 变更未提交。** 它当前能通过测试，但没有进入 `main`，且现有结案文档已在未跟踪状态中提前宣称 closure。先独立审查、提交并保留状态/SSE/checkpoint 不变，再更新结论。
- **STOP C 不能以 false PASS 进入 STOP E。** 当前完整 metadata 至少有 manifest 与 Skill class 两份作者源；需要先决定 params/description/examples 的权威归属与启动期派生方式。

### P1 应补

- 将 `DomainRouter` 从“事后决策投影”逐步明确为唯一粗分层边界，或在架构文档中明确当前适配过渡期；不得复制 prefilter 行为。
- 为架构守护补充静态/结构测试：Router 不 import/执行 Tool、Capability Router 不触库、Planner 不 import Tool、Tool 不 import Skill/Planner/Domain、TaskRouter 无生产引用、PlanValidator 不执行 capability、PlanExecutor 不做 LLM 业务判断、MCP 暴露白名单、Java integration 不依赖 MCP。
- 识别并记录 Tool Markdown 存量，而不是在未有 normalizer 时修改输出。
- STOP D 的已知观测债务仍存在：后台摘要 token usage 缺 user/tenant/role/stage 关联；不阻塞 STOP D，但应在独立观测任务处理。

### P2 延后

- `BaseSkill` → CapabilityExecutor 的全仓 rename；
- LangGraph node id 从 critique/supervisor 改名；
- 物理合并 travel/commerce/booking 包；
- 批量迁移 34 个 Tool 返回值；
- 目录重组与 README 总体改写（等 STOP E–G 的事实稳定后）。

### 范围扩大核验

相对 STOP A 提交 `c3d0a01` 到当前 `HEAD`，禁止范围中有三处 RAG 文件发生过改动：`backend/rag/indexing/indexer.py`、`backend/rag/pipeline.py`、`backend/rag/retrieval/bm25_store.py`。相应提交信息为 RAG evaluation runtime 隔离与评测修复，无法仅从 Git 归因其是否由 Architecture Simplification 引入；应将其登记为**同一时间窗口的并行范围变更**，不纳入本轮架构收口成果。未发现同一范围内 SQL、Memory、Authorization、Idempotency、Model Gateway、frontend API、migration、APISIX 或 Docker 的 Architecture Simplification 提交证据。

当前工作区另有与审计无关的三个前端 `tsconfig.json` 修改与 RAG evaluation 数据未跟踪文件；本审计未触碰。Router trace 相关四个未提交项则是本审计必须报告的关键状态，不应被并行工作误提交或覆盖。

## 6. Recommended Next Action

```text
NEXT_STEP_RECOMMENDATION=继续补 STOP A-D；优先完成 STOP B Trace metadata 冻结与 STOP C 完整 Capability metadata SSOT 决策/闭环。
REASON=STOP B 的可查询 Trace 证据尚未提交，STOP C 仅有 routing SSOT 而非完整 metadata SSOT。此时进入 STOP E 会让 Domain 改动建立在未冻结的路由和能力契约上。
```

建议顺序：

1. 将现有 Router trace adapter 与单测作为独立、最小、路径限定提交进行审查；确认只更新 `TraceRecord.metadata`，不改 state/checkpoint/SSE/路由结果。
2. 完成 STOP C 的数据归属设计：manifest 或另一个单源 CapabilitySpec 负责全部业务 capability metadata，Skill 保留执行 runtime；启动时派生并保留兼容字段，禁止全仓 rename。
3. 为上述两项增加缺失的架构守护测试；再重跑 Router、Registry/layer、CS/SQL/RAG/Travel evaluation 与 runtime 契约。
4. 只有 B/C 重新冻结后，再开始 STOP E 的 Domain Registry/Admin/Docs 展示收敛。

## 7. Do Not Touch List

- 不改 LangGraph `router`、`planner`、`critique`、`supervisor` node id；
- 不改 checkpoint schema、task resume、`/chat/stream` 与 SSE frame protocol；
- 不改 RAG 核心检索算法、SQL 安全链、Memory 语义、Authorization、Idempotency、Model Gateway；
- 不改 frontend/frontend-admin/frontend-cs API 契约或数据库 migration；
- 不删除 legacy Router、hierarchical Router 或 `TaskRouter`，直至其替代测试/生产引用均有证据；
- 不批量变更旧 Tool 返回格式、不把内部 Tool MCP 化、不将 Java integration 改经 MCP；
- 不覆盖当前工作区中其他会话的未提交前端、RAG 数据或 Router trace 变更。

## 验证与评测证据

本次只读/定向测试：

```text
Router + cross-domain continuity + TaskRouter tests: 57 passed in 80.74s
Registry/layer/ADR + Planner critique tests:        46 passed in 69.44s
Runtime checkpoint/error/trace/token tests:         52 passed in 9.56s
```

历史 STOP B/C closure 记录：Router 160 passed、CS 2 passed、SQL 14 passed、RAG 16 passed、Travel 2 passed、runtime 40 passed；本次未重跑这些 evaluation，故它们仅为历史证据。新鲜 Travel/CS/SQL/RAG evaluation 需要在 STOP B/C 补修完成后运行。

现有守护覆盖 manifest/Skill 双向一致性、`@tool` 注册完整性与 Tool 不在 Skill 层；尚未覆盖本报告 P1 所列的全部架构依赖方向约束。

## Final Status

```text
ARCHITECTURE_SIMPLIFICATION_STATUS_RECONCILED=true
STOP_A_PASS=true
STOP_B_PASS=false
STOP_C_PASS=false
STOP_D_PASS=true
ROUTING_CONSOLIDATION_PASS=false
CAPABILITY_SSOT_PASS=false
DOMAIN_BOUNDARY_PASS=false
PLAN_RUNTIME_PASS=true
BACKWARD_COMPATIBILITY_PASS=true
ARCHITECTURE_SIMPLIFICATION_PASS=false
```

`BACKWARD_COMPATIBILITY_PASS=true` 的依据是 STOP B/D 的兼容 state、旧 `route_mode`、node id、checkpoint/SSE 保持及本次定向回归；它不表示所有后续 STOP 已完成。

---

## 收官补记（2026-09-29 同日晚间）

本报告识别的两大阻塞缺口已在当日闭环。上文 Final Status 反映的是审计时点，以本节为准：

- **STOP B 已冻结**：Trace metadata 修复以 `984a9ba`
  （fix(router): persist sanitized decisions in trace metadata）提交，含
  `router_trace.py` 脱敏投影、`router_node` 写入与真实 current-trace 回归测试。
  结案记录 `2026-09-29-STOP_BC_Final_Closure.md`（初稿误将 STOP C 一并标 PASS，已更正为仅覆盖 STOP B）。
- **STOP C 已结案**：Capability 业务 metadata（description / params_schema /
  planner_examples）收口至 `capabilities.yaml` 唯一作者源，以 `39b3c8c`
  （refactor(capabilities): make manifest the single source of capability metadata）
  提交；Skill 仅保留执行 runtime，兼容属性由 `skills.metadata.bind_manifest_metadata()`
  启动期 fail-fast 绑定。结案记录 `2026-09-29-STOP_C-Capability-SSOT-Closure.md`。

```text
STOP_A_PASS=true
STOP_B_PASS=true                  # 984a9ba
STOP_C_PASS=true                  # 39b3c8c
STOP_D_PASS=true
ROUTING_CONSOLIDATION_PASS=true   # 审计时的唯一缺口（Trace 持久化）已由 984a9ba 补齐
CAPABILITY_SSOT_PASS=true         # 17/17 capability metadata 单一作者源 + fail-fast 绑定
DOMAIN_BOUNDARY_PASS=false        # STOP E 未开始（仅 travel 决策语义映射有部分基础）
ARCHITECTURE_SIMPLIFICATION_PASS=false  # E/F/G/H 未完成，整体不得结案
NEXT_STEP_RECOMMENDATION=进入 STOP E（Domain 边界收口）
```

交接复验证据（接管会话在代码冻结下新鲜运行）：四个契约门（registry / layer /
adr0001 / base_output）45 passed；tests/skills + tests/orchestration +
tests/evaluation 共 906 passed / 112 skipped / 0 failed；
tests/api/test_registry_overview_api + test_audit_fixes + test_map_lookup_skill
35 passed；全仓收集冒烟 7489 tests 无导入错误；改动文件 py_compile 全过。
