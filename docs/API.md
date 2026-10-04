# API — 接口设计

> 54 路由文件、~291 端点（2026-09-29 实测）、鉴权、SSE 协议、数据契约。
> 配套阅读：[PRD.md](PRD.md) / [ARCHITECTURE.md](ARCHITECTURE.md) / [AGENT_DESIGN.md](AGENT_DESIGN.md) / [RAG_DESIGN.md](RAG_DESIGN.md) / [DATABASE.md](DATABASE.md)
>
> ⚠️ **2026-09-29 口径注**：本文端点清单为 2026-08 口径（当时 22 文件 / ~90 端点），现路由文件数已翻倍以上，逐端点清单以 `backend/app/api/routes/` 目录为准；§2 的「单 API Key 鉴权」已被 **JWT + APISIX 网关验签** 替代（保留作历史记录）。

---

## 1. 总览

### 1.1 路由文件（下表为 2026-08 口径 22 个；现 62 个 .py〔含 2 个非路由共享文件〕，清单以目录为准）[backend/app/api/routes/](../backend/app/api/routes/)

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
| `/sql` | POST `/` · POST `/query` · GET `/tables`（角色可见表目录）· GET `/tables/{schema}/{table}`（分页浏览，脱敏） |
| `/budgets` | GET `/budgets/me`（上限/已用/占额即时口径）· `/admin/budgets/summary` · `/subjects` · `/policies` · `/events` · `/audit` · GET `/admin/budgets/reconciliation`（对账日报，响应含 `report.usage` cost_status 分布与 `report.derived` 未决率） |
| `/rag` | 搜索 / 文档 CRUD / 重索引（异步任务化 + `/reindex/status` 轮询）/ 上传 / `upload-failures`（入库失败待处理）/ 关键词 / 知识库 |
| `/memory` | `/sessions` · `/sessions/{id}` · `/sessions/{id}/context` · DELETE · PATCH |
| `/observability` | `/traces` · `/traces/active` · `/traces/{id}` · `/rag-traces` · `/metrics` · `/resources` · `/breakers` · `/alerts` · `/graph` |
| `/llm` | `/models` · `/current` · `/balance` · `/multiquery` · POST `/switch` |
| `/inventory` | `/thresholds` · `/cases` · `/stats` · `/cases/{id}` · POST `/cases/{id}/resolve` · PATCH · `/policies` |
| `/reports` | `/` · `/latest` · `/{id}` |
| `/report` | POST `/`（生成本体） |
| `/schedules` | `/` · `/{workflow}` · PATCH · POST `/{workflow}/run`（手动触发，admin） |
| `/workflows` | `/` · `/runs` · `/runs/{id}` · POST `/{name}/trigger` |
| `/data` | POST `/upload` · `/generate` · `/collect` · `/collect/all` · `/pipeline/run` · `/datasets` · `/pipeline/history` · `/collect/history` |
| `/mcp` | `/tools` · `/servers` · POST `/call` |
| `/demo` | POST `/seed` · `/run/{scenario_id}` |
| `/admin/tools` | GET `/`（契约 lock×运行统计合并）· `/stats`（Prometheus 直读聚合）· `/changes`（契约变更历史）【治理 M1/M2】 |
| `/consistency` | GET `/report`（七段资产对账矩阵，全实时派生）【治理 M6】 |
| `/admin/security` | GET `/events` · `/stats`（安全事件五类查询，M9） |
| `/admin/unanswered` | GET `/questions`（拒答未答问题分页，source/时间窗过滤）· `/stats`（来源分布 + 哈希聚类 Top20 问法）（工作区在途） |
| `/admin/clarify` | GET `/stats`（追问漏斗双口径：live=进程内 rate 口径 / persisted=PG 精确累计 + 转化率派生）（工作区在途） |
| `/admin/tasks` | 既有 CRUD + POST `/{id}/reexecute`（克隆重执行）· GET `/{id}/operations`（操作审计）· GET `/queues`（五队列 backlog）【M10】 |
| `/admin/releases` | 发布记录 + 12 门结果（M8） |
| `/evaluation` | 既有 + GET `/prompt-version-runs?key&version`（版本→评测 run 反查）· POST `/run`（admin）· 运行生命周期：GET `/runs` · GET `/runs/{id}` · POST `/runs/{id}/cancel`（协作式取消，幂等+审计，admin）· GET `/runs/{id}/operations`（run 操作审计，admin）· POST `/cases/from-trace`（admin）；数据集治理台：GET `/datasets` · GET `/datasets/{id}/versions/{version}` · GET `/dataset-candidates` · POST `/dataset-candidates/from-trace`（202）· POST `.../{id}/approve`（201）· POST `.../{id}/reject` · GET `/suites`（读开放、写 admin）；读端点挂 `X-Tenant-Id` 租户钩子（单租户 default，其他值显式 403）；`GET /runs/{id}` 报告响应经展示层 PII 脱敏 |
| `/observability/tokens` | 既有 + GET `/breakdown?group_by=user\|tenant\|model\|skill\|tool\|domain`（六维聚合）· summary 含 by_currency 分列【M11】 |
| `/prompts` | 既有 + POST `/{key}/aliases/{alias}`（production=发布语义 / staging=预发指针）· 版本带 change_kind + 发布门禁 6 端点：POST `/{key}/versions/{version}/release`（202 异步评测）· GET `/{key}/releases` · GET `/{key}/releases/{release_id}` · POST `.../approve` · POST `.../publish` · GET `.../comparison`（候选 vs production 审批对比，baseline 不可用时显式口径）（流程权威见根 AGENTS.md「Prompt 完整发布流程」；失败/超时/未审批不切换 production） |
| 系统 | `/health`（含 build/migrations/schema_consistency/redis 探测）· `/metrics`（绕过 auth + CORS，供 K8s scrape） |
| `/admin/email` | GET `/channel`（邮件通道健康只读：引擎 smtp/agently、凭据仅回布尔、beat 周期任务候选） |
| `/approvals` | GET `/`（写操作审批单列表，支持 `status`/`limit`/`tool` 筛选）+ 审批放行/拒绝（副作用 Tool 审批门） |

