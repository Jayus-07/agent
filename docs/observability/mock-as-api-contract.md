# 前端可观测中心 — Mock 数据系统文档

> **定位**：Mock 层 = 未来的 FastAPI Response 边界。前端所有组件都通过 `@/mock/traces/api` 取数，不直接 import JSON。
> **最后更新**：2026-07-16（基于当前代码 + mock 数据审计）

---

## 1. 架构总览

```
┌──────────────────────────────────────────────────────────────┐
│  消费页面                                                     │
│  traces/page.tsx    traces/[id]/page.tsx    compare/page.tsx │
│  sessions/[id]/page.tsx    alerts/page.tsx                   │
├──────────────────────────────────────────────────────────────┤
│  Mock API 层  (api.ts)                                       │
│  getAllSummaries()  getTraceDetail()  mergeTrace()           │
│  normalizeTimestamps()  ← 模块加载时自动平移时间戳              │
├──────────────────────────────────────────────────────────────┤
│  类型层                                                       │
│  schemas/trace.ts  (TraceSummary / TraceDetail / TraceQuery) │
│  schemas/span.ts   (Span / SpanKind / SpanType / SpanEvent)  │
│  types/trace.ts    (TraceRecord / Span(显示) / 工具函数)       │
├──────────────────────────────────────────────────────────────┤
│  Fixture 数据                                                 │
│  fixtures/summaries/  (12 个 JSON, 25 条)                    │
│  fixtures/details/    (25 个 JSON, 25 条)                     │
│  fixtures/agent/      (summaries.json + details.ts, 2 条)    │
└──────────────────────────────────────────────────────────────┘
```

**设计原则**：Summary/Detail 分离
- **Summary**（轻量 ~22 字段）→ 列表页用，一次加载全部
- **Detail**（重量 ~spans[]）→ 详情页按需懒加载
- **mergeTrace()** → 合并为统一的 `TraceRecord` 视图

---

## 2. 数据统计

| 维度 | 数值 | 详情 |
|------|------|------|
| **总 Trace 数** | 27 | 25 JSON + 2 TS |
| **Workflow 类型** | 12 | rag_agent, rag, multi_agent, multi_agent_compare, sql_agent, scheduler, approval_workflow, mcp_tool_call, content_generation, memory_agent, data_collection, agent |
| **状态分布** | success: 21, error: 3, running: 1, cancelled: 1, timeout: 1 | — |
| **总 Span 数** | ~186 | 按 type 分布见下 |
| **Detail 覆盖率** | 100% (27/27) | 每条 summary 都有对应 detail |

### Span type 分布

| Type | 数量 | 说明 |
|------|------|------|
| `llm_call` | 43 | LLM 调用（含 prompt/response/token/cost） |
| `agent` | 38 | Agent 节点（Planner/Supervisor/Worker/Reporter） |
| `tool_call` | 36 | 工具调用（MCP / skill 调用） |
| `retrieval` | 29 | 检索（向量/BM25/混合） |
| `rerank` | 13 | Rerank 重排序 |
| `workflow` | 10 | 工作流编排 span（含 route/loop） |
| `http` | 5 | HTTP 外部请求 |
| `sql` | 4 | SQL 查询 |
| `memory` | 4 | 记忆模块读写 |
| `database` | 2 | 数据库操作 |
| `cache` | 1 | 缓存命中 |
| `human` | 1 | 人工审批 |

### Span kind 分布

| Kind | 数量 | 说明 |
|------|------|------|
| `undefined` | ~178 | JSON fixture 不含 kind 字段（仅 agent/details.ts 有） |
| `graph_node` | 3 | LangGraph 图节点 |
| `graph_loop` | 1 | Supervisor 调度回合 |
| `graph_fallback` | 2 | 降级路径 |
| `internal` | 2 | 内部计算 |

> **已知 gap**：大部分 detail JSON 没有 `kind` 字段。`kind` 仅 agent/details.ts 使用。前端通过 `(s as any).kind` 安全访问，不影响功能。

---

## 3. 类型定义

### 3.1 Mock Schema 层（`schemas/trace.ts` + `schemas/span.ts`）

