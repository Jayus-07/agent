# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

基于 LangChain + LangGraph 的 RAG + Multi-Agent 智能问答与报告系统。FastAPI 提供 REST API，Next.js 前端通过 SSE 流式消费 Multi-Agent 工作流进度。PostgreSQL + pgvector 提供企业级记忆持久化。内置 CPU 保护机制（Ollama 线程限制 + API 并发控制），防止笔记本/低配机器过载关机。支持 Docker Compose 一键部署全栈。

## 常用命令

```bash
# ==================== 首次配置 ====================
# 限制 Ollama CPU 占用（防过载关机，以管理员身份运行）
powershell -File set_ollama_env.ps1       # 设置系统环境变量 + 重启 Ollama 服务
# 手动设置（如果脚本执行失败）：
[Environment]::SetEnvironmentVariable("OLLAMA_NUM_THREADS", "4", "Machine")
[Environment]::SetEnvironmentVariable("OLLAMA_NUM_PARALLEL", "1", "Machine")

# ==================== 环境 ====================
pip install -r requirements.txt           # 安装 Python 依赖
cd web && npm install && cd ..            # 安装前端依赖

# ==================== 数据库 ====================
# PostgreSQL 18 本地服务 (端口 5432)
PGPASSWORD=123456 psql -h localhost -U postgres -d demo -f memory/migrations/001_init.sql

# ==================== 启动服务 ====================
# 方式 1: 启动脚本（推荐，自动检查环境 + Ollama + 向量库）
.\start.ps1                               # Windows PowerShell (后端)
.\start.ps1 -Web                          # Windows PowerShell (后端 + 前端)
.\start.ps1 -Rebuild                      # 重建向量库后启动
bash start.sh                             # Git Bash / Linux / macOS
bash start.sh --web                       # 带前端
start.bat                                 # Windows cmd.exe

# 方式 2: 直接启动
python -m api.server                      # 启动 FastAPI (端口 8000)
uvicorn api.server:app --reload           # 开发模式（热重载）

# 方式 3: Docker 全栈（无需手动配置 Ollama/PostgreSQL）
docker compose up -d                      # 启动全栈 (ollama + postgres + api)
docker compose up -d postgres             # 仅启动 PostgreSQL
docker compose logs -f api                # 查看 API 日志
docker compose down                       # 停止
# 注意: Docker Desktop 29.2.1 CLI 与旧版引擎 API 不兼容时，建议用本地服务

# ==================== 演示入口 ====================
python multi_agent/demo.py               # Multi-Agent 工作流演示
python sql_agent/demo_sql_agent.py       # SQL 安全查询演示
python report_agent/demo_report_agent.py # 报告生成演示

# ==================== 测试 ====================
# 系统 Python (D:\Python\python.exe) 有 pytest 9.0.3，但缺少项目依赖
# .venv 有项目依赖但没有 pytest。用 PYTHONPATH 合并：
PYTHONPATH=".venv/lib/site-packages" python -m pytest tests/ -v --tb=short   # 全部 196 个测试
PYTHONPATH=".venv/lib/site-packages" python -m pytest tests/test_planner.py -v  # 单文件
# PowerShell 注意：需先 $env:PYTHONPATH=".venv/lib/site-packages"

# ==================== 前端 ====================
cd web && npm run dev                     # Next.js 开发服务器 (端口 3000)
cd web && npm run build                   # 生产构建

# ==================== 记忆系统维护 ====================
python -c "import asyncio; from memory.service import MemoryService; asyncio.run(MemoryService().run_decay())"  # 运行衰减

# ==================== API 端点 ====================
# POST /chat          — Multi-Agent 对话（支持 /chat/stream SSE 流式）
# POST /sql           — 自然语言 → 安全 SQL 查询
# POST /rag           — 知识库检索问答
# POST /report        — 自动化报告生成
# GET  /health        — 健康检查
# GET  /docs          — Swagger UI
```

### MCP 工具链

项目配置了 MCP (Model Context Protocol) 服务器，用于截图、文件操作等：

