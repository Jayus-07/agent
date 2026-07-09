# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## 项目简介

基于 LangChain + LangGraph 的 RAG + Multi-Agent 智能问答与报告系统。FastAPI 提供 REST API，Next.js 前端通过 SSE 流式消费 Multi-Agent 工作流进度。PostgreSQL + pgvector 提供企业级记忆持久化。内置 CPU 保护机制（Ollama 线程限制 + API 并发控制），防止笔记本/低配机器过载关机。支持 Docker Compose 一键部署全栈。

**核心子系统**：Multi-Agent 编排（Planner→Supervisor→Workers→Reporter）、RAG 知识库检索、SQL 安全 Agent、报告生成、企业级三段记忆。

---

## 开发原则

1. **稳定优先于优雅** — 系统已通过 P0 修复进入稳定开发阶段，不要为了"更优雅"而改代码
2. **修 Bug 而不是改架构** — 遇到问题先想"哪里错了"，不要想"怎么重构"
3. **小步快跑** — 单次改动不超过一个文件的最少行数
4. **文档先于实现** — 复杂改动先写设计文档到 `docs/`，再改代码
5. **可观测** — 新功能必须有日志、错误处理、测试

---

## Working Rules

修改代码前**必须**：

- 先阅读相关代码（用 Read / Grep 探索，不靠记忆或猜测）
- 不允许猜测实现 — 不确定时先 Read 文件确认
- 修改前说明原因（"为什么要改"放在 commit message / 汇报里）
- 修改后跑对应测试（详见 `常用命令`）
- 更新文档（如有必要）— 修改架构、API、新增模块时

修改代码后**必须**：

- 汇报"改了什么、为什么、影响范围、测试结果"
- 标注"可能回归的点"（如有）
- 如果发现"顺便可以重构的地方"，**不要**直接做，先记下来

---

## Stability Rules

进入稳定开发阶段。

**除非用户明确要求**：`refactor` / `architecture` / `cleanup`，**否则禁止**：

- ❌ 大规模重构
- ❌ 随意拆文件
- ❌ 修改公共接口
- ❌ 修改目录结构
- ❌ 为了"更优雅"而改代码

**允许**：

- ✅ 修 Bug
- ✅ 新增功能
- ✅ 删除 Dead Code
- ✅ 删除无效日志
- ✅ 增加测试

**新增功能 / 修 Bug 的额外限制**：

- 保持现有架构约束（ToolRegistry、MemoryService、reducer 等）
- 任何改动必须能解释"为什么以前不这么做"
- 跨文件改动要列影响范围

---

## Code Quality

新增代码**必须**：

- ✅ 单一职责 — 一个文件 / 函数只做一件事
- ✅ 不保留 `TODO` / `FIXME` 注释 — 真正要做就开 issue + 创建任务
- ✅ 不保留 `print()` 调试 — 用 `utils.logger.logger`
- ✅ logger 使用统一封装 — `from utils.logger import logger`
- ✅ 新增模块必须有测试 — 放 `tests/test_*.py`
- ✅ 修改公共 API 时同步更新类型注解 / 文档
- ✅ 通过现有 import 冒烟测试（详见 `常用命令` → 测试）

---

## Coding Rules

### Python

- 路径：用正斜杠 `/`（`from memory/service.py`）
- 导入：项目根已在 `sys.path`，各模块直接 `from config import ...`
- 类型注解：函数签名必须带类型（参数 + 返回值）
- 错误处理：用具体异常类，不用 `except Exception: pass`（finally 清理除外）
- 异步：`asyncio` + `asyncpg`；`MemoryManager` 是同步桥，禁止在 async 路径用 sync API
- 配置：`config.py` 统一管理默认值，`.env` 覆盖敏感值
- LLM 切换：`from llm.llm_factory import llm` 是 `_LLMProxy`，**所有调用方零修改**就能响应切换

### TypeScript / React

- 单一职责组件（一个文件 < 250 行）
- 状态：Zustand store 用于全局，useState 限于组件内
- SSE：统一通过 `useSSE` hook（带 AbortController）
- API：所有 fetch 走 `lib/api.ts`（不直接 fetch）

### 命名

- 模块：`snake_case`
- 类：`PascalCase`
- 函数 / 变量：`snake_case`
- 常量：`UPPER_SNAKE_CASE`
- 前端组件：`PascalCase.tsx`

---

## 项目目录

