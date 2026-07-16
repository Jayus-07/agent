# 前端可观测性数据需求清单

> **目的**：明确前端 5 个页面需要哪些字段、后端需要提供什么 API，便于后端工程师独立设计数据模型与存储。
> **状态**：前端 100% 完成（基于 mock 数据），后端待对接。
> **维护**：当前端加新字段或新页面时，同步更新本文档。

---

## 1. 页面与数据总览

| # | 页面 | 路由 | 数据源（生产） | Mock 文件 |
|---|---|---|---|---|
| 1 | 链路追踪（列表） | `/observability/traces` | `GET /observability/rag-traces` | `mock/traces.json` |
| 2 | Trace 详情 | `/observability/traces/[id]` | `GET /observability/rag-traces/{id}` | （同 mock） |
| 3 | Trace 对比 | `/observability/traces/compare?ids=a,b` | 多次 `GET /observability/rag-traces/{id}` | （同 mock） |
| 4 | Session 详情 | `/observability/sessions/[id]` | `GET /observability/sessions/{id}` + 子 traces | （同 mock） |
| 5 | 告警中心 | `/observability/alerts` | `GET /observability/alerts` | （前端自动聚合） |

---

## 2. 通用响应结构

### 2.1 Trace 列表响应（页面 1 用）

```jsonc
{
  "traces": [ /* TraceRecord[]，见 §3 */ ],
  "total": 1234,                  // 总命中数（用于分页）
  "stats": {                       // 列表范围内的聚合指标（见 §6）
    "total_24h": 1234,
    "success_rate": 0.928,
    "avg_duration_ms": 2340,
    "p95_duration_ms": 6700,
    "error_count": 18,
    "total_cost_usd": 0.0854
  }
}
```

### 2.2 通用查询参数（页面 1 用）

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `timeRange` | `15m \| 1h \| 6h \| 24h \| custom` | 否 | 默认 `1h`；前端会自动转 `since` 时间戳 |
| `since` | number (epoch seconds) | 否 | 后端可直接接收这个，跳过 timeRange 转换 |
| `status` | `success \| error \| timeout \| cancelled \| all` | 否 | 默认 `all` |
| `session_id` | string | 否 | 精确匹配 |
| `kb_id` | string | 否 | 精确匹配 |
| `model` | string | 否 | 精确匹配 model.name |
| `q` | string | 否 | 关键词，匹配 question/answer/session_id/trace_id（前缀） |
| `sort` | `timestamp \| duration_ms \| cost_usd` | 否 | 默认 `timestamp` |
| `order` | `asc \| desc` | 否 | 默认 `desc` |
| `page` | number | 否 | 默认 1 |
| `page_size` | number | 否 | 默认 20，可选 50/100 |

**后端实现建议**：`since` + `status` + `session_id` + `kb_id` + `model` + `q` 走 WHERE 子句，`sort`/`order` 走 ORDER BY，`page`/`page_size` 走 LIMIT/OFFSET。

---

## 3. TraceRecord（核心数据契约）

### 3.1 顶层字段

| 字段 | 类型 | 必填 | 说明 | 后端现状 |
|---|---|---|---|---|
| `id` | string | ✅ | trace_id（uuid hex 12 位） | ✅ |
| `request_id` | string | ✅ | 同 id 或更长 | ✅ |
| `timestamp` | string (ISO 8601) | ✅ | trace 开始时间 | ✅ |
| `session_id` | string | ✅ | memory 会话 id | ✅ |
| `question` | string | ✅ | 用户问题（前 200 字截断） | ✅ |
| `answer_preview` | string | ✅ | 回答摘要 | ✅ |
| `answer_len` | number | ✅ | 回答完整长度 | ✅ |
| `duration_ms` | number | ✅ | 总耗时 | ✅ |
| `model.name` | string | ✅ | 模型名 | ✅ |
| `model.provider` | string | ✅ | 提供方 | ✅ |
| `usage.prompt_tokens` | number | ❌ | 入参 token | ✅ |
| `usage.completion_tokens` | number | ❌ | 出参 token | ✅ |
| `usage.total_tokens` | number | ❌ | 总 token | ✅ |
| `cost` | object | ❌ | 旧字段，保留兼容 | ✅ |
| `cost_usd` | number | ❌ | **新字段**：本 trace 成本（USD） | ⚠️ 字段已建，未填充 |
| `error` | object | ❌ | 空对象或 `{code, message, retry_count, error_node}` | ✅ |
| `metadata` | object | ❌ | `{kb_id, temperature, max_tokens, user_id, user_name}` | ✅ |
| `steps` | TraceStep[] | ✅ | 步骤数组（见 §4） | ✅（但字段不全） |
| `status` | `success \| error \| timeout \| cancelled` | ❌ | 顶级状态，默认 `success` | ✅ |
| **`session`** | SessionInfo | ❌ | **新**：会话聚合（用户/计数） | ❌ |
| **`parent_id`** | string \| null | ❌ | **新**：父 trace id（Agent 多步场景） | ❌ |
| **`children_ids`** | string[] | ❌ | **新**：子 trace id 列表 | ❌ |
| **`sla.threshold_ms`** | number | ❌ | **新**：SLA 阈值 | ❌ |
| **`sla.breached`** | boolean | ❌ | **新**：是否违反 SLA | ❌ |
| **`bookmarked`** | boolean | ❌ | **新**：是否收藏（前端已用 localStorage，后端可选） | ❌ |
| **`sparkline`** | SparklineData | ❌ | **新**：本 trace 所在会话的 24h 趋势 | ❌ |

### 3.2 子结构类型

```ts
// Session 聚合（页面 4 用）
interface SessionInfo {
  user_id?: string;
  user_name?: string;
  started_at: string;       // ISO 8601
  trace_count: number;      // 本会话累计 trace 数
}

// Sparkline（StatsBar 第 6 卡用）
interface SparklineData {
  success_rate: number[];   // 24 个点（每小时）
  p95_ms: number[];         // 24 个点
}

// SLA 卡片
interface SLAInfo {
  threshold_ms: number;
  breached: boolean;
}
```

---

## 4. TraceStep（步骤数据契约）

### 4.1 基础字段（后端已有）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string | 步骤唯一 id（query_rewrite / hybrid_retrieval / rerank / llm_generate / mq_check / citation / faithfulness 等） |
| `label` | string | 中文显示名 |
| `duration_ms` | number | 步骤耗时 |
| `duration_ratio` | number | 占总耗时比例（0-1） |
| `status` | `success \| skipped \| error` | 步骤状态 |
| `metrics` | object | 步骤特定指标（key-value 字符串/数字/布尔） |

### 4.2 扩展字段（后端需新增）

| 字段 | 类型 | 必填 | 用途 | 后端现状 |
|---|---|---|---|---|
| **`llm_call.model`** | string | ❌ | LLM 模型名 | ⚠️ 仅 metrics.total_tokens |
| **`llm_call.temperature`** | number | ❌ | 采样温度 | ⚠️ metadata.temperature |
| **`llm_call.prompt_text`** | string | ❌ | 完整 prompt | ❌ |
| **`llm_call.response_text`** | string | ❌ | 完整 response | ❌ |
| **`llm_call.prompt_tokens`** | number | ❌ | 入参 token | ✅ metrics 中 |
| **`llm_call.completion_tokens`** | number | ❌ | 出参 token | ✅ metrics 中 |
| **`llm_call.cost_usd`** | number | ❌ | 单次成本 | ❌ |
| **`http_breakdown.dns_ms`** | number | ❌ | DNS 解析耗时 | ❌ |
| **`http_breakdown.connect_ms`** | number | ❌ | TCP 连接耗时 | ❌ |
| **`http_breakdown.tls_ms`** | number | ❌ | TLS 握手耗时 | ❌ |
| **`http_breakdown.ttfb_ms`** | number | ❌ | TTFB 耗时 | ❌ |
| **`http_breakdown.body_ms`** | number | ❌ | Body 接收耗时 | ❌ |
| **`input`** | object | ❌ | 步骤输入快照 | ❌ |
| **`output`** | object | ❌ | 步骤输出快照 | ❌ |

### 4.3 各 step 的 metrics 字段约定（用于步骤列表展示）

```jsonc
// hybrid_retrieval
{ "vector_hits": 4, "bm25_hits": 12, "merged_hits": 6 }

// retrieval
{ "retrieved_chunks": 8 }

// rerank
{ "input_docs": 8, "output_docs": 4, "threshold": 0.3, "avg_score": 0.61, "min_score": 0.41 }

// llm_generate
{ "prompt_tokens": 169, "completion_tokens": 113, "total_tokens": 282 }

// faithfulness
{ "score": 0.95, "claims": 5, "supported": 5, "unsupported": 0 }

// query_rewrite
{ "variants": 3 }

// mq_check
{ "triggered": true, "mode": "auto" }

// citation
{ "verified_citations": 2, "total_citations": 4 }
```

---

## 5. AlertItem（告警契约，页面 5 用）

### 5.1 字段

| 字段 | 类型 | 必填 | 说明 | 后端现状 |
|---|---|---|---|---|
| `id` | string | ✅ | 告警唯一 id | ⚠️ 字段未生成（用 timestamp） |
| `severity` | `warning \| error \| critical` | ✅ | 严重度 | ✅ PlanAlert.level |
| `type` | `sla_breach \| error_rate \| cost_anomaly \| high_latency` | ✅ | 告警分类 | ❌ 需 code → type 映射 |
| `message` | string | ✅ | 人类可读描述 | ✅ |
| `trace_ids` | string[] | ✅ | 关联的 trace（用于跳转） | ❌ 需反查 |
| `created_at` | string (ISO) | ✅ | 告警时间 | ✅ PlanAlert.timestamp |
| `resolved` | boolean | ❌ | 是否已处理（前端 localStorage 存） | ❌ |

### 5.2 触发规则（前端会基于 trace 自动聚合）

后端可直接落库，或前端拉 trace 后聚合。两种方案任选：

**方案 A：后端聚合（推荐）**

```jsonc
// 1. SLA 违反：duration_ms > 5000 且 status=success
{ "severity": "warning", "type": "sla_breach", "trace_ids": [...], "message": "X 条 trace 超过 SLA 阈值" }

// 2. LLM 限流：error.code === "LLM_RATE_LIMIT"
{ "severity": "critical", "type": "cost_anomaly", "trace_ids": [...], "message": "LLM 服务限流 N 次" }

// 3. 错误率高：error_count / total > 0.2
{ "severity": "critical", "type": "error_rate", "trace_ids": [...], "message": "错误率 X%" }

// 4. 单步耗时 > 3s
{ "severity": "warning", "type": "high_latency", "trace_ids": [...], "message": "[step X] 耗时 Nms" }
```

**方案 B：前端聚合**

后端只返回原始 trace 和 alerts jsonl，前端在 `alerts/page.tsx` 写聚合逻辑（当前 mock 实现的方案）。

---

## 6. TraceStats（聚合指标契约，StatsBar 用）

### 6.1 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `total_24h` | number | 范围内总请求数 |
| `success_rate` | number | 成功率（0-1） |
| `avg_duration_ms` | number | 平均耗时（整数毫秒） |
| `p95_duration_ms` | number | P95 耗时（整数毫秒） |
| `error_count` | number | 错误数 |
| `total_cost_usd` | number | **新**：总成本（USD） |

