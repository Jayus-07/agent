# Agent Platform

电商智能运营 Agent 平台 — 基于 LangGraph + MCP 的企业级 Agent 架构实践

---

## 核心能力

### 企业知识问答 RAG

- 文档解析：PDF / DOCX / Markdown / TXT
- 智能切片：文档类型感知分块（制度/FAQ/SOP/商品规格）
- Metadata 管理：LLM 自动抽取关键词/摘要/实体
- 混合检索：BM25 + Vector + RRF 融合
- Cross Encoder Rerank：全局重排序 + 阈值过滤
- Evidence Gate：三层主动拒答（Retrieval / Rerank / Faithfulness），防幻觉
- Citation：内联引用标注 [1][2] + 参考文献列表

### Multi-Agent 工作流

```
用户请求
   ↓
Planner     — 任务拆解 + Capability DAG
   ↓
Critique    — 计划审查 + 纠错
   ↓
Supervisor  — 并行调度 + 降级 + 重试
   ↓
Skills      — RAG / SQL / Report / Email / Web
   ↓
Reporter    — 结果汇总 + 引用格式化
```

支持 8 个 Skill 并行调度，超时降级，Self-Correction 自动修正。

### NL2SQL 数据分析

```
"分析最近 30 天库存异常"
   ↓
Schema Router   — 自动选表
   ↓
SQL Generator   — LLM 生成 SQL
   ↓
Validator       — 6 层硬校验 + 行级权限
   ↓
Executor        — 执行 + 脱敏 + Markdown 格式化
```

### Workflow 自动化

- 日报定时生成（Jinja2 模板 + 图表）
- 库存预警自动推送
- CSV 导出（UTF-8 BOM，Excel 兼容）
- 邮件发送（SMTP）

### 三层记忆系统

| 层级 | 存储 | 生命周期 |
|------|------|---------|
| L1 短期 | 消息缓冲区 | 单次会话 |
| L2 会话 | PostgreSQL | 持久化 |
| L3 长期 | pgvector | 跨会话检索 + 衰减归档 |

---

## 架构

### 分层设计

```
Agent      — 任务理解、规划、决策（不直接操作业务）
   ↓
Skill      — 业务能力封装（rag.search / sql.query / report.generate）
   ↓
Tool       — 底层执行（vector_search / postgres_query / send_email）
   ↓
External   — PostgreSQL / ChromaDB / SMTP / DuckDuckGo
```

**为什么不是 Agent 直接调 Tool？**

Skill 层提供业务语义封装和统一错误处理。新增业务能力只需加 Skill，Agent 无需感知底层 Tool 变化。

### 系统调用链

```
简单请求:
  API → Router → Skill → Tool / RAG / SQL / Memory

复杂任务:
  API → Planner → Critique → Supervisor → Skill → Tool → MCP → External
```

### 完整架构

```
┌──────────┐    ┌──────────────┐    ┌──────────┐
│ Frontend │    │ MCP Servers  │    │ External │
│ (3000)   │    │ (stdio/HTTP) │    │ Clients  │
└────┬─────┘    └──────┬───────┘    └────┬─────┘
     │                 │                 │
┌────┴─────────────────┴─────────────────┴────┐
│              FastAPI (8000)                  │
│  /chat  /chat/stream  /rag  /memory  /mcp  │
└────────────────────┬────────────────────────┘
                     │
┌────────────────────┴────────────────────────┐
│           Agent Runtime                      │
│  Planner → Critique → Supervisor → Reporter │
└────────────────────┬────────────────────────┘
                     │
┌────────────────────┴────────────────────────┐
│              Skills (8)                      │
│  rag  sql  report  email  web  data_export  │
└────────────────────┬────────────────────────┘
                     │
┌────────────────────┴────────────────────────┐
│              Tools (9)                       │
│  RAG  SQL  Report  Export  Web  Email  DC   │
└────────────────────┬────────────────────────┘
                     │
┌────────────────────┴────────────────────────┐
│         Infrastructure                       │
│  PostgreSQL  ChromaDB  pgvector  SMTP  LLM  │
└─────────────────────────────────────────────┘
```

---

## RAG Pipeline

