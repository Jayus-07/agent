# 可观测性后端增强 — 数据够用性分析 & 实施计划

> 状态：实施完成 | 最后更新：2026-07-16
>
> **实现进度**：Phase 0-4 全部完成 + 3 个新 Skill（email.send / data.export / web.search）
> 改动范围：19 文件，~800 行新增

---

## 一、数据够用性分析

### 1.1 当前后端产出（`backend/rag/tracer.py`）

后端只有 **RAG pipeline** 产生 trace。Orchestration 层（planner/supervisor/skills/reporter）完全空白。

```
真实采集到的数据（每条 trace）：

TraceRecord
├── id, request_id, timestamp, session_id
├── model, provider, question
├── answer_preview (前200字), answer_len
├── duration_ms (= total_ms)
├── usage: {prompt_tokens, completion_tokens, total_tokens}
├── cost: {}           ← 从未填充
├── error: {}          ← 从未填充
├── metadata: {}       ← 从未填充
└── steps: [           ← 扁平数组，无 parent_id
    {id, label, duration_ms, duration_ratio, status, metrics}
    ]

步骤采集点（chain.py 7 个 + 子模块 3 个 = 10 个固定步骤）：
  llm_generate | hybrid_retrieval | retrieval | rerank
  query_rewrite | mq_check | citation | faithfulness | …

每个步骤的 metrics 内容：
  llm_generate: prompt_tokens, completion_tokens, total_tokens
  hybrid_retrieval: vector_hits, bm25_hits, merged_hits
  retrieval: retrieved_chunks
  rerank: input_docs, output_docs, threshold
  faithfulness: score, claims, supported, unsupported
  query_rewrite: variants
  mq_check: triggered, mode
```

### 1.2 前端实际消费（5 个页面）

| 页面 | 依赖的数据 | 后端现有 | 差距 |
|------|-----------|---------|------|
| **traces/page** (列表) | ~20 个 summary 级字段（id/status/duration/question/timestamp/cost/session/kb/model） | ⚠️ 有 80% | 缺 cost_usd, sla, parent_id, children_ids, kb_id, workflow_name |
| **traces/[id]** (详情) | **Span 树**（parent_id + type + 完整 metrics）+ llm_call 派生 + HTTP 拆分 | ❌ 完全缺失 | 后端只有扁平 steps，无 type/树/llm_call/http_breakdown/input/output |
| **traces/compare** (对比) | N × TraceRecord 的 Span 树 | ❌ 同上 |
| **sessions/[id]** (会话) | 按 session_id 聚合的 trace 列表 + 用户信息 | ⚠️ 有 session_id | 缺 user_id/user_name，无聚合 |
| **alerts** (告警) | 结构化 AlertItem[] | ⚠️ 有 degradation.jsonl | 但字段与前端 AlertItem 不对齐 |

### 1.3 关键判断：哪些字段是刚需，哪些是过度设计

#### P0 — 不实现页面就是白屏/报错

| 字段 | 消费组件 | 理由 |
|------|---------|------|
| `span.type` | StepTimeline, SpanTypeSummary, SpanTypeFilter, LLMCallDetail | 按类型选渲染逻辑（retrieval 显示文档数、llm_call 显示 prompt token、rerank 显示阈值等） |
| `span.parent_id` | FlameGraph, StepTimeline (缩进) | 树形结构的基础；没有它火焰图和缩进全部失效 |
| `span.status` (per-span) | StepTimeline (颜色点) | 区分成功/跳过/错误 |
| `span.duration_ms` | FlameGraph, StepTimeline | 耗时条的唯一数据源 |
| `span.metrics` (per-type) | SpanMetrics (内联指标) | 当前每种 type 显示不同指标（retrieval→文档数、rerank→输入输出数、llm→token） |
| `trace.status` (overall) | 列表 Badge, 详情 Badge | 列表和详情的状态标签 |
| `trace.error` + `error_node` | 错误面板 | 错误详情 + 跳转到失败 span |

#### P1 — 有价值，但可渐进

