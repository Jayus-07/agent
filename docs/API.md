# API — 接口设计

> 22 路由文件、~90 端点、鉴权现状、SSE 协议、数据契约。
> 配套阅读：[PRD.md](PRD.md) / [ARCHITECTURE.md](ARCHITECTURE.md) / [AGENT_DESIGN.md](AGENT_DESIGN.md) / [RAG_DESIGN.md](RAG_DESIGN.md) / [DATABASE.md](DATABASE.md)

---

## 1. 总览

### 1.1 22 路由文件 [backend/app/api/routes/](../backend/app/api/routes/)

| 路由文件 | 端点数 | 用途 |
|---|---|---|
| `chat.py` | 4 | 主对话入口（同步 / SSE / abort / messages） |
| `sql.py` | 1 | NL2SQL |
| `rag.py` | 13 | 知识库 + 文档管理 |
| `rag_documents.py` | — | 文档 CRUD |
| `rag_search.py` | — | 语义检索 |
| `rag_upload.py` | 2 | 上传 + SSE 进度 |
| `rag/keywords` | 7 | 关键词规则管理 |
| `memory.py` | 5 | 会话 / 上下文 |
| `observability.py` | 11 | Trace / 指标 / 拓扑 |
| `llm.py` | 5 | 模型切换 / 余额 / multi-query |
| `inventory_alerts.py` | 9 | 阈值 / 告警 / 策略 |
| `report.py` | 1 | 报告生成（同步） |
| `reports.py` | 3 | 报告列表 / 详情 / 最新 |
| `schedules.py` | 3 | 定时任务 |
| `workflows.py` | 4 | workflow CRUD + 触发 |
| `data.py` | 9 | 数据采集 + 通用清洗 |
| `mcp.py` | 3 | MCP 集成 |
| `demo.py` | 2 | 演示场景 |
| `health.py` | 1 | 健康检查 |
| `keyword_routes.py` | — | 关键词规则 |
| `_rag_shared.py` | — | RAG 共享依赖 |
| `__init__.py` | — | 路由注册 |

### 1.2 端点速查表

| 前缀 | 关键端点 |
|---|---|
| `/chat` | POST `/` · `/stream` (SSE) · `/messages` · `/abort` |
| `/sql` | POST `/` |
| `/rag` | 13 个：搜索 / 文档 CRUD / 重索引 / 上传 / 关键词 / 知识库 |
| `/memory` | `/sessions` · `/sessions/{id}` · `/sessions/{id}/context` · DELETE · PATCH |
| `/observability` | `/traces` · `/traces/active` · `/traces/{id}` · `/rag-traces` · `/metrics` · `/resources` · `/breakers` · `/alerts` · `/graph` |
| `/llm` | `/models` · `/current` · `/balance` · `/multiquery` · POST `/switch` |
| `/inventory` | `/thresholds` · `/cases` · `/stats` · `/cases/{id}` · POST `/cases/{id}/resolve` · PATCH · `/policies` |
| `/reports` | `/` · `/latest` · `/{id}` |
| `/report` | POST `/`（生成本体） |
| `/schedules` | `/` · `/{workflow}` · PATCH |
| `/workflows` | `/` · `/runs` · `/runs/{id}` · POST `/{name}/trigger` |
| `/data` | POST `/upload` · `/generate` · `/collect` · `/collect/all` · `/pipeline/run` · `/datasets` · `/pipeline/history` · `/collect/history` |
| `/mcp` | `/tools` · `/servers` · POST `/call` |
| `/demo` | POST `/seed` · `/run/{scenario_id}` |
| 系统 | `/health` · `/metrics`（绕过 auth + CORS，供 K8s scrape） |

---

## 2. 鉴权现状

### 2.1 当前实现（[backend/app/api/middleware/auth.py](../backend/app/api/middleware/auth.py)）

```python
# 单一全局 API Key，无用户身份
if not API_KEY:           # 未配置 → 开发模式，全部放行
    return await call_next(request)
client_key = request.headers.get("X-API-Key", "")
if client_key != API_KEY:
    return JSONResponse(status_code=401, ...)
```

### 2.2 关键问题

| 问题 | 影响 |
|---|---|
| 单 key 共用 | 无法区分"谁" |
| 默认 `API_KEY = os.getenv("API_KEY", "")` → **默认空 = 全开放** | 生产环境一旦忘记配，全部端点公开 |
| 前端**从不发送 `X-API-Key`** | 一旦生产开启 API_KEY，前端全线 401 |
| 无 RBAC / 角色 / 权限 | 3 类用户角色无法落地 |
| `user_id` 是客户端自报字符串 | SQL 行级安全可被绕过 |

