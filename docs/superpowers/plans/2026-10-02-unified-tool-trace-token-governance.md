# Unified Tool Trace and Token Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 统一全平台 Tool Trace、补齐旅游请求 Trace、让监控下钻覆盖所有业务入口，并增加安全的管理员失败探针与 Token 追溯展示。

**Architecture:** 在 `core.tool_runtime` 增加可复用 Tool Span 生命周期，已有 BaseSkill Span 由调用方持有，其他执行入口由统一执行器或直调适配器创建。旅游 REST/SSE 入口建立 `workflow_name=agent` 请求 Trace；管理端失败探针复用同一指标/Trace出口，只生成模拟记录，不调用真实上游。Token 继续使用既有 `llm_usage` 与 Trace 关联，不另建成本事实源。

**Tech Stack:** FastAPI、Pydantic、Prometheus、现有 `trace_collector`、Next.js 14、React、Vitest、pytest。

**Spec:** `docs/superpowers/specs/2026-10-02-unified-tool-trace-token-governance-design.md`

## Global Constraints

- 所有统一错误分类必须复用 `observability/error_taxonomy.py` 的七分类映射。
- Tool 指标和 Trace 的 Tool 键必须使用 `tool_contracts.lock.json` 中的契约名。
- 旅游域真实 Tool 失败不得被映射为空结果。
- 强制失败测试在 `ENVIRONMENT=production` 下必须拒绝，且不得调用外部服务。
- Token 事实源继续使用既有 `llm_usage` 与 Trace 关联，不新增平行成本表。
- 不改动其他会话已有的工作区改动，不执行 reset/checkout/清理。

## Review Focus

- 没有 active Trace 时，Tool 调用仍能完成业务但不能伪造持久化 Trace；对应 Tool Trace 单测。
- BaseSkill 已有 Span 时，统一执行器不能生成重复 Tool Span；对应去重测试。
- 旅游 SSE 在线程中必须 bind Trace；对应同步与流式入口测试。
- Tool 失败文字可能只在 metrics 中；对应管理端 errors 读取 metrics 测试。
- 生产环境和未知 Tool 不能使用失败探针；对应接口拒绝测试。

---

### Task 1: 统一 Tool Span 辅助层与失败读取

**Files:**
- Create: `backend/core/tool_runtime/tracing.py`
- Modify: `backend/core/tool_runtime/executor.py`
- Modify: `backend/app/api/routes/admin_tools.py`
- Test: `backend/tests/tool_runtime/test_tool_tracing.py`

**Interfaces:**
- `start_tool_span(tool_name, capability, params, agent) -> Span`
- `finish_tool_span(span, result, output=None) -> None`
- `safe_tool_executor.run(..., trace_span=None, trace_capability="", trace_agent="")`

- [ ] **Step 1: Write failing tests** for canonical input fields, error metrics/events, executor-created Span, supplied-Span deduplication, and admin errors reading `span.metrics`.
- [ ] **Step 2: Run the focused pytest file** and verify failures are caused by missing tracing behavior.
- [ ] **Step 3: Implement the tracing helper** with noop-safe behavior, active-trace parent selection, contract Tool name, and unified error class from `unify_tool_status`.
- [ ] **Step 4: Integrate the helper into `SafeToolExecutor`** only when the caller did not supply an existing Span; preserve existing retry, circuit breaker, and result semantics.
- [ ] **Step 5: Update `/admin/tools/errors`** to include error/skipped Tool spans and read error fields from events, errors, and metrics in that order.
- [ ] **Step 6: Run the focused tests** and confirm all pass.

### Task 2: 补齐旅游 REST/SSE 与直调 Tool Trace

**Files:**
- Modify: `backend/travel/services/live_search_service.py`
- Modify: `backend/travel/core/events.py`
- Modify: `backend/app/api/routes/travel.py`
- Test: `backend/tests/travel/test_live_search_trace.py`
- Test: `backend/tests/api/test_travel_trace.py`

**Interfaces:**
- `run_travel_tool` keeps its current public signature and emits the same SSE events.
- Travel request Trace uses `workflow_name="agent"` and tags `travel_status`, `travel_run_id`, and `travel_destination`.

