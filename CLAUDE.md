# CLAUDE.md

> 自我约束：本文件 ≤ 80 行。详细规范见 `docs/`。

## 项目

电商 RAG + Multi-Agent 平台（FastAPI + Next.js 14 + LangGraph）
AI: DeepSeek / Qwen / Ollama · 自建 tracer（`backend/rag/tracer.py`）

## 调用路径

- **简单**：API → rag / sql / memory
- **复杂**：API → orchestration → planner → supervisor → skills → tools → subsystem → reporter

## 核心约束

- Planner 只输出 capability DAG，禁止调 Tool / Skill / DB
- Skill 不直接 HTTP，通过 Tool
- Tool 必须无状态 + 可测 + 完整 Trace

## 禁止

- thin wrapper（仅 re-export）→ 例外：`lib/api.ts` 兼容层、DTO 转换
- 文件名 `misc.py` / `helper.py` / `common.py` / `utils2.py`
- 裸 `fetch` → 例外：SSE 流 / EventSource / FormData 上传
- `except Exception: pass`（生产路径）→ 必须加注释说明原因，否则 P1
- `os.getenv()` / `os.environ[]` → config 模块内部可用，业务代码统一 `from backend.config import settings`

## 语言约定

- **Python**：snake_case + 类型注解 + logger（不用 print）+ 具体异常 + 参数化 SQL
- **React**：`"use client"` 仅在 useState / useEffect / 浏览器 API / SSE / WebSocket；API 按域 import `@/lib/api/<domain>`；Toast 用 `useToast()` 禁 `alert()`/`confirm()`
- **Mock**：结构需对应真实 API 返回类型，Mock 数据统一放 `src/mock/` 或 `src/services/mock/`；通过 `NEXT_PUBLIC_USE_MOCK` 开关切换

## 优先级

- **P0**（立即修）：数据错误 / 安全漏洞 / 内存泄漏 / 生产崩溃 / Trace 丢失
- **P1**（建议修）：God Object / 长函数 / 重复代码 / 强耦合
- **P2**（不改大量稳定代码）：命名 / 注释 / 小型重构

## Commit

```text
type(scope): 中文 subject
```
type: feat / fix / refactor / docs / test / perf / chore
scope: rag / frontend / observability / chat / memory / knowledge ...

## 分支

- `master` 稳定主分支 · `feat/*` / `fix/*` 从 master 开，本地 squash → master
- 不直接在 master 改（hotfix 除外）

## 修改流程

1. 读相关代码 → MCP `code-review-graph` 查影响范围（`detect_changes_tool` + `get_affected_flows`）
2. 改完跑 `npx tsc --noEmit` + `npm test` + Python `py_compile`
3. 人工验证：浏览器操作一遍才算通过
4. **踩坑即记**：遇到非显而易见的错误/坑/限制（编译不报但运行时报、路径/目录依赖、超时/缓存策略），写入 `memory/` 并更新 MEMORY.md 索引

### 何时重启

- 改 `backend/**/*.py` → 重启 uvicorn：**必须 `cd backend` 后跑** `..\.venv\Scripts\python.exe -m uvicorn app.server:app --host 127.0.0.1 --port 8000 --reload`（在项目根跑会报 `No module named 'app'`）
- 改 `frontend/.env.local` → 删 `.next` 重启 Next.js
- 改 `frontend/**/*.tsx` → 热更新，无需重启
- 一键重启：双击 `restart_all.bat`

## MCP 工具（code-review-graph）

核心流程：`detect_changes_tool` → `get_affected_flows`（风险 ≥0.7 必查）→ 按需 `get_impact_radius` / `query_graph` / `semantic_search_nodes`
改完跑 `build_or_update_graph_tool` 增量更新图。

## 规范索引

| 主题 | 位置 |
|------|------|
| 模块职责 / DTO / DB / State / Prompt | `docs/architecture/` |
| 测试 / 重构 / 优先级 / 输出格式 | `docs/development/` |
| 启动命令 | `docs/operations/commands.md` |
| Trace 模型 / 可观测性 / Mock | `docs/observability/` |