### 2.3 P0 修复路径

详情见 [ROADMAP.md §1 P0](ROADMAP.md)：

- FastAPI Depends + JWT 中间件 → `current_user` 注入
- `user_id = JWT.sub` 作为可信源
- 接上 JWT 后 `row_security.py` 立刻生效

---

## 3. SSE 协议（POST /chat/stream v2）

### 3.1 事件类型

SSE v2 支持 6 种事件：

| event | 含义 | data 字段 | 前端处理 |
|---|---|---|---|
| `meta` | 握手（node_labels 映射表） | `{node_labels: {...}}` | 写入 `store.nodeLabels` |
| `status` | 宏观阶段切换 | `{node: "planner", ts: ...}` | `store.currentStatus` |
| `log` | 详细时间线 | `{step_id, payload_in, payload_out, ...}` | 环形追加（200 上限） |
| `delta` | 流式内容块 | `{content: "..."}` | `store.deltaText += content` |
| `done` | 结束信号 | `{elapsed_ms, sources: [...]}` | `replaceLastAssistant` + `persistSession` |
| `error` | 错误/中止 | `{message: "...", ts: ...}` | 立即替换最后一条 assistant |

### 3.2 编码格式

```
event: <type>
data: <json>

```

示例：

```
event: meta
data: {"node_labels": {"planner": "📋 任务规划", "sql_worker": "📊 数据查询"}}

event: status
data: {"node": "planner", "ts": 1691654400.123}

event: delta
data: {"content": "根据您的查询，"}

event: done
data: {"elapsed_ms": 4521, "sources": [{"doc_id": "1", "title": "..."}]}
```

### 3.3 关键设计

- **Backpressure**（P0-1）：队列满 → 记 metric + 触发 `stop_event` + 入队 sentinel 干净收尾
- **阻塞拉取**（P1-14）：`q.get(timeout=0.5)` 而非 100Hz 轮询
- **真实指标**（P0-2）：ok / error / aborted 计数
- **中止双通道**：前端 `abort()` + `POST /chat/abort` 触发 `stop_event.set()`

### 3.4 HTTP 头

```http
Content-Type: text/event-stream
Cache-Control: no-cache
Connection: keep-alive
X-Accel-Buffering: no   # 禁用 nginx 缓冲
```

---

## 4. 数据契约

### 4.1 对话

[backend/app/api/schemas.py](../backend/app/api/schemas.py)

```python
class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    session_id: str = "default"
    kb_id: Optional[str] = None
    request_id: Optional[str] = "default"

class ChatResponse(BaseModel):
    answer: str
    session_id: str
    sources: list = Field(default_factory=list)

class AbortRequest(BaseModel):
    session_id: str = "default"
    request_id: str = "default"
```

### 4.2 数据查询

```python
class SQLAskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    current_user_id: Optional[int] = None   # Row Security 依据
```

### 4.3 RAG

```python
class RAGAskRequest(BaseModel):
    question: str
    session_id: str = "default"
    kb_id: Optional[str] = None
```

### 4.4 报告

```python
class ReportRequest(BaseModel):
    report_type: str = Field(...)             # daily_sales / product_performance / ...
    filters: dict = Field(default_factory=dict)
    user_id: str = "default"
    polish: bool = True                       # 是否 LLM 润色
```

### 4.5 SSE 事件

```python
class SSEEvent(BaseModel):
    stage: str = Field(...)                   # planning/supervising/executing/reporting/done/error
    label: str = ""
    message: str = ""
    node: str = ""
    data: dict = Field(default_factory=dict)
```

### 4.6 错误

```python
class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None
```

### 4.7 数据交换协议（业务层）

| 协议 | 定义 | 用途 |
|---|---|---|
| `SQLResult`（Pydantic） | sql / tables / columns / rows / row_count / execution_time | Skill 层 |
| `BusinessInsight` | summary / risks / suggestions / confidence / related_knowledge | 业务分析 |
| `StepResult` | step_id / capability / status / output / error / ... | 多 Agent 步骤 |
| `FaithfulnessResult` | score / cleaned_answer / supported_claims / unsupported_claims | 忠实度 |

---

## 5. 主要端点详解