```typescript
// ── 列表 Summary ──
interface TraceSummary {
  trace_id: string;
  workflow_name: string;
  status: "success" | "error" | "running" | "cancelled" | "timeout";
  start_time: string;          // ISO 8601
  duration_ms: number;
  session_id?: string;
  user_id?: string;
  user_name?: string;
  question: string;
  answer_preview: string;
  token_total: number;
  cost_usd: number;
  span_count: number;
  model_name: string;
  kb_id?: string;
  error_code: string | null;
  error_node?: string | null;   // 失败 span id
  parent_id?: string | null;    // 父 trace（子任务场景）
  children_ids?: string[];      // 子 trace 列表
  bookmarked?: boolean;
  sla_threshold_ms?: number;
  sla_breached?: boolean;
  tags?: string[];
}

// ── 详情 Detail ──
interface TraceDetail {
  trace_id: string;
  workflow_name: string;
  request: {
    question: string;
    kb_id?: string;
    temperature?: number;
    max_tokens?: number;
  };
  response: {
    answer: string;
    answer_len: number;
  };
  usage: {
    total_tokens: number;
    total_cost_usd: number;
    llm_calls: number;
  };
  statistics: {
    total_spans: number;
    llm_latency_ms: number;
    retrieval_latency_ms: number;
    http_latency_ms: number;
    db_latency_ms: number;
  };
  error: {
    code: string;
    type: string;
    message: string;
    node: string | null;
    retry_count: number;
  } | null;
  graph?: {                    // LangGraph 图拓扑（仅 agent workflow）
    nodes: { id: string; label: string }[];
    edges: { source: string; target: string; label?: string }[];
    max_loops: number;
    loop_count: number;
    degradation_triggered: boolean;
  };
  root_span_id: string;
  spans: Span[];
}

// ── Span ──
interface Span {
  trace_id: string;
  span_id: string;
  parent_id: string | null;
  name: string;
  type: SpanType;              // 见下方枚举
  kind: SpanKind;              // 见下方枚举
  status: "success" | "error" | "running" | "skipped";
  start_time: string;
  end_time: string;
  duration_ms: number;
  sequence: number;            // 排序序号
  attributes: Record<string, unknown>;  // OTEL 风格属性
  metrics: Record<string, number>;      // 数值指标
  input: Record<string, unknown> | null;
  output: Record<string, unknown> | null;
  events: SpanEvent[];
  errors: string[];
}

type SpanType =
  | "workflow" | "agent"
  | "llm_call"
  | "embedding" | "retrieval" | "rerank"
  | "http" | "sql" | "email"
  | "database" | "transform"
  | "tool_call";

type SpanKind =
  | "internal"        // 普通计算
  | "client"          // 外部请求
  | "server"          // 接收请求
  | "graph_node"      // LangGraph 图节点
  | "graph_route"     // 路由决策
  | "graph_loop"      // Supervisor 调度回合
  | "graph_fallback"; // 降级路径

interface SpanEvent {
  name: string;
  timestamp: string;
  level: "debug" | "info" | "warn" | "error";
  message: string;
  attributes: Record<string, unknown>;
}
```

### 3.2 显示层（`types/trace.ts`）

显示层 `Span` 由 `mergeSpan()` 从 mock Span 派生，增加了前端专用字段：

```typescript
interface Span {
  id: string;                    // ← span_id
  type: string;
  name: string;
  parent_id: string | null;
  status: "running" | "success" | "error" | "skipped";
  start_time: string;
  end_time?: string;
  duration_ms: number;
  duration_ratio: number;        // 占总 trace 耗时比例 (0-1) ← 派生
  attributes: Record<string, unknown>;
  metrics: Record<string, number | boolean | string>;
  input?: Record<string, unknown>;
  output?: Record<string, unknown>;
  llm_call?: {                   // type=llm_call 时派生
    model: string;
    temperature: number;
    prompt_text: string;
    response_text: string;
    prompt_tokens: number;
    completion_tokens: number;
    cost_usd: number;
  };
  http_breakdown?: {             // type=http 或含 HTTP 调用时
    dns_ms: number;
    connect_ms: number;
    tls_ms: number;
    ttfb_ms: number;
    body_ms: number;
  };
  children: string[];            // 子 span id 列表 ← 派生
  events: SpanEvent[];           // ← 从 mock SpanEvent 映射
  warnings: string[];
  errors: string[];
}
```