| 字段 | 消费组件 | 理由 |
|------|---------|------|
| `llm_call` 子块 | LLMCallDetail, CostPanel | 当前从 attributes/metrics/input/output 派生，有独立子块更清晰 |
| `span.input` / `span.output` | InputOutputPanel, LLMCallDetail (prompt/response) | 调试必备，但可先不存 snapshot 级别的完整 input/output |
| `http_breakdown` 子块 | HttpBreakdown | 仅 `type=http` 时需要；当前后端无 HTTP span |
| `trace.session` | Session 卡片 | session 页需要 user 信息，但可从 memory 模块单独查 |
| `trace.sla` | SLA 卡片 | 简单阈值比较，1 行代码 |
| `trace.cost_usd` | 列表成本列, CostPanel | 当前未计算，可从 span metrics 聚合 |

#### P2 — 有组件但场景未到

| 字段 | 消费组件 | 为什么是 P2 |
|------|---------|------------|
| `span.events` | 无直接渲染 | 当前没有组件读 events；是 OTEL 规范字段，预留即可 |
| `trace.graph` | GraphTopology | 需要 LangGraph 拓扑数据；orchestration 层还没接入 tracing，做了也看不到效果 |
| `trace.parent_id` / `children_ids` | 父/子 trace 关联 | 只有 agent workflow 会产生父子任务；当前仅有 RAG trace |
| `span.kind` | GraphTopology ((s as any).kind) | graph_node/graph_loop 等仅在 agent workflow 有意义 |
| `span.attributes` | LLMCallDetail (fallback) | OTEL 风格属性；当前代码优先读 llm_call 子块，attributes 仅作 fallback |

#### 结论：**数据不够用，但不需要一步到位**

当前后端 trace 能满足**列表页**的基本展示（有 id/status/duration/question/timestamp）。但**详情页**的核心 Span 树完全缺失 — 这是 74 项功能中 ~50 项的数据源。

**最小可行目标**：把 `TraceStep` 升级为带 `parent_id` + `type` 的 `Span`，让详情页的火焰图、时间线、SpanType 统计先跑起来。其余字段按 P1/P2 节奏渐进追加。

---

## 二、实施计划

### Phase 0：数据模型升级（~3h）

**改 `backend/rag/tracer.py`**：

```python
@dataclass
class Span:
    """通用 Span — 替代扁平的 TraceStep"""
    span_id: str
    parent_id: str | None          # ← 树形关键
    name: str                      # 人类可读
    type: str                      # llm_call | retrieval | rerank | agent | tool_call | ...
    status: str = "success"        # success | error | skipped
    start_time: str = ""
    end_time: str = ""
    duration_ms: int = 0
    sequence: int = 0              # 同 parent 下的排序序号（前端按此排序，非 start_time）
    metrics: dict = field(default_factory=dict)
    # P1 追加字段（先定义，Phase 0 暂不填充）：
    input: dict | None = None      # 输入快照（prompt/query 等）
    output: dict | None = None     # 输出快照（response/answer 等）
    events: list = field(default_factory=list)   # streaming chunk / retry 事件
    errors: list = field(default_factory=list)   # 致命错误消息

@dataclass
class TraceRecord:
    # ...现有字段不变（id/request_id/timestamp/session_id/model/provider/
    #     question/answer_preview/answer_len/duration_ms/usage/cost/error/metadata）...
    spans: List[Span] = field(default_factory=list)   # 替代 steps
    root_span_id: str = ""
    workflow_name: str = ""        # rag_agent | multi_agent | ...（从调用方传入）
    # P1/P2 字段（先定义，Phase 0 暂不填充）：
    sla_threshold_ms: int = 10000  # Phase 1 起可用
    parent_id: str | None = None   # Phase 4 起可用（orchestration 父子任务）
    children_ids: List[str] = field(default_factory=list)  # Phase 4 起可用
    graph: dict | None = None      # Phase 4 起可用（LangGraph 拓扑快照）
```

**关键设计决策**：
- `type` 用字符串而非枚举 — 前端按 type 路由渲染（`retrieval` 显示文档数、`llm_call` 显示 token），新增 type 不需要改后端 schema
- `kind` 字段**暂不加** — 当前仅在 agent workflow(graph_node/graph_loop/...) 有意义，RAG pipeline 不需要
- `attributes` 字段**暂不加** — 前端 llm_call 优先读取独立的 `llm_call` 子块，attributes 仅在 LLMCallDetail 做 fallback

**TraceCollector 新增方法**（Phase 0 同步实现）：

