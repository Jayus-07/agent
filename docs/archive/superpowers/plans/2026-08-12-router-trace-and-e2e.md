# Router Trace 埋点 + Server E2E 验证 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Server E2E 验证三路分流 + Router 决策埋点到 Trace（标准粒度：每层判断结果）

**Architecture:** 现有 `TraceCollector` + `TraceMiddleware` 已就绪。在 `Router.route()` 内加 `start_span`/`add_event`/`end_span`，在 `builder.py` 用 `TraceMiddleware` 包装节点。不改 TraceCollector 核心 API，不改 Prom 指标。

**Tech Stack:** Python 3.12, FastAPI, LangGraph, 自建 TraceCollector (SQLite 持久化)

**Design doc:** `docs/superpowers/specs/2026-08-12-router-trace-and-e2e-design.md`

## Global Constraints

- 不改 TraceCollector 核心 API（start_span / end_span / add_event 已稳定）
- Prom 指标 `record_router_decision()` 保持不变，Trace 是补充不是替代
- Span 粒度：标准（每层 rule/vector/llm 判断结果记录为 event）
- 执行顺序：P0（验证）→ P1（埋点）

---

## 文件结构

```
backend/orchestration/router/router.py          ← 修改: route() 加 trace span
backend/observability/trace_middleware.py        ← 修改: 加 router/skill_executor/workflow_executor 标签
backend/orchestration/graph/builder.py           ← 修改: 所有节点用 TraceMiddleware 包装
```

---

### Task 1: P0 — Server 启动 + /chat/stream 三路分流验证

**Files:**
- 不改任何代码（纯验证）

**Interfaces:**
- Consumes: `POST /api/chat/stream` SSE 端点
- Produces: 验证报告（3 条请求的结果）

- [ ] **Step 1: 确认 server 能启动**

```powershell
cd "d:\Program Files\workplace\agent\backend"
D:/Python/python.exe -c "from backend.app.server import app; print('OK: app imported')"
```

预期: `OK: app imported`

- [ ] **Step 2: 后台启动 server**

```powershell
cd "d:\Program Files\workplace\agent\backend"
Start-Process -NoNewWindow -FilePath "D:/Python/python.exe" -ArgumentList "-m", "uvicorn", "backend.app.server:app", "--host", "0.0.0.0", "--port", "8000"
Start-Sleep -Seconds 15
```

等 15 秒让 RAG 预热完成。如果 8000 端口被占用，先 `Stop-Process` 或换端口。

- [ ] **Step 3: 测试 workflow 模式 — "每天跑日报"**

```powershell
curl -X POST http://localhost:8000/api/chat/stream `
  -H "Content-Type: application/json" `
  -d '{"question":"每天跑日报","session_id":"e2e-p0","kb_id":"default","request_id":"w1"}'
```

验证点:
- HTTP 200
- SSE 事件包含 `event: meta` → `event: status` → `event: delta` → `event: done`
- `done` 事件中无 error
- 响应内容包含 "daily_report" 或 "日报" 或 "workflow"

- [ ] **Step 4: 测试 direct 模式 — "查销量"**

```powershell
curl -X POST http://localhost:8000/api/chat/stream `
  -H "Content-Type: application/json" `
  -d '{"question":"查销量","session_id":"e2e-p0","kb_id":"default","request_id":"d1"}'
```

验证点:
- HTTP 200
- SSE 事件结构完整 (meta → status → delta → done)
- 不走 Planner（速度明显比 plan 模式快）

- [ ] **Step 5: 测试 plan 模式 — "差评处理"**

```powershell
curl -X POST http://localhost:8000/api/chat/stream `
  -H "Content-Type: application/json" `
  -d '{"question":"差评处理","session_id":"e2e-p0","kb_id":"default","request_id":"p1"}'
```

验证点:
- HTTP 200
- SSE 事件结构完整
- 走 Planner → Supervisor → Skills 路径（可从 status 事件看到 planner/supervisor 节点）

- [ ] **Step 6: 汇总 P0 结果**

确认 3/3 通过，无 5xx 错误，然后停掉 server（后续 P1 需要重启）。

