# 系统架构

> 本文档描述系统整体架构、模块依赖、请求生命周期。需要修改任何模块时从这里开始。

## 1. 系统全景

```mermaid
graph TB
    subgraph 用户层
        UI[Next.js 前端<br/>端口 3000]
        API_直接[curl / HTTP Client]
    end

    subgraph 网关层
        FAST[FastAPI + Uvicorn<br/>端口 8000]
        CORS[CORS 中间件]
        CONCURRENCY[并发控制中间件<br/>asyncio.Semaphore]
        HEALTH[/health]
        DOCS[/docs Swagger]
    end

    subgraph API路由
        CHAT[POST /chat<br/>SSE /chat/stream]
        SQL[POST /sql]
        RAG[POST /rag]
        REPORT[POST /report]
        LLM[POST /llm/switch<br/>GET /llm/models]
        OBS[/observability/*]
    end

    subgraph Agent层
        MAS[MultiAgentSystem<br/>multi_agent/graph.py]
        SQL_AG[SQLAgent<br/>sql_agent/]
        RAG_AG[RAGPipeline<br/>retrieval/pipeline.py]
        REP_AG[ReportGenerator<br/>report_agent/]
    end

    subgraph 记忆层
        MEM_S[MemoryService<br/>memory/service.py]
        MEM_M[MemoryManager<br/>memory/manager.py]
        L1[ShortTermBuffer]
        L2[SessionMemory]
        L3[LongTermMemory]
    end

    subgraph 存储层
        PG[(PostgreSQL 18<br/>+ pgvector)]
        CHROMA[(ChromaDB<br/>向量库)]
        OLLAMA[Ollama<br/>qwen2.5:3b]
    end

    UI -->|SSE| FAST
    API_直接 -->|HTTP| FAST
    FAST --> CONCURRENCY
    FAST --> CHAT & SQL & RAG & REPORT & LLM & OBS
    CHAT --> MAS
    SQL --> SQL_AG
    RAG --> RAG_AG
    REPORT --> REP_AG
    MAS --> SQL_AG & RAG_AG & REP_AG
    MAS --> MEM_M
    SQL_AG --> PG
    RAG_AG --> CHROMA & OLLAMA
    REP_AG --> SQL_AG & OLLAMA
    MEM_M --> MEM_S
    MEM_S --> L1 & L2 & L3
    L1 -.-> MEM_S
    L2 --> PG
    L3 --> PG
```

## 2. 请求生命周期

### 2.1 普通对话 (POST /chat)

```
HTTP Request → FastAPI (api/server.py)
  → 并发控制中间件 (asyncio.Semaphore)
  → 惰性初始化 Agent 单例 (api/deps.py)
  → api/routes/chat.py:chat()
    → MultiAgentSystem.ask()
      → MemoryManager.start_session()    # L2→L1 恢复 + L3 长期记忆注入
      → LangGraph (Planner→Supervisor→Workers→Reporter)
      → MemoryManager.end_turn()         # L2 持久化 + 触发 L3 后台写入
    → JSON 响应
```

### 2.2 SSE 流式对话 (POST /chat/stream)

```
HTTP Request → FastAPI
  → api/routes/chat.py:chat_stream()
    → MultiAgentSystem.stream_events()
      → MemoryManager.start_session()
      → LangGraph.stream() 产生事件
        → Planner: meta 事件（node_labels 映射）
        → Supervisor: status 事件
        → Worker: log / status 事件
        → Reporter: delta 事件（句子块）
      → MemoryManager.end_turn()
    → SSE chunked response
```

事件类型：`meta` / `status` / `log` / `delta` / `done` / `error`（详见 `web/src/lib/types.ts`）。

### 2.3 其他路由

| 路由 | 调用链 | 流式 |
|---|---|---|
| `POST /sql` | `SQLAgent.ask()` → `router/select_tables` → `generator/generate_sql` → `validator/validate` → `row_security/inject` → `executor/execute_sql` | 否 |
| `POST /rag` | `RAGPipeline.ask()` → `QueryAnalyzer` → `BM25+vector` → `Reranker` → `Citation Filter` | 否 |
| `POST /report` | `ReportGenerator.generate()` → `data_fetcher` → `template_engine` → `chart_generator` → `llm_polisher` (可选) | 否 |
| `POST /llm/switch` | `LLMFactory.set_current()` → 重建 instance cache → `_LLMProxy` 自动切换 | 否 |
| `GET /observability/*` | 读 `multi_agent/observability.py:trace_store` + `utils.resource_monitor` | 否 |

## 3. 技术栈