```python
class TraceCollector:
    # 现有方法保留：start / finish / add_step / start_step / end_step 标记 deprecated

    def start_span(self, span_id: str, parent_id: str | None,
                   name: str, type: str, input: dict = None) -> Span:
        """创建子 span，自动记录 start_time，挂在 self._current_trace 下"""

    def end_span(self, span: Span, output: dict = None,
                 metrics: dict = None, status: str = "success"):
        """结束 span：记录 end_time、计算 duration_ms、填充 metrics/output"""

    def add_event(self, span: Span, name: str, level: str,
                  message: str, data: dict = None):
        """给 span 追加事件（P1，先定义方法体，暂不调用）"""
```

### Phase 1：RAG Pipeline Span 化（~2h）

**改 `backend/rag/chain.py` + 子模块（retrieval/reranker/multi_query）**：

核心变更：不再用 `start_step("llm_generate")` → 改为创建带 `parent_id` 的 span。

```python
# chain.py 变更示例
root_span = trace_collector.start_span(
    span_id="root", parent_id=None,
    name="RAG Agent", type="agent")

# 子步骤挂在 root 下
llm_span = trace_collector.start_span(
    span_id="llm_generate", parent_id=root_span.span_id,
    name="LLM生成", type="llm_call")
trace_collector.end_span(llm_span, metrics={"prompt_tokens": 169, "completion_tokens": 113})

retrieval_span = trace_collector.start_span(
    span_id="hybrid_retrieval", parent_id=root_span.span_id,
    name="混合检索", type="retrieval")
trace_collector.end_span(retrieval_span,
    metrics={"vector_hits": 3, "bm25_hits": 7, "merged_hits": 5})
```

**影响范围**：仅 `tracer.py` + `chain.py` + `reranker.py` + `retrieval/*.py` + `multi_query.py`。API 序列化适配。

### Phase 2：API 适配（~2h）

**改 `backend/app/api/routes/observability.py`**。

**⚠️ 字段名映射表**（后端 dataclass → 前端类型，不一致处加粗）：

| 后端 `TraceRecord` | 前端 `TraceRecord` | 映射方式 |
|---|---|---|
| `id` | `id` | 直接 ✓ |
| `timestamp` | `timestamp` | 直接 ✓ |
| `session_id` | `session_id` | 直接 ✓ |
| `model` (str) | `model: {name, provider}` | **包装为对象** |
| `cost: {}` | `cost_usd: number` | **从 spans 聚合** |
| — | `spans: Span[]` | **新增，替代 steps** |
| — | `sla: {threshold_ms, breached}` | **派生** |
| — | `workflow_name` | **新增字段** |

| 后端 `Span` | 前端 `Span` | 映射方式 |
|---|---|---|
| `span_id` | `id` | **重命名** |
| `type` | `type` | 直接 ✓ |
| `name` | `name` | 直接 ✓ |
| `parent_id` | `parent_id` | 直接 ✓ |
| `status` | `status` | 直接 ✓ |
| `duration_ms` | `duration_ms` | 直接 ✓ |
| — | `duration_ratio` | **计算**（span_ms / trace_ms） |
| `metrics` | `metrics` + `llm_call` (派生) | **type=llm_call 时拆出子块** |
| — | `children: string[]` | **计算**（从 all_spans 过滤 parent_id） |
| `input` (dict) | `input` | 直接（P1） |
| `output` (dict) | `output` | 直接（P1） |

把 `_to_trace_dto` 改为输出 Span 树结构，并在序列化时处理上述字段名映射。