```powershell
Get-Process -Name "python" -ErrorAction SilentlyContinue | Where-Object {$_.CommandLine -like "*uvicorn*"} | Stop-Process -Force
```

- [ ] **Step 7: Commit（空提交，记录验证结果）**

```bash
git commit --allow-empty -m "verify(p0): /chat/stream 三路分流 E2E 通过"
```

---

### Task 2: P1 — Router.route() 加 Trace Span + trace_middleware 补标签

**Files:**
- Modify: `backend/orchestration/router/router.py` (全文件重写，~105 行)
- Modify: `backend/observability/trace_middleware.py:21-30` (追加 3 个标签)

**Interfaces:**
- Consumes: `trace_collector.start_span()` / `add_event()` / `end_span()` (已有 API)
- Produces: Router 决策以 Span 形式写入 trace（含 events: rule_check / vector_check / llm_check）

- [ ] **Step 1: 修改 `router.py` — 加 trace span**

**替换整个 `route()` 方法**（原 34 行 → 新 ~60 行）。关键变更：
1. 方法开头 `start_span("router", kind="router")`
2. 每层尝试后用 `add_event()` 记录判断结果
3. 每个 return 前 `end_span()` 带决策 output + metrics

用 Edit 工具逐步替换。

先替换 import 区域（加 trace_collector 导入）：

```python
# old_string:
from backend.orchestration.router.types import (
    CapabilityScore,
    ExecutionMode,
    RouteDecision,
)
from backend.orchestration.router.rule_router import RuleRouter
from backend.orchestration.router.vector_router import VectorRouter
from backend.orchestration.router.llm_router import LLMRouter
from backend.shared.logger import logger

# new_string:
from backend.orchestration.router.types import (
    CapabilityScore,
    ExecutionMode,
    RouteDecision,
)
from backend.orchestration.router.rule_router import RuleRouter
from backend.orchestration.router.vector_router import VectorRouter
from backend.orchestration.router.llm_router import LLMRouter
from backend.observability import trace_collector
from backend.shared.logger import logger
```

然后替换整个 `route()` 方法体：

