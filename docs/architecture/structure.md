# 模块职责与目录

## 后端（`backend/`）

| 目录 | 职责 |
|------|------|
| `app/` | FastAPI 入口：server.py / deps.py / api/routes/ |
| `orchestration/` | 多 Agent 编排（planner / supervisor / skills / reporter / graph） |
| `rag/` | RAG 检索（indexing / retrieval / vectorstore / preprocessing） |
| `sql/` | SQL Agent |
| `memory/` | 三层记忆（短期/会话/长期） |
| `business_report/` | 报告生成 |
| `data_collection/` | 数据采集与治理 |
| `mcp/` | MCP 集成 |
| `prompts/` | Prompt 模板（与代码分离） |
| `seed/` | 种子数据生成 |
| `shared/` | 共享工具（logger / exceptions / monitoring） |
| `config/` | 配置（按模块拆分） |
| `evaluation/` | 评测框架 |
| `infra/llm/` | LLM 基础设施（factory / providers / proxy） |

## 前端（`frontend/src/`）

| 路径 | 角色 |
|------|------|
| `app/` | Next.js 路由（按模块分） |
| `components/<模块>/` | 业务组件 |
| `components/shared/` | 原子组件（EmptyState / ErrorState / Skeleton / Toast）— **必复用** |
| `lib/fetcher.ts` | 底层 fetch 抽象 |
| `lib/sse-parser.ts` | SSE 解析器（纯函数 async generator） |
| `lib/api/<domain>.ts` | 业务 API（按域拆：chat / llm / memory / observability） |
| `lib/api.ts` | 兼容层（**仅 re-export**，新代码禁直接 import） |
| `hooks/` | React Hooks（SSE / 数据获取等） |
| `store/` | Zustand 全局状态（按域拆） |
| `types/` | TypeScript 类型 + `*.test.ts` 单测 |
| `mock/` | 静态 Mock 数据 |

## 禁止

- 无业务逻辑的薄文件（`misc.py` / `helper.py` / `common.py` / `utils2.py`）
- 后端 `backend/config.py` 单文件管所有配置

## 数据约定

- `backend/data` 是 Windows Junction → `../data`，后端必须从 `backend/` 目录启动