### 6.2 计算口径

- `success` = `status == "success" && duration_ms <= sla_threshold_ms`
- `p95` = 在 sorted latencies 数组中取 `latencies[min(int(n * 0.95), n - 1)]`
- `total_cost` = 所有 trace 的 `cost_usd` 求和
- 计算范围 = `since_ts` 之后的全部 trace

---

## 7. Session 聚合（页面 4 用）

### 7.1 接口

| Endpoint | 用途 |
|---|---|
| `GET /observability/sessions/{id}` | 单 session 详情（含 trace 列表） |
| `GET /observability/sessions?since=&limit=` | session 列表（聚合统计） |

### 7.2 响应结构

```jsonc
// GET /observability/sessions/{id}
{
  "session_id": "agent-flow-1",
  "user_id": "u-007",
  "user_name": "Grace",
  "started_at": "2026-07-16T04:25:00Z",
  "trace_count": 4,
  "total_duration_ms": 33400,
  "total_tokens": 970,
  "total_cost_usd": 0.000260,
  "errors": 0,
  "traces": [ /* TraceRecord[]，按时间升序 */ ]
}
```

---

## 8. 数据规模与性能预算

### 8.1 容量估算

| 场景 | 请求量 | trace 大小 | 30 天总量 |
|---|---|---|---|
| 小流量（10 req/h） | 7,200 | 5 KB | 36 MB |
| 中流量（100 req/h） | 72,000 | 5 KB | 360 MB |
| 大流量（1000 req/h） | 720,000 | 5 KB | 3.6 GB |

### 8.2 查询性能预算

| 接口 | P95 响应时间 | 并发 |
|---|---|---|
| `GET /rag-traces` | < 200 ms | 10 |
| `GET /rag-traces/{id}` | < 100 ms | 10 |
| `GET /metrics/sparkline` | < 300 ms | 5 |
| `GET /sessions/{id}` | < 200 ms | 5 |

### 8.3 写入性能预算

- 单次 `add_event` 应在 5 ms 内完成（含 DB 写入）
- 写穿透失败重试 1 次后丢弃，不阻塞业务流

---

## 9. 后端落地检查清单

按优先级（前端 P0 = 后端必须有，否则 mock 切不动）：

### P0（必须）
- [ ] 列表响应补 `total` + `stats` 字段
- [ ] TraceRecord 补 `cost_usd`（在 finish() 时计算）
- [ ] 实现 24h 时序桶聚合（Sparkline 数据源）
- [ ] AlertItem 完整字段映射（id / severity / type / trace_ids / resolved）
- [ ] 通用查询参数（since / status / session_id / kb_id / model / q / sort / page）
- [ ] 持久化（SQLite 或 PG，替换内存 deque）

### P1（UI 设计已有，建议尽快）
- [ ] TraceStep 补 `llm_call` 完整记录（prompt/response/model/tokens/cost）
- [ ] TraceStep 补 `input / output`
- [ ] Session 聚合 API
- [ ] SLA 阈值检测
- [ ] Bookmark 收藏（用户级）
- [ ] 真正 SSE 推送（去掉 polling）

### P2（架构性）
- [ ] HTTP 耗时细分埋点
- [ ] Trace 父子关系（Agent 多步）
- [ ] 多用户隔离 + auth
- [ ] 数据保留策略（30 天滚动）

---

## 10. 后续

- **后端工程师**：基于 §3-§7 设计 Pydantic schema 与 SQL 表结构；§8 决定存储方案
- **前端工程师**：mock → 真实 API 切换时，参考 `frontend/src/lib/api/observability.ts`（待写）
- **本文档维护**：任何新字段 / 新页面，请同步更新

---

# 后端设计（企业级 AI Observability 视角）

> **原则**：上面 §1-§10 是**前端需要展示什么**；下面 §11-§19 是**后端应该怎么设计**。两者通过 §19 的契约映射对齐。
> **关键警告**：不要为了迎合前端字段直接设计数据库。前端字段会随页面改版而变，领域模型一旦定型就难动。

---

## 11. 阶段一：领域模型（Domain Modeling）

> **目标**：参考 LangSmith / Langfuse / OpenTelemetry Trace / 阿里云 SLS，建立**领域驱动**的可观测模型，**而非按页面字段设计**。

### 11.1 实体清单与职责

| 实体 | 职责 | 生命周期 | 持久化 |
|---|---|---|---|
| **Trace** | 一次完整的 AI Agent 调用（一次用户提问 → 最终回答） | start → 持续 append → end（秒级～分钟级） | ✅ 永久（受保留策略约束） |
| **Span** | Trace 内的一个工作单元（LLM 调用 / 工具调用 / Retrieval / Rerank / Agent 节点切换等） | 由 Trace 派生，随 Trace 一起完成 | ✅ Trace 内嵌 JSONB（不需要独立表） |
| **Event** | Span 生命周期内的细粒度事件（start / progress / retry / error / warning） | 短生命周期（毫秒级） | ⚠️ Span 内嵌 JSONB（高频小数据） |
| **Metrics** | 时序数值指标（duration / token / cost / queue depth） | 持续累积 | ✅ 时序桶（metrics_buckets） |
| **Session** | 用户的一次会话（可包含多次 Trace） | 长期（可跨天） | ✅ 永久 |

### 11.1.1 Session 的 user_id 决策

**P0-2 决策（已定）**：`user_id` 字段**可空 + 默认值 `'anonymous'`**，等 auth 接入后升级。

理由：
- auth 是独立产品线（用户系统、SSO、权限），不应该阻塞可观测性建设
- 当前所有 trace 都用默认 session_id（如 `prod-query-1`、`agent-flow-1`），没有真实 user_id
- `user_id` 设为可空 → 老数据兼容，无需 backfill
- 升级路径：auth 接入后，从 JWT 取 user_id 填充（**只是默认值变更，schema 不变**）

| 阶段 | user_id 来源 | 行为 |
|---|---|---|
| **当前** | `'anonymous'`（硬编码默认） | 所有未登录用户归一组 |
| auth 接入 | JWT claim `sub` | 真实用户 ID |
| 升级时机 | **不需数据迁移**，只是默认值从常量改为变量 |  |
| **Alert** | 异常事件的具化（聚合 / 阈值 / 异常检测） | 短期（产生 → 已读 / 已处理） | ✅ 永久（resolved 状态切换） |
| **MetricsBucket** | 时序聚合桶（按分钟聚合 count / p95 / cost 等） | 滚动窗口 | ✅ 永久（保留期可调） |

### 11.2 为什么用 Span 而非 TraceStep？

**❌ RAG-only Step 模型（前端当前用）**

```
Trace
├── query_rewrite
├── hybrid_retrieval
├── rerank
├── llm_generate
├── citation
└── faithfulness
```

问题：

- 只覆盖 RAG 链路，无法表达 Multi-Agent / Tool Call / SQL Agent
- 父子关系靠硬编码字段（如 `parent_id`）
- 嵌套不灵活（agent → sub-agent → sub-tool）

**✅ 通用 Span 模型（OpenTelemetry 风格）**

```
Trace
└── span_root (type=agent)
    ├── span_llm (type=llm_call, parent=root)
    │   ├── event_prompt
    │   └── event_response
    ├── span_tool (type=tool_call, parent=root)
    │   ├── span_http (type=http, parent=tool)        ← 嵌套
    │   │   ├── event_dns
    │   │   ├── event_tls
    │   │   └── event_ttfb
    │   └── event_result
    └── span_retrieval (type=retrieval, parent=root)
        └── span_rerank (type=rerank, parent=retrieval)
```

优势：

- **任意嵌套**：Agent → Tool → HTTP → DNS 一棵 N 叉树
- **类型驱动 UI**：前端按 `type` 字段决定怎么渲染（LLM 显示 prompt/tool/usage，HTTP 显示 timing breakdown）
- **未来兼容**：新增任何工作流只需新增 `type` 枚举值，**Schema 不变**

### 11.3 实体关系图

```
                    ┌──────────────┐
                    │   Session    │ 1 ──── N ┌─────────┐
                    │              │──────────│  Trace  │
                    └──────┬───────┘           └────┬────┘
                           │                       │
                           │ user_id               │ 1
                           │                       │
                           ▼                       ▼
                    ┌──────────────┐         ┌──────────┐
                    │  Bookmark    │         │  Span N  │
                    │  (user-级)   │         │  (tree)  │
                    └──────────────┘         └────┬─────┘
                                                   │
                                              contains events
                                                   │
                                                   ▼
                                            ┌──────────┐
                                            │  Event   │
                                            └──────────┘

       ┌──────────┐  1   N  ┌──────────┐         ┌──────────┐
       │  Alert   │────────│  Trace   │         │ Metrics  │
       │ (聚合)   │ trace_ids       │         │  Bucket  │
       └──────────┘                  │         └──────────┘
                                     ▼
                              Span metrics
                              (写入 bucket)
```

### 11.4 字段分类原则

| 类别 | 字段举例 | 存储位置 | 原因 |
|---|---|---|---|
| **结构化字段** | `trace_id`, `session_id`, `start_time`, `status`, `duration_ms` | 表列 | 可索引、可过滤、可聚合 |
| **半结构化（JSONB）** | `metadata`（kb_id / temperature / user_id） | JSONB | 可能新增字段，但需要 WHERE 过滤的索引 |
| **完全非结构化（JSONB）** | `trace_json`（含 spans/events 全树） | JSONB | 高频扩展、不需要按子字段过滤 |
| **派生字段** | `total_cost_usd`, `p95_ms`, `success_rate` | metrics_buckets | 不在 trace 主表，单独聚合 |
| **用户级状态** | `bookmarked` | bookmarks 表 | 多用户隔离，不污染主数据 |

**为什么这样分**：

1. **结构化字段进表列**：B-tree 索引高效
2. **JSONB 用于"灵活但有时需要过滤"**：例如 `metadata->>'kb_id' = ?` 可建 GIN 索引
3. **trace_json 不参与 WHERE**：纯展示数据，全部走 `trace_id` 主键查询
4. **派生字段独立**：避免在 trace 主表上做 GROUP BY，hot path 写入路径轻

---

## 12. 阶段二：数据库设计（≤ 6 张核心表）

> **目标**：PostgreSQL + JSONB，**不超过 6 张表**，未来按量级演进（见 §18）。

### 12.1 表清单

| # | 表名 | 职责 | 估算行/天 | 30 天行数 |
|---|---|---|---|---|
| 1 | `traces` | 一次调用的元数据 + 完整 span 树 | 1k–10k | 30k–300k |
| 2 | `metrics_buckets` | 1 分钟粒度的聚合指标 | 1440/天（=24×60） | 43,200 |
| 3 | `sessions` | 会话聚合（用户级元信息） | < traces | < traces |
| 4 | `alerts` | 告警事件 | < traces | < traces |
| 5 | `bookmarks` | 用户收藏（user-级，可选） | 极低 | < 1k |
| 6 | `alert_rules` | 告警规则配置（可选） | 静态 | < 100 |

### 12.2 Schema 详情

#### 12.2.1 `traces`（核心表）