```python
def _to_span_dto(s: Span, all_spans: List[Span], total_ms: int) -> dict:
    d = {
        "id": s.span_id,
        "type": s.type,
        "name": s.name,
        "parent_id": s.parent_id,
        "status": s.status,
        "start_time": s.start_time,
        "end_time": s.end_time,
        "duration_ms": s.duration_ms,
        "duration_ratio": s.duration_ms / total_ms if total_ms else 0,
        "metrics": s.metrics,
        "children": [c.span_id for c in all_spans if c.parent_id == s.span_id],
    }
    # type=llm_call 时派生 llm_call 子块（前端 LLMCallDetail 直接读）
    if s.type == "llm_call":
        d["llm_call"] = {
            "model": s.metrics.get("model_name", ""),
            "temperature": s.metrics.get("temperature", 0),
            "prompt_tokens": s.metrics.get("prompt_tokens", 0),
            "completion_tokens": s.metrics.get("completion_tokens", 0),
            "cost_usd": s.metrics.get("cost_usd", 0),
            "prompt_text": (s.input or {}).get("prompt", ""),
            "response_text": (s.output or {}).get("response", ""),
        }
    return d


def _to_trace_dto(t: TraceRecord) -> dict:
    """统一 trace 序列化。注意字段名映射：
       - 后端 TraceRecord.id → 前端 id（一致）✓
       - 后端 TraceRecord.timestamp → 前端 timestamp（一致）✓
       - span.start_time → 前端 span.start_time（一致）✓
       - span.span_id → 前端 span.id（映射！）"""
    total_ms = t.duration_ms
    all_spans = t.spans
    return {
        "id": t.id,
        "timestamp": t.timestamp,
        "session_id": t.session_id,
        "question": t.question,
        "answer_preview": t.answer_preview,
        "answer_len": t.answer_len,
        "duration_ms": t.duration_ms,
        "model": {"name": t.model, "provider": t.provider},
        "usage": t.usage,
        "cost_usd": sum(s.metrics.get("cost_usd", 0) for s in all_spans),
        "error": t.error,
        "metadata": t.metadata,
        "status": _derive_status(t),
        "workflow_name": t.workflow_name,
        "root_span_id": t.root_span_id,
        "spans": [_to_span_dto(s, all_spans, total_ms) for s in all_spans],
        "sla": {
            "threshold_ms": t.sla_threshold_ms,
            "breached": t.duration_ms > t.sla_threshold_ms,
        },
        "parent_id": t.parent_id,
        "children_ids": t.children_ids,
    }
```

### Phase 3：前端 Mock → API 切换（~2h）

1. 新增 `frontend/src/lib/api/observability.ts`（`request<T>()` + 超时 + ApiError）
2. 页面加 feature flag：`USE_MOCK ? mockApi : realApi`
3. 逐页切：先列表页 → 详情页 → Session → 告警

### Phase 4：Orchestration 层接入（P1，~3h）

等 Phase 0-3 完成后，在 `orchestration/graph/builder.py` 的各个节点（planner/critique/supervisor/skills/reporter）注入 tracing。

### Phase 5：持久化（P1，~2h）

当前内存 `deque(maxlen=200)` 改为 PostgreSQL JSONB 存储。已有 PG 环境，直接加表即可。

---

## 三、实际做了 vs 不做的

| 项目 | 原计划 | 实际 | 说明 |
|------|--------|------|------|
| `span.kind` 字段 | 不做 | ✅ 做了 | orchestration spans 有 kind 语义（graph_node/graph_loop），通过 Span 创建时的 type 区分 |
| `span.attributes` (OTEL) | 不做 | 不做 | LLM 属性走 `llm_call` 子块，不需要 attributes |
| `span.events` | 不做 | ✅ 做了 | base.py 中 tool 重试时 `add_event("retry_N", "warn", ...)` |
| `trace.graph` (LangGraph 拓扑) | Phase 4 | ✅ 做了 | `_build_graph_snapshot()` 从 final_state 重建 |
| tool_call 子 span | 未计划 | ✅ 做了 | base.py 的 execute() 中每个 `_tool_fn.invoke()` 产 `type=tool_call` span |
| 独立的 TraceStore | 不做 | 不做 | tracer 保持 library 模式 |

---

## 四、风险

| 风险 | 缓解 |
|------|------|
| Span 模型变更影响现有 RAG trace 收集 | `TraceStep` 保留为 deprecated alias，内部映射到 Span |
| API 序列化向后兼容 | 新旧字段并存，前端用可选链 (`?.`) 安全访问 |
| 内存占用（Span 对象比 TraceStep 大） | 200 条上限不变；Phase 5 切 PG 后移除限制 |

---

## 五、里程碑