```json
// .mcp.json — 在 Claude Code 中通过 enableAllProjectMcpServers: true 启用
{
  "puppeteer": {   // 浏览器自动化（截图、爬取）
    "type": "stdio",
    "command": "node",
    "args": [".../@modelcontextprotocol/server-puppeteer/dist/index.js"],
    "env": { "PUPPETEER_EXECUTABLE_PATH": "C:\\...\\msedge.exe" }
  },
  "filesystem": {  // 文件系统操作
    "type": "stdio",
    "command": "node",
    "args": [".../@modelcontextprotocol/server-filesystem/dist/index.js",
             "D:\\...\\agent", "D:\\tmp", "...\\Downloads", "...\\Desktop"]
  }
}
```

权限在 `.claude/settings.local.json` 中配置（`mcp__puppeteer__*`, `mcp__filesystem__*`），需重启会话使 MCP 服务器加载生效。

## 核心架构

### 请求生命周期

```
HTTP Request → FastAPI (api/server.py)
  → 惰性初始化 Agent 单例 (api/deps.py)
  → MultiAgentSystem.ask()
    → MemoryManager.start_session()   # L2→L1 恢复 + L3 长期记忆注入
    → LangGraph (Planner→Supervisor→Workers→Reporter)
    → MemoryManager.end_turn()        # L2 持久化 + 触发 L3 后台写入
  → JSON 响应 或 SSE 流
```

### 记忆系统 (Memory)

三层架构，全异步 PostgreSQL + pgvector，统一入口 `MemoryService`：

```
memory/
├── service.py              # MemoryService 统一入口（Agent 唯一接入点）
├── manager.py              # MemoryManager 兼容层（同步包装器，持久事件循环线程）
├── short_term.py           # L1 ShortTermBuffer（环形缓冲区，内存）
├── session.py              # L2 SessionMemory（PG async，替代 SQLite）
├── long_term.py            # L3 LongTermMemory（pgvector，事实提取+检索）
├── trigger.py              # MemoryWorthinessClassifier（规则+LLM：STORE/IGNORE）
├── importance.py           # ImportanceScorer（5维评分 0.0-1.0，阈值 0.6）
├── retriever.py            # HybridRetriever（0.5×sim + 0.3×imp + 0.2×recency）
├── decay.py                # MemoryDecayService（>90d ×0.95, >180d ×0.9, <0.2 归档）
├── dedup.py                # 向量去重（保留）
├── pii_filter.py           # PII 正则过滤器（保留）
├── database.py             # AsyncEngine + AsyncSessionLocal（连接池 20+10）
├── repository/
│   ├── session_repo.py     # chat_sessions + chat_messages CRUD
│   └── memory_repo.py      # memory_records CRUD + pgvector hybrid search
├── models/
│   ├── session.py          # ChatSession, ChatMessage ORM
│   └── memory.py           # MemoryRecord ORM (Vector(512))
└── migrations/
    └── 001_init.sql        # DDL：3 表 + 7 索引
```

L3 写入管线（后台异步，不阻塞主流程）：
```
LLM Extract Facts → PII Filter → Trigger(STORE/IGNORE)
  → Importance Score(≥0.6) → Vector Dedup → pgvector Write
```

**约束**：Agent 禁止直接访问 repository/models，统一通过 `MemoryService`。

### CPU 保护机制 (防过载关机)

针对笔记本/低配机器（如 i7-9750H 等移动 CPU），多层防线防止 LLM 推理触发过热关机：

```
第一层: Ollama 服务端限流
  OLLAMA_NUM_THREADS=4       # 只用 4 个 CPU 线程（留余量给 embedding/reranker）
  OLLAMA_NUM_PARALLEL=1      # 同时只处理 1 个推理请求
  OLLAMA_MAX_LOADED_MODELS=1 # 只加载 1 个模型到内存
  OLLAMA_KEEP_ALIVE=120s     # 2 分钟不活动卸载模型（释放 ~2GB 内存）
  ⚠️ 必须设为系统环境变量后重启 Ollama 才能生效（见 set_ollama_env.ps1）

第二层: API 并发控制 (api/server.py 中间件)
  asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)  # 默认 1，同一时间只处理 1 个请求
  繁忙时返回 503 + Retry-After: 5             # 不排队，避免雪崩
  /health /docs /redoc 路径不受限             # 健康检查和文档始终可用

第三层: LLM 上下文减半
  LLM_CONTEXT_LENGTH=2048    # 从 4096 减半，每次推理计算量减半
```