### 5.1 /chat

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/chat` | 同步对话，返回 Markdown 答案 |
| POST | `/chat/stream` | SSE 流式（v2 协议） |
| POST | `/chat/abort` | 中止生成（设 `stop_event`） |
| POST | `/chat/messages` | 持久化会话消息到 PG |

**`POST /chat/stream` 详解**（[backend/app/api/routes/chat.py](../backend/app/api/routes/chat.py)）：

```python
@router.post("/stream")
async def chat_stream(r: Request, _rate=Depends(require_rate_limit)):
    raw = await r.json()  # 手动解析，绕过 fastapi 中文 bug
    req = ChatRequest(**raw)

    queue = queue.Queue(maxsize=1024)
    stop_event = threading.Event()

    # 启动 producer（线程池）
    producer = lambda: agent.stream_events(question, session_id, kb_id, stop_event)
    future = loop.run_in_executor(_executor, producer)

    # 异步生成器
    async def event_generator():
        yield meta_event
        while True:
            evt = await loop.run_in_executor(None, q.get, True, 0.5)
            if evt is None: break
            yield _sse_encode(evt)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

### 5.2 /rag 系列（13 端点）

**知识库管理**：

```
GET   /rag/stats                          总览统计
GET   /rag/documents?keyword=&type=...      搜索 + 分页 + 过滤
GET   /rag/documents/{id}                  详情
DELETE /rag/documents/{id}                 软删除 + 向量清理
GET   /rag/documents/{id}/chunks           Chunk 预览
POST  /rag/documents/{id}/reindex          重索引
GET   /rag/operations                      操作日志
POST  /rag/upload                          上传 + SSE 进度
GET   /rag/upload/{id}/stream              SSE 进度流
GET   /rag/knowledge-bases                 知识库列表
POST  /rag/search                          语义检索
POST  /rag/ask                             RAG 问答
```

**关键词规则**（`keyword_routes.py`）：

```
GET    /rag/keywords                       列表
GET    /rag/keywords/doc-types             文档类型
GET    /rag/keywords/categories           分类
POST   /rag/keywords                       新增
POST   /rag/keywords/batch                 批量
DELETE /rag/keywords/{kw}                  删除
PUT    /rag/keywords/{kw}/toggle           启用/禁用
```

### 5.3 /memory

```
GET    /memory/sessions?user_id=&limit=&before=    会话列表（游标分页）
GET    /memory/sessions/{id}                       会话消息列表
GET    /memory/sessions/{id}/context               Agent 工作上下文
DELETE /memory/sessions/{id}                       删除级联消息
PATCH  /memory/sessions/{id}                       重命名
```

### 5.4 /observability（11 端点）

```
GET /observability/traces?limit=N        最近 N 条（SQLite，轻量）
GET /observability/traces/active         活跃（contextvar / thread-local）
GET /observability/traces/{id}           完整详情（内存 → SQLite 兜底）
GET /observability/rag-traces            RAG 专用（向后兼容）
GET /observability/rag-traces/stream     SSE 实时流
GET /observability/rag-traces/{id}
GET /observability/metrics               指标
GET /observability/resources             CPU/内存
GET /observability/breakers              熔断器状态
GET /observability/alerts                告警
GET /observability/graph                 拓扑图
```

**DTO 适配**（`_to_span_dto` / `_to_trace_dto` / `_stored_dict_to_dto`）：

- `span_id → id`
- `model` 字符串 → `{name, provider}` 对象
- 派生 `duration_ratio` / `children` / `llm_call`
- 前端零解析成本

### 5.5 /workflows + /schedules

```
GET   /api/workflows                              所有注册 workflow + metadata
POST  /api/workflows/{name}/trigger               手动触发
GET   /api/workflows/runs?workflow_name=&page=    运行历史
GET   /api/workflows/runs/{run_id}                单次 run 详情

GET   /api/schedules                              定时任务
GET   /api/schedules/{workflow_name}              单个 schedule
PATCH /api/schedules/{workflow_name}              修改 hour / minute
```

### 5.6 /reports + /inventory

```
POST  /api/report {report_type, filters, user_id, polish}
GET   /api/reports?type=&page=                    报告列表
GET   /api/reports/latest?type=                   最新一条
GET   /api/reports/{report_id}                    详情

GET   /api/inventory/thresholds                   阈值规则
POST  /api/inventory/thresholds
DELETE /api/inventory/thresholds/{id}
GET   /api/inventory/cases?status=&level=&page=   告警 case
GET   /api/inventory/cases/{id}
POST  /api/inventory/cases/{id}/resolve           人工 resolve
PATCH /api/inventory/cases/{id}                   更新状态
GET   /api/inventory/policies                     通知策略
GET   /api/inventory/stats                        按级别统计
```