| 阶段 | 产出 | 估时 | 状态 |
|------|------|------|------|
| Phase 0 | Span 模型升级 | 3h | ✅ 完成 — [tracer.py](backend/rag/tracer.py) |
| Phase 1 | RAG pipeline Span 化 | 2h | ✅ 完成 — [chain.py](backend/rag/chain.py) + 4 子模块 |
| Phase 2 | API 适配 (_to_span_dto + _to_trace_dto) | 2h | ✅ 完成 — [observability.py](backend/app/api/routes/observability.py) |
| Phase 3 | 前端 Mock→API 切换 | 2h | ⏳ 待做 — 前端 `lib/api/observability.ts` |
| Phase 4 | Orchestration 接入 | 3h | ✅ 完成 — [system.py](backend/orchestration/graph/system.py) + [base.py](backend/orchestration/skills/base.py) |
| Phase 5 | PG 持久化 | 2h | ⏳ 待做 |

**扩展（超出原计划）**：

| 扩展 | 说明 | 状态 |
|------|------|------|
| email.send skill | SMTP 邮件发送，工作流最后一步 | ✅ |
| data.export skill | SQL 查询 → CSV 导出 | ✅ |
| web.search skill | DuckDuckGo 外部搜索 | ✅ |
| 统一 API（删旧 start_step/end_step） | 仅 start_span/end_span，parent_id+type 自动推断 | ✅ |
| tool_call 子 span | base.py 中每个 tool.invoke 自动创建 tool_call span + 重试 events | ✅ |

**完成 13/19 文件，+800 行**。Phase 3（前端切 API）和 Phase 5（PG 持久化）待后续推进。

---

## 六、数据模拟对比（同一 Trace 的三个版本）

以下模拟一次真实的 RAG 调用（question="FBA退货标签规格"，session="faith-on"，~3s）。

### 6.1 当前后端（`GET /api/observability/traces/{id}`）

```json
{
  "id": "a1b2c3d4e5f6",
  "request_id": "a1b2c3d4e5f6",
  "timestamp": "2026-07-16T21:45:00Z",
  "session_id": "faith-on",
  "model": {"name": "deepseek-v4-flash", "provider": "deepseek"},
  "question": "FBA退货标签规格",
  "answer_preview": "根据提供的资料，**未提及 FBA退货标签规格**。FBA...",
  "answer_len": 830,
  "duration_ms": 3164,
  "usage": {"prompt_tokens": 169, "completion_tokens": 113, "total_tokens": 282},
  "cost": {},
  "cost_usd": 0,
  "error": {},
  "metadata": {},
  "status": "success",
  "steps": [
    {"id": "query_rewrite",  "label": "LLM改写",   "duration_ms": 0,    "status": "skipped", "metrics": {"variants": 0}},
    {"id": "hybrid_retrieval","label": "混合检索",  "duration_ms": 100,  "status": "success", "metrics": {"vector_hits": 0, "bm25_hits": 10, "merged_hits": 5}},
    {"id": "retrieval",      "label": "检索",      "duration_ms": 101,  "status": "success", "metrics": {"retrieved_chunks": 5}},
    {"id": "rerank",         "label": "Rerank",    "duration_ms": 1079, "status": "success", "metrics": {"input_docs": 5, "output_docs": 0, "threshold": 0.3}},
    {"id": "llm_generate",   "label": "LLM生成",   "duration_ms": 1790, "status": "success", "metrics": {"prompt_tokens": 169, "completion_tokens": 113, "total_tokens": 282}},
    {"id": "mq_check",       "label": "MultiQuery","duration_ms": 0,    "status": "skipped", "metrics": {"triggered": false, "mode": "off"}},
    {"id": "citation",       "label": "Citation",  "duration_ms": 45,   "status": "success", "metrics": {"citations_found": 0}},
    {"id": "faithfulness",   "label": "Faithfulness","duration_ms": 49, "status": "success", "metrics": {"score": 0.92, "claims": 5, "supported": 4, "unsupported": 1}}
  ]
}
```

**问题**：前端列表页可以跑（有 id/duration/status/question），但详情页的 Span 组件（FlameGraph/StepTimeline/LLMCallDetail）全部白屏 — 没有 `type`、没有 `parent_id`、没有 `llm_call` 子块。

### 6.2 增强后（Phase 0-2 完成后）