---

## 2. 鉴权（✅ 2026-09 已切换为 JWT + 网关验签，下文为历史方案记录）

> **当前实现**：自建 JWT（issuer=agent-platform，`backend/security/local_jwt.py`，migration 008），登录链路 前端 `/login` → APISIX `/api/auth/**` → auth-service；APISIX `gateway-auth` 插件验签（**Bearer 优先于 X-API-Key**）并注入 X-User-Id 身份头（契约见 [contracts/identity-header-protocol.md](contracts/identity-header-protocol.md)）。RBAC 见 `backend/app/api/routes/rbac.py`。以下历史方案仅作对照：

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

SSE v2 支持 **13 种事件，分三层**（契约权威：[backend/orchestration/graph/event_schema.py](../backend/orchestration/graph/event_schema.py)，回归门 `tests/test_sse_event_schema.py`；data 契约只锁必填字段，未知字段放行）：

**CORE（帧序约束参与者）**：

| event | 含义 | data 字段 | 前端处理 |
|---|---|---|---|
| `meta` | 握手（首帧且唯一） | `{request_id, node_labels}`，附 `stream_id`（=request_id）、`resume_supported: true` | 写入 `store.nodeLabels`，记录 stream_id 供 resume |
| `status` | 宏观阶段切换 | `{node: "planner", ts}` | `store.currentStatus` |
| `log` | 详细时间线 | 必填 `{node, ts}`，常见 `level / step_id / message / payload` | 环形追加（200 上限） |
| `delta` | 流式内容块 | `{content: "...", ts}` | `store.deltaText += content` |
| `done` | 结束信号（终帧） | `{elapsed（秒，1 位小数）, sources: [...]}`；可选 `usage / trace_id / pending_action / context_usage` 与 RAG 语义 `answer_status / confidence`；sources 元素可选定位字段：`department`（部门代码）、`pages`（PDF 页码升序数组）、`section`（章节标题）、`doc_id`（原文预览钥匙）——来源卡据此展示原文定位并支持点开快照预览；`answer_status` 取值 rag_no_evidence / rag_permission_denied / rag_hallucination（缺省=正常回答），`confidence`（0~1，<0.6 前端提示「建议核实」） | `replaceLastAssistant` + `persistSession` |
| `error` | 错误/中止（终帧） | `{message: "...", ts: ...}` | 立即替换最后一条 assistant |