```
文档上传
   ↓
Load (PDF/DOCX/MD) → Parse → Clean → Dedup → Chunk (类型感知)
   ↓
Metadata: 关键词 + 摘要 + 实体 + 模拟问题 (LLM 一次调用)
   ↓
Embedding (BGE) → ChromaDB + BM25 索引
   ↓
检索: BM25 + Vector → RRF 融合 → Rerank (CrossEncoder)
   ↓
Evidence Gate: Retrieval Gate → Rerank Gate → Faithfulness Gate
   ↓
生成: LLM + Citation [1][2] + META 注释 → 参考文献格式化
```

---

## MCP Integration

基于 Model Context Protocol，将系统 Tool 能力标准化暴露：

```
外部 Agent (Claude/Cursor/GPT)
   ↓
MCP Protocol (stdio / HTTP SSE)
   ↓
mcp_servers/
   ├── sql  — sql_query / list_tables
   └── rag  — search_knowledge / list_documents
```

| 端点 | 说明 |
|------|------|
| `GET /mcp/tools` | 列出所有可用 tool |
| `POST /mcp/call` | 调用指定 tool |

---

## Observability

### Trace（9 阶段全链路）

每次 Agent 请求完整记录：

```
User Input
   ↓ Planner        — 任务拆解
   ↓ Critique       — 计划审查
   ↓ Supervisor     — 调度决策
   ↓ Skill Select   — 能力匹配
   ↓ Retrieval      — 向量+BM25 检索
   ↓ Tool Execute   — SQL / Web / Export
   ↓ LLM Generate   — 答案生成
   ↓ Citation Verify — 引文校验
   ↓ Final Response — 结果汇总
```

每个 Span 记录：latency / token_usage / retrieval_score / tool_args / execution_result

### Metrics

- Prometheus `/metrics` 端点
- 4 类黄金指标：请求延迟 / 错误率 / LLM Token 用量 / Skill 执行时长

---

## 技术栈

| 层次 | 技术 |
|------|------|
| Web | FastAPI + SSE Streaming |
| Agent | LangGraph (StateGraph + Send API) |
| LLM | DeepSeek / Qwen / Ollama（可切换） |
| 向量 | ChromaDB + HuggingFace BGE |
| 检索 | BM25 + Vector → RRF → CrossEncoder Rerank |
| 数据 | PostgreSQL + pgvector + Pandas |
| 可观测 | 自建 Tracer + Prometheus |
| MCP | stdio / HTTP SSE |
| 前端 | Next.js 14 + React 18 + Zustand |

---

## 项目结构

```
agent/
├── pyproject.toml         # 依赖分层
├── mcp_servers/           # MCP 服务（独立进程）
├── backend/
│   ├── agents/            # Agent 节点（planner/reporter）
│   ├── orchestration/     # LangGraph 运行时（supervisor/graph/state）
│   ├── skills/            # 业务能力（rag/sql/report/email/web/...）
│   ├── tools/             # 工具层（sql/rag/web/email/export/...）
│   ├── observability/     # 可观测（tracer/metrics/alerts/topology）
│   ├── rag/               # RAG 管道（retrieval/indexing/preprocessing）
│   ├── memory/            # 三层记忆（L1/L2/L3）
│   ├── app/               # FastAPI（server + routes + middleware）
│   ├── config/            # 纯配置
│   ├── infra/             # 基础设施（LLM/限流/超时）
│   ├── shared/            # 最小共享层（logger/exceptions）
│   ├── evaluation/        # RAG 评估
│   ├── seed/              # 种子数据
│   ├── data_collection/   # ETL 管道
│   ├── sql/               # NL-to-SQL
│   └── prompts/           # 提示词模板
├── scripts/               # Demo 脚本
├── docs/                  # 架构文档 + ADR
├── frontend/              # Next.js（3000）
└── data/                  # 运行时数据
```

---

## 快速开始

```bash
# 一键启动
start_all.bat

# 浏览器
http://localhost:3000        # 前端
http://localhost:8000/docs   # Swagger API
http://localhost:8000/metrics # Prometheus
```

```bash
# 手动启动
cd backend && uvicorn app.server:app --host 0.0.0.0 --port 8000 --reload
cd frontend && npm run dev
```

---

## 开发

```bash
pip install -e ".[dev]"        # Python（pyproject.toml）
cd frontend && npm install      # Node

pytest backend/tests/ -q        # 500 tests
```

**代码规范与架构约束** 见 [CLAUDE.md](CLAUDE.md)。

---

## License

Private — 仅供内部使用。
