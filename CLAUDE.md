# CLAUDE.md

## Project

电商 RAG + Multi-Agent 平台（FastAPI + Next.js + LangGraph）

---

## 目录

- [Architecture](#architecture) — 两条调用路径 + 模块角色
- [Structure](#structure) — 后端 / 前端目录约定
- [Rules](#rules) — 修改前/后 + 人工验证 + Commit 格式 + 分支策略
- [Quick Templates](#quick-templates) — 新增 API / 组件 / Skill 的流程
- [MCP Tools](#mcp-tools) — code-review-graph 使用规则
- [Quality](#quality) — SOLID / DRY / 长度阈值
- [Coding](#coding) — Python / React 风格
- [Data Rules](#data-rules) — Config / State / DTO / DB
- [Priority](#priority) — P0/P1/P2 判断
- [Hooks](#hooks) — Stop hook 配置
- [Commands](#commands) — 启动 / 测试命令
- [Docs](#docs) — 可观测性文档链接

> **规则标签**：每条规则用 `[硬]` / `[软]` / `[风]` 标记
> - `[硬]` 硬约束 —— 违反必须修
> - `[软]` 软建议 —— 默认遵守，可有例外
> - `[风]` 风格 —— 可读性偏好

---

## Architecture

**两条调用路径，按任务复杂度分流**：

```
简单操作:  API → rag / sql / memory          （直连，无调度开销）
复杂任务:  API → orchestration → planner → supervisor → skills → tools → rag/sql/business_report
                                                            └─ reporter（汇总）→ 最终回答
```

**模块 → 架构角色映射**：

| 目录 | 角色 | 说明 |
|---|---|---|
| `app/` | API 入口 | FastAPI: routes → deps → orchestration 或子系统 |
| `orchestration/planner/` | Planner | 任务拆解为 DAG，只输出 capability 不调 tool |
| `orchestration/supervisor/` | Supervisor | 调度、降级、告警 |
| `orchestration/skills/` | Skill | 组合 Tool，统一接口（rag/sql/report） |
| `orchestration/tools.py` | Tool | 封装子系统为 LangChain Tool |
| `orchestration/reporter/` | Reporter | 汇总 step_results → LLM → Markdown |
| `rag/` `sql/` `business_report/` `memory/` | 能力层 | 可独立调用，也可被 orchestration 调度 |

**设计原则**：

| 规则 | 说明 |
|---|---|
| **禁止 thin wrapper** | 每个模块必须有真实业务逻辑，禁止仅 re-export 的转发文件 |
| **禁止 `misc.py` / `helper.py` / `common.py`** | 无语义文件名 |
| **Prompt 与代码分离** | 放 `prompts/` 目录 |
| **LLM 调用统一** | `from backend.infra.llm import llm`（代理，自动跟随切换） |
| **Tool 统一接口** | `@tool` 装饰 → `tool_registry` 注册 → Skill 调用 |
| **Planner 不直接调 Tool** | Planner 只输出 capability，由 Supervisor + ToolRegistry 解析 |
| **Tool 调用记录** | Prompt / 模型 / Token / 耗时 / 错误 |
| **禁止越层跳过编排** | 复杂多步任务必须经 orchestration，简单单步操作可直连子系统 |

---

## Structure

```
agent/
├── frontend/              # Next.js（详见下方"前端分层"）
├── backend/
│   ├── app/               # FastAPI: server.py / deps.py / api/routes/
│   ├── config/            # 配置（按模块拆分）
│   ├── infra/llm/         # LLM 基础设施（factory / providers / proxy）
│   ├── orchestration/     # 编排：planner / supervisor / skills / reporter / graph
│   ├── rag/               # RAG：indexing / retrieval / vectorstore / preprocessing
│   ├── sql/               # SQL Agent
│   ├── memory/            # 三层记忆（短期/会话/长期）
│   ├── business_report/   # 业务报告生成（SQL/API → Jinja2 → LLM → Markdown）
│   ├── data_collection/   # 数据采集与治理
│   ├── evaluation/        # 评测框架
│   ├── mcp/               # MCP 集成
│   ├── prompts/           # Prompt 模板（与代码分离）
│   ├── seed/              # 种子数据生成
│   └── shared/            # 共享工具（logger / exceptions / monitoring）
├── docs/  start_all.bat  stop_all.bat  restart_all.bat  .env
└── .venv/  data/  logs/  requirements.txt
```

**`backend/data` 是 Windows Junction → `../data`，后端必须从 `backend/` 目录启动。**

**前端分层（`frontend/src/`）**：

| 路径 | 角色 | 说明 |
|---|---|---|
| `app/` | Next.js 路由 | 按模块分：agent / observability / knowledge / data-source / data-pipeline |
| `components/<模块>/` | 业务组件 | 每个模块独立目录（如 `observability/trace/`） |
| `components/shared/` | 原子组件 | EmptyState / ErrorState / Skeleton / Toast — **禁止复制，每个页面必须用** |
| `lib/fetcher.ts` | 底层 fetch | 统一 JSON、超时、`ApiError` |
| `lib/sse-parser.ts` | SSE 解析器 | 纯函数 async generator，可独立测 |
| `lib/api/<模块>.ts` | 业务 API | 按域拆：chat / llm / memory |
| `lib/api.ts` | 兼容层 | **仅 re-export**，新代码禁止直接 import |
| `hooks/` | React Hooks | SSE / 数据获取等可复用 hooks |
| `store/` | Zustand | 全局状态（chat 等） |
| `types/` | 类型 + 测试 | 按域拆的 TypeScript 类型 + `*.test.ts` 单测 |
| `mock/` | 静态 mock | 未接 API 时的占位数据 |

**禁止**：`misc.py` / `helper.py` / `common.py` / `utils2.py` 等无语义文件。

---

## Rules

**修改前**：
- `[硬]` Read 相关代码，不猜实现
- `[硬]` 查 MCP `code-review-graph` 影响范围：
  - 先 `detect_changes_tool`（看风险评分、受影响函数、test gap）
  - 高风险（≥0.6）或跨模块改动时追加 `get_affected_flows` + `get_impact_radius`
  - 新增文件后 `build_or_update_graph_tool`（增量或全量，让图谱跟上当前代码）
- `[硬]` 最小修改、保持现有架构、不改 Public API

**Commit 消息格式**（Conventional Commits 变体）：

**Commit 消息格式**（Conventional Commits 变体）：

```
<type>(<scope>): <subject 中文标题>

<body 详细说明（改了什么 / 为什么 / 影响范围 / 测试结果）>

<footer 关联 issue 或 break change>
```

`<type>`：`feat` / `fix` / `refactor` / `docs` / `chore` / `test` / `perf`
`<scope>`：模块名（`rag` / `frontend` / `observability` / `docs` ...）
- Subject 中文，≤50 字
- Body 多行，列点说明
- 示例：
  ```
  feat(rag): Span 数据模型 + RAG pipeline 全面 Span 化

  - tracer.py：TraceStep (扁平) → Span (树形 + parent_id + type)
  - chain.py：root span + 8 个子 span
  - observability.py：_to_span_dto + _to_trace_dto
  ```

**分支策略**：
- `master`：稳定主分支，CI 必须通过
- `feat/*` / `fix/*`：功能/修复分支，从 master 开
- 合并方式：本地 squash commit → master
- 不直接在 master 上改代码（除非 hotfix）
- Push 前：`npm test` / `tsc --noEmit` / `git diff` 检查范围

**修改后**：
- 跑真实测试
- 再跑一次 `detect_changes_tool` 验证实际影响范围与预期一致
- 汇报：改了什么 / 为什么 / 影响范围 / 测试结果

**人工验证（不可省略）**：
- 每次功能验证（无论 Claude 用 curl/puppeteer/E2E 测得多彻底）**不算结束**
- 必须由用户**手动在浏览器/前端操作一遍**才算完成
- 验证流程：Claude 给出测试步骤清单 → 用户执行 → 用户确认通过/失败
- 目的：避免只通过自动化路径（绕过 UI、跳过真实交互）导致的隐性 bug
- 适用于：UI 交互、按钮点击、表单输入、上传流程、SSE 流等所有前端可触及的功能

**修改原则**（按优先级）：
1. 业务逻辑一致
2. API 向后兼容
3. 影响范围最小
4. 优先降低耦合
5. 收益 > 成本 > 风险（If it isn't broken, don't fix it）

**每次完成开发后输出**：
- Development Summary（完成内容 / 影响模块 / 兼容性 / 风险）
- Test Coverage（新增/修改的纯函数是否有 `*.test.ts` 覆盖，测试是否通过）
- Code Review（评分 + P0/P1/P2 问题 + 修改建议）
- Architecture Assessment（SOLID/DRY/KISS/YAGNI 符合度）

---

## Quick Templates

新增功能的标准流程（按类型走对应模板）：

### A. 新增后端 API endpoint

```
1. backend/app/api/routes/<domain>.py 加 @router.get/post/...
2. 若需新 Pydantic 模型 → backend/app/api/schemas.py
3. 若需 DB 访问 → 走 Repository → Service → API 三层
4. 若需配置 → backend/config/<domain>.py 加常量
5. 检测：detect_changes_tool 看影响范围 + test gap
6. 测试：curl 端到端验证（带 4 开关组合：正常/异常/边界/降级）
7. 提交：commit message 含 endpoint 路径 + DTO 字段
```

### B. 新增前端组件

```
1. 业务组件 → frontend/src/components/<module>/<Name>.tsx
2. 原子组件 → frontend/src/components/shared/<Name>.tsx（先看现有是否复用）
3. 若是展示型组件（无业务逻辑）→ 加 *.test.tsx 单测
4. 类型定义 → frontend/src/types/<domain>.ts 复用/扩展
5. 若是 API 调用 → @/lib/api/<domain>.ts 加函数，不用裸 fetch
6. 若是页面 → frontend/src/app/<module>/<page>/page.tsx
7. 检测：detect_changes_tool
8. 测试：npx tsc --noEmit + npm test + 浏览器实测（人工验证不可省）
9. 提交
```

### C. 新增 Skill（orchestration 扩展能力）

```
1. backend/orchestration/skills/<capability>/
   ├── __init__.py
   └── skill.py      # 继承 BaseSkill，实现 execute()
2. tools.py 加 @tool 装饰函数
3. tool_registry.py 注册 capability → skill 映射
4. 提示词 → backend/prompts/<capability>.md（不放代码里）
5. 测试：单元测 BaseSkill.execute() 路径 + 集成测 MultiAgent 调用
6. 检测：detect_changes_tool（tool_registry + skills + tools 跨模块）
7. 提交
```

### D. 修改 DTO（前端 TraceRecord / Span 等）

```
1. 后端改 backend/rag/tracer.py + observability.py 的 _to_*_dto
2. 前端 frontend/src/types/trace.ts 同步
3. 检查所有使用方：search 前端 grep '<FieldName>'
4. 老字段保留为 optional（?. 安全访问），新字段加默认值
5. 前端加 *.test.ts 单测覆盖新字段映射
6. 检测：detect_changes_tool 看后端→前端→所有消费方
7. 提交
```

---

## Quality

**原则**：SOLID / DRY / KISS / YAGNI / 高内聚 / 低耦合 / 组合优于继承

**禁止**：过度设计、为抽象而抽象、无意义拆分类/函数

**长度阈值**：
- 函数：建议 20-50 行，> 100 行评估拆分
- 类：建议 300-500 行，> 800 行 Review，> 1000 行拆分

**命名**：表达职责（`DocumentManager` / `RetrieverService` / `MemoryRepository`）
避免：`helper` / `common` / `test2` / `new_manager` / `final_v2`

**Magic Number/String**：必须定义常量（如 `DEFAULT_KB` / `MAX_RETRY`），禁止硬编码 `"default"` / `3` / `300` 等。

---

## Coding

> 规则标签说明：`[硬]` 必须遵守 / `[软]` 默认遵守可有例外 / `[风]` 可读性偏好

**Python**

- `[硬]` snake_case、类型注解、logger（不用 print）
- `[硬]` 优先具体异常（`IOError` / `ValueError` / `TimeoutError`），自定义业务异常（`ResourceLimitError` / `DocumentNotFound`）
- `[软]` `except Exception: pass` 在生产路径禁止；测试 fixtures / 顶层 fallback handler 可有但必须打日志
- `[硬]` Import 默认文件顶部（仅循环依赖/懒加载/重量级依赖放函数内）
- `[硬]` 配置集中管理，按模块拆分（`config/llm.py` / `config/rag.py`），禁止一个 `config.py` 管所有
- `[硬]` SQL 必须参数化查询，禁止拼接
- `[软]` 日志含 Request ID / Session ID / 耗时 / 模块名；不用大量无意义 INFO

**React**

- `[硬]` API 按域 import：`@/lib/api/chat` / `@/lib/api/llm` / `@/lib/api/memory`（**不直接 import `lib/api.ts`**）
- `[软]` 优先用 `@/lib/fetcher` 的 `request<T>()`；**SSE 流式** / **EventSource** / **FormData 上传** 可用裸 `fetch`（fetcher 不支持流式响应）
- `[硬]` SSE 用 `useSSE` hook + `@/lib/sse-parser`（chat 流式对话已封装）
- `[硬]` 原子组件统一 `@/components/shared/{EmptyState,ErrorState,Skeleton,Toast}`，禁止各页面自造
- `[硬]` Toast 通知用 `useToast()`（已内置 provider），**禁止 `alert()` / `confirm()`**（用 modal 替代）
- `[软]` 全局状态用 Zustand，按域拆 store（避免单 store 臃肿）
- `[风]` 函数组件 + Hooks，避免 class component

**TypeScript**

- `[硬]` 新增/修改纯函数必须有 `*.test.ts` 单测覆盖
- `[硬]` `npx tsc --noEmit` 必须 0 错误
- `[软]` 命名：组件 PascalCase，hooks/use* 开头，类型/接口 PascalCase，工具函数 camelCase

---

## MCP Tools

`code-review-graph` MCP server（项目级，必用工具）：

| 工具 | 何时用 |
|------|--------|
| `detect_changes_tool` | 改完代码后，自动找 git diff 中的受影响函数/风险评分/test gap |
| `get_affected_flows` | 改动涉及 call chain 时，确认是否断流 |
| `get_impact_radius` | 高风险改动（≥0.6）或跨模块时，扩展 blast radius |
| `build_or_update_graph_tool` | 新增/删除文件后，刷新图谱 |
| `list_graph_stats` | 启动时确认图谱健康 |

**使用规则**：
- 每次 commit 前必跑 `detect_changes_tool`，对照预期影响范围
- 工作流入口：`get_minimal_context_tool` 可先看 100-token 概览
- 风险评分 ≥ 0.7 强制追加 `get_affected_flows` 验证
- 新文件后 24 小时内必须 `build_or_update_graph_tool`

---

## Config Rules

所有配置统一放 config/。

禁止业务代码直接：

os.getenv(...)
os.environ[...]

统一通过配置对象读取。

禁止：

- Magic String
- Magic Number
- 重复配置

配置修改不得影响 Public API。

## Workflow State Rules

Workflow State 必须使用 TypedDict 或 Pydantic。

Node：

输入：
WorkflowState

输出：
WorkflowState

禁止：

state["xxx"] = ...

动态增加未知字段。

State 修改必须可预测、可序列化。

## DTO Rules

API 与数据库模型解耦。

禁止：

API → ORM → JSON

统一：

ORM
↓

DTO(Pydantic)
↓

Response

API Response 必须稳定，数据库结构允许演进。

## Database Rules

数据库访问统一：

Repository
↓

Service
↓

API

禁止：

API

↓

SQLAlchemy Session

↓

SQL

所有 SQL：

- 参数化查询
- 禁止字符串拼接
- Repository 不包含业务逻辑

## Priority

| 级别 | 类型 | 处理 |
|---|---|---|
| **P0** | Bug / 安全漏洞 / 数据错误 / 资源泄漏 | 必须立即修复 |
| **P1** | God Object / 重复代码 / 长函数 / Magic String | 建议修复 |
| **P2** | 命名 / 注释 / 小型重构 | 长期优化，不为 P2 改大量稳定代码 |

---

## Hooks

`scripts/check_code_changes.sh` — Stop hook：检测未提交的 .py/.ts/.tsx 变更，**30 分钟内仅提醒一次**。

防止因文件 mtime 持续满足条件而无限触发。

**个人安装**（写入 `.claude/settings.local.json`）：

```json
{
  "hooks": {
    "Stop": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "bash \"<repo>/scripts/check_code_changes.sh\"",
        "timeout": 10
      }]
    }]
  }
}
```

脚本自动用 `git rev-parse --show-toplevel` 定位仓库根，无硬编码路径，跨机器/跨平台可用。

---

## Commands

| 操作 | 命令 |
|---|---|
| 一键启动 | `start_all.bat` |
| 一键停止 | `stop_all.bat` |
| 重启 | `restart_all.bat` |
| 仅后端 | `cd backend && ..\.venv\Scripts\python.exe -m uvicorn app.server:app` |
| 仅前端 | `cd frontend && npm run dev` |
| 前端测试（单次） | `cd frontend && npm test` |
| 前端测试（watch） | `cd frontend && npm run test:watch` |
| 前端测试覆盖率 | `cd frontend && npm run test:coverage` |
| API 文档 | http://localhost:8000/docs |
| 前端 | http://localhost:3000 |
| MCP 工具 | http://localhost:8000/mcp/tools |

---

## Docs

- [前端可观测性 Mock 数据系统](docs/observability/mock-as-api-contract.md) — 27 条 Trace / 12 workflow / Span 模型 / mergeTrace 逻辑
- [可观测性后端增强方案](docs/observability/backend-enhancement-plan.md) — 数据够用性分析 + 6 Phase 实施计划

架构/模块说明请直接参考代码与 README。（项目演进中文档容易过时，代码即文档。）