**AUX（中段辅助帧，任意位置任意次）**：

| event | 含义 | data 字段 |
|---|---|---|
| `todo` | 任务清单 | `{items, ts}` |
| `usage` | 本轮 token 用量 | `{ts, ...}` |
| `file` | 产物文件 | `{node, files, ts}` |
| `clarification` | 追问澄清（发出即双写追问漏斗 shown：Prometheus + PG） | `{question, options, ts}` |
| `context` | 上下文事件（L2 裁剪 / L4 压缩，字段随事件类型变化） | dict |
| `thinking` | 思考过程 | `{content, ts}` |

**TRANSPORT**：`ping`（`{ts}`，空闲心跳保活，不计入帧序，前端忽略）。

帧序约束：① `meta` 必为首帧且唯一；② `done` / `error` 互斥、只能有一个终帧且必为最后一帧；③ 中段帧（status/log/delta + AUX）次序与次数不约束。

### 3.2 编码格式

带 `seq` 的事件（F2 Resume Protocol，2026-09-25 起）附加 `id: <seq>` 行（置于 `event:` 行之后），且 data JSON 注入 `seq` 字段；前端按 `(stream_id, seq)` 去重：

```
event: <type>
id: <seq>
data: {"seq": <seq>, ...}

```

示例：

```
event: meta
data: {"request_id": "req-abc", "node_labels": {"planner": "📋 任务规划", "sql_worker": "📊 数据查询"}, "stream_id": "req-abc", "resume_supported": true}

event: status
data: {"node": "planner", "ts": 1691654400.123}

event: delta
data: {"content": "根据您的查询，"}

event: done
data: {"elapsed": 4.5, "sources": [{"doc_id": "1", "title": "..."}]}
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
    session_id: str = "default"             # 同会话支持 SQL 追问
    reset_context: bool = False              # 清除上一轮 SQL 摘要
    current_user_id: Optional[int] = None    # 已废弃，身份来自可信链
```

`POST /sql/query` 返回结构化结果，并在 `memory` 中返回是否识别为追问、
是否复用了上一轮 SQL 摘要。`POST /sql/query/stream` 使用既有 SSE
`meta/status/log/done/error` 事件，`log.payload.phase` 包含
`understanding`、`table_routing`、`sql_generation`、`sql_validation`、
`tool_start`、`tool_result`，用于管理端和用户端展示真实执行过程。

SQL 数据目录和查询均经过 `sql.read` 预检及 `SQLPolicyGuard`：`viewer`
无 SQL 查询权限，`editor` 只能访问其数据范围内的共享表，`admin`/
`super_admin` 才能访问全量授权域；目录不会返回当前角色不可见的表。

表浏览通道（管理端数据查询页，2026-10-01）：`GET /sql/tables` 返回当前
角色可见的白名单表与列元数据（敏感列不出口，纯元数据零 DB 查询）；
`GET /sql/tables/{schema_name}/{table_name}` 分页只读浏览
（`page`/`page_size`/`sort`/`order`，排序列白名单，敏感列脱敏）。

### 4.3 RAG

```python
class RAGAskRequest(BaseModel):
    question: str
    session_id: str = "default"
    kb_id: Optional[str] = None
```

`POST /rag/ask` 响应携带 `answer_meta` 观测字段；其中
`vector_degraded=true`（2026-10-02）表示本轮全部 query embedding 检索
失败、召回仅剩 BM25——分数尺度不可比，下游（客服置信度/评测/管理端）
据此解读残缺分数，读取失败时静默跳过该字段（不假装正常）。

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
| POST | `/chat/stream/resume` | SSE 断线重连续播（F2 Resume Protocol）：body `{request_id, after_seq}`，按 seq 重放原流缓冲中 `after_seq` 之后的事件；响应头 `X-Request-Id`。不可恢复（进程重启/记录过期/缓冲 gap）→ 404 `STREAM_NOT_RESUMABLE`；跨用户 → 403；非法游标 → 422 |
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

### 5.2 /rag 系列（节选，全量以目录为准）

**知识库管理**：

