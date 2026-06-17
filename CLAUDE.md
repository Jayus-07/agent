# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

基于 LangChain + LangGraph 的 RAG + Multi-Agent 智能问答与报告系统。FastAPI 提供 REST API，Next.js 前端通过 SSE 流式消费 Multi-Agent 工作流进度。PostgreSQL + pgvector 提供企业级记忆持久化。

## 常用命令

```bash
# ==================== 环境 ====================
pip install -r requirements.txt           # 安装 Python 依赖
cd web && npm install && cd ..            # 安装前端依赖

# ==================== 数据库 ====================
# PostgreSQL 18 本地服务 (端口 5432)
PGPASSWORD=123456 psql -h localhost -U postgres -d demo -f memory/migrations/001_init.sql

# ==================== 启动服务 ====================
# 方式 1: 启动脚本（推荐）
.\start.ps1                               # Windows PowerShell
bash start.sh                             # Git Bash / Linux / macOS
start.bat                                 # Windows cmd.exe

# 方式 2: 直接启动
python -m api.server                      # 启动 FastAPI (端口 8000)
uvicorn api.server:app --reload           # 开发模式（热重载）

# 方式 3: Docker 全栈
# 注意: Docker Desktop 29.2.1 CLI 与旧版引擎 API 不兼容，建议用本地 PostgreSQL
docker compose up -d postgres             # 仅启动 PostgreSQL
docker compose logs -f api                # 查看日志
docker compose down                       # 停止

# ==================== 演示入口 ====================
python multi_agent/demo.py               # Multi-Agent 工作流演示
python sql_agent/demo_sql_agent.py       # SQL 安全查询演示
python report_agent/demo_report_agent.py # 报告生成演示

# ==================== 前端 ====================
cd web && npm run dev                     # Next.js 开发服务器 (端口 3000)
cd web && npm run build                   # 生产构建

# ==================== 记忆系统维护 ====================
python -c "import asyncio; from memory.service import MemoryService; asyncio.run(MemoryService().run_decay())"  # 运行衰减

# ==================== API 端点 ====================
# POST /chat          — Multi-Agent 对话（支持 /chat/stream SSE 流式）
# POST /sql           — 自然语言 → 安全 SQL 查询
# POST /rag           — 知识库检索问答
# POST /report        — 自动化报告生成
# GET  /health        — 健康检查
# GET  /docs          — Swagger UI
```

## 核心架构

### 请求生命周期

```
HTTP Request → FastAPI (api/server.py)
  → 惰性初始化 Agent 单例 (api/deps.py)
  → MultiAgentSystem.ask()
    → MemoryManager.start_session()   # L2→L1 恢复 + L3 长期记忆注入
    → LangGraph (Planner→Supervisor→Workers→Reporter)
    → MemoryManager.end_turn()        # L2 持久化 + 触发 L3 后台写入
  → JSON 响应 或 SSE 流
```

### 记忆系统 (Memory)

三层架构，全异步 PostgreSQL + pgvector，统一入口 `MemoryService`：

```
memory/
├── service.py              # MemoryService 统一入口（Agent 唯一接入点）
├── manager.py              # MemoryManager 兼容层（同步包装器，持久事件循环线程）
├── short_term.py           # L1 ShortTermBuffer（环形缓冲区，内存）
├── session.py              # L2 SessionMemory（PG async，替代 SQLite）
├── long_term.py            # L3 LongTermMemory（pgvector，事实提取+检索）
├── trigger.py              # MemoryWorthinessClassifier（规则+LLM：STORE/IGNORE）
├── importance.py           # ImportanceScorer（5维评分 0.0-1.0，阈值 0.6）
├── retriever.py            # HybridRetriever（0.5×sim + 0.3×imp + 0.2×recency）
├── decay.py                # MemoryDecayService（>90d ×0.95, >180d ×0.9, <0.2 归档）
├── dedup.py                # 向量去重（保留）
├── pii_filter.py           # PII 正则过滤器（保留）
├── database.py             # AsyncEngine + AsyncSessionLocal（连接池 20+10）
├── repository/
│   ├── session_repo.py     # chat_sessions + chat_messages CRUD
│   └── memory_repo.py      # memory_records CRUD + pgvector hybrid search
├── models/
│   ├── session.py          # ChatSession, ChatMessage ORM
│   └── memory.py           # MemoryRecord ORM (Vector(512))
└── migrations/
    └── 001_init.sql        # DDL：3 表 + 7 索引
```

L3 写入管线（后台异步，不阻塞主流程）：
```
LLM Extract Facts → PII Filter → Trigger(STORE/IGNORE)
  → Importance Score(≥0.6) → Vector Dedup → pgvector Write
```

**约束**：Agent 禁止直接访问 repository/models，统一通过 `MemoryService`。

### Multi-Agent 工作流 (LangGraph)

```
START → Planner (DAG 规划) → Supervisor (调度)
  → route_after_supervisor 返回 list[Send] → Workers 并行执行
    ├─ SQL Worker   → sql_agent
    ├─ RAG Worker   → retrieval/pipeline.py
    └─ Report Worker → report_agent
  → 结果返回 Supervisor → 循环或 → Reporter → END
```