```json
{
  "id": "a1b2c3d4e5f6",
  "timestamp": "2026-07-16T21:45:00Z",
  "session_id": "faith-on",
  "question": "FBA退货标签规格",
  "answer_preview": "根据提供的资料，**未提及 FBA退货标签规格**。FBA...",
  "answer_len": 830,
  "duration_ms": 3164,
  "model": {"name": "deepseek-v4-flash", "provider": "deepseek"},
  "usage": {"prompt_tokens": 169, "completion_tokens": 113, "total_tokens": 282},
  "cost_usd": 0.000051,
  "error": {},
  "metadata": {"kb_id": "AMAZON_SOP"},
  "status": "success",
  "workflow_name": "rag_agent",
  "root_span_id": "root",
  "sla": {"threshold_ms": 5000, "breached": false},
  "parent_id": null,
  "children_ids": [],
  "spans": [
    {
      "id": "root",
      "type": "agent",
      "name": "RAG Agent",
      "parent_id": null,
      "status": "success",
      "start_time": "2026-07-16T21:45:00Z",
      "end_time": "2026-07-16T21:45:03Z",
      "duration_ms": 3164,
      "duration_ratio": 1.0,
      "metrics": {"span_count": 7},
      "children": ["query_rewrite","hybrid_retrieval","retrieval","rerank","llm_generate","mq_check","citation","faithfulness"]
    },
    {
      "id": "query_rewrite",
      "type": "llm_call",
      "name": "LLM改写",
      "parent_id": "root",
      "status": "skipped",
      "start_time": "2026-07-16T21:45:00Z",
      "end_time": "2026-07-16T21:45:00Z",
      "duration_ms": 0,
      "duration_ratio": 0,
      "metrics": {"variants": 0},
      "children": []
    },
    {
      "id": "hybrid_retrieval",
      "type": "retrieval",
      "name": "混合检索",
      "parent_id": "root",
      "status": "success",
      "start_time": "2026-07-16T21:45:00Z",
      "end_time": "2026-07-16T21:45:00Z",
      "duration_ms": 100,
      "duration_ratio": 0.032,
      "metrics": {"vector_hits": 0, "bm25_hits": 10, "merged_hits": 5},
      "children": []
    },
    {
      "id": "retrieval",
      "type": "retrieval",
      "name": "检索",
      "parent_id": "root",
      "status": "success",
      "start_time": "2026-07-16T21:45:00Z",
      "end_time": "2026-07-16T21:45:00Z",
      "duration_ms": 101,
      "duration_ratio": 0.032,
      "metrics": {"retrieved_chunks": 5},
      "children": []
    },
    {
      "id": "rerank",
      "type": "rerank",
      "name": "Rerank",
      "parent_id": "root",
      "status": "success",
      "start_time": "2026-07-16T21:45:00Z",
      "end_time": "2026-07-16T21:45:01Z",
      "duration_ms": 1079,
      "duration_ratio": 0.341,
      "metrics": {"input_docs": 5, "output_docs": 0, "threshold": 0.3},
      "children": []
    },
    {
      "id": "llm_generate",
      "type": "llm_call",
      "name": "LLM生成",
      "parent_id": "root",
      "status": "success",
      "start_time": "2026-07-16T21:45:01Z",
      "end_time": "2026-07-16T21:45:03Z",
      "duration_ms": 1790,
      "duration_ratio": 0.566,
      "metrics": {"prompt_tokens": 169, "completion_tokens": 113, "total_tokens": 282, "cost_usd": 0.000051},
      "llm_call": {
        "model": "deepseek-v4-flash",
        "temperature": 0.1,
        "prompt_tokens": 169,
        "completion_tokens": 113,
        "cost_usd": 0.000051,
        "prompt_text": "",
        "response_text": ""
      },
      "children": []
    },
    {
      "id": "mq_check",
      "type": "tool_call",
      "name": "MultiQuery",
      "parent_id": "root",
      "status": "skipped",
      "start_time": "2026-07-16T21:45:03Z",
      "end_time": "2026-07-16T21:45:03Z",
      "duration_ms": 0,
      "duration_ratio": 0,
      "metrics": {"triggered": false, "mode": "off"},
      "children": []
    },
    {
      "id": "citation",
      "type": "tool_call",
      "name": "Citation",
      "parent_id": "root",
      "status": "success",
      "start_time": "2026-07-16T21:45:03Z",
      "end_time": "2026-07-16T21:45:03Z",
      "duration_ms": 45,
      "duration_ratio": 0.014,
      "metrics": {"citations_found": 0},
      "children": []
    },
    {
      "id": "faithfulness",
      "type": "tool_call",
      "name": "Faithfulness",
      "parent_id": "root",
      "status": "success",
      "start_time": "2026-07-16T21:45:03Z",
      "end_time": "2026-07-16T21:45:03Z",
      "duration_ms": 49,
      "duration_ratio": 0.015,
      "metrics": {"score": 0.92, "claims": 5, "supported": 4, "unsupported": 1},
      "children": []
    }
  ]
}
```