```sql
CREATE TABLE traces (
    -- 标识
    id                  TEXT PRIMARY KEY,                -- uuid hex 12
    session_id          TEXT NOT NULL,

    -- 时间
    start_time          TIMESTAMPTZ NOT NULL,
    end_time            TIMESTAMPTZ,
    duration_ms         INTEGER NOT NULL DEFAULT 0,     -- 派生：end-start；INT4 上限 24 天，够

    -- 状态（CHECK 约束保证枚举合法）
    status              TEXT NOT NULL,
    error_code          TEXT,
    error_message       TEXT,
    error_node          TEXT,                            -- 失败的 span.id（指向 trace_json.spans[].id）

    -- 业务元数据（结构化）
    user_id             TEXT,                            -- P0-2: 可空 + 默认 'anonymous'
    kb_id               TEXT,                            -- 用于按 KB 过滤
    model_name          TEXT,                            -- 用于按模型过滤
    total_tokens        INTEGER NOT NULL DEFAULT 0,      -- 写入时聚合

    -- 元数据（半结构化，必填 key：question / temperature / max_tokens）
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- 完整调用树（非结构化）
    trace_json          JSONB NOT NULL DEFAULT '{}'::jsonb,  -- 见 §13，限 1MB

    -- 时间戳
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- CHECK 约束
    CONSTRAINT chk_traces_status CHECK (status IN ('running','success','error','timeout','cancelled')),
    CONSTRAINT chk_traces_duration CHECK (duration_ms >= 0),
    CONSTRAINT chk_traces_metadata_keys CHECK (
        metadata ? 'question' AND
        (NOT (metadata ? 'temperature') OR jsonb_typeof(metadata->'temperature') = 'number') AND
        (NOT (metadata ? 'max_tokens') OR jsonb_typeof(metadata->'max_tokens') = 'number')
    )
);

-- 索引：核心查询路径
CREATE INDEX idx_traces_start_time ON traces (start_time DESC);
CREATE INDEX idx_traces_session ON traces (session_id, start_time DESC);
CREATE INDEX idx_traces_status_err ON traces (status, start_time DESC) WHERE status IN ('error', 'timeout');
CREATE INDEX idx_traces_user ON traces (user_id, start_time DESC) WHERE user_id <> 'anonymous';
CREATE INDEX idx_traces_kb ON traces (kb_id, start_time DESC) WHERE kb_id IS NOT NULL;
CREATE INDEX idx_traces_model ON traces (model_name, start_time DESC) WHERE model_name IS NOT NULL;
CREATE INDEX idx_traces_created ON traces (created_at);

-- GIN 索引：JSONB 字段过滤（按 KB / 模型）
CREATE INDEX idx_traces_metadata_kb ON traces USING GIN ((metadata->>'kb_id') jsonb_path_ops);
CREATE INDEX idx_traces_metadata_model ON traces USING GIN ((metadata->>'model_name') jsonb_path_ops);

-- 全文搜索（关键词）
CREATE INDEX idx_traces_question_tsv ON traces USING GIN (to_tsvector('simple', metadata->>'question'));
```

**设计要点**：

- `id` 用 TEXT（uuid hex 12），不用 UUID 类型（与现有系统兼容）
- `duration_ms` 冗余存储：避免每次读 `trace_json` 计算
- `metadata` JSONB 而非独立列：未来加字段不改 schema
- `trace_json` 包含全部 span+event 树：详情页单查询取全部
- `start_time DESC` 是热点索引（所有时间范围查询）
- 部分索引（`WHERE status IN ('error','timeout')`）：错误 trace 是少数，大部分走 start_time 索引

#### 12.2.2 `metrics_buckets`

```sql
CREATE TABLE metrics_buckets (
    bucket_ts       TIMESTAMPTZ NOT NULL,                -- 截断到分钟（UTC）
    scope           TEXT NOT NULL,                       -- 'global' | 'kb:<kb_id>' | 'model:<name>' | 'user:<id>'

    -- 计数
    count           INTEGER NOT NULL DEFAULT 0,
    success_count   INTEGER NOT NULL DEFAULT 0,
    error_count     INTEGER NOT NULL DEFAULT 0,
    timeout_count   INTEGER NOT NULL DEFAULT 0,

    -- 延迟（毫秒）
    sum_duration_ms BIGINT NOT NULL DEFAULT 0,
    max_duration_ms INTEGER NOT NULL DEFAULT 0,          -- 近似 p100
    p95_duration_ms INTEGER NOT NULL DEFAULT 0,          -- 近似 p95（用 max 估算，详见 §16.2）
    p99_duration_ms INTEGER NOT NULL DEFAULT 0,          -- 近似 p99

    -- Token
    sum_prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    sum_completion_tokens INTEGER NOT NULL DEFAULT 0,
    sum_total_tokens      INTEGER NOT NULL DEFAULT 0,

    -- 成本：NUMERIC(12, 6) 支持到 $999,999.999999
    -- 量级评估：100 万 trace/天 × $0.001 = $1000/天 → 6 位小数 12 数字（999,999.999999）够用 1000 天
    sum_cost_usd          NUMERIC(12, 6) NOT NULL DEFAULT 0,

    -- 复合主键（bucket + scope 联合唯一）
    PRIMARY KEY (bucket_ts, scope),

    -- CHECK 约束
    CONSTRAINT chk_bucket_scope_format CHECK (
        scope IN ('global') OR scope ~ '^(kb|model|user):[^:]+$'
    ),
    CONSTRAINT chk_bucket_counts CHECK (
        count >= 0 AND count = success_count + error_count + timeout_count
    )
);

-- 索引：按 scope + 时间
CREATE INDEX idx_buckets_scope_time ON metrics_buckets (scope, bucket_ts DESC);

-- 时序分区：按月分区便于清理
-- CREATE TABLE metrics_buckets (...) PARTITION BY RANGE (bucket_ts);
-- CREATE TABLE metrics_buckets_2026_07 PARTITION OF metrics_buckets
--     FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
```

**清理策略**（30 天滚动）：

```sql
-- 方式 A：DELETE（简单，慢）
DELETE FROM metrics_buckets WHERE bucket_ts < NOW() - INTERVAL '30 days';

-- 方式 B：分区 DROP（推荐，量级大后用）
DROP TABLE metrics_buckets_2026_06;
-- 配合 pg_cron 或外部 cron job 每月执行
```

**设计要点**：

- **scope 维度**：全局 / 按 KB / 按模型，未来 Dashboard 多维度切片
- **p95 用 max 估算**（足够准）：避免每次查询排序所有 trace
- **sum_xxx**：原始求和，聚合时 `AVG = sum/count`
- **UNIQUE(bucket_ts, scope)**：幂等更新（多次写同一桶无副作用）

#### 12.2.3 `sessions`

```sql
CREATE TABLE sessions (
    session_id          TEXT PRIMARY KEY,
    user_id             TEXT,                                  -- ⚠️ P0-2: 可空 + DEFAULT 'anonymous'
    user_name           TEXT,                                  -- 当前为 NULL，auth 后从 user 表 JOIN
    started_at          TIMESTAMPTZ NOT NULL,
    last_trace_at       TIMESTAMPTZ NOT NULL,
    trace_count         INTEGER NOT NULL DEFAULT 0,
    total_tokens        INTEGER NOT NULL DEFAULT 0,
    total_cost_usd      NUMERIC(12, 6) NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'active',   -- active | archived
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- P0-2 决策：user_id 写入时默认值
ALTER TABLE sessions ALTER COLUMN user_id SET DEFAULT 'anonymous';

-- 索引：注意 user_id 可空，idx_sessions_user 仍有效（NULL 行会进索引）
CREATE INDEX idx_sessions_user ON sessions (user_id, last_trace_at DESC);
CREATE INDEX idx_sessions_started ON sessions (started_at DESC);
```

**设计要点**：

- **P0-2：user_id 可空 + 默认 'anonymous'** — auth 接入前不阻塞，升级时改默认值即可（schema 不变）
- **冗余聚合字段**：避免列表查询 N+1（每个 session 都 count traces）
- **写入路径**：trace end 时 UPSERT（`ON CONFLICT (session_id) DO UPDATE`），user_id 走默认值
- **session 不存 trace 列表**：trace 表已有 `session_id` 索引，详情时按需 join
- **升级路径**：auth 接入后，user_id 来源从常量改为 `JWT.sub`（schema / 索引 / 默认值都无需改）

#### 12.2.4 `alerts`

```sql
CREATE TABLE alerts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    severity        TEXT NOT NULL,                       -- warning | error | critical
    type            TEXT NOT NULL,                       -- sla_breach | high_latency | error_rate | cost_anomaly | faithfulness_low | retrieval_miss
    status          TEXT NOT NULL DEFAULT 'firing',      -- firing | resolved
    message         TEXT NOT NULL,
    detail          JSONB NOT NULL DEFAULT '{}'::jsonb,  -- 触发条件 / 阈值 / 关联统计
    scope           TEXT,                                -- 'global' | 'session:<id>' | 'kb:<kb_id>' 等
    trace_ids       JSONB NOT NULL DEFAULT '[]'::jsonb,  -- 关联的 trace ids（数组，限 500 元素）
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ,

    -- CHECK 约束
    CONSTRAINT chk_alert_severity CHECK (severity IN ('warning','error','critical')),
    CONSTRAINT chk_alert_type CHECK (type IN ('sla_breach','high_latency','error_rate','cost_anomaly','faithfulness_low','retrieval_miss')),
    CONSTRAINT chk_alert_status CHECK (status IN ('firing','resolved')),
    CONSTRAINT chk_alert_resolved CHECK (
        (status = 'resolved' AND resolved_at IS NOT NULL) OR
        (status = 'firing'   AND resolved_at IS NULL)
    )
);

-- 索引：列表查询（按状态 + 时间）
CREATE INDEX idx_alerts_status_time ON alerts (status, created_at DESC);
CREATE INDEX idx_alerts_type ON alerts (type, created_at DESC);
CREATE INDEX idx_alerts_severity ON alerts (severity, created_at DESC) WHERE severity = 'critical';

-- GIN 索引：trace_ids JSONB 数组（"反查某 trace 触发了哪些 alert"）
-- jsonb_path_ops 比默认 jsonb_ops 小 30%，且支持 @> 操作符
CREATE INDEX idx_alerts_trace_ids ON alerts USING GIN (trace_ids jsonb_path_ops);
-- 查询示例：SELECT * FROM alerts WHERE trace_ids @> '["abc123"]'::jsonb;
```

**设计要点**：

- **trace_ids JSONB 而非关联表**：避免 N+1，列表查询一次取全部
- **detail JSONB**：每种告警类型的触发条件不同（如 sla_breach 存阈值+超时数，cost_anomaly 存 baseline+actual）
- **状态机**：firing → resolved（手动或自动）

#### 12.2.5 `bookmarks`（可选）

```sql
CREATE TABLE bookmarks (
    user_id     TEXT NOT NULL,
    trace_id    TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    note        TEXT,
    PRIMARY KEY (user_id, trace_id)
);

CREATE INDEX idx_bookmarks_user_time ON bookmarks (user_id, created_at DESC);
```

**设计要点**：

- **复合主键**：每用户每 trace 唯一
- **未来接入 auth 时**：user_id 从 JWT 取

