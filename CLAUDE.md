# CLAUDE.md

## Project

跨境电商 RAG + Multi-Agent 平台（FastAPI + Next.js + LangGraph）

架构: Planner → Supervisor → Skills(RAG/SQL/Report) → Reporter

---

## Architecture

**分层（不允许越层调用）**：

```
API → Planner → Capability → Skill → Tool → Repository → Storage
```

**职责划分**：

| 角色 | 职责 | 不应做 |
|---|---|---|
| Pipeline | 编排流程、调度模块 | DB / Embedding / Prompt |
| Manager | 资源调度 | 业务流程 |
| Service | 业务逻辑 | 数据访问 |
| Repository | 数据访问 | 业务逻辑 |
| Factory | 对象创建 | 业务逻辑 |
| Builder | 复杂对象组装 | 业务逻辑 |

**AI Agent 专项**：
- Prompt 与代码分离，放 `prompts/` 目录
- 模型调用统一经 `ModelManager` / `LLMProvider`
- Tool 必须经统一接口；Skill 组合 Tool，Capability 对外暴露
- Planner 只调度 Capability，不直接调 Tool
- Tool 调用记录：Prompt / 模型 / Token / 耗时 / 错误

---

## Structure

```
agent/
├── frontend/         # Next.js
├── backend/
│   ├── app/          # server.py / deps.py / api/routes/
│   ├── rag/  agent/  llm/  sql/  report/  memory/
│   ├── mcp/          # Phase 5: MCP 集成
│   └── utils/
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