相关文件：`set_ollama_env.ps1`（一键设置 Ollama 系统环境变量），`api/server.py`（并发控制中间件），`.env`（`MAX_CONCURRENT_REQUESTS`, `LLM_CONTEXT_LENGTH`）。

### Docker 部署

`docker-compose.yml` 编排 3 个服务：ollama（LLM 模型）+ postgres（数据库）+ api（FastAPI）。

```bash
docker compose up -d                    # 启动全栈
# 模型首次需手动拉入 Ollama 容器：
docker compose exec ollama ollama pull qwen2.5:4b
```

关键文件：
```
Dockerfile              # 多阶段构建：torch cpu → requirements → 源码
docker-compose.yml      # 服务编排（ollama + postgres + api）
docker/
├── entrypoint.sh       # 容器入口（等待 PG 就绪 → 迁移 → 启动 uvicorn）
└── init-db.sql         # 数据库初始化 DDL
```

环境变量通过 docker-compose.yml 注入，`OLLAMA_HOST=http://ollama:11434` 指向容器内 Ollama 服务。

### Multi-Agent 工作流 (LangGraph)

```
START → Planner (DAG 规划) → Supervisor (调度)
  → route_after_supervisor 返回 list[Send] → Workers 并行执行
    ├─ SQL Worker   → sql_agent
    ├─ RAG Worker   → retrieval/pipeline.py
    └─ Report Worker → report_agent
  → 结果返回 Supervisor → 循环或 → Reporter → END
```

#### Planner — 任务规划 (`multi_agent/planner.py`)

- 输入用户问题 → LLM 分解为 capability + params 的 DAG（nodes + edges）
- **只输出 capability**，不指定具体 tool；tool 选择由 Supervisor + ToolRegistry 完成
- `_normalize_plan()`：校验 capability 合法性（从 ToolRegistry 获取白名单）、规范化 nodes/edges 结构
- `_filter_plan()`：后置规则过滤器 — 混合计划（SQL + RAG）中，如果问题不含知识库关键词（`_KNOWLEDGE_KEYWORDS` 列表），自动移除冗余 RAG 步骤
- `_fallback_plan()`：空计划兜底 — 自动创建 `search_knowledge` 步骤
- `is_knowledge_question()`：公共函数，Supervisor 也用它判断是否触发 RAG 降级
- `_extract_json()`：从 LLM 输出中提取 JSON（处理 markdown 代码块包裹）

**关键决策**：Planner prompt 包含完整数据库 schema（4 张表）和能力选择指南，区分"纯数据类问题→SQL"和"制度/规范/经验→RAG"，避免知识问题误路由到 SQL。

#### State 与并发合并 (`multi_agent/state.py`)

`AgentState.step_results` 定义为 `Annotated[dict[str, StepResult], _merge_step_results]`，自定义 reducer 处理并行 Worker 并发写入，解决 LangGraph `INVALID_CONCURRENT_GRAPH_UPDATE` 错误。

`StepResult` 包含：`step_id`, `capability`, `description`, `status` (pending/running/success/failed/skipped), `output`, `error`, `retries`, `started_at`, `finished_at`。

#### ToolRegistry (`multi_agent/tool_registry.py`)

capability → worker 映射表（全局单例）：

| capability | worker 节点 |
|---|---|
| `query_database` | `sql_worker` |
| `search_knowledge` | `rag_worker` |
| `generate_report` | `report_worker` |

每个 capability 注册了描述和参数 schema，Planner prompt 从中动态生成。新增能力只需在此添加映射。

#### Supervisor — 调度与降级 (`multi_agent/supervisor.py`)

- `supervisor_node()`：检查依赖（edges），将就绪步骤设为 `running` 并收集到 `_ready_dispatch`
- `route_after_supervisor()`：返回 `list[Send]`（LangGraph 并行执行 Worker）或 `"reporter"`
- `_check_sql_fallback()`：SQL 空结果降级逻辑，**有条件触发**：
  1. 检查计划中是否已有 RAG 步骤（有则不重复添加）
  2. 检查问题是否含知识库关键词（`is_knowledge_question()`，非知识类查询不触发 RAG）
  3. 满足条件则动态注入 `{step_id}_rag_fallback` 步骤