```
agent/                         # 项目根
├── api/                       # FastAPI 入口、路由、依赖注入
│   ├── server.py              # 启动入口
│   ├── deps.py                # 惰性单例
│   ├── schemas.py             # Pydantic 模型
│   └── routes/                # chat / sql / rag / report / llm / observability
├── config.py                  # 全局配置（环境变量 + 默认值）
├── llm/                       # LLM 工厂（多 Provider + 运行时切换）
│   └── llm_factory.py         # _LLMProxy + LLMFactory
├── multi_agent/               # LangGraph 工作流
│   ├── graph.py               # 编译图 + SSE 流
│   ├── planner.py             # 任务规划（DAG）
│   ├── supervisor.py          # 调度 + 降级
│   ├── reporter.py            # 汇总 + Context Filter
│   ├── state.py               # AgentState + reducers
│   ├── tool_registry.py       # capability → worker 映射
│   └── workers/               # sql_worker / rag_worker / report_worker
├── sql_agent/                 # SQL 安全 Agent（6 层校验）
│   ├── sql_agent.py           # 主编排
│   ├── sql_validator.py       # 6 层校验
│   ├── row_security.py        # 行级安全（参数化）
│   ├── executor.py            # 只读事务执行
│   ├── schema_loader.py       # Schema 加载
│   └── data/schema_config.py  # 唯一真相源
├── report_agent/              # 报告生成
│   ├── report_generator.py    # 主编排
│   ├── template_engine.py     # Jinja2 模板
│   ├── chart_generator.py     # matplotlib 图表
│   ├── llm_polisher.py        # LLM 润色（带硬校验）
│   ├── data_fetcher.py        # 数据获取（SQL/API）
│   ├── preference.py          # 用户偏好
│   └── snapshot.py            # 快照持久化
├── retrieval/                 # RAG 检索
│   ├── pipeline.py            # 主入口
│   ├── knowledge_store.py     # KnowledgeStore 抽象（Chroma → pgvector 可切换）
│   ├── doc_registry.py         # DocumentRegistry（SQLite 文档元数据）
│   ├── indexer.py              # IncrementalIndexer（增量索引 SHA256 diff）
│   ├── chain.py               # LCEL 链
│   ├── retrievers.py          # Chunk/Adaptive Retriever
│   ├── hybrid.py              # 混合排序（BM25 + 向量）
│   ├── reranker.py            # CrossEncoder
│   ├── context.py             # Request-scoped contextvars
│   └── query_analyzer.py      # Query 意图分析
├── memory/                    # 三层记忆
│   ├── service.py             # 统一入口（Agent 唯一接入点）
│   ├── manager.py             # sync 桥（持久后台 loop）
│   ├── short_term.py          # L1 环形缓冲
│   ├── session.py             # L2 PG 持久
│   ├── long_term.py           # L3 pgvector
│   ├── trigger.py             # 写入触发分类
│   ├── importance.py          # 5 维评分
│   ├── retriever.py           # 加权检索
│   ├── decay.py               # 衰减 + 归档
│   ├── pii_filter.py          # PII 脱敏
│   ├── database.py            # async engine
│   ├── repository/            # CRUD 封装
│   └── models/                # ORM
├── seed_data/                  # 种子数据框架（9 领域跨境电商模拟数据）
│   ├── cli.py                  # CLI 入口: python -m seed_data --profile mvp
│   ├── demo_all.py             # 端到端演示脚本
│   ├── core/                   # Generator / Factory / Context / Profile / Validator
│   ├── generators/             # 9 领域生成器（master_data/product/order/customer/...）
│   ├── validators/             # 引用完整性 / 数量级 / 业务规则
│   ├── exporters/              # JSON 文件 / Python dict / PostgreSQL
│   ├── profiles/               # tiny.yaml / mvp.yaml / medium.yaml / full.yaml
│   └── utils/                  # constants / distributions
├── preprocessing/             # 文档预处理
│   ├── loader.py              # 多格式加载
│   ├── chunking.py            # 类型感知分块
│   ├── metadata.py            # 元数据构建
│   ├── keyword.py             # 关键词提取
│   └── entity.py              # 人名提取
├── web/                       # Next.js 前端
│   └── src/                   # 详见 docs/frontend.md
├── tests/                     # pytest 测试
├── utils/                     # 通用工具（logger / timeout / async_utils / resource_monitor）
├── docker/                    # Dockerfile / docker-compose 配置
├── docs/                      # 详细文档（架构 / 子系统说明）
├── start_all.bat              # Windows 一键启动（后端 + 前端）
└── set_ollama_env.ps1         # 设置 Ollama CPU 保护系统环境变量
```

---

## 常用命令

```bash
# ==================== 种子数据 ====================
# 生成跨境电商模拟数据（9 领域，31 实体，tiny/mvp/medium/full 四种规模）
./.venv/Scripts/python.exe -m seed_data --profile tiny --export json --validate
./.venv/Scripts/python.exe -m seed_data --profile mvp --export json --output data/seed/mvp/
./.venv/Scripts/python.exe -m seed_data.demo_all --profile tiny   # 端到端演示

# ==================== 启动服务 ====================
start_all.bat                             # Windows 一键启动（推荐）

python -m api.server                      # 仅启动 FastAPI (端口 8000)
cd web && npm run dev                     # 仅启动 Next.js (端口 3000)

# ==================== 测试 ====================
# 重要：用 venv 站点包（系统 Python 缺 sqlglot / asyncpg / chromadb）
PYTHONPATH=".venv/lib/site-packages" ./.venv/Scripts/python.exe -m pytest tests/ -v --tb=short
PYTHONPATH=".venv/lib/site-packages" ./.venv/Scripts/python.exe -m pytest tests/test_row_security.py -v

# ==================== 演示入口 ====================
./.venv/Scripts/python.exe multi_agent/demo.py
./.venv/Scripts/python.exe sql_agent/demo_sql_agent.py
./.venv/Scripts/python.exe report_agent/demo_report_agent.py

# ==================== 前端构建 ====================
cd web && npx next build                   # TypeScript 校验 + 静态生成

# ==================== 冒烟测试 ====================
# 验证 import 链路
PYTHONPATH=".venv/lib/site-packages" ./.venv/Scripts/python.exe -c "
import config, llm.llm_factory, multi_agent.graph, multi_agent.observability,
       memory.service, retrieval.pipeline, sql_agent.sql_agent, report_agent.report_generator,
       api.server
print('ok')
"

# ==================== 记忆维护 ====================
PYTHONPATH=".venv/lib/site-packages" ./.venv/Scripts/python.exe -c "
import asyncio
from memory.service import MemoryService
asyncio.run(MemoryService().run_decay())
"  # 手动运行衰减（默认未调度）
```