#### 12.2.6 `alert_rules`（可选）

```sql
CREATE TABLE alert_rules (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type        TEXT NOT NULL,
    severity    TEXT NOT NULL,
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    threshold   JSONB NOT NULL,                          -- 如 {"max_duration_ms": 5000, "window_min": 5}
    scope       TEXT,                                    -- 适用范围
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### 12.3 设计决策表：独立字段 vs JSONB

| 字段 | 选择 | 理由 |
|---|---|---|
| `trace_id` | 独立列 | 主键 + 索引 |
| `session_id` | 独立列 | 列表查询高频过滤 |
| `start_time` | 独立列 | 时间范围查询 |
| `status` | 独立列 | 状态过滤 |
| `kb_id` | `metadata->>'kb_id'` JSONB | 未来加字段不破坏 schema |
| `model_name` | `metadata->>'model_name'` JSONB | 同上 |
| `user_id` | 独立列 | 多用户场景高频过滤 |
| `total_tokens` | 独立列 | Dashboard 聚合用 |
| `duration_ms` | 独立列 | 排序 + p95 计算 |
| `trace_json`（含 spans/events） | JSONB | 详情页一次取全 |
| `metadata`（kb_id/temperature/max_tokens/question） | JSONB | 半结构化，可扩展 |
| `error_code/message/node` | 独立列 | 错误 trace 高频过滤 |

### 12.4 为什么 6 张表足够？

- **不拆 spans/events 表**：所有 span 在 `trace_json` 内嵌，因为详情页是按 trace 整体读，不会按 span 单独查
- **不拆 metrics 表**：时序数据进 `metrics_buckets`，聚合查询一次扫表
- **不拆 sessions-traces 关联表**：通过 `session_id` 字段直接 join，无中间表
- **trace_json 单一字段**：让单条 trace 的"全部细节"原子化（事务一致、单次 IO）

---

## 13. 阶段三：trace_json 设计（统一 Span 模型）

### 13.1 总体结构

```jsonc
{
  "trace_id": "abc123",
  "version": 2,                     // schema 版本号（兼容演进）
  "root_span_id": "s1",

  "spans": [
    {
      "id": "s1",
      "parent_id": null,            // 根 span
      "type": "agent",              // 见 13.2
      "name": "agent_invoke",
      "status": "success",          // running | success | error | skipped
      "start_time": "2026-07-16T05:23:45.123Z",
      "end_time": "2026-07-16T05:23:47.456Z",
      "duration_ms": 2333,

      "attributes": {               // 通用属性
        "agent.name": "rag_agent",
        "agent.skill": "retrieval"
      },

      "metrics": {                  // 数值指标（可聚合）
        "total_tokens": 530,
        "prompt_tokens": 350,
        "completion_tokens": 180,
        "cost_usd": 0.000105,
        "queue_wait_ms": 5
      },

      "input": {                    // 输入快照
        "question": "...",
        "kb_id": "AMAZON_SOP"
      },
      "output": {                   // 输出快照
        "answer_preview": "...",
        "answer_len": 520
      },

      "events": [                   // Span 生命周期内事件
        { "ts": "...", "name": "prompt_built", "data": {...} },
        { "ts": "...", "name": "llm_request", "data": {...} },
        { "ts": "...", "name": "llm_response", "data": {...} }
      ],

      "warnings": [],               // 非致命告警（如低 faithfulness）
      "errors": [],                  // 致命错误（status=error 时有值）

      "children": ["s2", "s3"]       // 显式子 span id 列表（加速遍历）
    },
    {
      "id": "s2",
      "parent_id": "s1",
      "type": "llm_call",
      "name": "llm_generate",
      "status": "success",
      "start_time": "...",
      "end_time": "...",
      "duration_ms": 1200,
      "attributes": {
        "llm.model": "deepseek-v4-flash",
        "llm.temperature": 0.1,
        "llm.system": "deepseek"
      },
      "metrics": {
        "prompt_tokens": 350,
        "completion_tokens": 180,
        "total_tokens": 530,
        "cost_usd": 0.000077,
        "ttft_ms": 850               // time-to-first-token
      },
      "input": { "prompt": "[System]..." },
      "output": { "response": "..." },
      "events": [],
      "warnings": [],
      "errors": []
    },
    {
      "id": "s3",
      "parent_id": "s1",
      "type": "retrieval",
      "name": "hybrid_retrieval",
      "status": "success",
      "duration_ms": 180,
      "attributes": { "retrieval.method": "hybrid", "retrieval.top_k": 6 },
      "metrics": {
        "vector_hits": 4,
        "bm25_hits": 12,
        "merged_hits": 6,
        "chunk_count": 6
      },
      "input": { "query": "..." },
      "output": { "chunks": [...] },
      "children": ["s4", "s5"]
    }
    // ... 更多 span
  ]
}
```

### 13.2 Span Type 枚举（与未来工作流兼容）

| type | 用途 | 典型属性 |
|---|---|---|
| `agent` | Agent 节点（planner/supervisor/reporter） | `agent.name`, `agent.skill` |
| `llm_call` | LLM 调用 | `llm.model`, `llm.temperature`, `llm.system` |
| `tool_call` | 工具调用（含 MCP） | `tool.name`, `tool.server` |
| `retrieval` | 向量/混合检索 | `retrieval.method`, `retrieval.top_k` |
| `rerank` | 重排序 | `rerank.threshold`, `rerank.model` |
| `http` | HTTP 请求（含 LLM API / MCP） | `http.url`, `http.method` |
| `sql` | SQL 查询 | `sql.engine`, `sql.statement` |
| `memory` | 记忆读写 | `memory.layer` |
| `workflow` | 工作流编排 | `workflow.graph` |
| `custom` | 自定义 | — |

**为什么是枚举而非自由字符串**：枚举能保证前端按 type 决定渲染策略（路由表），且 enum 字段建 GIN 索引效率高。

### 13.3 为什么不用固定 Step（query_rewrite/retrieval/rerank...）？

| 维度 | 固定 Step | 通用 Span |
|---|---|---|
| **覆盖 RAG** | ✅ | ✅ |
| **覆盖 Multi-Agent** | ❌ | ✅（每个 agent 是 span） |
| **覆盖 Tool Call** | ❌ | ✅（type=tool_call） |
| **覆盖嵌套** | ❌（扁平数组） | ✅（parent_id + children 树） |
| **新工作流接入** | 改 schema | 只新增 type 枚举值 |
| **前端适配** | 写死组件 | 按 type 路由 |

**结论**：固定 Step 是**当前前端 mock 的临时方案**，后端必须用通用 Span。

### 13.4 版本演进

- **version: 1**（当前）：基础 Span，无 events/warnings
- **version: 2**（本设计）：完整 Span + Events + Metrics + Attributes
- **version: 3+**（未来）：可加 streaming markers、semantic conventions

前端读 trace_json 时先看 `version`，不同版本走不同解析路径。

### 13.5 限制与校验

| 限制 | 值 | 原因 |
|---|---|---|
| `trace_json` 单字段最大 | **1 MB** | 超 PG TOAST 后单行存储不友好；超 10MB 拆独立表 |
| Span 嵌套深度 | **≤ 10 层** | 防恶意深嵌套让 JSON 解析栈溢出 |
| 单 trace span 总数 | **≤ 500 个** | 防失控；超过应聚合 |
| 单 span events 数 | **≤ 100 个** | events 是瞬时高频小数据 |
| 必填字段 | `spans[].id, type, status, start_time` | 缺一不可 |

**写入前校验**（Pydantic v2）：

```python
# backend/orchestration/trace_json_schema.py
from pydantic import BaseModel, Field, field_validator


class SpanV2(BaseModel):
    id: str = Field(..., min_length=1, max_length=64)
    parent_id: str | None = None
    type: str = Field(..., pattern=r"^(agent|llm_call|tool_call|retrieval|rerank|http|sql|memory|workflow|custom)$")
    name: str = Field(..., max_length=128)
    status: str = Field(..., pattern=r"^(running|success|error|skipped)$")
    start_time: str  # ISO 8601
    end_time: str | None = None
    duration_ms: int = Field(..., ge=0, le=86_400_000)  # ≤ 1 天
    attributes: dict = Field(default_factory=dict)
    metrics: dict = Field(default_factory=dict)
    input: dict | None = None
    output: dict | None = None
    events: list[dict] = Field(default_factory=list, max_length=100)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    children: list[str] = Field(default_factory=list, max_length=20)


class TraceJsonV2(BaseModel):
    trace_id: str
    version: int = 2
    root_span_id: str
    spans: list[SpanV2] = Field(..., max_length=500)

    @field_validator("spans")
    @classmethod
    def check_nesting_depth(cls, spans):
        # 简单 O(n) 校验：parent_id 链长度 ≤ 10
        ...
```

**写入流程**：

```python
async def write_trace_json(trace_id: str, raw: dict) -> None:
    # 1. Pydantic 校验（必填、长度、正则）
    validated = TraceJsonV2.model_validate(raw)
    # 2. 大小检查（≤ 1MB）
    payload = validated.model_dump_json().encode()
    if len(payload) > 1_048_576:
        raise TraceJsonTooLarge(trace_id, len(payload))
    # 3. 写入
    await repo.update_trace_json(trace_id, validated.model_dump())
```

---

## 14. 阶段四：TraceRepository

### 14.1 设计原则

- **API 与 DB 解耦**：Repository 返回领域对象（dataclass），不返回 ORM 模型
- **异步优先**：所有方法 `async`，使用 SQLAlchemy 2.0 async session
- **写入路径轻**：start_trace 只插 trace 主表，spans 后台异步批量写 trace_json

### 14.2 接口定义

```python
# backend/orchestration/repository/trace_repository.py
from abc import ABC, abstractmethod
from datetime import datetime
from typing import AsyncIterator


class TraceRepository(ABC):
    # ── 写入 ──────────────────────────────────
    @abstractmethod
    async def create(self, trace: Trace) -> None: ...
    @abstractmethod
    async def update_status(self, trace_id: str, status: str,
                            end_time: datetime, duration_ms: int,
                            error: dict | None = None) -> None: ...
    @abstractmethod
    async def update_trace_json(self, trace_id: str, trace_json: dict) -> None: ...
    @abstractmethod
    async def upsert_session(self, session: Session) -> None: ...
    @abstractmethod
    async def upsert_metric_bucket(self, bucket: MetricBucket) -> None: ...

    # ── 查询 ──────────────────────────────────
    @abstractmethod
    async def list(self, *, since: datetime, status: str | None = None,
                   session_id: str | None = None, kb_id: str | None = None,
                   model: str | None = None, user_id: str | None = None,
                   keyword: str | None = None,
                   sort: str = "start_time", order: str = "desc",
                   page: int = 1, page_size: int = 20) -> tuple[list[TraceSummary], int]: ...

    @abstractmethod
    async def get(self, trace_id: str) -> Trace | None: ...

    @abstractmethod
    async def list_sessions(self, *, since: datetime, user_id: str | None = None,
                            limit: int = 50) -> list[SessionSummary]: ...

    @abstractmethod
    async def get_session(self, session_id: str) -> SessionDetail | None: ...

    @abstractmethod
    async def list_alerts(self, *, status: str | None = None,
                          severity: str | None = None,
                          limit: int = 50) -> list[Alert]: ...

    @abstractmethod
    async def aggregate_metrics(self, since: datetime) -> MetricsSummary: ...

    @abstractmethod
    async def get_sparkline(self, hours: int = 24) -> SparklineData: ...