#### Reporter — 汇总 + Context Filter (`multi_agent/reporter.py`)

- LLM 汇总所有 step_results，生成 Markdown 最终回答
- **Context Filter** (`_filter_step_results()`)：对 RAG 步骤输出用 CrossEncoder 以**原始问题**为 query 重新打分，低于阈值 (0.35) 的输出折叠为 `<details>` 并标记过滤
- `_extract_rag_references()`：从 RAG 输出中按文件名去重提取参考文献，追加到 LLM 回答末尾
- `_format_step_outputs()`：格式化步骤输出（剥离参考文献 → 统一追加）
- 降级模式：LLM 调用失败时直接拼接原始输出

#### SSE 流式进度 (`multi_agent/graph.py` — `stream_events()`)

- LangGraph `stream()` 驱动，在 Planner/Supervisor/Worker/Reporter 各节点产出事件
- **去重粒度**：`emitted = set()` 按 `(step_id, status)` 去重（非仅 step_id），保证同一步骤的 running→success/failed 都能发出
- **计时**：事件 data 包含 `started_at`, `finished_at`, `elapsed` 三个字段
- `_yield_step_events()`：从 step_results 提取 executing 事件（running/success/failed/skipped）
- running 状态在 Supervisor 派发时设定（上移到 Supervisor 节点），Worker 完成时更新为 success/failed

关键文件：`multi_agent/graph.py`（编译图 + MultiAgentSystem 类 + SSE 流），`multi_agent/supervisor.py`（调度+降级），`multi_agent/planner.py`（规划+过滤+兜底），`multi_agent/reporter.py`（汇总+Context Filter），`multi_agent/state.py`（状态定义+并发 reducer），`multi_agent/tool_registry.py`（能力注册表）。

### RAG 检索管线

`retrieval/pipeline.py` (RAGPipeline) 是主入口，协调：
向量检索 (ChromaDB) + BM25 关键词检索 → 混合排序 → BGE-Reranker 重排序 → Citation Filter 来源验证 → LLM 生成答案。

模型路径从 `config.py` 读取（环境变量覆盖），首次请求时自动创建向量库。

### SQL 安全 Agent

6 层安全防护：sqlglot 语法校验 → 只读执行器 → 行级安全注入 → 敏感列拦截 → 结果脱敏 → 超时控制。

### 报告生成

`report_agent/` 流程：SQL/API 取数 → Jinja2 模板渲染 → matplotlib 图表 → LLM 仅做语言润色。**数字/事实通过硬校验锁定，不依赖 LLM 承诺。**

### API 层设计

- `api/server.py`：FastAPI 应用入口 + **并发控制中间件**（`asyncio.Semaphore`，默认并发 1，防 CPU 过载）
- `api/deps.py`：所有 Agent 通过惰性单例模式初始化，避免启动时加载全部模型
- `api/schemas.py`：Pydantic 请求/响应模型
- `api/routes/`：chat / sql / rag / report 四个路由模块
- 路由直接调用现有 Agent 模块，**零侵入**设计
- 并发控制：`MAX_CONCURRENT_REQUESTS` 环境变量控制最大并发数（默认 1），超限返回 503 + `Retry-After` 头

### 前端 (Next.js)

统一单页面对话界面。后端 Multi-Agent 自动路由到 SQL/RAG/Report Worker，用户无需选择模式。

```
web/src/
├── app/layout.tsx + page.tsx    # 唯一入口
├── components/
│   ├── ChatView.tsx             # 主容器（连接 ChatInput + MessageList + StatusBar）
│   ├── ChatInput.tsx            # 通用输入框（通过 onSend prop 解耦）
│   ├── MessageBubble.tsx        # 消息气泡（SSE 进度 + Markdown）
│   ├── ThinkingPanel.tsx        # 数据驱动 Worker 进度面板（📊/📚/📄/🧠）
│   ├── StatusBar.tsx            # 底部实时进度条
│   └── EmptyState.tsx           # 统一示例页
├── hooks/
│   ├── useSSE.ts                # SSE 流消费（带 AbortController）
│   └── useChat.ts               # 发送消息 hook
├── lib/
│   ├── api.ts                   # API 客户端（含 SSE buffer 刷新）
│   ├── sse-parser.ts            # 共享 SSE 进度解析
│   ├── constants.ts             # Worker 图标映射
│   └── types.ts                 # 类型定义
└── store/
    └── chat.ts                  # Zustand store（replaceLastAssistant + sessionId 安全）
```

