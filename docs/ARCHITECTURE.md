# ARCHITECTURE — 顶层架构

> 项目的"5 分钟看完"视图。配套阅读：[PRD.md](PRD.md) / [DESIGN.md](DESIGN.md) / [RAG_DESIGN.md](RAG_DESIGN.md) / [AGENT_DESIGN.md](AGENT_DESIGN.md) / [DATABASE.md](DATABASE.md) / [API.md](API.md) / [ROADMAP.md](ROADMAP.md)
>
> ⚠️ **2026-09-29 口径注**：§1 概览与数据流已按 **STOP A-H 冻结基线**刷新。分层权威视图 = [architecture/Architecture-Baseline.md](architecture/Architecture-Baseline.md)（Runtime 九层 + 请求生命周期，契约红线见 [Frozen-Contracts.md](architecture/Frozen-Contracts.md)）；部署权威 = [architecture/system-overview.md](architecture/system-overview.md)。与 8 月版的关键差异：主链路为**四分流**（域预过滤 + direct/workflow/plan/寒暄直答），plan 链只是支线之一；存储统一 PG + pgvector（SQLite / ChromaDB 均已下线）；鉴权落地（JWT + 网关验签）；主图 9 核心节点 + 5 域图。

---

## 1. 一页纸概览

```
  用户（三端 Next.js :3100/:3200/:3300）
        │ HTTP / SSE
        ▼
  APISIX 网关 :9080 ── JWT 验签 · 注入身份头（唯一入口）
        ▼
  ┌──────────────── Agent Platform（FastAPI :8000 + LangGraph）────────────────┐
  │ Router Runtime ── 域预过滤 + RoutingEngine（域→能力→执行方式）拍板 route_mode │
  │   ├─ 域命中 ──► Domain Runtime（3 顶级域 / 5 物理域图，自带专家与 reporter）│
  │   ├─ direct/workflow ──► Capability Runtime（17 capability · 12 Skill）    │
  │   ├─ plan ──► Plan Runtime（任务拆解 → 并行调度）──► Capability Runtime     │
  │   └─ 寒暄/咨询 ──► general_chat 直答                                       │
  │ Expert Runtime ── 域内专家公共生命周期（超时/异常/遥测钩子）                 │
  │ Tool Contract Boundary ── 两型输出契约（text/structured）+ 边界归一          │
  │ Tool Runtime ── 执行治理（超时/重试/熔断/隔离舱）   Integration Adapter(MCP)│
  │ Shared Governance ── 认证 · 记忆 · 上下文预算 · 模型治理 · 幂等 · 可观测     │
  └────────────────────────────────┬───────────────────────────────────────────┘
                                   ▼
        PostgreSQL + pgvector（agent_business / agent_memory，唯一存储）
        Redis + Celery 双队列 · rag-service :8090 · mcp-service :8091
```

**核心链路（用户问问题）**：

```
1. 用户 input → POST /chat/stream（网关验签 → Input Guard → 记忆装配 → 指代解析）
2. Router Runtime：域预过滤优先，RoutingEngine 统一编排域判断、能力候选和执行方式；规则/向量/LLM 只提供证据，故障走受控降级
3. 按路由分流：direct 直连 / workflow 预定义编排 / plan 拆解并行 / 域图接管 / 寒暄直答
4. 能力执行：Skill → Tool Contract Boundary 归一 → Tool Runtime 治理执行
5. Reporter / 域图 reporter 汇总 → SSE 流式输出 → 前端增量渲染
6. 写入 Trace（每请求一棵 Span 树）+ memory.end_turn
```

---

## 2. 5 大子系统地图

### 2.1 Multi-Agent 编排（核心）

| 项 | 详情 |
|---|---|
| 定位 | 复杂任务自动拆解 / 调度 / 执行的"大脑" |
| 关键文件 | [backend/orchestration/](../backend/orchestration/)（graph / supervisor / state / workflow） |
| 关键 API | `POST /chat` / `POST /chat/stream` / `POST /chat/abort` |
| 当前边界 | 主图 9 核心节点 + 17 Capability（3 个 `routed:false`）+ 10 轮 Supervisor 上限 + recursion_limit 80 + 3 条降级链；另有 5 个垂直域图（代码默认关） |
| 详细设计 | [AGENT_DESIGN.md](AGENT_DESIGN.md) |