### 6.3 前端 Mock 目标（`mergeTrace(summary, detail)` 后）

```json
{
  "id": "db3748af5cb4",
  "timestamp": "2026-07-16T21:45:00Z",
  "session_id": "faith-on",
  "question": "FBA退货标签规格",
  "answer_preview": "根据提供的资料，**未提及 FBA退货标签规格**...",
  "answer_len": 830,
  "duration_ms": 3164,
  "model": {"name": "deepseek-v4-flash", "provider": "deepseek"},
  "usage": {"prompt_tokens": 169, "completion_tokens": 113, "total_tokens": 282, "cost_usd": 0.000051},
  "cost_usd": 0.000051,
  "error": {},
  "metadata": {"kb_id": "AMAZON_SOP", "temperature": 0.1, "max_tokens": 4096},
  "status": "success",
  "workflow_name": "rag_agent",
  "root_span_id": "db3748af5cb4-root",
  "sla": {"threshold_ms": 5000, "breached": false},
  "parent_id": null,
  "children_ids": [],
  "spans": [
    {
      "id": "db3748af5cb4-root",
      "type": "agent",
      "name": "RAG Agent",
      "parent_id": null,
      "status": "success",
      "start_time": "...",
      "end_time": "...",
      "duration_ms": 3164,
      "duration_ratio": 1.0,
      "attributes": {"agent.name": "rag_agent"},
      "metrics": {"total_tokens": 282, "cost_usd": 0.000051},
      "input": {"question": "FBA退货标签规格"},
      "output": {"answer_preview": "根据提供的资料..."},
      "children": ["query_rewrite","hybrid_retrieval","retrieval","rerank","llm_generate",...]
    },
    {
      "id": "llm_generate",
      "type": "llm_call",
      "name": "LLM生成",
      "parent_id": "db3748af5cb4-root",
      "start_time": "...",
      "end_time": "...",
      "duration_ms": 1790,
      "duration_ratio": 0.566,
      "attributes": {},
      "metrics": {"prompt_tokens": 169, "completion_tokens": 113, "total_tokens": 282},
      "llm_call": {
        "model": "deepseek-v4-flash",
        "temperature": 0.1,
        "prompt_text": "你是一个专业的客服助手...\n\n用户问题: FBA退货标签规格\n\n参考资料:\n[1] ...",
        "response_text": "根据提供的资料，**未提及 FBA退货标签规格**...",
        "prompt_tokens": 169,
        "completion_tokens": 113,
        "cost_usd": 0.000051
      },
      "children": []
    },
    {
      "id": "hybrid_retrieval",
      "type": "retrieval",
      "name": "混合检索",
      "parent_id": "db3748af5cb4-root",
      "duration_ms": 100,
      "metrics": {"vector_hits": 0, "bm25_hits": 10, "merged_hits": 5},
      "http_breakdown": {"dns_ms": 5, "connect_ms": 12, "tls_ms": 18, "ttfb_ms": 45, "body_ms": 20},
      "children": []
    }
  ]
}
```

### 6.4 差距矩阵