**注意**：
- `MAX_CONCURRENT_REQUESTS=1`（`.env`）：防 CPU 过载
- `LLM_CONTEXT_LENGTH=2048`（`.env`）：上下文减半
- `set_ollama_env.ps1`（管理员权限运行）：Ollama 系统级 CPU 限流

---

## Claude 工作流程

收到任务后按以下顺序：

1. **理解需求** — 复述用户要做什么
2. **探索代码** — Read 相关文件（`docs/` 子系统文档 + 实际代码）
3. **评估分类** — 是 P0 修复 / 新功能 / Bug / Dead Code / 重构？
4. **遵守规则**：
   - 修 Bug：可以改，但说明原因
   - 新功能：在现有架构内（ToolRegistry / MemoryService / etc.）
   - 重构：**先问**，不要自作主张
5. **执行** — 单次改动最小化，避免连带
6. **验证** — 跑对应测试 + import 冒烟
7. **汇报** — 改了什么 / 为什么 / 测试结果 / 影响范围

**禁止**：
- ❌ 一次性改 5+ 个文件
- ❌ "顺便" 改无关代码
- ❌ 不写测试就声称"修好了"
- ❌ 把 docs 写完但代码不跑
- ❌ 修改 public API（`_` 开头的私有符号 / `__all__` 中的名字）

---

## 修改代码前后的要求

### 修改前

- [ ] 已读相关代码
- [ ] 已查 `docs/` 中相关子系统文档
- [ ] 改动行数心里有数（< 50 行优先）
- [ ] 列出影响范围（哪些文件 / 哪些调用方）

### 修改中

- [ ] 保持现有代码风格（命名 / 缩进 / 注释密度）
- [ ] 不引入新依赖（除非必要）
- [ ] 不删除现有的注释 / 测试（除非明确无用）
- [ ] 错误处理用具体异常
- [ ] 日志用 `utils.logger.logger`

### 修改后

- [ ] 跑相关测试
- [ ] 跑 import 冒烟
- [ ] 前端改动跑 `next build`
- [ ] 汇报改了什么 / 为什么 / 测试结果

---

## 文档索引

详细文档在 `docs/`，按需阅读：

| 文档 | 何时阅读 |
|---|---|
| [docs/architecture.md](docs/architecture.md) | 理解系统整体架构、请求生命周期、模块依赖 |
| [docs/multi-agent.md](docs/multi-agent.md) | 修改 LangGraph 节点、Worker、State 时 |
| [docs/rag.md](docs/rag.md) | 修改 RAG 检索、向量库、BM25、Reranker 时 |
| [docs/sql-agent.md](docs/sql-agent.md) | 修改 SQL 生成、校验、行级安全时 |
| [docs/report-agent.md](docs/report-agent.md) | 修改报告生成、模板、图表、润色时 |
| [docs/memory.md](docs/memory.md) | 修改记忆系统、Service / Manager 桥、pgvector 时 |
| [docs/frontend.md](docs/frontend.md) | 修改 Next.js 前端、组件、SSE 流、Zustand 时 |
| [docs/deployment.md](docs/deployment.md) | 部署、Docker、CPU 保护、环境变量时 |

**业务领域 + 种子数据**：

| 文档 | 何时阅读 |
|---|---|
| [docs/business-domain.md](docs/business-domain.md) | 理解跨境电商 9 领域业务模型、实体关系、PR 路线 |
| [docs/superpowers/specs/2026-07-02-seed-framework-design.md](docs/superpowers/specs/2026-07-02-seed-framework-design.md) | 修改种子数据框架、新增加 Generator 时 |

历史设计文档在 `docs/superpowers/`（规格 / 实施计划，已完成），如需了解"为什么这样设计"。

---

## MCP 工具链

`.mcp.json` 配置了：

- **puppeteer**：浏览器自动化（截图 / 爬取）
- **filesystem**：文件系统操作

权限在 `.claude/settings.local.json` 配置（`mcp__puppeteer__*` / `mcp__filesystem__*`），需重启会话生效。
