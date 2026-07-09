# 部署

> 本地启动 / Docker 全栈 / CPU 保护机制 / 环境变量。

## 1. 启动方式

### 1.1 一键启动（推荐，Windows）

```bash
start_all.bat
```

- 自动检查 Ollama 运行状态
- 加载 `.env` 配置
- 启动 FastAPI (端口 8000)
- 启动 Next.js (端口 3000)
- 两个独立窗口，关闭即停止

### 1.2 直接启动

```bash
# 后端
python -m api.server                      # 生产模式
uvicorn api.server:app --reload           # 开发模式（热重载）

# 前端
cd web && npm run dev
```

### 1.3 Docker 全栈

```bash
docker compose up -d                      # 启动全栈 (ollama + postgres + api)
docker compose up -d postgres             # 仅启动 PostgreSQL
docker compose logs -f api                # 查看 API 日志
docker compose down                       # 停止
```

**注意**：Docker Desktop 29.2.1 CLI 与旧版引擎 API 不兼容时，建议用本地服务（`start_all.bat`）。

**模型首次需手动拉入 Ollama 容器**：
```bash
docker compose exec ollama ollama pull qwen2.5:4b
```

## 2. Docker 服务编排

`docker-compose.yml` 编排 3 个服务：

| 服务 | 端口 | 镜像 | 角色 |
|---|---|---|---|
| `ollama` | 11434 | ollama/ollama | LLM 模型服务 |
| `postgres` | 5432 | pgvector/pgvector:pg18 | PostgreSQL + pgvector |
| `api` | 8000 | 本地 Dockerfile | FastAPI 应用 |

**关键文件**：

```
Dockerfile                  # 多阶段构建：torch cpu → requirements → 源码
docker-compose.yml          # 服务编排
docker/
├── entrypoint.sh           # 容器入口（等待 PG 就绪 → 迁移 → 启动 uvicorn）
└── init-db.sql             # 数据库初始化 DDL
```

环境变量通过 `docker-compose.yml` 注入，`OLLAMA_HOST=http://ollama:11434` 指向容器内 Ollama 服务。

## 3. 首次配置

### 3.1 限制 Ollama CPU 占用（❗重要：笔记本/低配机器）

管理员权限运行 PowerShell：

```powershell
.\set_ollama_env.ps1
```

脚本会设置以下系统环境变量并重启 Ollama：

| 变量 | 值 | 作用 |
|---|---|---|
| `OLLAMA_NUM_THREADS` | 4 | 只用 4 个 CPU 线程（留余量给 embedding/reranker） |
| `OLLAMA_NUM_PARALLEL` | 1 | 同时只处理 1 个推理请求 |
| `OLLAMA_MAX_LOADED_MODELS` | 1 | 只加载 1 个模型到内存 |
| `OLLAMA_KEEP_ALIVE` | 120s | 2 分钟不活动卸载模型（释放 ~2GB 内存） |

**手动设置**（如果脚本执行失败）：
```powershell
[Environment]::SetEnvironmentVariable("OLLAMA_NUM_THREADS", "4", "Machine")
[Environment]::SetEnvironmentVariable("OLLAMA_NUM_PARALLEL", "1", "Machine")
```

**注意**：必须设为**系统环境变量**后**重启 Ollama** 才能生效。

### 3.2 安装 Python 依赖

```bash
pip install -r requirements.txt
```

### 3.3 安装前端依赖

```bash
cd web && npm install && cd ..
```

### 3.4 初始化数据库

```bash
PGPASSWORD=123456 psql -h localhost -U postgres -d demo -f memory/migrations/001_init.sql
```

PostgreSQL 18 本地服务（端口 5432）。

## 4. CPU 保护机制（防过载关机）

针对笔记本/低配机器（如 i7-9750H 等移动 CPU），多层防线防止 LLM 推理触发过热关机：

```
第一层: Ollama 服务端限流
  OLLAMA_NUM_THREADS=4
  OLLAMA_NUM_PARALLEL=1
  OLLAMA_MAX_LOADED_MODELS=1
  OLLAMA_KEEP_ALIVE=120s

第二层: API 并发控制 (api/server.py 中间件)
  asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)  # 默认 1
  繁忙时返回 503 + Retry-After: 5             # 不排队，避免雪崩
  /health /docs /redoc 路径不受限

第三层: LLM 上下文减半
  LLM_CONTEXT_LENGTH=2048    # 从 4096 减半
```