### 2.2 RAG（企业知识库）

| 项 | 详情 |
|---|---|
| 定位 | 把企业文档变成"可对话的知识库" |
| 关键文件 | [backend/rag/](../backend/rag/)（chain / retrieval / indexing / vectorstore / guardrails） |
| 关键 API | 11 个 `/rag/*` 端点（上传 / 搜索 / 重索引 / 删 / 操作日志 / 知识库） |
| 当前边界 | 6 段流水线 + Hybrid RRF + CrossEncoder Rerank + Evidence Gate 三层拒答 + Faithfulness NLI |
| 详细设计 | [RAG_DESIGN.md](RAG_DESIGN.md) |

### 2.3 SQL Agent（业务数据查询）

| 项 | 详情 |
|---|---|
| 定位 | 自然语言 → SQL → 安全执行 → 业务解释 |
| 关键文件 | [backend/sql/](../backend/sql/)（sql_agent / router / sql_validator / row_security / executor） |
| 关键 API | `POST /sql` |
| 当前边界 | 6 层硬校验 + agent_readonly 4 层防线 + Row Security + 8 种 SQLStatus |
| 详细设计 | [PRD.md §4.3](PRD.md) + [AGENT_DESIGN.md](AGENT_DESIGN.md) |

### 2.4 Memory（3 层记忆）

| 项 | 详情 |
|---|---|
| 定位 | 短期 / 会话 / 长期 3 层记忆，让 Agent 有"上下文"和"经验" |
| 关键文件 | [backend/memory/](../backend/memory/)（manager / short_term / session / long_term / importance / decay / pii_filter） |
| 关键 API | `/memory/sessions` / `/memory/sessions/{id}/context` |
| 当前边界 | L1 进程内（20 条）/ L2 PG 持久化 / L3 pgvector（pgvector cosine + ivfflat） |
| 已补齐 | 衰减 cron（Celery beat `memory-daily-decay`）/ 多用户隔离（JWT + 多租户，2026-09 落地） |

### 2.5 Observability（可观测性）

| 项 | 详情 |
|---|---|
| 定位 | 每个请求一棵 Span 树，含 LLM 成本 / SLA / 父子链 |
| 关键文件 | [backend/observability/](../backend/observability/)（tracer / trace_middleware / trace_store / metrics / topology / alerts） |
| 关键 API | `/observability/traces` / `/observability/metrics` / `/observability/graph` |
| 当前边界 | TraceCollector + 内存 + SQLite 兜底 + ContextVar 异步隔离 + 14 个前端组件（FlameGraph / GraphTopology / 成本面板） |
| 详细设计 | [observability/trace-model.md](observability/trace-model.md) |

### 2.6 附加子系统（次要）

| 子系统 | 定位 | 详细 |
|---|---|---|
| **Workflow** | 确定性业务流程（daily_report / inventory_alert） | [AGENT_DESIGN.md §8](AGENT_DESIGN.md) |
| **Report** | 6 种内置报告 + 模板 + 图表 | [PRD.md §4.5](PRD.md) |
| **Data Collection** | 5 阶段 Pipeline（Fetcher / Parser / Cleaner / Analyzer / Writer） | [PRD.md §4.5](PRD.md) |
| **Seed** | 演示数据生成（real 真实品类名） | [PRD.md](PRD.md)（原 learn/07 文档已删） |
| **Evaluation** | 评测框架（Faithfulness / 引用率） | [PRD.md](PRD.md) + [backend/evaluation/](../backend/evaluation/)（原 learn/08 文档已删） |

---

## 3. 关键数据流

### 3.1 Chat 链路（用户问问题）

