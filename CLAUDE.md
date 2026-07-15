# CLAUDE.md

## Project

跨境电商 RAG + Multi-Agent 平台（FastAPI + Next.js + LangGraph）

架构: Planner → Supervisor → Skills(RAG/SQL/Report) → Reporter

---

## Structure

```
agent/
├── frontend/                  # Next.js（原 web/）
│   └── src/
│       ├── app/  components/  hooks/  lib/  store/
├── backend/                   # FastAPI 后端
│   ├── app/                   # API 入口（server.py / deps.py / api/routes/）
│   ├── rag/                   # 原 retrieval/ + preprocessing/
│   ├── agent/                 # 原 multi_agent/（Planner/Supervisor/Skills/Reporter）
│   ├── llm/  sql/  report/  memory/  data_collection/  evaluation/
│   ├── mcp/                   # Phase 5: MCP 集成（/mcp/tools + /mcp/call）
│   ├── utils/                 # logger / async_utils / resource_monitor / monitoring
│   └── config.py
├── docs/  start_all.bat  stop_all.bat  restart_all.bat  .env
└── .venv/  data/  logs/  requirements.txt
```

**注意**：`backend/data` 是 Windows Junction 符号链接 → `../data`，必须从 `backend/` 目录启动后端才能正确解析路径。

---

## Rules

**修改前**：
- Read 相关代码，不猜实现
- 最小修改
- 保持现有架构
- 不改 Public API
- 不重构（除非用户明确要求）

**修改后**：
- 跑对应真实测试（模拟用户操作）
- 更新必要文档
- 汇报：改了什么 / 为什么 / 影响范围 / 测试结果

---

## Coding

**Python**
- snake_case、类型注解、logger（不用 print）
- 不写 `except Exception`（除非有充分理由）

**React**
- API 调用统一走 `lib/api.ts`
- SSE 用 `useSSE`
- 全局状态用 Zustand

---

## Priority

1. Bug
2. 新功能
3. Dead Code
4. Refactor（仅用户要求）

---

## Commands

| 操作 | 命令 |
|---|---|
| 一键启动 | `start_all.bat` |
| 一键停止 | `stop_all.bat` |
| 重启 | `restart_all.bat` |
| 仅后端 | `cd backend && ..\.venv\Scripts\python.exe -m uvicorn app.server:app` |
| 仅前端 | `cd frontend && npm run dev` |
| 后端 API 文档 | http://localhost:8000/docs |
| 前端 | http://localhost:3000 |
| MCP 工具列表 | http://localhost:8000/mcp/tools |

---

## Docs

按需阅读（Path 全部对应 backend/ 重构后结构）：
- docs/data-collection-center.md
- docs/rag-full-pipeline.md
- docs/phased-refactor-plan.md

---

## 待办

- 记忆模块（等用户说）