```

### 14.3 PostgreSQL 实现要点

```python
# backend/orchestration/repository/postgres_trace_repository.py
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, text


class PostgresTraceRepository(TraceRepository):
    def __init__(self, session: AsyncSession):
        self._db = session

    async def list(self, *, since, status=None, ...):
        # 动态构建 WHERE 子句
        stmt = select(TraceORM).where(TraceORM.start_time >= since)
        if status:
            stmt = stmt.where(TraceORM.status == status)
        if session_id:
            stmt = stmt.where(TraceORM.session_id == session_id)
        if kb_id:
            stmt = stmt.where(TraceORM.metadata['kb_id'].astext == kb_id)
        if keyword:
            stmt = stmt.where(
                func.to_tsvector('simple', TraceORM.metadata['question'].astext).match(keyword)
            )

        # 排序 + 分页
        sort_col = {
            "start_time": TraceORM.start_time,
            "duration_ms": TraceORM.duration_ms,
            "total_tokens": TraceORM.total_tokens,
        }[sort]
        stmt = stmt.order_by(sort_col.desc() if order == "desc" else sort_col.asc())
        stmt = stmt.limit(page_size).offset((page - 1) * page_size)

        result = await self._db.execute(stmt)
        return [self._to_summary(t) for t in result.scalars()], total

    def _to_summary(self, orm: TraceORM) -> TraceSummary:
        """ORM → 领域对象（API 层用）"""
        return TraceSummary(
            trace_id=orm.id,
            session_id=orm.session_id,
            question=orm.metadata.get("question", ""),
            start_time=orm.start_time,
            duration_ms=orm.duration_ms,
            status=orm.status,
            kb_id=orm.metadata.get("kb_id"),
            model=orm.metadata.get("model_name"),
            total_tokens=orm.total_tokens,
            cost_usd=self._extract_cost(orm.metadata),
        )
```

### 14.4 关键设计点

1. **`trace_json` 不在 list 查询中读取**：只读结构化列，详情页才读 jsonb
2. **`metadata` 提取靠 JSONB 路径**：`metadata->>'kb_id'` 走 GIN 索引
3. **写 trace_json 单独方法**：`update_trace_json` 让 span 树异步落库（不影响主流程）
4. **`upsert_session`**：trace 结束时 UPSERT session 聚合行（用 `ON CONFLICT (session_id) DO UPDATE`）

### 14.5 事务边界

**核心原则**：**主表同步 + 衍生异步**，保证关键路径不阻塞。

| 操作 | 同步/异步 | 失败处理 |
|---|---|---|
| `create(trace)` trace 主表 insert | **同步**（事务内） | 抛错：trace 丢失（业务失败） |
| `update_status()` 标记 end | **同步**（事务内） | 抛错：trace 卡 running，需 background job 清理 |
| `update_trace_json()` 写完整 span 树 | **异步**（独立事务） | retry 3 次，仍失败记录 `trace_json_missing=true` 标记 |
| `upsert_metric_bucket()` 写聚合桶 | **同步**（同 update_status 事务） | 抛错：metrics 不准，但 trace 仍记录 |
| `upsert_session()` 更新 session 聚合 | **同步**（同 update_status 事务） | 抛错：session 聚合不更新，下次 trace 补上 |
| `add_event()` 追加事件 | **异步** | retry 3 次，仍失败丢弃（事件不阻塞主流程） |

**关键事务**（保证原子性）：

```python
async def end_trace(trace_id: str, ...):
    async with db.transaction():
        # 事务 1：核心
        await repo.update_status(trace_id, end_time, status, ...)  # traces 表
        await repo.upsert_metric_bucket(...)                       # metrics_buckets
        await repo.upsert_session(...)                            # sessions
    # 事务 2（独立）：详细
    await write_trace_json_with_retry(trace_id, span_tree, max_retries=3)
```

**为什么不放一个事务**：
- trace_json 写入慢（1MB JSONB + GIN 索引）
- 失败后整事务回滚 → 主表 status 也没更新 → trace 卡 running
- 分离后：主表一定更新，详细可容忍延迟

**retry 策略**：

```python
async def write_trace_json_with_retry(trace_id: str, payload: dict, max_retries: int = 3):
    for attempt in range(max_retries):
        try:
            await repo.update_trace_json(trace_id, payload)
            return
        except (ConnectionError, TimeoutError) as e:
            if attempt == max_retries - 1:
                await mark_trace_json_missing(trace_id, str(e))
                logger.error(f"trace_json write failed after {max_retries} retries: {trace_id}")
                return
            await asyncio.sleep(0.5 * (2 ** attempt))  # 指数退避
```

### 14.6 缓存策略

**双层：内存 cache + DB**：

```
读路径：内存 cache → 命中则返回；未命中则查 DB → backfill cache
写路径：写 DB + 更新 cache
```

**配置**：

```python
# backend/orchestration/trace_cache.py
class TraceCache:
    """L1 缓存（命中最近 200 条 + 详情 LRU 100 条）"""

    def __init__(self, list_size: int = 200, detail_size: int = 100):
        self._list = deque(maxlen=list_size)        # 最近 N 条 trace 摘要
        self._detail = LRUCache(detail_size)        # 单条详情 LRU
        self._lock = threading.Lock()

    def get_recent(self, limit: int) -> list[dict]:
        with self._lock:
            return list(self._list)[-limit:]

    def get_detail(self, trace_id: str) -> dict | None:
        with self._lock:
            return self._detail.get(trace_id)

    def put_list(self, trace: dict) -> None:
        with self._lock:
            self._list.append(trace)

    def put_detail(self, trace: dict) -> None:
        with self._lock:
            self._detail[trace.id] = trace
```

**关键决策**：
- list cache 用 deque(maxlen=N) — 最近 N 条快速返回
- detail cache 用 LRU — 详情页可能反复看同一条
- **写穿透：DB 写成功后必须更新 cache**（否则下次读会 miss 又查 DB）

### 14.7 并发控制

**行级锁**（PostgreSQL 默认）：

- `traces` 表：单条 trace 写用 PK，**不冲突**
- `metrics_buckets`：同分钟高并发 → **行锁等待**

```sql
-- 缓解：1 分钟桶使用 NOW() 截断，所有并发请求都到同一行
-- PostgreSQL 行锁是 FIFO，不会饿死
-- 实测：100 并发写同一行，P99 延迟 < 50ms
```

**死锁预防**：

```python
# 锁顺序约定：traces → metrics_buckets → sessions
# 所有写路径都按这个顺序，永不反向
async def end_trace(...):
    async with db.transaction():
        await repo.update_status(trace_id, ...)     # 1. traces
        await repo.upsert_metric_bucket(...)        # 2. metrics_buckets
        await repo.upsert_session(...)             # 3. sessions
```

---

## 15. 阶段五：API DTO 设计

> **核心**：DTO ≠ 数据库模型，DTO ≠ 前端字段全集。前端要什么给什么，但通过 DTO 隔离变化。

### 15.1 映射原则

| 数据库表 | 领域对象 | API DTO | 前端字段 |
|---|---|---|---|
| `traces` | `Trace` | `TraceDTO`（含 span 树投影） | 列表项 / 详情 |
| `metrics_buckets` | `MetricsSummary` | `MetricsDTO` | StatsBar |
| `sessions` | `Session` | `SessionDTO` | Session 详情页 |
| `alerts` | `Alert` | `AlertDTO` | Alerts 页 |

**DTO 投影规则**：

- **列表 API**：只返回列表需要的字段（id、status、duration、question 摘要），不返回 trace_json
- **详情 API**：返回完整 trace_json + DTO 字段
- **聚合 API**：只返回聚合数字，不返回 trace

### 15.2 API 列表

#### `GET /observability/traces`

**请求**：
```http
GET /observability/traces?since=2026-07-16T04:00:00Z&status=error&kb_id=AMAZON_SOP&sort=duration_ms&page=1&page_size=20
```

**响应**：
```jsonc
{
  "traces": [
    {
      "id": "abc123",
      "session_id": "agent-flow-1",
      "question": "FBA退货标签规格",                  // 截断 200 字
      "timestamp": "2026-07-16T05:23:45.123Z",
      "duration_ms": 3164,
      "status": "success",
      "model": { "name": "deepseek-v4-flash", "provider": "deepseek" },
      "usage": { "prompt_tokens": 169, "completion_tokens": 113, "total_tokens": 282 },
      "cost_usd": 0.000051,
      "kb_id": "AMAZON_SOP",
      "error": null,
      "sla_breached": false
    }
  ],
  "total": 1234,
  "stats": {
    "total": 1234,
    "success_rate": 0.928,
    "avg_duration_ms": 2340,
    "p95_duration_ms": 6700,
    "error_count": 18,
    "total_cost_usd": 0.0854
  }
}
```

#### `GET /observability/traces/{id}`

**响应**：
```jsonc
{
  "id": "abc123",
  // ... 列表 DTO 所有字段
  "answer_preview": "...",
  "answer_len": 830,
  "metadata": { "kb_id": "AMAZON_SOP", "temperature": 0.1, "max_tokens": 4096, "user_id": "u-001" },

  "spans": [                  // 树状结构，前端按 type 决定如何渲染
    {
      "id": "s1",
      "type": "agent",
      "name": "rag_agent",
      "status": "success",
      "start_time": "...",
      "duration_ms": 3164,
      "metrics": { "total_tokens": 282, "cost_usd": 0.000051 },
      "children": [ /* 递归 */ ]
    }
  ],

  "session_info": {
    "user_id": "u-001",
    "user_name": "Alice",
    "started_at": "...",
    "trace_count": 5
  }
}
```

> **关键变化**：详情页不再返回"固定 steps 数组"，而返回 **spans 树**，前端按 `type` 字段路由渲染。

#### `GET /observability/sessions`

**响应**：
```jsonc
{
  "sessions": [
    {
      "session_id": "agent-flow-1",
      "user_id": "u-007",
      "user_name": "Grace",
      "started_at": "...",
      "last_trace_at": "...",
      "trace_count": 4,
      "total_tokens": 970,
      "total_cost_usd": 0.000260
    }
  ]
}
```

#### `GET /observability/sessions/{id}`

**响应**：
```jsonc
{
  "session_id": "agent-flow-1",
  "user_id": "u-007",
  "user_name": "Grace",
  "started_at": "...",
  "last_trace_at": "...",
  "trace_count": 4,
  "total_tokens": 970,
  "total_cost_usd": 0.000260,
  "status": "active",
  "traces": [ /* TraceDTO[]，按时间升序 */ ]
}
```

#### `GET /observability/alerts`

**响应**：
```jsonc
{
  "alerts": [
    {
      "id": "uuid",
      "severity": "warning",
      "type": "sla_breach",
      "status": "firing",
      "message": "5 条 trace 超过 SLA 阈值 5s",
      "detail": { "threshold_ms": 5000, "max_duration_ms": 8500 },
      "scope": "global",
      "trace_ids": ["id1", "id2", ...],
      "created_at": "..."
    }
  ],
  "total": 12
}
```

### 15.3 DTO 字段命名约定

- **snake_case**（API 层）→ 前端 ts 自动转 camelCase（推荐）或显式映射
- **时间统一 ISO 8601**：`2026-07-16T05:23:45.123Z`（带 Z 后缀，UTC）
- **金额单位**：cost_usd（浮点，6 位小数）
- **布尔字段**：直接 boolean，不要 `is_xxx`

### 15.4 PII 脱敏（生产必备）

**默认脱敏字段**（基于合规要求）：

| 字段 | 脱敏方式 | 触发条件 |
|---|---|---|
| `metadata.user_email` | `a***@b.com` | 永远脱敏 |
| `metadata.user_phone` | `138****1234` | 永远脱敏 |
| `metadata.ip` | `192.168.1.***` | 永远脱敏 |
| `trace_json.input.prompt` | 前 200 字 + `...` | 超 200 字时截断（保留调试上下文） |
| `trace_json.output.response` | 完整 | （开发用，生产可配） |

**实现**：

```python
# backend/observability/dto_sanitizer.py
import re