```
[Frontend ChatInput]
        ↓ POST /chat/stream {question, session_id, request_id}
[FastAPI chat_stream()]  ← manual r.json() 绕过 fastapi 中文 bug
        ↓
[queue.Queue (1024) + threading.Event]
        ↓
[ThreadPoolExecutor] 启动 producer()
        ↓
[GraphRunner 前置] Input Guard 门禁 → memory.start_session → Follow-up 指代解析
        ↓
[Router Runtime] 域预过滤（客服锁域/旅游/选品）→ RoutingEngine（domain → capability → execution）拍板 route_mode
   ├─ direct:  tool_selector → skill_executor（直连能力）
   ├─ workflow: workflow_executor（预定义编排）
   ├─ plan:    任务拆解 → 并行调度（Send）→ Skills
   ├─ 域命中:   Domain Runtime 域图接管（客服/旅游/选品…）
   └─ 寒暄/咨询: general_chat 直答
[Skills（主图支线经 Tool Contract Boundary 归一 → Tool Runtime 治理执行）]:
   ├─ SQLSkill → SQL Agent → 6 层校验 → PG
   ├─ RAGSkill → 6 段流水线 → PG + pgvector
   └─ ReportSkill → DataFetcher → 模板引擎 → 图表
        ↓
[Reporter / 域图自有 reporter] 汇总 → SSE 编码
        ↓
[event_generator() 异步取事件 → yield "event: type\ndata: {...}\n\n"]
        ↓
[前端 SSE 解析 → 流式渲染]
```

详细：[PRD.md Chat 链路完整说明](PRD.md)

### 3.2 文档入库链路

```
[POST /rag/upload]  ← 后台 IncrementalIndexer.sync()
        ↓
[_index_file()]  ← 9 阶段埋点
   ① index_load       → 加载文件
   ② index_parse      → PyPDFLoader / Docx2txtLoader / TextLoader
   ③ index_clean      → DocumentCleaner (11 种清洗)
   ④ index_dedup      → SHA256 比对
   ⑤ index_chunk      → ChunkStrategyRouter + ChunkFilter
   ⑥ index_metadata   → LLM+规则: 分类/摘要/关键词/实体
   ⑦ index_embed      → 模型网关 embedding（DB 治理注册表）
   ⑧ index_vector_db  → PG + pgvector（rag_vectors）写入
   ⑨ registry         → DocumentRegistry.register()
        ↓
[Trace 上报 SSE] → 前端 /knowledge/operations/traces/{id}
```

### 3.3 报告生成链路

```
[Workflow daily_report]    ←    [POST /report]   ←   [APScheduler 9:00]
        ↓
[Step 1: fetch_sales]        → SQL
[Step 2: fetch_inventory]    → SQL
[Step 3: fetch_promotions]   → SQL
[Step 4: rag_query_template] → RAG kb=analytics
[Step 5: agent_analyze]      → BusinessAnalyzer (LLM)
[Step 6: generate_report]    → ReportSkill
   ├─ TemplateEngine (Jinja2 沙箱)
   ├─ ChartGenerator (matplotlib)
   └─ LLMPolisher (数值硬校验)
[Step 7: send_email]         → EmailSkill
        ↓
[POST /chat/messages] 持久化会话
```

### 3.4 库存预警链路

```
[APScheduler 每日扫描]    ←    [POST /inventory/cases]
        ↓
[inventory_alert workflow]
   scan_inventory → fetch_sales_history → calculate_health
   → evaluate_thresholds → alert_state_machine
   → create_event → load_policies → send_alert_email
        ↓
[PostgreSQL（SQLite 散落存储已于 2026-09 全量下线）]
        ↓
[前端 /alerts 页面]
```

---

## 4. 设计原则

来自 [CLAUDE.md](../CLAUDE.md)：

| 原则 | 落地 |
|---|---|
| **可理解** | 一份文档讲清楚一件事；模块边界清晰 |
| **可测试** | Tool 必须独立可测试；接口契约明确 |
| **可观测** | 每个请求一棵 Trace 树；Spans 异步入库 |
| **可维护** | 配置外置（env）；禁止临时堆叠 / `except: pass` |
| **可扩展** | 新增 Skill 只需 3 步（写类 / 导入 / 自动发现） |
| **可控制** | 限流 / 降级 / 熔断 / 队列 backpressure |
| **可靠性** | 6 层 SQL 校验 / Faithfulness 校验 / 错误兜底友好 |

**禁止**：

