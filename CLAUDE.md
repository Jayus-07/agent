# CLAUDE.md

> 项目级约束与架构知识，随代码一起演进。
> 个人偏好（语言/环境路径/工具）见全局 ~/.claude/CLAUDE.md

## Project

电商 RAG + Multi-Agent 平台

Stack:
- Backend: FastAPI + LangGraph
- Frontend: Next.js 14 + React
- AI: DeepSeek（langchain-openai 兼容接口）
- DB: PostgreSQL agent_business（业务仓库）+ agent_memory（元数据库）

## Architecture

```
API → Router → Planner → Critique → Supervisor → Skills → Reporter
                                                  ↓
                                              Tool / RAG / SQL / Memory
```

### 节点职责

- **Planner**: 只负责任务拆解 → 输出 Capability DAG（nodes + edges），禁止调用 Tool/Skill/DB
- **Critique**: 审查修正计划
- **Supervisor**: 按 edges 依赖顺序调度，通过 Send[] 并行派发，自动注入 previous_outputs
- **Skill**: 业务能力封装，不直接访问外部系统
- **Reporter**: 汇总 step_results → Markdown
- **Tool**: 无状态、可测试

### Capability DAG

```json
{
  "nodes": {
    "1": {"step_id": "1", "capability": "sql.query", "params": {"question": "..."}},
    "2": {"step_id": "2", "capability": "business.analyze", "params": {}}
  },
  "edges": {"2": ["1"]}
}
```

### 已注册 Capability（9 个）

| capability | Skill | 节点名 |
|---|---|---|
| sql.query | SQLSkill | sql_skill |
| business.analyze | BusinessAnalysisSkill | business_analysis_skill |
| rag.search | RAGSkill | rag_skill |
| report.generate | ReportSkill | report_skill |
| email.send | EmailSkill | email_skill |
| data.export | DataExportSkill | data_export_skill |
| web.search | WebSearchSkill | web_search_skill |
| web.crawl | WebCrawlSkill | web_crawl_skill |
| data.collect | DataCollectionSkill | data_collection_skill |

新增 Skill: 创建 `skills/<name>/skill.py` → `skills/registry.py` 注册 → 自动发现。

### SQL 子系统

```
SQLSkill → SQLAgent → Router → Generator → Validator(6层) → RowSecurity → Executor(连接池) → PostgreSQL
```

6 层安全: ①SELECT 类型校验 ②表名白名单 ③敏感列拒绝 ④禁止函数黑名单 ⑤LIMIT 强制 ⑥agent_readonly 只读角色

数据协议:
- **SQLResult** (Pydantic): sql/tables/columns/rows/row_count/execution_time — Skill 层输出
- **BusinessInsight** (Pydantic): summary/risks/suggestions/confidence — BusinessAnalyzer 输出

步骤间数据传递: Supervisor 在 Send 中注入 `previous_outputs`（前置步骤的 output 自动传给后置步骤）。

### 数据库

7 schema × 18 表: product / order / inventory / customer / crawler / finance / ai
连接池: ThreadedConnectionPool（min=2, max=10）
只读账号: agent_readonly（scram-sha-256 认证）
Migration: `sql/migrations/001~005`

## Design Principle

必须满足: 可理解、可测试、可观测、可维护、可扩展、可控制、可靠性
禁止: Demo 跑通式开发、临时堆叠、except Exception: pass

### Priority

P0: 数据错误、安全问题、崩溃、Trace 丢失
P1: 架构问题、强耦合、重复代码
P2: 命名、注释

### 数据库生产标准

P0: 只读角色 + scram-sha-256 + 连接池 + connect_timeout/keepalives
P1: 外键完整 + CHECK 约束 + 高频列索引

## Code Rules

- Python: snake_case、类型注解、logger 替代 print、具体异常、SQL 参数化
- 禁止: 业务代码直接 os.getenv、文件名 misc/helper/common/utils2
- Tool 必须独立可测试

## Change Flow

明确目标 → 阅读代码 → 分析影响 → 修改 → 测试
Bug 先复现、Refactor 测试通过、Feature 优先补测试

## Validation

Backend: `py_compile` + `pytest tests/sql/ -v`
Frontend: `npx tsc --noEmit` + `npm test`
E2E: `cd backend && python e2e_demo.py`

## Docs

设计文档: `docs/README.md`（7 个顶层文档 + 7 个关键深读）
记忆: 用户级 `~/.claude/projects/<project>/memory/MEMORY.md`（按项目分类的会话记忆）


```bash
# 一键启动
start_all.bat
# 一键关闭
stop_all.bat
# 一键重启 
restart_all.bat
