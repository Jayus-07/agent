# CLAUDE.md

## Project

RAG + Multi-Agent + FastAPI + Next.js

Architecture:
Planner → Supervisor → Skills(RAG/SQL/Report) → Reporter

Core:
- retrieval/
- memory/
- multi_agent/
- sql_agent/
- report_agent/
- api/
- web/

---

## Rules

修改前：

- Read 相关代码，不猜实现
- 最小修改
- 保持现有架构
- 不改 Public API
- 不重构（除非明确要求）

修改后：

- 跑对应真实测试（模拟用户真实操作）
- 更新必要文档
- 汇报：
  - 改了什么
  - 为什么
  - 影响范围
  - 测试结果

---

## Coding

Python

- snake_case
- 类型注解
- logger
- 不用 print
- 不 except Exception

React

- API 统一 lib/api.ts
- SSE 用 useSSE
- Zustand 管全局状态

---

## Priority

1. Bug
2. 新功能
3. Dead Code
4. Refactor（仅用户要求）

---

## Commands

启动：

start_all.bat

测试：

pytest tests/

前端：

npm run dev

构建：

next build

---

## Docs

修改前按需阅读：

docs/architecture.md

docs/rag.md

docs/memory.md

docs/sql-agent.md

docs/report-agent.md

docs/frontend.md

## 代做
记忆模块（等用户说）