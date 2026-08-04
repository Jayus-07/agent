# Agent Platform

电商 RAG + Multi-Agent 智能平台 — LangGraph + MCP 生产级架构。

---

## 技术栈

- **后端**：FastAPI + LangGraph + LangChain
- **前端**：Next.js 14 + React 18
- **数据**：PostgreSQL（结构化）+ ChromaDB（向量）+ BM25（关键词）
- **LLM**：DeepSeek / Qwen / Ollama（可切换）
- **可观测**：Prometheus + 自建 Trace（9 阶段全链路）
- **MCP**：独立进程，暴露 Agent 能力给外部调用

---

## 架构

```
CLAUDE.md 8 层调用链:

简单请求:
  API → Router → Skill → Tool / RAG / SQL / Memory

复杂任务:
  API → Router / Agent Runtime → Planner → Supervisor → Skill → Tool
       → MCP Client → MCP Server → External Resource
```

```
当前架构实现:

  agents/planner/          → Planner（任务拆解）
  orchestration/supervisor/ → Supervisor（调度+降级）
  skills/                  → Skill（8 个，业务能力封装）
  tools/                   → Tool（9 个，底层调用）
  mcp_servers/             → MCP Server（独立进程）
```

---

## 目录结构

```
agent/
├── pyproject.toml        # 依赖分层（Web/LLM/存储/NLP/可观测）
├── mcp_servers/          # MCP 服务（独立于 backend）
├── backend/
│   ├── agents/           # Agent 节点（planner/reporter/capability）
│   ├── skills/           # 业务能力（rag/sql/report/email/...）
│   ├── tools/            # 工具层（sql/rag/web/email/...）
│   ├── observability/    # 可观测（tracer/metrics/alerts/topology）
│   ├── orchestration/    # LangGraph 运行时（supervisor/graph/workflow/state）
│   ├── rag/              # RAG 管道（retrieval/indexing/preprocessing/evidence_gate）
│   ├── memory/           # 三层记忆（L1 短期 + L2 会话 + L3 长期）
│   ├── app/              # FastAPI（server + routes + middleware）
│   ├── config/           # 纯配置（按模块拆分，os.getenv 集中管理）
│   ├── infra/            # 基础设施（LLM 工厂/限流/超时/异步）
│   ├── shared/           # 最小共享层（logger/exceptions）
│   ├── evaluation/       # RAG 评估框架
│   ├── seed/             # 种子数据生成
│   ├── data_collection/  # ETL 管道
│   ├── sql/              # NL-to-SQL 引擎
│   └── prompts/          # LLM 提示词模板
├── scripts/              # Demo 脚本
├── docs/                 # 架构文档 + ADR
├── frontend/             # Next.js（端口 3000）
└── data/                 # 运行时数据（chroma/doc_db/memory/*.db）
```

---

## 快速开始

```bash
# Windows 一键启动
start_all.bat

# 手动启动
cd backend && uvicorn app.server:app --host 0.0.0.0 --port 8000 --reload  # 后端
cd frontend && npm run dev                                                # 前端

# 浏览器
http://localhost:3000        # 前端
http://localhost:8000/docs   # Swagger API
http://localhost:8000/metrics # Prometheus
```

---

## MCP 端点

| 端点 | 说明 |
|------|------|
| `GET /mcp/tools` | 列出所有可用 tool |
| `POST /mcp/call` | 调用指定 tool |

**已注册 tool**：`sql_query` / `search_knowledge` / `generate_report` / `web_search` / `send_email` / `export_csv` / `data_collection`

---

## 开发

```bash
pip install -e ".[dev]"       # Python（pyproject.toml）
cd frontend && npm install     # Node

# 测试
pytest backend/tests/ -q      # 500 tests
```

**代码规范**、**修改流程**、**7 项设计原则** 见 [CLAUDE.md](CLAUDE.md)。

---

## License

Private — 仅供内部使用。