相关文件：
- `set_ollama_env.ps1` — 一键设置 Ollama 系统环境变量
- `api/server.py:concurrency_limit_middleware` — 并发控制中间件
- `.env` 中 `MAX_CONCURRENT_REQUESTS` / `LLM_CONTEXT_LENGTH`

## 5. 环境变量

### 5.1 系统级（Ollama）

| 变量 | 推荐值 | 说明 |
|---|---|---|
| `OLLAMA_NUM_THREADS` | 4 | CPU 线程数 |
| `OLLAMA_NUM_PARALLEL` | 1 | 并行请求数 |
| `OLLAMA_MAX_LOADED_MODELS` | 1 | 内存中最大模型数 |
| `OLLAMA_KEEP_ALIVE` | 120s 或 300s | 空闲后多久卸载 |
| `OLLAMA_HOST` | http://ollama:11434 | Ollama 服务地址（Docker 容器名） |

### 5.2 项目级（`.env`）

**LLM 配置**：

| 变量 | 默认 | 说明 |
|---|---|---|
| `LLM_MODEL` | qwen2.5:4b | Ollama 模型名 |
| `LLM_TEMPERATURE` | 0.1 | 温度 |
| `LLM_CONTEXT_LENGTH` | 2048 | 上下文窗口（减半） |
| `LLM_REQUEST_TIMEOUT` | 30 | LLM 请求超时（秒） |
| `DEEPSEEK_API_KEY` | (空) | DeepSeek API Key（切换时用） |
| `DEEPSEEK_API_BASE` | https://api.deepseek.com/v1 | DeepSeek API 基础 URL |

**数据库**：

| 变量 | 默认 | 说明 |
|---|---|---|
| `PGHOST` | localhost | PostgreSQL 主机 |
| `PGPORT` | 5432 | 端口 |
| `PGDATABASE` | demo | 数据库名 |
| `PGUSER` | postgres | 用户 |
| `PGPASSWORD` | 123456 | 密码 |

**RAG / 检索**：

| 变量 | 默认 | 说明 |
|---|---|---|
| `EMBEDDING_MODEL_PATH` | (Windows 路径) | Embedding 模型本地路径 |
| `RERANKER_MODEL_PATH` | (Windows 路径) | Reranker 模型本地路径 |
| `CHROMA_PATH` | data/chroma | ChromaDB 路径 |
| `DOC_DB_PATH` | data/doc_db | 文档级 ChromaDB 路径 |
| `DOCS_DIRECTORY` | data/docs | 文档源目录 |
| `BM25_SEARCH_K` | 20 | BM25 召回数 |
| `HYBRID_SEARCH_K` | 20 | 混合检索融合后数量 |
| `RERANK_TOP_K` | 8 | Reranker 保留数 |
| `RERANK_SCORE_THRESHOLD` | 0.3 | Reranker 最低分 |
| `CITATION_SUPPORT_THRESHOLD` | 0.4 | Citation Filter 最低分 |

**API 并发**：

| 变量 | 默认 | 说明 |
|---|---|---|
| `MAX_CONCURRENT_REQUESTS` | 1 | FastAPI 并发请求上限（防 CPU 过载） |
| `OVERALL_REQUEST_TIMEOUT` | 60 | 软超时（检索管线告警阈值） |

**记忆**：

| 变量 | 默认 | 说明 |
|---|---|---|
| `SHORT_TERM_MAX_MESSAGES` | 20 | L1 容量 |
| `SESSION_MAX_MESSAGES` | 50 | L2 单会话消息数 |
| `ENABLE_LONG_TERM_MEMORY` | true | L3 总开关 |
| `L3_PII_FILTER_ENABLED` | true | PII 脱敏开关 |
| `L3_DEDUP_COSINE_THRESHOLD` | 0.85 | 去重阈值 |
| `L3_SUPERSEDE_THRESHOLD` | 0.92 | 替换旧事实阈值 |
| `MEMORY_ASYNC_POOL_SIZE` | 5 | async engine 连接池（实测 5 即可） |
| `MEMORY_ASYNC_MAX_OVERFLOW` | 5 | 溢出连接池 |