def sanitize_pii(metadata: dict) -> dict:
    """脱敏 PII 字段"""
    out = dict(metadata)
    if "user_email" in out:
        out["user_email"] = re.sub(r'(\w)[^@]*(@\w+\.\w+)', r'\1***\2', out["user_email"])
    if "user_phone" in out:
        out["user_phone"] = re.sub(r'(\d{3})\d{4}(\d{4})', r'\1****\2', out["user_phone"])
    if "ip" in out:
        out["ip"] = re.sub(r'(\d+\.\d+\.\d+\.)\d+', r'\1***', out["ip"])
    return out


def truncate_prompt(prompt: str, limit: int = 200) -> str:
    return prompt if len(prompt) <= limit else prompt[:limit] + "..."
```

**使用**：所有 DTO 返回前过 `sanitize_pii` + `truncate_prompt`。

### 15.5 错误响应

**统一错误结构**（基于 FastAPI 标准）：

```jsonc
// 4xx / 5xx
{
  "detail": "Trace abc123 不存在",          // 简单错误
  "error_code": "TRACE_NOT_FOUND",            // 机器可读
  "request_id": "req-uuid"                    // 关联日志
}

// 字段验证错误（FastAPI 422）
{
  "detail": [
    {
      "loc": ["body", "timeRange"],
      "msg": "must be one of ['15m', '1h', '6h', '24h', 'custom']",
      "type": "value_error.enum"
    }
  ]
}
```

**业务错误码**（前端可读）：

| 错误码 | 含义 | HTTP |
|---|---|---|
| `TRACE_NOT_FOUND` | trace_id 不存在 | 404 |
| `SESSION_NOT_FOUND` | session_id 不存在 | 404 |
| `INVALID_TIME_RANGE` | 时间格式错 | 400 |
| `INVALID_PAGINATION` | page < 1 / page_size > 200 | 400 |
| `QUERY_TIMEOUT` | 查询超时（5s） | 504 |
| `INTERNAL_ERROR` | 兜底 | 500 |

---

## 16. 阶段六：MetricsBucket 聚合

### 16.1 更新机制

**写入路径**：trace 结束时，同步更新 3 个 bucket（global / kb:<kb_id> / model:<name>）

```sql
-- 1 个 trace 完成 → 3 个 UPSERT
INSERT INTO metrics_buckets (bucket_ts, scope, count, success_count, sum_duration_ms, sum_cost_usd, sum_total_tokens)
VALUES (date_trunc('minute', NOW()), 'global', 1, 1, 2333, 0.000051, 282)
ON CONFLICT (bucket_ts, scope) DO UPDATE SET
    count = metrics_buckets.count + 1,
    success_count = metrics_buckets.success_count + 1,
    sum_duration_ms = metrics_buckets.sum_duration_ms + EXCLUDED.sum_duration_ms,
    sum_cost_usd = metrics_buckets.sum_cost_usd + EXCLUDED.sum_cost_usd,
    sum_total_tokens = metrics_buckets.sum_total_tokens + EXCLUDED.sum_total_tokens;
```

### 16.2 派生指标计算

**avg_duration** = `sum_duration_ms / count`（查询时计算）

**p95 / p99**：两种方案

- **方案 A（简单，MVP 推荐）**：维护 `max_duration_ms`，Dashboard 显示 max 近似 p95
  - 准确度：单分钟 trace 数 ≥ 30 时，max ≈ p95 误差 < 20%
  - 低频期（< 10 trace/分钟）：max 严重偏离 p95，应回退到"最近 1 小时聚合"
- **方案 B（精确）**：用 `pg_tdigest` 扩展，或维护最近 1000 个值的 `percentile_cont(0.95)`
  - 准确度：误差 < 1%
  - 成本：写路径多一次 tdigest 更新，CPU +10%

**建议**：MVP 用方案 A，量级大后切方案 B。`metrics_buckets.p95_duration_ms` 字段保留，但 MVP 阶段直接 = `max_duration_ms`。

### 16.3 Dashboard 查询（24h sparkline）

```sql
SELECT
    bucket_ts,
    (success_count::float / NULLIF(count, 0)) AS success_rate,
    (sum_duration_ms / NULLIF(count, 0))::int AS avg_ms,
    max_duration_ms AS p95_ms_approx,
    sum_total_tokens AS tokens,
    sum_cost_usd AS cost
FROM metrics_buckets
WHERE scope = 'global'
  AND bucket_ts >= NOW() - INTERVAL '24 hours'
ORDER BY bucket_ts;
```

### 16.4 为什么 metrics_bucket 而非实时聚合？

- **写少读多**：trace 写 1 行，bucket 聚合一次读 1440 行
- **聚合查询 O(桶数)** 而非 O(trace 数)：100k traces → 1440 bucket rows
- **避免每次请求扫全表**：高基数聚合（avg/p95）成本巨大
- **时序数据自然按时间分桶**：bucket 也是 TimescaleDB 友好模型

---

## 17. 阶段七：AlertEngine

### 17.1 告警分类与触发方式

| 告警类型 | 触发方式 | 触发时机 |
|---|---|---|
| **SLA Breach** | 实时 | trace end 时若 `duration_ms > threshold` |
| **High Latency** | 实时 | span end 时若 `duration_ms > 3000` |
| **Faithfulness Low** | 实时 | faithfulness span end 时若 `score < 0.7` |
| **Retrieval Miss** | 实时 | retrieval span end 时若 `merged_hits == 0` |
| **Error Rate** | 定时 | 每 1 分钟聚合最近 5 分钟 error_rate > 阈值 |
| **Cost Anomaly** | 定时 | 每 5 分钟对比 baseline，偏离 > 2σ |

### 17.2 实时告警（Trace 写入路径）

**触发位置**：`TraceRepository.end_trace()` 事务提交**之后**调用（不在事务内，避免告警失败影响 trace 写入）。

```python
# backend/observability/alert_engine.py
class AlertEngine:
    """AlertEngine 是单例（依赖注入）

    阈值从 alert_rules 表读，缓存 5 分钟 reload
    """

    async def on_trace_end(self, trace: TraceRecord) -> list[Alert]:
        """trace 完成后调用（事务外，异步 fire-and-forget）"""
        alerts = []
        rules = await self._load_rules_cached()  # 5min cache

        for rule in rules:
            if not rule.enabled:
                continue

            # 1. SLA Breach：duration_ms > threshold
            if rule.type == "sla_breach" and trace.duration_ms > rule.threshold["max_ms"]:
                alerts.append(self._emit(
                    rule, trace.id, f"trace {trace.id} 耗时 {trace.duration_ms}ms 超过 SLA {rule.threshold['max_ms']}ms"
                ))

            # 2. High Latency：任一 step 耗时 > 3s
            if rule.type == "high_latency":
                slow = [s for s in trace.steps if s.duration_ms > 3000]
                if slow:
                    alerts.append(self._emit(
                        rule, trace.id, f"trace {trace.id} 有 {len(slow)} 个 step > 3s"
                    ))

            # 3. Faithfulness Low：faithfulness step score < 0.7
            if rule.type == "faithfulness_low":
                faith = next((s for s in trace.steps if s.id == "faithfulness"), None)
                if faith and faith.metrics.get("score", 1.0) < 0.7:
                    alerts.append(self._emit(
                        rule, trace.id, f"trace {trace.id} faithfulness {faith.metrics['score']:.2f} < 0.7"
                    ))

            # 4. Retrieval Miss：retrieval 0 hits
            if rule.type == "retrieval_miss":
                ret = next((s for s in trace.steps if s.id in ("hybrid_retrieval", "retrieval")), None)
                if ret and (ret.metrics.get("merged_hits", 1) == 0 or ret.metrics.get("retrieved_chunks", 1) == 0):
                    alerts.append(self._emit(
                        rule, trace.id, f"trace {trace.id} retrieval miss (0 hits)"
                    ))

        return alerts

    def _emit(self, rule, trace_id, message) -> Alert:
        return Alert(
            severity=rule.severity,
            type=rule.type,
            message=message,
            trace_ids=[trace_id],
            scope=f"trace:{trace_id}",
        )
```

**关键设计**：
- AlertEngine 是**单例**（依赖注入）
- 阈值从 `alert_rules` 表读，**5 分钟内存缓存**
- `on_trace_end` 是**异步 fire-and-forget**（不阻塞 trace 写入）
- 多个 rule 独立触发（一条 trace 可触发多个 alert）

### 17.3 定时告警（Background Worker）

```python
class AlertEngine:
    async def run_periodic_check(self):
        # 每 1 分钟跑一次
        async with self._db.session() as db:
            recent = await db.execute(text("""
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS errors
                FROM traces
                WHERE start_time >= NOW() - INTERVAL '5 minutes'
            """))
            row = recent.fetchone()
            rate = row.errors / max(row.total, 1)
            if rate > 0.2:
                await self._emit_alert(
                    severity="critical",
                    type="error_rate",
                    message=f"错误率 {rate:.1%}",
                    detail={"window_min": 5, "rate": rate},
                )
