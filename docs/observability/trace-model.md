# Trace / Span 模型

## 结构

```
Trace
└── Span（树形）
    └── Event
    └── Metric
```

## Span 必填字段

- id
- parent_id
- type
- name
- start_time
- end_time
- duration_ms
- status

## 常见 Span Type

- `llm` / `llm_call`
- `retrieval`
- `rerank`
- `sql`
- `tool` / `tool_call`
- `skill`
- `planner`
- `reporter`
- `memory`
- `agent`
- `http`

## Tool 调用记录（必填）

- trace_id
- span_id
- input
- output
- duration_ms
- model
- prompt_tokens
- completion_tokens
- error
- retry_count

## 当前实现

- Tracer: `backend/rag/tracer.py`（自建，内存 deque 存储）
- Orchestration tracing: `backend/orchestration/graph/system.py`（LangGraph）
- API DTO: `backend/app/api/routes/observability.py`

## 待集成

- Langfuse（外部 SaaS）
- Prometheus exporter
- PG 持久化（Phase 5）

详见 `docs/observability/backend-enhancement-plan.md`。