## 技术栈

| 层 | 技术 |
| --- | --- |
| LLM | Ollama (qwen2.5:4b)，通过 `OLLAMA_HOST` 环境变量连接 |
| Embedding | BAAI/bge-small-zh-v1.5 (ModelScope) |
| Reranker | BAAI/bge-reranker-base |
| 向量库 (RAG) | ChromaDB |
| 向量库 (Memory) | PostgreSQL 18 + pgvector (ivfflat cosine) |
| 关键词检索 | rank-bm25 |
| Multi-Agent | LangGraph (StateGraph + Send API 并行扇出) |
| SQL 安全 | sqlglot + 只读执行器 |
| 报告 | Jinja2 + matplotlib + LLM 润色 |
| 后端 | FastAPI + uvicorn + SQLAlchemy 2.0 Async |
| 前端 | Next.js 14 + Tailwind CSS + Zustand + SSE streaming |
| 数据库 | PostgreSQL 18 (demo)，本地服务 `postgresql-x64-18` |
| 异步 | asyncio + asyncpg + 持久事件循环线程 |
| MCP | @modelcontextprotocol/server-puppeteer + server-filesystem |
| 测试 | pytest (7 个测试文件，覆盖 memory / sql_agent / report_agent) |

## 关键约定

- **配置**：`config.py` 统一管理默认值，敏感/环境相关配置放 `.env` 覆盖；连接地址通过环境变量覆盖（`OLLAMA_HOST`, `PGHOST` 等）
- **CPU 保护**（❗重要）：笔记本/低配机器务必限制 Ollama 线程数（`set_ollama_env.ps1`）；`MAX_CONCURRENT_REQUESTS=1` 防止并发推理叠加；系统环境变量修改后需重启 Ollama 才能生效
- **模块路径**：项目根在 `sys.path` 中，各模块直接从 `config`、`utils` 等导入；路径使用正斜杠 `/`
- **记忆系统**：Agent 禁止直接访问 `memory/repository/` 或 `memory/models/`，统一通过 `MemoryService`；L3 写入为后台异步，不阻塞主流程
- **数据库**：PostgreSQL 18 本地 Windows 服务，`PGPASSWORD=123456`；异步引擎连接池 20+10
- **SQL Agent**：只做只读查询，`row_security.py` 强制行级过滤
- **Report Agent**：LLM 仅做语言润色，数字/事实通过硬校验锁定
- **Multi-Agent**：
  - 通过 `ToolRegistry` 接入已有子系统，Worker 并行执行无共享状态
  - `MemoryManager` 在 `ask()`/`stream_events()` 入口/出口调用 `start_session`/`end_turn`
  - `AgentState.step_results` 使用 `Annotated[dict, _merge_step_results]` 自定义 reducer 处理并发写入
  - Supervisor SQL 空结果降级有条件触发：问题必须含知识库关键词（`is_knowledge_question()`）
  - Planner `_filter_plan()` 后置移除混合计划中冗余的 RAG 步骤
  - Planner `_fallback_plan()` 在空计划时自动创建 RAG 兜底步骤
  - SSE 流去重粒度为 `(step_id, status)`，非仅 step_id
- **前端**：统一单页面，后端自动路由 Worker；ChatInput 通过 onSend prop 解耦；SSE 流支持 AbortController 中止
- **API 层**：不修改业务代码，通过 `deps.py` 惰性导入现有模块；`api/server.py` 含并发控制中间件
- **模型下载**：`HF_HUB_OFFLINE=1` / `TRANSFORMERS_OFFLINE=1` 设置为离线模式，模型需预下载到本地路径
- **事件循环**：SQLAlchemy async engine 是事件循环绑定的；`MemoryManager` 使用持久后台线程+单事件循环处理所有 async→sync 桥接
- **LLM 工厂**：`llm/llm_factory.py` 仅初始化 ChatOllama（LLM），Embedding 由 `retrieval/pipeline.py` 自行加载；支持 `OLLAMA_HOST` 环境变量切换地址