```
GET   /rag/stats                          总览统计
GET   /rag/documents?keyword=&type=...      搜索 + 分页 + 过滤
GET   /rag/documents/{id}                  详情
GET   /rag/documents/{id}/file             原文快照预览（原文定位 P1）：
                                           KB ABAC + permission_scope 双重
                                           鉴权，无权/不存在同形 404；local
                                           直读 DOCS_DIRECTORY，remote 经内
                                           部令牌转发 rag-service；二进制
                                           inline 返回（FileResponse 无
                                           filename → 不落 attachment）
DELETE /rag/documents/{id}                 软删除 + 向量清理
GET   /rag/documents/{id}/chunks           Chunk 预览
POST  /rag/documents/{id}/reindex          重索引——幂等提交进 rag_index
                                           队列（remote 由 rag-index-worker
                                           执行；local broker 不可达时降级
                                           本机同步，remote 明确报错）
GET   /rag/documents/{id}/reindex/status   重索引任务轮询（task 空=从未提交；
                                           PENDING/RUNNING 进行中；终态+
                                           Redis progress 镜像）
GET   /rag/upload-failures                 入库失败待处理（failed 且同
                                           file_path 无更新 published，重传
                                           成功自动消数；require_rag_editor）
POST  /rag/knowledge/{doc_id}/lifecycle    知识生命周期动作：
                                           状态 CAS 迁移（deprecated/active
                                           等，require_rag_editor）；deprecated
                                           与过期 active 均退出混合检索候选
POST  /rag/knowledge/{doc_id}/expire-at    设置到期时间，过期后不可检索
GET   /rag/knowledge/{doc_id}/lifecycle/events  生命周期事件审计
GET   /rag/operations                      操作日志
POST  /rag/upload                          上传 + SSE 进度
GET   /rag/upload/{id}/stream              SSE 进度流（帧结构见下）
GET   /rag/knowledge-bases                 知识库列表
POST  /rag/search                          语义检索
POST  /rag/ask                             RAG 问答（answer_meta 见 §4.3）
```

**上传 SSE 进度流**（`POST /rag/upload` 同步返回 upload_id 后，`GET /rag/upload/{id}/stream` 消费；Celery 模式下前端轮询同键 Redis 快照）：事件名恒为 `message`，终态以 `data.stage` 判定（`uploading → parsing/indexing → done/error`），进度帧带 `progress/bytes`；`done` 帧携带 `was_overwrite`（本次上传是否覆盖同名正式文件，仅响应语义；os.replace 前一刻 isfile 判定 + result 双源兜底）及耗时等汇总字段。

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

### 7.1 鉴权缺失（✅ 已于 2026-09 解决）

原四项缺口（单 API Key 全开放 / 前端不发 key / 无 RBAC / user_id 自报可越权）已由 JWT + 网关验签 + RBAC + 身份头注入全部关闭，见 §2 口径注。

### 7.2 前端两套 API 客户端（✅ 已于 2026-09 统一）

三端现为统一形态：`src/api/*` 域模块 + `src/app/api/[...path]/route.ts` BFF 代理（服务端注入凭据）+ react-query。

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
| `backend/app/api/routes/*.py` | 路由文件（数量见 §1.1，以目录为准） |
| `backend/app/api/schemas.py` | Pydantic 数据契约 |
| `backend/app/api/deps.py` | 依赖注入（get_multi_agent / get_rag_pipeline / get_sql_agent） |
| `backend/app/api/middleware/auth.py` | API Key 中间件 |
| `backend/app/server.py` | FastAPI 入口 |
| `backend/app/api/router.py` | 路由注册 |
| `backend/app/api/routes/_rag_shared.py` | RAG 共享依赖 |

---

## 旅游规划问答与天数选择（v3 P0-A）

`POST /api/travel/plan` 与 `/api/travel/plan/stream` 共用域图输出契约；流式 `done.data.result` 返回相同结构。静态问答、动态问答、目的地探索及尚不支持的逐条改单返回 `status=answered`、`itinerary=null`、`validation=null`，不写规划版本或规划 pending。

明确规划且仅缺天数时，`clarification_options` 返回两项：`{label:"按 3 天参考规划", days:3, message:"规划<目的地>3天行程"}` 与 `{label:"自己填天数", days:null, message:""}`。`days` 在点击前仍为空；前端发送第一项完整消息后，后端重新抽取并校验。第二项只聚焦输入，不发送请求。`requirement.interpreted` 同步携带 `intent` 与 `clarification_options`，两者均声明在旅游状态 schema。