```python
# old_string:
    def route(self, query: str) -> RouteDecision:
        """同步路由入口。

        链路:
          1. Rule (0.001s)
          2. Embedding (0.03s)
          3. LLM (3-5s)
        """
        from backend.observability.metrics import record_router_decision

        t0 = time.time()
        # 1. Rule Router（强信号）
        result = self.rule.route(query)
        if result and result.confidence >= 0.8:
            logger.info(
                f"[Router] Rule 命中: {result.reason} (latency={int((time.time()-t0)*1000)}ms)"
            )
            record_router_decision(result.execution_mode.value, "rule", result.confidence)
            return result

        # 2. Embedding Router（语义匹配）
        result = self.vector.route(query)
        if result and result.confidence >= 0.85:
            logger.info(
                f"[Router] Embedding 命中: {result.reason} (latency={int((time.time()-t0)*1000)}ms)"
            )
            record_router_decision(result.execution_mode.value, "embedding", result.confidence)
            return result

        # 3. LLM Router（兜底）
        result = self.llm.route(query)
        logger.info(
            f"[Router] LLM 兜底: {result.reason} (latency={int((time.time()-t0)*1000)}ms)"
        )
        record_router_decision(result.execution_mode.value, "llm", result.confidence)
        return result

# new_string:
    def route(self, query: str) -> RouteDecision:
        """同步路由入口 — 含 Trace Span（每层判断结果记录为 event）。

        链路:
          1. Rule (0.001s)
          2. Embedding (0.03s)
          3. LLM (3-5s)
        """
        from backend.observability.metrics import record_router_decision

        t0 = time.time()

        # ── Trace Span: 路由决策 ──
        span = trace_collector.start_span(
            "router", name="路由决策", kind="router",
            input={"query": query},
        )
        final_layer = "llm"  # 默认 LLM 兜底

        # 1. Rule Router（强信号）
        result = self.rule.route(query)
        rule_conf = result.confidence if result else 0.0
        rule_matched = result is not None and result.confidence >= 0.8
        trace_collector.add_event(
            span, "rule_check", "info",
            f"Rule 层: matched={rule_matched} confidence={rule_conf:.2f}",
            {"layer": "rule", "matched": rule_matched, "confidence": rule_conf},
        )
        if rule_matched:
            final_layer = "rule"
            logger.info(
                f"[Router] Rule 命中: {result.reason} (latency={int((time.time()-t0)*1000)}ms)"
            )
            record_router_decision(result.execution_mode.value, "rule", result.confidence)
            trace_collector.end_span(
                span,
                output=result.model_dump(),
                metrics={"layer": final_layer, "confidence": result.confidence,
                         "mode": result.execution_mode.value},
                status="success",
            )
            return result

        # 2. Embedding Router（语义匹配）
        result = self.vector.route(query)
        vec_conf = result.confidence if result else 0.0
        vec_matched = result is not None and result.confidence >= 0.85
        vec_top = result.candidates[0].name if (result and result.candidates) else ""
        trace_collector.add_event(
            span, "vector_check", "info",
            f"Embedding 层: matched={vec_matched} confidence={vec_conf:.2f} top={vec_top}",
            {"layer": "vector", "matched": vec_matched, "confidence": vec_conf,
             "top_capability": vec_top},
        )
        if vec_matched:
            final_layer = "embedding"
            logger.info(
                f"[Router] Embedding 命中: {result.reason} (latency={int((time.time()-t0)*1000)}ms)"
            )
            record_router_decision(result.execution_mode.value, "embedding", result.confidence)
            trace_collector.end_span(
                span,
                output=result.model_dump(),
                metrics={"layer": final_layer, "confidence": result.confidence,
                         "mode": result.execution_mode.value},
                status="success",
            )
            return result

        # 3. LLM Router（兜底）
        result = self.llm.route(query)
        final_layer = "llm"
        llm_conf = result.confidence if result else 0.0
        trace_collector.add_event(
            span, "llm_check", "info",
            f"LLM 兜底: confidence={llm_conf:.2f} reason={result.reason}",
            {"layer": "llm", "matched": True, "confidence": llm_conf,
             "reason": result.reason or ""},
        )
        logger.info(
            f"[Router] LLM 兜底: {result.reason} (latency={int((time.time()-t0)*1000)}ms)"
        )
        record_router_decision(result.execution_mode.value, "llm", result.confidence)
        trace_collector.end_span(
            span,
            output=result.model_dump(),
            metrics={"layer": final_layer, "confidence": result.confidence,
                     "mode": result.execution_mode.value},
            status="success",
        )
        return result
```

- [ ] **Step 2: 修改 `trace_middleware.py` — 加新节点标签**

在 `_NODE_LABELS` dict 追加 3 个条目（`builder.py` 新增的 V2 节点）：

```python
# old_string:
    "business_analysis_skill": "业务分析",
}

# new_string:
    "business_analysis_skill": "业务分析",
    "router":              "路由决策",
    "skill_executor":      "直接执行",
    "workflow_executor":   "工作流执行",
}
```

- [ ] **Step 3: 验证 — Router Span 在无 active trace 时不崩溃**

```powershell
cd "d:\Program Files\workplace\agent\backend"
D:/Python/python.exe -X utf8 -c "
from backend.orchestration.router import get_router
r = get_router()
# 无 active trace 时 route() 不崩溃（start_span 返回 noop）
d = r.route('每天跑日报')
print(f'OK: mode={d.execution_mode.value} conf={d.confidence:.2f}')
"
```

预期: `OK: mode=workflow conf=0.95`

- [ ] **Step 4: Commit**

```bash
git add backend/orchestration/router/router.py backend/observability/trace_middleware.py
git commit -m "feat(trace): Router 决策埋点到 Trace Span + 补 V2 节点标签"
```

---

### Task 3: P1 — builder.py 用 TraceMiddleware 包装所有节点

**Files:**
- Modify: `backend/orchestration/graph/builder.py:84-142` (build_graph 函数)

**Interfaces:**
- Consumes: `trace_middleware.wrap_sync_node()` (已有 API)
- Produces: 所有 LangGraph 节点执行时自动记录 Span