**关键差异**：
- mock `Span.kind` → 前端**不使用**（GraphTopology 通过 `(s as any).kind` 读取）
- mock `Span.type` → 前端 `Span.type`（直接透传）
- mock `Span.span_id` → 前端 `Span.id`
- `duration_ratio` = `span.duration_ms / trace.duration_ms`（`mergeSpan` 计算）
- `children` = `allSpans.filter(c => c.parent_id === span_id).map(c => c.span_id)`（`mergeSpan` 计算）
- `llm_call` = 从 `attributes["llm.model"]` / `metrics.prompt_tokens` / `input.prompt` / `output.response` 等字段**自动派生**

---

## 4. mergeTrace 转换逻辑

`mergeTrace(summary, detail) → TraceRecord` 是 mock→display 的**唯一边界**：

```
TraceRecord.id               ← summary.trace_id
TraceRecord.timestamp        ← summary.start_time（已归一化）
TraceRecord.session_id       ← summary.session_id
TraceRecord.question         ← detail.request.question
TraceRecord.answer_preview   ← summary.answer_preview
TraceRecord.duration_ms      ← summary.duration_ms
TraceRecord.model            ← { name: summary.model_name, provider: 派生 }
TraceRecord.usage            ← { prompt_tokens: 60%, completion_tokens: 40%, ... }
TraceRecord.cost_usd         ← summary.cost_usd
TraceRecord.error            ← { ...detail.error, error_node: summary.error_node ?? detail.error.node }
TraceRecord.metadata         ← { kb_id, user_id, temperature, max_tokens }
TraceRecord.spans            ← detail.spans.map(mergeSpan)
TraceRecord.graph            ← detail.graph（仅 agent workflow 有值）
TraceRecord.status           ← summary.status
TraceRecord.parent_id        ← summary.parent_id
TraceRecord.children_ids     ← summary.children_ids
TraceRecord.sla              ← { threshold_ms, breached }
TraceRecord.session          ← { user_id, user_name, started_at, trace_count }
```

---

## 5. Fixture 清单

### 5.1 Summary 文件（`fixtures/summaries/`）

| 文件 | 条数 | workflow_name | 特色 |
|------|------|---------------|------|
| `rag_agent.json` | 12 | rag_agent | 核心场景：knowledge QA，含 rerank 零结果、Faithfulness、MultiQuery、Streaming |
| `rag.json` | 3 | rag | BM25-only、LLM 429 错误、检索未命中 |
| `multi_agent.json` | 2 | multi_agent | 并行 Agent 编排 |
| `multi_agent_compare.json` | 1 | multi_agent_compare | 多平台对比 |
| `sql_agent.json` | 1 | sql_agent | SQL 查询场景 |
| `scheduler.json` | 1 | scheduler | 定时任务调度 |
| `approval_workflow.json` | 1 | approval_workflow | 人工审批 Workflow |
| `mcp_tool_call.json` | 1 | mcp_tool_call | MCP 工具调用 |
| `content_generation.json` | 1 | content_generation | 内容生成 |
| `memory_agent.json` | 1 | memory_agent | 记忆模块 |
| `data_collection.json` | 1 | data_collection | 数据采集 Pipeline |

### 5.2 Detail 文件（`fixtures/details/`）

25 个 JSON，每个 1 条，通过 `DETAILS` map 按 trace_id 索引。

含特殊场景：
- `a1b2c3retrievalmiss.json` — 检索未命中
- `e7f8llmerror429.json` — LLM 429 限流
- `cancel-001.json` — 取消状态
- `timeout-001.json` — 超时
- `running-001.json` — 运行中
- `error-variety-001.json` — 多类型错误
- `retry-001.json` — 重试逻辑
- `cache-001.json` — 缓存命中
- `stream-001.json` — Streaming 流式
- `deep-001.json` — 深层嵌套 span

### 5.3 Agent 扩展（`fixtures/agent/`）

| 文件 | 内容 | 说明 |
|------|------|------|
| `summaries.json` | 2 条 | ma-002 (22.5s multi-agent), agent-degraded-001 (35s 降级) |
| `details.ts` | 2 条 | 含完整的 `kind` 字段：graph_node/graph_loop/graph_route/graph_fallback |

---

## 6. API 函数