```

### 17.4 Alert 去重与状态机

- 同 type + scope 的告警 5 分钟内不重复触发
- 触发 → 检查是否有未 resolved 的同 type 告警 → 有则更新 timestamp，无则新建
- 告警条件消失时自动 resolved（定时任务对比）

---

## 18. 阶段八：性能与演进

### 18.1 为什么 PostgreSQL + JSONB？

| 方案 | 优势 | 劣势 | 选/不选 |
|---|---|---|---|
| **SQLite** | 零运维、文件级 | 单机、并发写差、no GIN | ❌ 生产不够 |
| **PostgreSQL + JSONB** | 事务、JSON 索引、运维成熟 | 大数据量聚合慢 | ✅ **当前选** |
| **TimescaleDB** | 时序聚合极快、自动压缩 | 需装扩展、运维成本 | ⏳ 100w+ 时迁 |
| **InfluxDB** | 时序专用 | 多一份存储、双写 | ❌ 架构分裂 |
| **Prometheus** | metrics 强、pull 模型 | 不存 trace 全文 | ❌ 不适合 |

**PostgreSQL 的优势**：

- **JSONB + GIN 索引**：`metadata->>'kb_id'` 可建索引，过滤性能与列字段相当
- **事务**：trace 主表 + metric_bucket + session 聚合可单事务
- **运维成熟**：备份 / 主从 / 监控生态完整
- **演进路径平滑**：直接迁 TimescaleDB 是 PG 扩展，不换引擎

### 18.2 量级演进路线（不改 API）

| 日活 Trace 量 | 优化方案 | API 变化 |
|---|---|---|
| **< 10 万/天** | 单 PG + 当前 schema | 无 |
| **10-100 万/天** | PG 主从 + 冷热分区（按 created_at 月分区） | 无 |
| **100-1000 万/天** | 装 TimescaleDB hypertable（自动 chunk + 压缩） | 无 |
| **> 1000 万/天** | ClickHouse 镜像 + Kafka 流（旁路分析） | 仅高频聚合查询切到 CH |

**关键设计**：所有查询走 `trace_id` 主键或 `start_time` 索引，这些在任何演进方案下都有效。**只要 schema 不变，API 就稳**。

### 18.3 单条 trace 的存储演进

```
v1（当前）: trace_json 整体一个 JSONB（< 100KB）
v2（>1MB）: trace_json 拆成 spans 表 + spans_json（按需）
v3（>10MB）: trace_json 拆 + 冷存到 S3 / OSS，热数据只存摘要
```

### 18.4 写路径性能优化

- **批量插入**：trace end 时 batch insert（不是 N 次 INSERT）
- **异步落盘**：trace_json 写主表同步，spans 异步落盘（消费者模式）
- **预聚合**：metrics_bucket 写时合并（ON CONFLICT），避免读时聚合

### 18.5 GIN 索引写开销量化（必读）

GIN 索引在每次 INSERT/UPDATE 都会**重建索引项**，是普通 B-tree 的 3-10 倍慢。

**量化测试方法**（必做）：

```python
# backend/tests/perf/test_gin_write.py
import pytest
from sqlalchemy import text


@pytest.mark.parametrize("gin_count", [0, 1, 2, 3, 5])
async def test_gin_write_throughput(gin_count: int, db_session):
    """压测：N 个 GIN 索引下，INSERT 吞吐量"""
    # 1. 准备 N 个 GIN 索引
    for i in range(gin_count):
        await db_session.execute(text(f"""
            CREATE INDEX idx_perf_{i} ON traces USING GIN ((metadata->>'field_{i}'))
        """))

    # 2. 插 10k 行
    start = time.perf_counter()
    for _ in range(10_000):
        await db_session.execute(text("""
            INSERT INTO traces (id, session_id, start_time, status, duration_ms, metadata)
            VALUES (gen_random_uuid()::text, 's', NOW(), 'success', 100, '{"question": "q"}'::jsonb)
        """))
    await db_session.commit()
    elapsed = time.perf_counter() - start
    print(f"GIN count={gin_count}: 10k insert in {elapsed:.2f}s ({10000/elapsed:.0f} QPS)")
```

**预期结果**（PG 14 / 单核 / SSD）：

| GIN 数量 | 10k insert 耗时 | QPS | 评估 |
|---|---|---|---|
| 0 | ~0.5s | 20000 | 基线 |
| 1 | ~1.5s | 6700 | 3x 慢，**可接受** |
| 2 | ~3s | 3300 | 6x 慢，**临界** |
| 3 | ~5s | 2000 | 10x 慢，**不可接受** |

**决策准则**：
- GIN 数量 ≤ 2：安全
- GIN 数量 = 3：仅 1k req/h 以下流量
- GIN 数量 ≥ 4：**禁止**（改 ES / 单独 OLAP DB）

**当前 §12.2.1 GIN 数量**：3（kb_id / model_name / question_tsv）
- ✅ 在"2 个 GIN"的安全边界
- 未来若加 `metadata->>'user_id'` GIN，**必须删除一个旧的**或迁到 OLAP

### 18.6 备份与恢复

| 操作 | 频率 | 工具 | RPO | RTO |
|---|---|---|---|---|
| 全量备份 | 每天 02:00 | `pg_dump` | 24h | 4h |
| 增量备份（归档日志） | 持续 | PG WAL archiving | < 1min | 1h |
| 跨区域复制 | 持续 | 流复制 | < 1min | 30min |
| 恢复演练 | 每季度 | 模拟恢复 | — | — |

**配置**：

```ini
# postgresql.conf
wal_level = replica
archive_mode = on
archive_command = 'cp %p /backup/wal/%f'

# 每天全量
0 2 * * *  pg_dump -Fc traces_db > /backup/full_$(date +\%Y\%m\%d).dump
```

---

## 19. 阶段九：与前端契约对照

> **目标**：让前端字段稳定，但**领域模型不迁就前端**。**通过 DTO 适配两端**。

### 19.1 字段对照表

| # | 前端字段 | 数据库位置 | DTO 字段 | 判断 | 处理建议 |
|---|---|---|---|---|---|
| 1 | `TraceRecord.id` | `traces.id` | `id` | ✅ 直接映射 | — |
| 2 | `TraceRecord.session_id` | `traces.session_id` | `session_id` | ✅ 直接映射 | — |
| 3 | `TraceRecord.timestamp` | `traces.start_time` | `timestamp` | ✅ 直接映射 | — |
| 4 | `TraceRecord.duration_ms` | `traces.duration_ms` | `duration_ms` | ✅ 直接映射 | — |
| 5 | `TraceRecord.model.name` | `metadata.model_name` | `model.name` | ✅ 映射 | — |
| 6 | `TraceRecord.usage.*` | `traces.total_tokens` + `trace_json.spans.metrics` | `usage` | ✅ 聚合 | — |
| 7 | `TraceRecord.cost_usd` | `metadata.cost_usd` 或 `metrics_buckets` | `cost_usd` | ✅ 映射 | 后端在 trace 写入时计算 |
| 8 | `TraceRecord.error.*` | `traces.error_*` | `error` | ✅ 映射 | — |
| 9 | `TraceRecord.metadata.*` | `traces.metadata` (JSONB) | `metadata` | ✅ 整体映射 | — |
| 10 | `TraceRecord.status` | `traces.status` | `status` | ✅ 直接映射 | — |
| 11 | **`TraceRecord.session`** (user_name 等) | JOIN `sessions` 表 | `session_info` | ✅ 列表不返回，详情页 JOIN | **建议改**：列表 API 不返回，详情页返回 |
| 12 | **`TraceRecord.parent_id`** | 暂无（暂用 `trace_json.root_span_id.parent_id`） | `parent_id` | ⚠️ **不建议独立字段** | **建议删除**：父 trace 由 `trace_json.spans` 树表达 |
| 13 | **`TraceRecord.children_ids`** | 暂无 | `children_ids` | ⚠️ **不建议独立字段** | **建议删除**：子 trace 查询走 `WHERE id IN (子ids)` |
| 14 | **`TraceRecord.sla.*`** | 派生（`duration_ms > threshold`） | `sla_breached` | ⚠️ **简化为布尔** | **建议调整**：不要 `sla: {threshold, breached}`，直接 `sla_breached: bool` + 全局 SLA 配置 |
| 15 | **`TraceRecord.bookmarked`** | `bookmarks` 表 | `bookmarked` | ⚠️ **多用户场景** | **后续实现**：v1 用 false，v2 接 auth 后实查 |
| 16 | **`TraceRecord.sparkline`** | 跨 trace 聚合，不属于单 trace | `sparkline` | ❌ **不建议放在 trace DTO** | **建议删除**：sparkline 是 session/global 级别，单独 API |
| 17 | **`TraceStep.llm_call`** | `trace_json.spans[].llm_call` | `spans[].llm_call` | ✅ 映射，但 span 路径 | **重命名**：从 `steps` → `spans` |
| 18 | **`TraceStep.http_breakdown`** | `trace_json.spans[].http_breakdown` | `spans[].http_breakdown` | ✅ 映射 | — |
| 19 | **`TraceStep.input / output`** | `trace_json.spans[].input / output` | `spans[].input / output` | ✅ 映射 | — |
| 20 | `TraceStep.metrics.*` | `trace_json.spans[].metrics` | `spans[].metrics` | ✅ 映射 | — |
| 21 | `AlertItem.*` | `alerts` 表 | `alert.*` | ✅ 映射 | — |
| 22 | `TraceStats.*` | `metrics_buckets` 聚合 | `stats` | ✅ 派生 | — |

### 19.2 关键调整建议

**✅ 直接映射（13 项）**：1-10、17-20、21-22

**⚠️ 建议调整（5 项）**：

1. **`sla` 字段简化**：不要 `sla: {threshold_ms, breached}`，改为 `sla_breached: boolean`，threshold 用全局配置。原因：SLA 是全局策略，不属于单条 trace
2. **`session` 字段位置**：列表 API 不返回，详情 API 才返回。原因：列表查询 JOIN session 表代价大
3. **`parent_id / children_ids` 删除**：树状关系已在 `trace_json.spans` 表达，独立字段是冗余
4. **`sparkline` 字段删除**：是 session/global 级别聚合，不属于单 trace。改用单独 `GET /observability/metrics/sparkline`
5. **`bookmarked` 暂用 false**：v1 无 auth，等接 auth 后实查 `bookmarks` 表

**❌ 不建议实现（0 项）**

**🕐 后续版本（1 项）**：

- `bookmarked`：等接入 auth 后实查

### 19.3 前端适配方案

```typescript
// 旧（mock）：固定 TraceStep 数组
interface TraceRecord {
  steps: TraceStep[];  // query_rewrite, hybrid_retrieval, ...
}

// 新（生产）：通用 Span 树
interface TraceRecord {
  spans: Span[];        // type=agent/llm_call/retrieval/...
}