**资源**：

| 变量 | 默认 | 说明 |
|---|---|---|
| `LOG_LEVEL` | INFO | 日志级别 |
| `LOG_FILE` | rag_system.log | 日志文件 |
| `ENABLE_RESOURCE_MONITOR` | false | 资源监控开关 |
| `ENABLE_HISTORY_AWARE_RETRIEVAL` | true | 历史感知检索开关 |

## 6. 测试

```bash
# 用 venv 站点包（系统 Python 缺依赖）
PYTHONPATH=".venv/lib/site-packages" ./.venv/Scripts/python.exe -m pytest tests/ -v --tb=short
PYTHONPATH=".venv/lib/site-packages" ./.venv/Scripts/python.exe -m pytest tests/test_row_security.py -v
```

PowerShell 注意：先 `$env:PYTHONPATH=".venv/lib/site-packages"`。

## 7. 演示入口

```bash
PYTHONPATH=".venv/lib/site-packages" ./.venv/Scripts/python.exe multi_agent/demo.py
PYTHONPATH=".venv/lib/site-packages" ./.venv/Scripts/python.exe sql_agent/demo_sql_agent.py
PYTHONPATH=".venv/lib/site-packages" ./.venv/Scripts/python.exe report_agent/demo_report_agent.py
```

## 8. 记忆维护

手动运行衰减（默认无调度器）：

```bash
PYTHONPATH=".venv/lib/site-packages" ./.venv/Scripts/python.exe -c "
import asyncio
from memory.service import MemoryService
asyncio.run(MemoryService().run_decay())
"
```

## 9. API 端点

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/chat` | Multi-Agent 对话（JSON 响应） |
| POST | `/chat/stream` | Multi-Agent 对话（SSE 流） |
| POST | `/chat/abort` | 中止流式生成 |
| POST | `/sql` | 自然语言 → 安全 SQL 查询 |
| POST | `/rag` | 知识库检索问答 |
| POST | `/report` | 自动化报告生成 |
| GET | `/llm/models` | 列出可用模型 |
| GET | `/llm/current` | 当前模型 |
| POST | `/llm/switch` | 切换模型 |
| GET | `/llm/balance` | 余额查询 |
| GET | `/observability/alerts` | 告警列表 |
| GET | `/observability/graph` | 图拓扑 |
| GET | `/observability/metrics` | 监控指标 |
| GET | `/observability/resources` | 资源使用 |
| GET | `/observability/traces` | 执行 trace |
| GET | `/observability/traces/active` | 活跃 trace |
| GET | `/health` | 健康检查 |
| GET | `/docs` | Swagger UI |

## 10. MCP 工具链

`.mcp.json` 配置了两个 MCP 服务器（用于 Claude Code 集成）：

- **puppeteer**：浏览器自动化（截图、爬取）
- **filesystem**：文件系统操作

权限在 `.claude/settings.local.json` 配置（`mcp__puppeteer__*` / `mcp__filesystem__*`），需重启会话生效。

## 11. 故障排查

| 现象 | 排查 |
|---|---|
| LLM 一直超时 | Ollama 未启动 / `OLLAMA_HOST` 配置错误 / CPU 满载 |
| ChromaDB 找不到 | `data/chroma/` 不存在 → 首次请求会自动创建 |
| 内存错误（OOM） | `OLLAMA_MAX_LOADED_MODELS=1` + `OLLAMA_KEEP_ALIVE=120s` |
| API 503 重试 | `MAX_CONCURRENT_REQUESTS=1` + 推理慢 → 减少并发或减小 `LLM_CONTEXT_LENGTH` |
| PG 连接失败 | 检查 `PGHOST` / `PGPORT` / `PGPASSWORD` + `pg_isready` |
| L3 长期记忆写不进去 | `ENABLE_LONG_TERM_MEMORY=true` + PII 过滤未脱敏掉所有 PII |
| 前端 monitor 页空白 | 后端 `/observability/*` 端点未注册 → 检查 `api/server.py:app.include_router(observability.router)` |