```typescript
// ── 列表 ──
getAllSummaries(): TraceSummary[]
//  返回全部 27 条 summary（已归一化时间戳）。
//  模块顶层执行 normalizeTimestamps()，保证时间戳始终"新鲜"。

getTraceSummaries(query: TraceQuery): TraceListResponse
//  支持：workflow_name / status / tags / user_id / keyword 筛选
//  支持：sort_by (start_time/duration_ms/cost_usd/token_total) + sort_order
//  支持：page + page_size 分页

// ── 详情 ──
getTraceDetail(id: string): TraceDetail | null
//  按 trace_id 查 detail，未找到返回 null

// ── 合并 ──
mergeTrace(summary: TraceSummary, detail: TraceDetail): TraceRecord
//  Summary + Detail → 统一的 TraceRecord 显示视图
//  内部调用 mergeSpan() 对每个 span 做字段映射 + llm_call 派生

// ── 查询参数 ──
interface TraceQuery {
  workflow_name?: string;
  status?: string;
  tags?: string[];
  user_id?: string;
  keyword?: string;
  start_time?: string;
  end_time?: string;
  sort_by?: "start_time" | "duration_ms" | "cost_usd" | "token_total";
  sort_order?: "asc" | "desc";
  page?: number;
  page_size?: number;
}
```

---

## 7. 时间戳归一化 + 字段别名

**问题 1**：mock 数据有绝对时间戳（如 `2026-07-16T04:31:27Z`），默认 `"1h"` 筛选在几小时后就会过滤掉所有数据。

**问题 2**：列表页通过 `getAllSummaries() as unknown as TraceRecord[]` 强制 cast。`TraceSummary` 的字段名是 `start_time`/`trace_id`，但 `TraceRecord`（以及 `filterByTimeRange`）读写的是 `timestamp`/`id`。**强制 cast 不重命名字段**，导致 `t.timestamp` 始终 `undefined` → `filterByTimeRange` 过滤全部数据。

**修复**：`normalizeTimestamps()` 在模块加载时执行两项操作：

1. **时间平移** — 找最新 trace → 放 5 分钟前，所有 trace 按同一 offset 平移
2. **注入别名** — 每条 summary 追加 `timestamp`（= start_time 新值）和 `id`（= trace_id）

```typescript
function normalizeTimestamps(summaries: TraceSummary[]) {
  // ...
  return summaries.map((s) => {
    const newStartTime = new Date(new Date(s.start_time).getTime() + offset).toISOString();
    return {
      ...s,
      start_time: newStartTime,
      timestamp: newStartTime,   // ← TraceRecord 兼容别名
      id: s.trace_id,            // ← TraceRecord 兼容别名
    };
  });
}
```

**影响范围**：`ALL_SUMMARIES` 常量。所有消费页（列表/详情/Session/告警/对比）通过 `getAllSummaries()` 获取，自动继承别名。Detail 中的 span 时间戳未归一化（不影响功能，span 显示用 `duration_ms`）。

---

## 8. 消费页面总览

| 页面 | 路由 | 数据源 | 核心数据函数 |
|------|------|--------|-------------|
| **链路追踪列表** | `/observability/traces` | `getAllSummaries()` → cast TraceRecord[] | `filterByTimeRange` + 多条件筛选 |
| **Trace 详情** | `/observability/traces/[id]` | `getAllSummaries()` + `getTraceDetail(id)` + `mergeTrace()` | 74 项功能（见旧版清单） |
| **Trace 对比** | `/observability/traces/compare?ids=` | 同上，N 个 trace 并行加载 | Span type 矩阵 + 火焰图对比 |
| **Session 详情** | `/observability/sessions/[id]` | `getAllSummaries()` → 按 session_id 筛选 | session 聚合统计 |
| **告警中心** | `/observability/alerts` | `getAllSummaries()` + 全部 `getTraceDetail()` | `buildAlerts()` 从 trace 动态聚合 |

### 各页面数据加载方式

```typescript
// traces/page.tsx — 列表
useEffect(() => {
  setTypedTraces(getAllSummaries() as unknown as TraceRecord[]);
}, []);

// traces/[id]/page.tsx — 详情（useMemo，无 deps）
const summaries = useMemo(() => getAllSummaries(), []);
const detail = summary ? getTraceDetail(id) : null;
const trace = summary && detail ? mergeTrace(summary, detail) : null;

// traces/compare/page.tsx — 对比
const traces = useMemo(() => {
  const summaries = getAllSummaries();
  return ids.map(id => {
    const s = summaries.find(t => t.trace_id === id);
    const d = s ? getTraceDetail(id) : null;
    return s && d ? mergeTrace(s, d) : null;
  }).filter(Boolean);
}, [ids]);

// sessions/[id]/page.tsx — Session（模块顶层直接调用）
const typedTraces = getAllSummaries() as unknown as TraceRecord[];
// → useMemo 按 session_id 筛选

// alerts/page.tsx — 告警（模块顶层加载全部 detail）
const summaries = getAllSummaries();
const typedTraces = summaries.map(s => {
  const d = getTraceDetail(s.trace_id);
  return d ? mergeTrace(s, d) : null;
}).filter(Boolean);
```