- [ ] **Step 1: Write failing tests** for direct train/map Tool spans, success/error closure, `/travel/plan` request Trace, and worker-thread binding for `/travel/plan/stream`.
- [ ] **Step 2: Run the tests** and verify they fail because current direct calls have no active Tool/request Trace.
- [ ] **Step 3: Add direct Tool Span lifecycle** to `live_search_service._invoke`, passing canonical capability and travel agent metadata without changing returned envelopes.
- [ ] **Step 4: Add request Trace start/finish** to REST and SSE travel entrypoints; bind/unbind in the SSE worker and preserve existing exception/terminal event behavior.
- [ ] **Step 5: Run the focused travel/API tests** and verify the new spans and tags.

### Task 3: Workflow and monitoring unified query surface

**Files:**
- Modify: `backend/orchestration/workflow/skill_adapter.py`
- Modify: `backend/app/api/routes/observability.py`
- Modify: `frontend-admin/src/lib/observability/source.ts`
- Modify: `frontend-admin/src/components/workbench/observability/TracesPanel.tsx`
- Test: `backend/tests/orchestration/workflow/test_skill_trace.py`
- Test: `backend/tests/api/test_observability_tool_filter.py`
- Test: `frontend-admin/src/components/workbench/observability/TracesPanel.test.tsx`

- [ ] **Step 1: Write failing tests** proving direct Workflow SQL creates a Tool Span and `has_tool` queries are not restricted to `workflow_name=agent`.
- [ ] **Step 2: Run focused backend/frontend tests** and verify the expected failures.
- [ ] **Step 3: Pass trace metadata through Workflow direct execution** so the executor-created Span includes `sql.query` and `execute_sql_tool`.
- [ ] **Step 4: Change the monitoring data source** so normal interactive traces remain readable while Tool downlinks search all workflow types.
- [ ] **Step 5: Run focused tests** and confirm travel, Workflow, and Agent Tool filters share the same contract key.

### Task 4: 管理端强制失败测试入口

**Files:**
- Modify: `backend/app/api/routes/admin_tools.py`
- Modify: `frontend-admin/src/api/governance.ts`
- Modify: `frontend-admin/src/app/tools/page.tsx`
- Test: `backend/tests/api/test_admin_tool_failure_probe.py`
- Test: `frontend-admin/src/app/tools/page.test.tsx`

**Interfaces:**
- `POST /api/admin/tools/failure-probe`
- Request: `{tool: string, error_class: UnifiedErrorClass, domain?: string}`
- Response: `{trace_id: string, tool: string, error_class: string, status: string, simulated: true}`

- [ ] **Step 1: Write failing API/UI tests** for allowed tool, each mapped error class, unknown tool rejection, production rejection, and visible Trace ID/result.
- [ ] **Step 2: Run focused tests** and verify the endpoint and panel are missing.
- [ ] **Step 3: Implement the endpoint** using lock validation, `ToolStatus` mapping, `record_tool_result`, and the shared Tool Span helper; do not invoke a real Tool.
- [ ] **Step 4: Add the panel** with Tool/error-class selectors, explicit simulated-data copy, loading/error/success states, and refresh of inventory/stats after success.
- [ ] **Step 5: Run backend/frontend tests** and confirm the panel contract.

### Task 5: Token 追溯与端到端验证

**Files:**
- Inspect/modify only if required: `backend/observability/llm_usage.py`, `backend/observability/tracer.py`, `backend/app/api/routes/observability.py`, `frontend-admin/src/components/observability/trace/TraceDetailPanels.tsx`
- Test: `backend/tests/observability/test_trace_token_provenance.py`
- Test: `frontend-admin/src/components/observability/trace/TraceDetailPanels.test.tsx`

- [ ] **Step 1: Write failing tests** for Trace token/cost fields and preservation of existing `llm_usage` attribution.
- [ ] **Step 2: Run focused tests** to distinguish missing data from frontend rendering gaps.
- [ ] **Step 3: Add only the minimal missing projection** from existing `llm_usage`/Trace fields; do not create a second cost store.
- [ ] **Step 4: Run backend/frontend type and unit tests.**
- [ ] **Step 5: Use the authenticated browser** to generate timeout, network, and business probes; verify Tool stats, expanded failure source, Trace downlink, and Token fields in `/observability/monitoring`.
- [ ] **Step 6: Run the final verification set and inspect `git diff --check`; report exact results without claiming unrelated dirty files are ours.