- [ ] **Step 1: 修改 `builder.py` — import TraceMiddleware**

```python
# old_string:
from backend.orchestration.graph.router_node import router_node, route_selector
from backend.orchestration.graph.direct_executor import skill_executor_node, workflow_executor_node

# new_string:
from backend.orchestration.graph.router_node import router_node, route_selector
from backend.orchestration.graph.direct_executor import skill_executor_node, workflow_executor_node
from backend.observability.trace_middleware import trace_middleware
```

- [ ] **Step 2: 修改 `builder.py` — 包装内置节点**

将 `build_graph()` 中所有 `wf.add_node(...)` 用 `trace_middleware.wrap_sync_node()` 包装：

```python
# old_string:
    # ── 内置节点（永远不变）────────────────────────
    wf.add_node("router", router_node)  # 2026-08-11：3 层 fallback Router
    wf.add_node("skill_executor", skill_executor_node)  # V2: direct mode
    wf.add_node("workflow_executor", workflow_executor_node)  # V2: workflow mode
    wf.add_node("planner", planner_node)
    wf.add_node("critique", critique_node)
    wf.add_node("supervisor", supervisor_node)
    wf.add_node("reporter", reporter_node)

# new_string:
    # ── 内置节点（永远不变，TraceMiddleware 自动记录 Span）──
    wf.add_node("router", trace_middleware.wrap_sync_node("router", router_node))
    wf.add_node("skill_executor", trace_middleware.wrap_sync_node("skill_executor", skill_executor_node))
    wf.add_node("workflow_executor", trace_middleware.wrap_sync_node("workflow_executor", workflow_executor_node))
    wf.add_node("planner", trace_middleware.wrap_sync_node("planner", planner_node))
    wf.add_node("critique", trace_middleware.wrap_sync_node("critique", critique_node))
    wf.add_node("supervisor", trace_middleware.wrap_sync_node("supervisor", supervisor_node))
    wf.add_node("reporter", trace_middleware.wrap_sync_node("reporter", reporter_node))
```

- [ ] **Step 3: 修改 `builder.py` — 包装动态 Skill 节点**

Skill 节点是 `_make_sync(func)` 包过的，再包一层 `wrap_sync_node`：

```python
# old_string:
    # ── Skill 节点（自动发现：谁注册了就加谁）───────
    for name, func in tool_registry.get_skill_nodes().items():
        wf.add_node(name, _make_sync(func))
        wf.add_edge(name, "supervisor")  # 完成 → 回到 Supervisor
        logger.info(f"[Graph] 自动注册 Skill 节点: {name}")

# new_string:
    # ── Skill 节点（自动发现 + TraceMiddleware 自动记录 Span）──
    for name, func in tool_registry.get_skill_nodes().items():
        sync_func = _make_sync(func)
        traced_func = trace_middleware.wrap_sync_node(name, sync_func)
        wf.add_node(name, traced_func)
        wf.add_edge(name, "supervisor")  # 完成 → 回到 Supervisor
        logger.info(f"[Graph] 自动注册 Skill 节点: {name}")
```

- [ ] **Step 4: 验证 — Graph 编译成功**

```powershell
cd "d:\Program Files\workplace\agent\backend"
D:/Python/python.exe -X utf8 -c "
from backend.orchestration.graph.builder import build_graph
g = build_graph()
print(f'OK: graph compiled, nodes={list(g.nodes.keys())}')
"
```

预期: 输出所有节点名，包含 router / skill_executor / workflow_executor / planner 等。

- [ ] **Step 5: Commit**

```bash
git add backend/orchestration/graph/builder.py
git commit -m "feat(trace): builder.py 全节点启用 TraceMiddleware"
```

---

### Task 4: P1 — 端到端 Trace 验证（router span 在 SQLite 中可见）

**Files:**
- 不改代码（纯验证）

**Interfaces:**
- Consumes: `trace_store.list()` / `trace_store.get()` (已有 API)
- Produces: 验证报告

- [ ] **Step 1: 重启 server（加载新代码）**