| 层 | 技术 | 备注 |
|---|---|---|
| LLM | Ollama (qwen2.5:3b/4b) + DeepSeek (云端) | 通过 `llm_factory.py:llm`（`_LLMProxy`）统一访问 |
| Embedding | BAAI/bge-small-zh-v1.5 (ModelScope) | 首次请求时懒加载 |
| Reranker | BAAI/bge-reranker-base | CrossEncoder |
| 向量库 (RAG) | ChromaDB | 路径 `data/chroma/` |
| 向量库 (Memory) | PostgreSQL 18 + pgvector (ivfflat cosine) | `Vector(512)` |
| 关键词检索 | rank-bm25 + jieba | BM25 索引内存 |
| Multi-Agent | LangGraph (StateGraph + Send API 并行扇出) | `langgraph==0.4+` |
| SQL 安全 | sqlglot 解析 + psycopg2 只读事务 | 行级安全参数化注入 |
| 报告 | Jinja2 + matplotlib + LLM 润色 | 数字/事实硬校验锁定 |
| 后端 | FastAPI + uvicorn + SQLAlchemy 2.0 Async | 并发控制中间件 |
| 前端 | Next.js 14 + Tailwind CSS + Zustand + SSE streaming | 统一单页 + monitor 子页 |
| 数据库 | PostgreSQL 18 (本地服务 `postgresql-x64-18`) | 端口 5432 |
| 异步 | asyncio + asyncpg + 持久后台事件循环线程 | `MemoryManager` 桥接 |
| MCP | @modelcontextprotocol/server-puppeteer + server-filesystem | 截图 / 文件操作 |
| 测试 | pytest 9.0.3 | 33 个测试文件，~200+ 用例 |

## 4. 模块依赖

```
api/server.py
  └─→ api/routes/{chat,sql,rag,report,llm,observability}
       └─→ multi_agent/graph.py:MultiAgentSystem
       │    └─→ multi_agent/{planner,supervisor,reporter,critique,degradation,alerts,tools,state,tool_registry,observability}
       │         └─→ multi_agent/workers/{sql,rag,report}_worker → tools.py
       │              └─→ {sql_agent, retrieval, report_agent}
       │    └─→ memory/manager.py:memory_manager
       │         └─→ memory/service.py:MemoryService
       │              └─→ {L1, L2, L3}
       └─→ sql_agent/sql_agent.py:SQLAgent
       └─→ retrieval/pipeline.py:RAGPipeline
       └─→ report_agent/report_generator.py:ReportGenerator
       └─→ llm/llm_factory.py:llm（_LLMProxy）
       └─→ utils/{logger,timeout,resource_monitor}
```

**关键约束**：
- Agent 层**禁止**直接访问 `memory/repository/` 或 `memory/models/`，统一通过 `MemoryService`
- 所有 fetch 走 `lib/api.ts`（前端）
- 所有 fetch 走 `utils/timeout.py` 的超时保护
- 所有日志走 `utils/logger.py`

## 5. 关键设计决策

| 决策 | 理由 | 相关文件 |
|---|---|---|
| `MemoryService` 单一入口 | 防止 Agent 绕过规则直接 ORM 操作 | `memory/__init__.py` |
| `LLMFactory` + `_LLMProxy` | 多 Provider 切换对业务代码零侵入 | `llm/llm_factory.py` |
| `ToolRegistry` capability 抽象 | Worker 并行解耦，新增能力只改注册表 | `multi_agent/tool_registry.py` |
| `row_security` 参数化 | 防止 user_id 硬编码到 SQL 文本 | `sql_agent/row_security.py` |
| `executor` 显式 `BEGIN + SET TRANSACTION READ ONLY` | autocommit 模式下 `set_session(readonly=True)` 失效 | `sql_agent/executor.py` |
| `state._degraded_steps` 用 `Annotated[set, operator.or_]` | 防止 set 被原地修改破坏 LangGraph state 不可变语义 | `multi_agent/state.py` |
| `_merge_step_results` 自定义 reducer | 解决 LangGraph `INVALID_CONCURRENT_GRAPH_UPDATE` | `multi_agent/state.py` |
| 并发控制中间件 | 笔记本/低配机器防 CPU 过载关机 | `api/server.py` |
| 单页 + SSE | 简化前端，让后端 Multi-Agent 自动路由 | `web/src/app/page.tsx` |
| Zustand 单 store | 简单场景不需要 RTK Query / Redux | `web/src/store/chat.ts` |

## 6. 性能与可观测

- **L3 写入**：后台异步，不阻塞主请求（依赖 `MemoryService.end_turn`）
- **事件流**：SSE 用 `(step_id, status)` 粒度去重，避免重复
- **Monitor 页面**：`/monitor` 路由调用 `/observability/*` 端点，每 5s 轮询
- **资源监控**：`utils/resource_monitor.py` 持续跟踪 CPU / 内存，超阈值时 `WARNING`