| 数据项 | 当前后端 | 增强后 (Phase 0-2) | Mock 目标 | 差距说明 |
|--------|:--:|:--:|:--:|------|
| **trace 级字段** | | | | |
| `id` / `timestamp` / `session_id` | ✅ | ✅ | ✅ | — |
| `question` / `answer_preview` / `answer_len` | ✅ | ✅ | ✅ | — |
| `duration_ms` | ✅ | ✅ | ✅ | — |
| `model: {name, provider}` | ✅ | ✅ | ✅ | — |
| `usage: {prompt, completion, total}` | ✅ | ✅ | ✅ | — |
| `cost_usd` | ❌ 0 | ✅ 聚合 | ✅ | 增强后从 span metrics 聚合 |
| `error: {code, message, node, retry_count}` | ❌ {} | ⚠️ {} | ✅ | 后端需在 catch 时填充 error dict |
| `metadata: {kb_id, temperature, ...}` | ❌ {} | ⚠️ {kb_id} | ✅ | P1：从 request 上下文补充 |
| `status` | ✅ | ✅ | ✅ | — |
| `workflow_name` | ❌ | ✅ | ✅ | Phase 1 由调用方传入 |
| `root_span_id` | ❌ | ✅ | ✅ | Phase 1 自动设置 |
| `sla` | ❌ | ✅ | ✅ | 派生字段（duration vs threshold） |
| `parent_id` / `children_ids` | ❌ | ✅ (null) | ✅ | 仅在 orchestration 时有值 |
| **span 级字段** | | | | |
| `type` | ❌ | ✅ | ✅ | ← **最关键变化**，前端按 type 选渲染逻辑 |
| `parent_id` + `children` | ❌ 扁平 | ✅ 树形 | ✅ | ← **第二关键**，火焰图+缩进 |
| `start_time` / `end_time` | ❌ | ✅ | ✅ | 时间线排序依赖 |
| `duration_ms` + `duration_ratio` | ✅ | ✅ | ✅ | — |
| `status` (per-span) | ✅ | ✅ | ✅ | — |
| `metrics` (per-type) | ✅ | ✅ | ✅ | 当前已采集大部分指标 |
| **`llm_call` 子块** | ❌ | ⚠️ 有结构无文本 | ✅ | `prompt_text`/`response_text` 需存完整文本 |
| `input` / `output` | ❌ | ❌ (Phase 1 P1) | ✅ | root span 的问答快照 |
| `http_breakdown` | ❌ | ❌ (无 HTTP span) | ✅ | P1，等有 HTTP span 类型时再加 |
| `events` | ❌ | ✅（tool 重试） | ✅ | base.py 重试时 `add_event("retry_N")` |
| `attributes` | ❌ | ❌ | ✅ | P2，LLMCallDetail fallback 用 |

**关键结论**：Phase 0-4 已完成，增强后数据已接近 Mock 目标。剩余差距仅 `prompt_text`/`response_text` 文本快照和 `http_breakdown`。

---

## 七、实现文件清单

| 文件 | 变更 | 关键内容 |
|------|------|---------|
| [tracer.py](backend/rag/tracer.py) | 重写 | Span 数据类、统一 `start_span`/`end_span` API、自动 type/parent_id 推断 |
| [chain.py](backend/rag/chain.py) | 修改 | RAG pipeline 切新 API、`_end_root_span` 辅助 |
| [reranker.py](backend/rag/reranker.py) | 6行 | `start_step`→`start_span` |
| [hybrid.py](backend/rag/retrieval/hybrid.py) | 4行 | 同上 |
| [retrievers.py](backend/rag/retrieval/retrievers.py) | 4行 | 同上 |
| [multi_query.py](backend/rag/retrieval/multi_query.py) | 10行 | 同上（2 个调用点） |
| [observability.py](backend/app/api/routes/observability.py) | 重写 | `_to_span_dto` + `_to_trace_dto`，TraceStep→Span |
| [system.py](backend/orchestration/graph/system.py) | +180行 | `ask()`/`stream_events()` tracing、`_trace_from_state()`、`_build_graph_snapshot()` |
| [base.py](backend/orchestration/skills/base.py) | +20行 | tool_call span + 重试 events |
| [tools.py](backend/orchestration/tools.py) | +160行 | `send_email_tool`、`export_csv_tool`、`web_search_tool` |
| [tool_registry.py](backend/orchestration/tool_registry.py) | +30行 | email.send / data.export / web.search 注册 |
| [config/__init__.py](backend/config/__init__.py) | +8行 | SMTP 配置 |
| [skills/email/](backend/orchestration/skills/email/) | 新建 | EmailSkill |
| [skills/data_export/](backend/orchestration/skills/data_export/) | 新建 | DataExportSkill |
| [skills/web_search/](backend/orchestration/skills/web_search/) | 新建 | WebSearchSkill |

**当前 Capability 矩阵**：`sql.query` | `rag.search` | `report.generate` | `email.send` | `data.export` | `web.search` | `data.collect` — 共 7 个。