- ❌ Demo 跑通式开发
- ❌ 临时堆叠 / `except Exception: pass`
- ❌ 业务代码直接 `os.getenv`（必须走 `config/`）
- ❌ 命名 `misc.py / helper.py / common.py / utils2.py`

---

## 5. 技术栈

### 5.1 后端

| 层 | 技术 |
|---|---|
| Web 框架 | FastAPI + Uvicorn |
| Agent 编排 | LangGraph (`StateGraph` + `Send[]`) |
| LLM SDK | `langchain-openai`（DeepSeek 兼容接口） |
| LLM 兜底 | `langchain-ollama`（本地 qwen2.5:3b） |
| RAG 框架 | LangChain LCEL + 自研 `_rag_shared.py` |
| 向量库 | ChromaDB（langchain_chroma） |
| 重排序 | sentence-transformers CrossEncoder |
| 关系数据库 | PostgreSQL 18（`agent_business` + `agent_memory` 双库） |
| 连接池 | `psycopg2.pool.ThreadedConnectionPool` |
| 数据校验 | sqlglot（AST 改写） |
| 文档加载 | PyPDFLoader / Docx2txtLoader / TextLoader |
| 图表 | matplotlib（`Agg` 后端） |
| 模板 | Jinja2（`SandboxedEnvironment`） |
| Embedding | HuggingFace（本地） |
| 调度 | APScheduler（`AsyncIOScheduler`） |
| 测试 | pytest + pytest-asyncio + Vitest |

### 5.2 前端

| 层 | 技术 |
|---|---|
| 框架 | Next.js 14.2 App Router |
| UI | React 18 + TypeScript 5.7 |
| 样式 | Tailwind CSS 3.4 |
| 状态 | Zustand 5（仅 chat 域） |
| 数据请求 | `fetch` + `useState/useEffect`（**react-query 装但未用**） |
| 图表 | recharts / 自研 SVG |
| SSE | 自研 `parseSSEStream` AsyncGenerator |
| 测试 | Vitest |

### 5.3 基础设施

| 层 | 技术 |
|---|---|
| 容器 | Docker + Docker Compose |
| 数据库 | PostgreSQL 18（双实例：docker 5433 权威库 + 宿主机 5432，见根 [AGENTS.md](../AGENTS.md)） |
| 进程 | Uvicorn（生产） |
| 监控 | 自建 Trace + Prometheus metrics |
| 日志 | 结构化 JSON + 日志中间件 |

---

## 6. 运行环境

### 6.1 开发

```bash
# 后端
cd backend
python -m uvicorn backend.app.server:app --reload --port 8000

# 前端
cd frontend
npm run dev    # → http://localhost:3000

# 启动时序约束（详见 CLAUDE.md）
# 1. PG 必须先启动（先 cd backend/ && docker compose up -d postgres）
# 2. RAG 预热 10-15s（首次 /chat/stream 会触发）
# 3. 失败可重试，_rag_init_error 状态由 /health 暴露
```

### 6.2 生产

```bash
docker compose -f docker-compose.yml up -d
# 自动启动：postgres + backend + frontend
```

### 6.3 关键依赖

- Python 3.10+
- Node 20+
- PostgreSQL 18（scram-sha-256 + 只读角色，见根 [AGENTS.md](../AGENTS.md)）
- Windows: 数据卷挂载注意 CRLF；Docker in Docker 限制

---

## 7. 关键决策（ADR 索引）

详见 [architecture/adr/](architecture/adr/)：

- [ADR-0001 — 合并双注册表](architecture/adr/0001-merge-dual-registry.md)
- [ADR-0002 — RAGChain 拆解](architecture/adr/0002-ragchain-decomposition.md)
- [ADR-0003 — 目录分层规范](architecture/adr/0003-directory-layering.md)

`decisions/` 跨主题决策：

- [auth-decision.md](decisions/auth-decision.md) — 认证方案（✅ 已实现：自建 JWT，`security/local_jwt.py`，migration 008）

---

## 验证

最后验证：2026-09-29 · 过时口径已按根 [README.md](../README.md)「系统规模」校准（鉴权/存储/节点数）；数量类以根 README 为唯一权威。