```powershell
cd "d:\Program Files\workplace\agent\backend"
# 停旧进程
Get-Process -Name "python" -ErrorAction SilentlyContinue | Where-Object {$_.CommandLine -like "*uvicorn*"} | Stop-Process -Force
Start-Sleep -Seconds 2
# 启新进程
Start-Process -NoNewWindow -FilePath "D:/Python/python.exe" -ArgumentList "-m", "uvicorn", "backend.app.server:app", "--host", "0.0.0.0", "--port", "8000"
Start-Sleep -Seconds 15
```

- [ ] **Step 2: 发一条请求触发 Router → 查 Trace**

```powershell
curl -X POST http://localhost:8000/api/chat/stream `
  -H "Content-Type: application/json" `
  -d '{"question":"每天跑日报","session_id":"e2e-p1","kb_id":"default","request_id":"t1"}'
```

等 SSE 流结束（看到 `event: done`）。

- [ ] **Step 3: 验证 Trace 中有 router span**

```powershell
cd "d:\Program Files\workplace\agent\backend"
D:/Python/python.exe -X utf8 -c "
from backend.observability.trace_store import get_trace_store
import json

store = get_trace_store()
traces = store.list(5)
found_router = False
for t in traces:
    tid = t.get('id', '')
    full = store.get(tid)
    if not full:
        continue
    spans = full.get('spans', [])
    for s in spans:
        name = s.get('name', '')
        if '路由' in name or 'router' in str(s.get('span_id', '')):
            print(f'Trace {tid[:12]}: question={t.get(\"question\",\"\")[:40]}')
            print(f'  Router span: name={name} kind={s.get(\"kind\",\"\")} status={s.get(\"status\",\"\")}')
            events = s.get('events', [])
            for evt in events:
                print(f'    event: {evt.get(\"name\",\"\")} — {evt.get(\"message\",\"\")[:80]}')
            metrics = s.get('metrics', {})
            print(f'    metrics: layer={metrics.get(\"layer\",\"?\")} mode={metrics.get(\"mode\",\"?\")} confidence={metrics.get(\"confidence\",\"?\")}')
            output = s.get('output', {})
            if output:
                print(f'    output: mode={output.get(\"execution_mode\",\"?\")} workflow={output.get(\"workflow_name\",\"?\")}')
            found_router = True
            break
if not found_router:
    print('WARNING: 未找到 router span')
else:
    print('OK: Router span 已记录到 Trace')
"
```

预期输出:
```
Trace xxx: question=每天跑日报
  Router span: name=路由决策 kind=router status=success
    event: rule_check — Rule 层: matched=True confidence=0.95
    metrics: layer=rule mode=workflow confidence=0.95
    output: mode=workflow workflow=daily_report
OK: Router span 已记录到 Trace
```

- [ ] **Step 4: 验证 Prom 指标仍然正常**

```powershell
curl http://localhost:8000/metrics 2>$null | Select-String "router_"
```

预期: 能看到 `router_decision_total`、`router_layer_total`、`router_confidence` 指标（Prom 指标和 Trace 共存）。

- [ ] **Step 5: 验收脚本复跑（确认无回归）**

```powershell
cd "d:\Program Files\workplace\agent"
D:/Python/python.exe -X utf8 scripts/verify_router.py
```

预期: 39/39 通过（同 f655a5b）。

- [ ] **Step 6: 停 server + Commit**

```powershell
Get-Process -Name "python" -ErrorAction SilentlyContinue | Where-Object {$_.CommandLine -like "*uvicorn*"} | Stop-Process -Force
```

```bash
git commit --allow-empty -m "verify(p1): Router Trace Span + TraceMiddleware E2E 验证通过"
```

---

## 完成检查清单

- [ ] P0: 3 路 `/chat/stream` 均返回 200 + SSE 事件完整
- [ ] P1: Router 决策以 Span 记录到 SQLite trace_store
- [ ] P1: Span events 包含 rule_check / vector_check / llm_check
- [ ] P1: Span metrics 含 layer / confidence / mode
- [ ] P1: Prom 指标 `router_decision_total` 等继续工作
- [ ] P1: `scripts/verify_router.py` 39/39 通过，无回归