### 5.7 /data（数据采集 + 通用清洗）

```
POST /api/data/upload                             上传 CSV/JSON
GET  /api/data/datasets                           开源 + 本地数据集
POST /api/data/generate?types=&count=             模拟数据生成
POST /api/data/pipeline/run                       通用清洗（detect/clean/dedup/convert）
GET  /api/data/pipeline/history                   最近 20 job
GET  /api/assets                                  stg_* 数据资产
POST /api/data/collect                            单数据集采集
POST /api/data/collect/all                        批量采集
GET  /api/data/collect/history                    采集历史
```

### 5.8 /llm

```
GET  /api/llm/models                          模型列表
GET  /api/llm/current                         当前模型
POST /api/llm/switch                         切换模型
GET  /api/llm/balance                        余额
POST /api/llm/multiquery                     切换 multi-query 模式
```

---

## 6. 错误处理

### 6.1 HTTP 错误

```python
class ApiError(Exception):
    def __init__(self, message, status, detail=None):
        ...
```

后端 FastAPI 习惯：`detail` 字段含错误信息。

### 6.2 错误响应格式

```json
{
  "error": "validation_error",
  "detail": "ChatRequest 解析失败: ..."
}
```

### 6.3 业务错误码

| 业务 | 错误 | 状态码 |
|---|---|---|
| RAG 拒答 | `no_evidence` / `low_relevance` / `insufficient` / `out_of_scope` | 200（Markdown） |
| SQL 失败 | `SQLStatus` 8 种 | 200（Markdown） |
| Service Unready | `SERVICE_NOT_READY` | 503 |
| Schema 校验失败 | `validation_error` | 422 |
| 鉴权失败 | `unauthorized` | 401 |
| 资源不存在 | `not_found` | 404 |

### 6.4 SSE 错误事件

```json
{
  "event": "error",
  "data": {
    "message": "用户中止",
    "ts": 1691654400.123
  }
}
```

### 6.5 后端 8 种 SQLStatus

```python
class SQLStatus(str, Enum):
    SUCCESS = "success"
    NO_DATA = "no_data"
    FAILED = "failed"
    TIMEOUT = "timeout"
    SYNTAX_ERROR = "syntax_error"
    PERMISSION_DENIED = "permission_denied"
    VALIDATION_ERROR = "validation_error"
    NO_TABLE = "no_table"
```

Supervisor 根据错误类型决定降级（详见 [AGENT_DESIGN.md §6](AGENT_DESIGN.md)）。

---

## 7. 已知问题

### 7.1 鉴权缺失（[ROADMAP.md §1 P0](ROADMAP.md)）

- 单 API Key 默认空 = 全开放
- 前端不发 X-API-Key
- 无 RBAC / 角色 / 权限
- `user_id` 客户端自报，可越权

### 7.2 前端两套 API 客户端

```
lib/fetcher.ts + lib/api/*    ← 走 NEXT_PUBLIC_API_URL，无 /api 前缀
services/*                    ← 走 /api/xxx，依赖 next.config.js rewrite
```

**路径不一致**是真实风险：鉴权 / 错误处理 / 超时行为完全不同。

### 7.3 前端错误语义不一致

- observability 静默吞错返回 `[]`
- alerts 严格区分"无告警"与"接口失败"

同项目内两种相反的错误哲学。

### 7.4 路由文件命名历史负担

- `rag.py` / `rag_documents.py` / `rag_search.py` / `rag_upload.py` —— 同一域拆 4 个文件
- `report.py`（POST 生成本体） vs `reports.py`（列表 / 详情）—— 同时存在

### 7.5 路由文件 `_rag_shared.py`

- 共享依赖（auth / db / 业务工具）
- 不是路由文件但放在 `routes/` 目录

---

## 8. 关键文件索引

| 文件 | 职责 |
|---|---|
| `backend/app/api/routes/*.py` | 22 个路由文件 |
| `backend/app/api/schemas.py` | Pydantic 数据契约 |
| `backend/app/api/deps.py` | 依赖注入（get_multi_agent / get_rag_pipeline / get_sql_agent） |
| `backend/app/api/middleware/auth.py` | API Key 中间件 |
| `backend/app/server.py` | FastAPI 入口 |
| `backend/app/api/router.py` | 路由注册 |
| `backend/app/api/routes/_rag_shared.py` | RAG 共享依赖 |

---

## 验证

最后验证：2026-08-10 · 与代码一致（22 路由 / ~90 端点 / SSE v2 6 事件 / 8 种 SQLStatus）。