最后验证：2026-10-02 · 见 [P0-A 收尾验收](reports/2026-10-02-旅游灵感式规划v3-P0-A收尾验收.md)。

## 历史规划与版本链（2026-10-02）

`GET /api/travel/plans?limit=`（1–100，默认 30）——当前用户的历史规划列表（每会话最新版）。
`GET /api/travel/plans/{conversation_id}/latest`——会话最新版行程（含完整 itinerary，供恢复历史规划）；不存在/越权一律 404，不泄露存在性。
`GET /api/travel/plans/{conversation_id}/versions`——行程版本历史（新→旧）。
`POST /api/travel/plans/confirm`——确认整份行程（waiting_confirmation → confirmed）。
`POST /api/travel/plans/restore`——以旧版内容生成新版本恢复。
`GET /api/travel/plans/{conversation_id}/diff`——两版行程确定性差异。

版本语义：`plan_version` 在修复重排时 +1，携带 `parent_plan_version` / `brief_version` / `data_snapshot_version`；并发/版本冲突返回 409（带 current_version），参数错误 422（`_version_http_error` 统一收口）。版本历史存 agent_memory 库运行时幂等建表，`TRAVEL_PLAN_VERSIONS_ENABLED` 默认开、保留 20 版（保留期内可恢复，不做永久历史承诺）。规划输出契约含 `intercity: list[IntercityTrain]`——城际班次摘要（12306 实时检索，前 6 车次）；空 = 未触发车票查询，非规划硬依赖，旧 checkpoint 兼容。

## 方案档位与预算协商 + 城市指南（2026-10-03 M3）

`POST /api/travel/plan`（及 stream）契约扩展：`brief.tier` 方案档位（`economy` / `comfortable`，缺省 `economy`，从用户消息抽取）；预算超出当前档位上限时 budget 专家自动降档并把协商过程写入 `rationale.budget_negotiation`（原始档位、压缩顺序、经济档仍超时的缺口 `floor_total_cny` / `gap_cny`）；前端以档位切换器 + 预算协商卡消费。档位画像（餐交乘数与下限系数）为代码配置 `backend/config/travel.py::TIER_PROFILES` / `BUDGET_POLICY`，非数据库表。

`GET /api/travel/city-guide?destination=&force=` —— 城市指南轻端点（不进域图）：四级内容链（文档摘要 → RAG travel 库 → 知乎攻略 → 暂无），7 天缓存（`force=true` 绕过）；供前端 CityGuideDrawer 顶栏与条件 chips 双入口消费。

同批引入旅游域通用 Tool 缓存层：`TRAVEL_TOOL_CACHE_ENABLED`（默认开）/ `TRAVEL_TOOL_CACHE_TTL`（默认 86400s），对域内 live 检索 Tool 生效。

## 用户决策留痕（2026-10-04 M4）

`POST /api/travel/decisions`——用户决策留痕（草案应用 / 放弃 / 画布替换 / 档位切换 / 删减协商五类），决策提交幂等（唯一约束防重）；`GET /api/travel/decisions?conversation_id=`——某会话决策链（时间新→旧）。持久化 `ai.travel_decision_audit`（迁移 072）+ 幂等约束（073），管理端/排查按 `decision_type` + `source` 归因（决策留痕权威口径见根 AGENTS.md 旅游段）。

## 验证

最后验证：2026-10-05 · SSE §3 按 event_schema.py 13 事件三层契约重写（CORE/AUX/TRANSPORT、done 帧 `elapsed` 秒、log 帧字段、meta 帧四字段、seq/id 帧行示例顺序修正）、发布门 6 端点（补 comparison）、评测运行生命周期与数据集治理台端点族、`/evaluation` X-Tenant-Id 租户钩子 403 与 PII 脱敏、旅游 `/decisions` 决策留痕、上传 SSE 帧契约（was_overwrite）、RAG 知识生命周期 3 端点转已合并（3fd3c0b）。端点明细以 `backend/app/api/routes/` 目录为准；`/chat/stream` SSE 契约含 F2 Resume Protocol（seq/id 帧行、`/chat/stream/resume` 端点）。