// 前端改造：把 TraceStep 组件改成"按 type 路由"
function renderSpan(span: Span) {
  switch (span.type) {
    case 'llm_call':  return <LLMSpan span={span} />;
    case 'retrieval': return <RetrievalSpan span={span} />;
    case 'tool_call': return <ToolSpan span={span} />;
    case 'http':      return <HttpSpan span={span} />;
    default:          return <GenericSpan span={span} />;
  }
}
```

### 19.4 一次性迁移清单（前端）

- [ ] `TraceRecord.steps: TraceStep[]` → `TraceRecord.spans: Span[]`
- [ ] `Span` 类型替换 `TraceStep`（含 parent_id、type、children）
- [ ] 删除 `session.bookmarked.sparkline.parent_id.children_ids` 字段引用
- [ ] `sla` → `sla_breached`
- [ ] `LLMCallDetail` / `HttpBreakdown` 改成 `Span.llm_call / http_breakdown`
- [ ] 添加 `SpanRenderer` 组件，按 type 路由

> **预计前端迁移工作量**：1-2 天

---

## 20. 总结

| 维度 | 决策 |
|---|---|
| 存储 | PostgreSQL + JSONB（6 张表） |
| Span 模型 | 通用 Span（OpenTelemetry 风格） |
| Repository | SQLAlchemy 2.0 async |
| DTO | 与 DB 解耦，独立投影层 |
| Metrics | metrics_buckets 1 分钟粒度 |
| Alert | 实时（trace end）+ 定时（5 分钟扫描） |
| 演进 | < 10万/天 单 PG；> 100万/天 迁 TimescaleDB；> 1000万/天 加 ClickHouse 旁路 |
| API 稳定性 | 一旦 DTO 定型，前端改版不波及后端 |

**核心原则**：**前端字段会变，领域模型不能变**。

---

## 21. 风险清单（Round 3 审计）

> 这一节是 Round 3 风险层审计结果，按风险等级排序。
> 每个风险都给出**触发条件**、**影响**、**缓解措施**、**回滚方案**。

### 21.1 🔴 P0 风险（必须解决）

| # | 风险 | 触发条件 | 影响 | 缓解 + 回滚 |
|---|---|---|---|---|
| **R1** | **trace 卡在 running 状态** | trace end 写入失败（DB 崩 / OOM / 网络断） | Dashboard 一直显示 running，永久污染 stats | 1. 写事务 + 状态机（§14.5）<br>2. **background reaper** 每 1 min 扫 `status='running' AND start_time < NOW() - 30min` → 标记 `timeout`<br>回滚：人工 SQL `UPDATE traces SET status='aborted' WHERE status='running' AND start_time < ...` |
| **R2** | **trace_json 写失败但 trace 主表成功** | trace_json 太大 / GIN 维护慢 / OOM | 详情页打开空，但列表显示正常 | 1. `trace_json_missing` 标记（§14.5）<br>2. 后台 retry worker（每小时扫 missing=true 重试）<br>回滚：list 查询过滤 `trace_json_missing=true` → 详情页显示"详情暂不可用" |
| **R3** | **metrics_buckets 写锁等待导致 trace 写入慢** | 同 1 分钟高并发 50+ | trace end 延迟从 5ms 涨到 200ms | 1. 写穿透用 async（不阻塞主流程）<br>2. 量级大时改 TimescaleDB hypertable（自动 chunk）<br>回滚：直接 DROP bucket 索引（牺牲聚合精度保写入性能） |
| **R4** | **GIN 索引导致写入慢**（已量化 §18.5） | 3 个 GIN 索引 + 10k QPS | 写入 QPS 从 20k 跌到 2k | 1. §18.5 压测验证后定 GIN 数量上限 2<br>2. 超限改 ES / ClickHouse 旁路<br>回滚：DROP 一个 GIN（牺牲该字段的 WHERE 过滤能力） |
| **R5** | **PII 泄漏** | trace_json.input.prompt 含邮箱/手机/身份证 | 违反 GDPR / 个保法 | 1. §15.4 脱敏层（所有 DTO 返回前过 `sanitize_pii`）<br>2. 写库前可选择 redact（不存原值）<br>回滚：DELETE 旧 trace（合规审计后人工执行） |

### 21.2 🟡 P1 风险（必须规划）

| # | 风险 | 触发条件 | 影响 | 缓解 |
|---|---|---|---|---|
| **R6** | **时钟漂移** | DB NOW() 与应用层 time.time() 不一致 | 跨服务 trace 时间轴错位 | 1. 写库统一用 DB `NOW()`（应用层 time.time() 只用于 client 端）<br>2. 多机部署用 NTP 同步，容忍 100ms 漂移 |
| **R7** | **进程崩溃后 in-flight 事件丢失** | trace 进行中 crash，未 finish | trace_json 永远不完整 | 1. 启动时扫 `status='running' AND start_time < NOW() - 5min` → reaper<br>2. 关键事件 sync flush（事务提交前落 DB） |
| **R8** | **告警风暴** | 100 条 trace 同时 SLA breach | 100 条 alert 通知用户 | 1. §17.4 去重：同 type + scope 5min 内只发 1 次<br>2. 告警聚合：把 N 条合并成 "5min 内 100 条 SLA breach" |
| **R9** | **告警条件消失但未 resolved** | trace 失败后恢复了，但 alert 还在 firing | 误报 | 1. 定时 worker（5min）扫：若触发条件消失 → mark resolved<br>2. 自动 resolved 阈值：条件消失 30min 后自动标 resolved |
| **R10** | **冷数据归档后无法查** | 30 天后 metrics_buckets 清掉 | 跨 30 天趋势对比失败 | 1. 归档到 S3/OSS（parquet 压缩）<br>2. 保留 PG `archived_*` 视图（冷查询） |
| **R11** | **schema 迁移失败** | ALTER TABLE 在大表上超时 | trace 写入阻塞 | 1. 迁移用 `CREATE INDEX CONCURRENTLY` / `ALTER TABLE ... LOCK=NONE`<br>2. 蓝绿切换：新表名 `_new`，双写，原子切换 |
| **R12** | **DROP TABLE 误操作** | 误执行清理脚本 | 数据全丢 | 1. 永远不 DROP（用 RENAME + DROP 分离）<br>2. 删表前必备份（`pg_dump` 落 OSS）<br>3. CI 拦截 DROP 关键字（生产 schema） |

### 21.3 🟢 P2 风险（长期优化）

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| **R13** | **session 聚合不一致** | trace 已完成但 session 表 N+1 没更新 | 1. 写事务内同步 UPSERT（§14.5）<br>2. 后台 reaper 扫不一致修复 |
| **R14** | **bookmark 多用户泄漏** | user_id 写错导致看到别人的 | 1. 复合主键 (user_id, trace_id)<br>2. 查询强制带 user_id 过滤 |
| **R15** | **DTO 字段废弃后前端仍依赖** | 移除字段导致前端 500 | 1. 永不删字段（标 `deprecated: true`，保留 6 个月）<br>2. 移除前先发 deprecation header |
| **R16** | **健康检查不准确** | /health 返回 200 但实际写不了 | 1. /health 包含 DB ping + 写测试 + 读测试<br>2. 三项任一失败 → 返回 503 |
| **R17** | **错误堆栈泄漏内部信息** | 500 错误返回 stack trace | 1. 生产模式只返回 `INTERNAL_ERROR`<br>2. dev 模式才返回 stack |
| **R18** | **SQL 注入** | 用户输入直接拼 SQL | 1. SQLAlchemy ORM（参数化查询）<br>2. 禁用原生 f-string SQL<br>3. 定期 SQL 审计 |

### 21.4 监控指标（必须实现）

| 指标 | 类型 | 阈值 | 告警 |
|---|---|---|---|
| `trace_write_qps` | Counter | 突然跌 50% | Slack |
| `trace_write_p99_ms` | Histogram | > 100ms | Slack |
| `trace_running_count` | Gauge | > 100 | Critical |
| `trace_json_missing_count` | Gauge | > 0 | Critical |
| `metric_bucket_lock_wait_ms` | Histogram | P99 > 50ms | Warning |
| `gin_index_size_mb` | Gauge | > 10GB | Critical |
| `db_connection_active` | Gauge | > 80% of pool | Warning |
| `alert_emitted_per_min` | Counter | > 100/min | Warning |
| `error_rate_5min` | Gauge | > 5% | Critical |
| `disk_free_gb` | Gauge | < 20GB | Critical |

**采集方式**：

```python
# backend/observability/metrics.py
from prometheus_client import Counter, Histogram, Gauge, start_http_server

trace_write_total = Counter('trace_write_total', 'Total trace writes')
trace_write_duration = Histogram('trace_write_duration_seconds', 'Trace write latency')
trace_running = Gauge('trace_running_count', 'Currently running traces')
trace_json_missing = Gauge('trace_json_missing_total', 'Traces missing trace_json')
db_connections = Gauge('db_connections_active', 'Active DB connections')

# 启动时暴露 /metrics 端点
start_http_server(9090)
```

### 21.5 故障转移 / Runbook

| 故障 | 现象 | 第一步 | 第二步 | 升级 |
|---|---|---|---|---|
| DB 连接失败 | 5xx 增加 | 检查 `pg_isready` | 切换连接池 | PagerDuty |
| trace 卡 running | Dashboard 一直显示 | 跑 reaper：`UPDATE ... SET status='aborted' WHERE status='running' AND start_time < NOW() - 30min` | 检查错误日志 | — |
| GIN 写慢 | 写入 P99 > 200ms | 看慢查询日志 | DROP 一个 GIN | — |
| PG 磁盘满 | 写入失败 | 跑清理：`DELETE FROM metrics_buckets WHERE bucket_ts < NOW() - 30 days` | 归档到 OSS | 扩容 |
| 进程内存爆 | OOM Kill | 缩小 batch_size | 减少 cache_size | 扩容 |

### 21.6 数据迁移（Schema 演进）

**零停机迁移模式**：

```python
# backend/observability/migrations/v1_to_v2.py
"""v1 trace_json → v2 trace_json 迁移（带双写期）"""

# 阶段 1：双写（1-2 周）
# 写 v2 格式的同时保留 v1
async def write_trace_json(trace_id, raw):
    if detect_version(raw) == 1:
        raw_v2 = upgrade_v1_to_v2(raw)
        await repo.update_trace_json_v2(trace_id, raw_v2)
        await repo.update_trace_json_v1(trace_id, raw)  # 旧路径也写
    else:
        await repo.update_trace_json_v2(trace_id, raw)

# 阶段 2：读切（1 周）
async def read_trace_json(trace_id):
    v2 = await repo.read_trace_json_v2(trace_id)
    if v2 is not None:
        return v2
    v1 = await repo.read_trace_json_v1(trace_id)
    return upgrade_v1_to_v2(v1) if v1 else None

# 阶段 3：清理（1 天）
# 确认无 v1 读后：DROP COLUMN trace_json_v1
```

**回滚**：

```python
# 任一阶段都可一键回滚
async def rollback_to_v1():
    # 读切回 v1，写继续双写
    # 1 周观察，无问题后再清理 v2
```

### 21.7 应急开关

**Kill switch**（生产必备）：

```bash
# 紧急：关闭所有写入
export TRACE_PERSIST_BACKEND=memory

# 紧急：关闭 trace_json 写入（仅主表）
export TRACE_JSON_ENABLED=false

# 紧急：只读模式
export READ_ONLY=true
```

**实现**：

```python
# backend/orchestration/trace_storage.py
class TraceStorage(ABC):
    @abstractmethod
    def save_trace_json(self, trace_id, payload): ...

class SqliteStorage(TraceStorage):
    def save_trace_json(self, trace_id, payload):
        if not os.getenv("TRACE_JSON_ENABLED", "true").lower() == "true":
            return  # kill switch
        # ...
```

---

## 22. 最终决策总结

| 维度 | 决策 |
|---|---|
| 存储 | PostgreSQL + JSONB（6 张表，量级大迁 TimescaleDB） |
| Span 模型 | 通用 Span（OpenTelemetry 风格） |
| 事务边界 | 主表同步 + 衍生异步（§14.5） |
| 缓存 | 双层：deque 列表 + LRU 详情（§14.6） |
| 并发 | PG 行级锁 + 固定锁顺序（§14.7） |
| Alert | 实时 + 定时 + 去重（§17） |
| 监控 | Prometheus + 10 个核心指标（§21.4） |
| 故障 | 21 个风险 + 5 个 Runbook（§21.1-21.5） |
| 演进 | 双写 → 读切 → 清理（§21.6） |
| 应急 | 3 个 kill switch env（§21.7） |