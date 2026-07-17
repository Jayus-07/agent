# CLAUDE.md

> 自我约束：本文件 ≤ 100 行。详细规范见 `docs/{architecture,development,operations,observability}/`。

## 项目

电商 RAG + Multi-Agent 平台（FastAPI + Next.js + LangGraph）

## Tech Stack（实际）

* Backend: FastAPI + PostgreSQL + LangGraph
* Frontend: Next.js 14 + TypeScript + Zustand
* AI: DeepSeek / Qwen / Ollama
* Observability: 自建 tracer（`backend/rag/tracer.py`）+ Span 树

## 调用路径

**简单**：API → rag / sql / memory
**复杂**：API → orchestration → planner → supervisor → skills → tools → subsystem → reporter

## Orchestration 核心约束

- **Planner 只输出 capability DAG**，禁止调 Tool / Skill / DB
- **Skill 不直接 HTTP**，通过 Tool
- **Tool 必须无状态 + 可测 + 有完整 Trace**（详见 `docs/observability/trace-model.md`）

## 禁止

- thin wrapper（仅 re-export）→ 例外：`lib/api.ts` 兼容层
- 文件名 `misc.py` / `helper.py` / `common.py` / `utils2.py`
- 裸 `fetch` → **例外**：SSE 流 / EventSource / FormData 上传
- `except Exception: pass`（生产路径）
- `os.getenv()` / `os.environ[]` → 统一 `from backend.config import settings`

```python
# ❌ Bad: thin wrapper（无业务逻辑）
def get_user(id): return db.get_user(id)

# ❌ Bad: 文件名无语义 / 配置硬读
utils.py                  os.getenv("OPENAI_API_KEY")

# ✅ Good: 加了 DTO 转换 = 真实逻辑
def get_user(id): return db.get_user(id).to_dto()
```

## 优先级（P0/P1/P2）

- **P0**：数据错误 / 安全漏洞 / 内存泄漏 / 生产崩溃 / Trace 丢失 → 立即修
- **P1**：God Object / 长函数 / 重复代码 / 强耦合 → 建议修
- **P2**：命名 / 注释 / 小型重构 → 不为 P2 改大量稳定代码

## Python 核心

- snake_case + 类型注解 + logger（不用 print）
- 具体异常类型
- 参数化 SQL

## React 核心

- `"use client"` 仅在 useState / useEffect / 浏览器 API / SSE / WebSocket
- API 按域 import：`@/lib/api/<domain>`（不直接 import `lib/api.ts`）
- Toast 用 `useToast()`，禁 `alert()` / `confirm()`

## Commit 格式

```text
type(scope): 中文 subject
```

- type: feat / fix / refactor / docs / test / perf / chore
- scope: rag / frontend / observability / chat / memory ...

## 分支策略

- `master`：稳定主分支
- `feat/*` / `fix/*`：从 master 开，本地 squash → master
- 不直接在 master 改（hotfix 除外）

## 修改流程

1. 读相关代码 → 查 MCP `code-review-graph` 影响范围
2. 改完跑 `npx tsc --noEmit` + `npm test` + `detect_changes_tool`
3. **人工验证**：用户手动浏览器操作一遍才算通过
4. 输出格式见 `docs/development/output-format.md`

## MCP 工具（code-review-graph）

- `detect_changes_tool` — 改完必跑
- `get_affected_flows` — 风险 ≥0.7 强制追加

## 详细规范索引

| 主题 | 位置 |
|------|------|
| 模块职责 / 目录 | `docs/architecture/structure.md` |
| DTO / DB / State / Prompt | `docs/architecture/*.md` |
| 测试 / 重构 / 优先级 / 输出 | `docs/development/*.md` |
| 启动命令 | `docs/operations/commands.md` |
| Trace 模型 / 可观测性 / Mock | `docs/observability/*.md` |