关键文件：`multi_agent/graph.py`（编译图 + MultiAgentSystem 类），`multi_agent/supervisor.py`（路由函数 `route_after_supervisor` 返回 `list[Send]` 实现并行扇出）。

### RAG 检索管线

`retrieval/pipeline.py` (RAGPipeline) 是主入口，协调：
向量检索 (ChromaDB) + BM25 关键词检索 → 混合排序 → BGE-Reranker 重排序 → Citation Filter 来源验证 → LLM 生成答案。

模型路径从 `config.py` 读取（环境变量覆盖），首次请求时自动创建向量库。

### SQL 安全 Agent

6 层安全防护：sqlglot 语法校验 → 只读执行器 → 行级安全注入 → 敏感列拦截 → 结果脱敏 → 超时控制。

### 报告生成

`report_agent/` 流程：SQL/API 取数 → Jinja2 模板渲染 → matplotlib 图表 → LLM 仅做语言润色。**数字/事实通过硬校验锁定，不依赖 LLM 承诺。**

### API 层设计

- `api/deps.py`：所有 Agent 通过惰性单例模式初始化，避免启动时加载全部模型
- `api/schemas.py`：Pydantic 请求/响应模型
- `api/routes/`：chat / sql / rag / report 四个路由模块
- 路由直接调用现有 Agent 模块，**零侵入**设计

### 前端 (Next.js)

统一单页面对话界面。后端 Multi-Agent 自动路由到 SQL/RAG/Report Worker，用户无需选择模式。

```
web/src/
├── app/layout.tsx + page.tsx    # 唯一入口
├── components/
│   ├── ChatView.tsx             # 主容器（连接 ChatInput + MessageList + StatusBar）
│   ├── ChatInput.tsx            # 通用输入框（通过 onSend prop 解耦）
│   ├── MessageBubble.tsx        # 消息气泡（SSE 进度 + Markdown）
│   ├── ThinkingPanel.tsx        # 数据驱动 Worker 进度面板（📊/📚/📄/🧠）
│   ├── StatusBar.tsx            # 底部实时进度条
│   └── EmptyState.tsx           # 统一示例页
├── hooks/
│   ├── useSSE.ts                # SSE 流消费（带 AbortController）
│   └── useChat.ts               # 发送消息 hook
├── lib/
│   ├── api.ts                   # API 客户端（含 SSE buffer 刷新）
│   ├── sse-parser.ts            # 共享 SSE 进度解析
│   ├── constants.ts             # Worker 图标映射
│   └── types.ts                 # 类型定义
└── store/
    └── chat.ts                  # Zustand store（replaceLastAssistant + sessionId 安全）
```

## 技术栈

| 层 | 技术 |
| --- | --- |
| LLM | Ollama (qwen2.5:3b)，通过 `OLLAMA_HOST` 环境变量连接 |
| Embedding | BAAI/bge-small-zh-v1.5 (ModelScope) |
| Reranker | BAAI/bge-reranker-base |
| 向量库 (RAG) | ChromaDB |
| 向量库 (Memory) | PostgreSQL 18 + pgvector (ivfflat cosine) |
| 关键词检索 | rank-bm25 |
| Multi-Agent | LangGraph (StateGraph + conditional_edges 并行扇出) |
| SQL 安全 | sqlglot + 只读执行器 |
| 报告 | Jinja2 + matplotlib + LLM 润色 |
| 后端 | FastAPI + uvicorn + SQLAlchemy 2.0 Async |
| 前端 | Next.js 14 + Tailwind CSS + Zustand + SSE streaming |
| 数据库 | PostgreSQL 18 (demo)，本地服务 `postgresql-x64-18` |
| 异步 | asyncio + asyncpg + 持久事件循环线程 |

## 关键约定

- **配置**：`config.py` 统一管理，敏感信息放 `.env`；连接地址通过环境变量覆盖（`OLLAMA_HOST`, `PGHOST` 等）
- **模块路径**：项目根在 `sys.path` 中，各模块直接从 `config`、`utils` 等导入；路径使用正斜杠 `/`
- **记忆系统**：Agent 禁止直接访问 `memory/repository/` 或 `memory/models/`，统一通过 `MemoryService`；L3 写入为后台异步，不阻塞主流程
- **数据库**：PostgreSQL 18 本地 Windows 服务，`PGPASSWORD=123456`；异步引擎连接池 20+10
- **SQL Agent**：只做只读查询，`row_security.py` 强制行级过滤
- **Report Agent**：LLM 仅做语言润色，数字/事实通过硬校验锁定
- **Multi-Agent**：通过 `ToolRegistry` 接入已有子系统，Worker 并行执行无共享状态；`MemoryManager` 在 `ask()` 入口/出口调用 `start_session`/`end_turn`
- **前端**：统一单页面，后端自动路由 Worker；ChatInput 通过 onSend prop 解耦；SSE 流支持 AbortController 中止
- **API 层**：不修改业务代码，通过 `deps.py` 惰性导入现有模块
- **模型下载**：`HF_HUB_OFFLINE=1` / `TRANSFORMERS_OFFLINE=1` 设置为离线模式，模型需预下载到本地路径
- **事件循环**：SQLAlchemy async engine 是事件循环绑定的；`MemoryManager` 使用持久后台线程+单事件循环处理所有 async→sync 桥接
