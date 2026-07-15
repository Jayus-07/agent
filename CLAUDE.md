# CLAUDE.md

## Project

跨境电商 RAG + Multi-Agent 平台（FastAPI + Next.js + LangGraph）

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
├── frontend/              # Next.js
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

**禁止**：`misc.py` / `helper.py` / `common.py` / `utils2.py` 等无语义文件。

---

## Rules

**修改前**：
- Read 相关代码，不猜实现
- 最小修改、保持现有架构、不改 Public API

**修改后**：
- 跑真实测试
- 汇报：改了什么 / 为什么 / 影响范围 / 测试结果

**修改原则**（按优先级）：
1. 业务逻辑一致
2. API 向后兼容
3. 影响范围最小
4. 优先降低耦合
5. 收益 > 成本 > 风险（If it isn't broken, don't fix it）

**每次完成开发后输出**：
- Development Summary（完成内容 / 影响模块 / 兼容性 / 风险）
- Code Review（评分 + P0/P1/P2 问题 + 修改建议）
- Architecture Assessment（SOLID/DRY/KISS/YAGNI 符合度）

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

**Python**
- snake_case、类型注解、logger（不用 print）
- 异常：优先具体异常（`IOError` / `ValueError` / `TimeoutError`），自定义业务异常（`ResourceLimitError` / `DocumentNotFound`），禁止 `except Exception: pass`
- Import 默认文件顶部（仅循环依赖/懒加载/重量级依赖放函数内）
- 配置集中管理，按模块拆分（`config/llm.py` / `config/rag.py`），禁止一个 `config.py` 管所有
- SQL 必须参数化查询，禁止拼接
- 日志含 Request ID / Session ID / 耗时 / 模块名；不用大量无意义 INFO

**React**
- API 统一 `lib/api.ts`、SSE 用 `useSSE`、全局状态用 Zustand

---

## Priority

| 级别 | 类型 | 处理 |
|---|---|---|
| **P0** | Bug / 安全漏洞 / 数据错误 / 资源泄漏 | 必须立即修复 |
| **P1** | God Object / 重复代码 / 长函数 / Magic String | 建议修复 |
| **P2** | 命名 / 注释 / 小型重构 | 长期优化，不为 P2 改大量稳定代码 |

---

## Commands

| 操作 | 命令 |
|---|---|
| 一键启动 | `start_all.bat` |
| 一键停止 | `stop_all.bat` |
| 重启 | `restart_all.bat` |
| 仅后端 | `cd backend && ..\.venv\Scripts\python.exe -m uvicorn app.server:app` |
| 仅前端 | `cd frontend && npm run dev` |
| API 文档 | http://localhost:8000/docs |
| 前端 | http://localhost:3000 |
| MCP 工具 | http://localhost:8000/mcp/tools |

---

## Docs

暂无独立文档。架构/模块说明请直接参考代码与 README。
（项目演进中文档容易过时，代码即文档。）

---

## 待办

- 记忆模块（等用户说）