# Agent Platform

跨境电商 RAG + Multi-Agent 智能平台 — 让 AI 检索企业知识库、自动拆解任务、协同 SQL 查询与报告生成。

---

## 技术栈

- **后端**：FastAPI + LangGraph Multi-Agent + LangChain
- **前端**：Next.js 14 + React 18 + Zustand
- **数据**：PostgreSQL（结构化）+ ChromaDB（向量）+ BM25（关键词）
- **LLM**：DeepSeek / Qwen / Ollama（可切换）

---

## 架构

```
用户问题
   ↓
FastAPI (port 8000)
   ↓
MultiAgentSystem
   ├── Planner     — 任务拆解 + DAG 计划
   ├── Supervisor  — Skill 调度 + 降级
   ├── Skills      — RAG / SQL / Report
   └── Reporter    — 结果汇总 + 引用格式化
   ↓
外部 API（POST /mcp/call）
```

详见 `backend/app/server.py` 和 `backend/agent/graph/system.py`。

---

## 目录结构

```
agent/
├── frontend/         # Next.js 前端（端口 3000）
├── backend/          # FastAPI 后端（端口 8000）
│   ├── app/          # API 入口 + 路由
│   ├── rag/          # RAG 检索（向量 + BM25 + Citation）
│   ├── agent/        # Multi-Agent（Planner/Supervisor/Skills）
│   ├── mcp/          # MCP 集成（/mcp/tools, /mcp/call）
│   └── sql/  report/  llm/  memory/  data_collection/
├── docs/learn/       # 教程资料（架构演进参考）
├── start_all.bat     # 一键启动前后端
├── stop_all.bat      # 一键停止
└── restart_all.bat   # 重启
```

---

## 快速开始

```bash
# Windows 一键启动
start_all.bat

# 浏览器打开
http://localhost:3000   # 前端
http://localhost:8000   # 后端
http://localhost:8000/docs   # Swagger API 文档
```

**仅后端**：`cd backend && ..\.venv\Scripts\python.exe -m uvicorn app.server:app`
**仅前端**：`cd frontend && npm run dev`

---

## MCP 端点

外部 Agent（Claude/Cursor 等）可通过 MCP 协议调用本平台的能力：

| 端点 | 说明 |
|---|---|
| `GET  /mcp/tools` | 列出所有可用 tool |
| `GET  /mcp/servers` | 列出已注册 server |
| `POST /mcp/call` | 调用指定 tool（body: `{tool_name, params}`） |

**已注册 tool**：`rag.search_knowledge` / `rag.list_documents` / `rag.get_stats` / `sql.sql_query` / `sql.list_tables`

详见 [backend/mcp/manager.py](backend/mcp/manager.py)

---

## 核心模块

| 模块 | 路径 | 职责 |
|---|---|---|
| RAG Pipeline | `backend/rag/pipeline.py` | 文档加载 + 向量化 + 检索 + Citation |
| Multi-Agent | `backend/agent/graph/system.py` | Planner/Supervisor/Skills 工作流 |
| MCP Server | `backend/mcp/servers/` | 能力暴露给外部 Agent |
| API Routes | `backend/app/api/routes/` | HTTP 端点（chat/rag/sql/...） |

---

## 开发

```bash
# 依赖
pip install -r requirements.txt      # Python
cd frontend && npm install            # Node

# 重启
restart_all.bat
```

**代码风格**、**修改流程**、**优先级** 见 [CLAUDE.md](CLAUDE.md)。

---

## License

Private — 仅供内部使用。