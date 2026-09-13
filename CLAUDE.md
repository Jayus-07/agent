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
POST /chat/stream → GraphRunner（Input Guard 门禁 → memory.start_session → graph.stream）
START → router ─┬─ CS 预过滤命中（灰度放量） ──────────→ 客服域图 cs_graph_node → END
                └─ 三层 Router（rule→vector→LLM）→ route_selector
                      ├─ direct  → skill_executor（跳过 Planner 直调 skill）→ reporter → END
                      ├─ workflow → workflow_executor → reporter → END
                      └─ plan    → planner → critique → supervisor（Send 并行）→ reporter → END
```

> planner→critique→supervisor 只是 plan 模式支线；direct/workflow/客服域图均绕过它。
> 客服子图：cs_state_loader → cs_pending_handler → cs_supervisor（handoff 拦截/循环上限/LLM 兜底）→ 5 专家 → 回 supervisor → cs_reporter。
> RAG 子链路：改写 → MultiQuery → 混合检索（向量+BM25）→ 同文档扩展 → Rerank → EvidenceGate → 带引用生成 → META 尾拒答判定。
> 流式：节点 status/log + LLM stream_sink delta 汇入 merged_q；SSE 帧序 meta → status/log/delta → done/error。

### 节点职责

- **Planner**: 只负责任务拆解 → 输出 Capability DAG（nodes + edges），禁止调用 Tool/Skill/DB
- **Critique**: 审查修正计划（规则校验 0ms 优先，仅 anomaly 时调 LLM）
- **Supervisor**: 按 edges 依赖顺序调度，通过 Send[] 并行派发，自动注入 previous_outputs
- **Skill**: 业务能力封装，不直接访问外部系统
- **Reporter**: 汇总 step_results → Markdown
- **Tool**: 无状态、可测试

### Agent 口径

描述系统规模时区分节点类型，勿把所有节点统称 Agent：
- **LLM 决策节点（3）**: planner / critique（仅 anomaly 时）/ reporter
- **混合路由点（2）**: router（rule→vector→llm 三层兜底）、cs_supervisor（规则优先+LLM 兜底）
- **规则/执行节点**: 主图 supervisor 是纯规则 DAG 调度器（不调 LLM）；客服 5 专家中仅 knowledge 走 LLM（经 RAG），query/action/complaint/handoff 为业务服务编排
- **工具节点（13）**: skills/registry.py 注册的 Skill，非决策 agent；LangChain tool 见 backend/tools/（含 memory_search/memory_store/calculate）

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

### 写操作审批门（human-in-the-loop）

写副作用工具（send_email/export_csv/data_collection/competitor 写动作）执行前
必须调用 `security/tool_approval.ensure_approved()`：TOOL_APPROVAL_MODE=required
（默认）时建审批单返回待批提示，管理员经 `/api/approvals` 批准后重试同指纹操作
放行（TTL 内）。表: ai.tool_approval_requests（migration 007）。工具层身份来自
`tools/session.get_tool_user_id()`（RequestContext.bind 注入），禁止硬编码 user_id。

### 工具契约兼容规则（代替 per-schema 版本号）

工具/Skill 契约（params_schema、output_type、capability 名）与消费方（Planner、
Reporter）同仓同发布，schema 变更与消费方适配原子提交，**不加 per-schema 版本号**。
兼容靠三条规则：

1. **向后兼容演进**：只新增可选参数；不改既有参数语义；不删除/改名已有字段
   （proto 演进规则）。破坏性变更 = 新 capability 名 + 旧Capability 保留一个废弃期
2. **测试守护**：`test_registry_consistency.py`（注册表一致性）+
   `test_base_output_contract.py`（输出契约）+ e2e 离线故障注入集
   （`datasets/e2e/cases.jsonl` F-* 用例）构成契约回归门
3. **变更跑评估**：改 params_schema/描述/prompt 后跑 planner 评估（live），
   关键写操作参数用 `datasets/planner_params.json` 的 expected.params 断言

只有当工具以独立部署制品对外（MCP Server 发布、跨团队共享）时，才引入显式版本号。

### 主图 LangGraph 保护

- recursion_limit: MAIN_GRAPH_RECURSION_LIMIT（默认 80），runner 每次 stream 传入
- checkpointer: MAIN_GRAPH_CHECKPOINTER_ENABLED（默认关）；开启后 request_context
  以 checkpoint_safe() dict 进状态（trace/sink 不序列化），thread_id 每轮唯一

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
