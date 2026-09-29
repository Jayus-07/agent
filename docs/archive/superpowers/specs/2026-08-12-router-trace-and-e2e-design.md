# Router Trace 埋点 + Server E2E 验证

> 2026-08-12 | 状态: 设计完成

## 背景

Router 已有 Prom 指标埋点（`router_decision_total` / `router_layer_total` / `router_confidence`），验收 39/39 通过。但缺少 Trace 级别的决策上下文记录（每层判断结果、候选变化），排查问题时只能看聚合指标，无法回溯单次决策。

现有 Trace 基础设施（`TraceCollector`、`TraceMiddleware`、`SpanKind.ROUTER`）已就绪，核心工作是**连线**。

## 目标

- **P0**: 启动 server，通过 `/chat/stream` 验证 workflow/direct/plan 三路分流端到端
- **P1**: Router 决策埋点到 Trace，记录每层判断结果（标准粒度）

## 非目标

- 不引入 OpenTelemetry SDK / Langfuse（自建 SQLite trace 已满足需求）
- 不改 TraceCollector 核心 API
- 不改造 Planner/Critique/Supervisor 的 trace（聚焦 Router）

---

## P0: Server E2E 验证

### 步骤

1. 启动 backend server（`uvicorn` 或项目启动脚本）
2. 发送 3 条 SSE 请求：
   - `"每天跑日报"` → 期望 `workflow` 模式 → `daily_report`
   - `"查销量"` → 期望 `direct` 模式 → `sql.query`
   - `"差评处理"` → 期望 `plan` 模式 → Planner 生成 DAG
3. 验证：SSE 事件结构完整（meta→status→delta→done）、workflow status=success

### 验收标准

- 3 条请求均返回 HTTP 200
- SSE 流包含 meta / status / delta / done 事件
- workflow 路径返回 `status=success`
- 无报错、无 5xx

---

## P1: Router Trace 埋点

### 改动清单（4 文件，~40 行）

#### 1. `backend/orchestration/router/router.py` — Router 主流程加 Span

`route()` 方法内：

```python
# 在 route() 开头
span = trace_collector.start_span(
    "router", name="路由决策", kind="router",
    input={"query": query}
)

# 每层尝试后 add_event
span_events = []
# Rule 层
result = self.rule.route(query)
span_events.append({"layer": "rule", "confidence": result.confidence if result else 0, 
                     "matched": result is not None and result.confidence >= 0.8})
if result and result.confidence >= 0.8:
    # ... 现有 Prom 埋点 + return（前面加 end_span）

# Vector 层（同理）
# LLM 层（同理）

# 最终 end_span
trace_collector.end_span(span, 
    output=decision.model_dump(),
    metrics={"layer": final_layer, "confidence": decision.confidence, "mode": mode},
    status="success"
)
```

#### 2. `backend/orchestration/graph/builder.py` — 启用车轮

用已有 `TraceMiddleware` 包装关键节点：

```python
from backend.observability.trace_middleware import trace_middleware

wf.add_node("router", trace_middleware.wrap_sync_node("router", router_node))
wf.add_node("skill_executor", trace_middleware.wrap_sync_node("skill_executor", skill_executor_node))
wf.add_node("workflow_executor", trace_middleware.wrap_sync_node("workflow_executor", workflow_executor_node))
```

planner / critique / supervisor / reporter 也一并包装（已有中间件，零成本）。

#### 3. `backend/orchestration/graph/router_node.py` — 无需改动

`router_node` 内部调 `router.route()`，trace span 在 Router 内部记录。节点本身被 TraceMiddleware 包裹后会自动记录外层 span。

#### 4. `backend/app/api/routes/chat.py` — 无需改动

`MultiAgentSystem.stream_events()` 已在内部调用 `trace_collector.start()`，链路完整。

### 最终 Trace Span 树

```
Trace
├─ root (agent)
│  ├─ router (router)                    ← 新增
│  │   events:
│  │     {layer: "rule", matched: false, confidence: 0.6}
│  │     {layer: "vector", matched: true, confidence: 0.85, top: "sql.query"}
│  │   output: {execution_mode: "direct", candidates: [...], confidence: 0.85, reason: "..."}
│  │   metrics: {layer: "vector", confidence: 0.85, mode: "direct"}
│  ├─ skill_executor / workflow_executor / planner
│  ├─ ...
│  └─ reporter
```

### Prom 指标保持不变

`record_router_decision()` 继续调用。Trace 是补充（单次决策回溯），不是替代 Prom（聚合告警）。

---

## 执行顺序

1. **P0 先行**：启动 server → 验证 3 路 `/chat/stream` → 确认无回归
2. **P1 跟进**：加 Router trace span → 验证 trace 数据出现在 SQLite

---

## 测试验证

### P0 验证命令

```powershell
# 启动 server（后台）
cd backend
D:/Python/python.exe -m uvicorn backend.app.server:app --host 0.0.0.0 --port 8000

# 终端 2：发送 3 条测试
curl -X POST http://localhost:8000/api/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"question":"每天跑日报","session_id":"e2e-test","kb_id":"default"}'

curl -X POST http://localhost:8000/api/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"question":"查销量","session_id":"e2e-test","kb_id":"default"}'

curl -X POST http://localhost:8000/api/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"question":"差评处理","session_id":"e2e-test","kb_id":"default"}'
```

### P1 验证命令

```powershell
# 发一条请求后查 trace
D:/Python/python.exe -X utf8 -c "
from backend.observability.trace_store import get_trace_store
store = get_trace_store()
traces = store.list(5)
for t in traces:
    print(t.get('id'), t.get('question')[:40], t.get('workflow_name'))
"
```