---

## 9. 已知 Gap / 待改进

| # | 问题 | 严重度 | 说明 |
|---|------|--------|------|
| 1 | **Span.kind 缺失** | P2 | 大部分 detail JSON 没有 `kind` 字段。只有 agent/details.ts 有。前端通过 optional chain 安全访问。 |
| 2 | **Detail span 时间戳未归一化** | P2 | `normalizeTimestamps` 只处理 summary。Detail span 的 start_time/end_time 保持原始值，与 trace 级别时间戳有微小不一致（不影响功能）。 |
| 3 | **Session/Alerts 页直接 cast** | P1 | `sessions/[id]/page.tsx` 和 `alerts/page.tsx` 直接 `getAllSummaries() as unknown as TraceRecord[]`，不走 mergeTrace。由于 `normalizeTimestamps` 已注入 `timestamp`/`id` 别名，运行时不会出错，但访问 `t.spans`/`t.usage` 等 detail 字段仍是 undefined。当前 Session 页不需要这些字段，Alerts 页走 `mergeTrace`。 |
| 4 | **Alerts 页全量加载** | P1 | `alerts/page.tsx` 模块顶层调用全部 27 条 `getTraceDetail()` + `mergeTrace()`，生产环境需要分页/后端聚合。 |
| 5 | **旧 `traces.json` 文件** | P2 | `frontend/src/mock/traces.json` 仍存在但无任何引用，可安全删除。 |
| 6 | **agent/details.ts 使用 TypeScript 文件** | P2 | 不同于其他 detail JSON，它是 `.ts` 文件导出对象。 |
| 7 | **Compare 页 session_id 缺失** | P1 | `multi_agent_compare.json` 没有 `session_id` 字段，`mergeTrace` 会返回 `""`。不影响对比功能。 |
| 8 | **`docs/observability/frontend-data-requirements.md` 已陈旧** | P1 | 该文档定义的是旧 `TraceRecord/TraceStep` 模型。当前已迁移到 `Span` 新模型，建议用本文档替代。 |
| 9 | **`as unknown as TraceRecord[]` 不安全** | P1 | 列表/Session/Alerts 页都用裸 cast 绕过类型检查。`normalizeTimestamps` 的别名注入消除了运行时错误（timestamp/id），但其他 TraceRecord 专属字段（spans/usage/graph）仍为 undefined。建议长期加 `toTraceRecordListItem()` 显式映射函数。 |

---

## 10. 相关文件索引

| 文件 | 角色 |
|------|------|
| `frontend/src/mock/traces/api.ts` | Mock API 层：数据加载 + mergeTrace + normalizeTimestamps |
| `frontend/src/mock/traces/schemas/trace.ts` | Mock 类型：TraceSummary / TraceDetail / TraceQuery |
| `frontend/src/mock/traces/schemas/span.ts` | Mock 类型：Span / SpanKind / SpanType / SpanEvent |
| `frontend/src/mock/traces/index.ts` | Barrel re-export（`export * from "./api"`） |
| `frontend/src/types/trace.ts` | 显示层类型 + 工具函数（Span / TraceRecord / formatXxx / filterByTimeRange） |
| `frontend/src/mock/traces/api.test.ts` | mergeTrace 回归测试（3 个 P0 拦截） |
| `frontend/src/types/trace.test.ts` | 工具函数单元测试（65 个） |
| `frontend/src/mock/traces/fixtures/summaries/*.json` | 12 个 summary 文件 |
| `frontend/src/mock/traces/fixtures/details/*.json` | 25 个 detail 文件 |
| `frontend/src/mock/traces/fixtures/agent/*` | agent workflow 的 summary + detail（含 kind） |
| `frontend/src/mock/traces.json` | ⚠️ 已废弃，无引用 |
| `docs/observability/frontend-data-requirements.md` | ⚠️ 旧版数据契约（TraceStep 模型），待更新 |
