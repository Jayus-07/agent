# SQL Agent Production Closure — STOP C 验收报告

> 阶段：SQL Agent Production Closure｜STOP C（生产接线收口）
> 日期：2026-09-23
> 结论：**STOP_C_PASS=true**（§一 17 项完成标准全部满足）

## A. 修改文件 / Commit

| Commit | 内容 |
|---|---|
| `feat(sql): close production authorization paths` | 路由/Tool/MCP 接线、execute_sql_tool 旁路封死、precheck 前置、deny 终态化、kill switch、list_tables 修复 |
| `feat(sql): add audit observability and db hardening` | audit.py、metrics、sql.guard span、migration 042/043、MIGRATION_TARGETS |
| `test(sql): add production closure coverage` | 24 例单测 + E2E 脚本 |
| `docs(sql): STOP C acceptance report` | 本报告 |

核心文件：`backend/sql/policy.py`（precheck + source_channel + sql.guard span）、`sql/sql_agent.py`（统一观测出口 `_observe` + kill switch + 旧链终态）、`sql/sql_validator.py`（ValidationError.reason 分类，行为零变化）、`sql/audit.py`（新）、`app/api/routes/sql.py`（重写接线）、`app/api/routes/mcp.py`、`tools/sql.py`（重写收口）、`core/request_context.py` + `tools/session.py`（roles contextvar）、`config/__init__.py`（SQL_AGENT_ENABLED）、`mcp_servers/servers/sql.py`、`observability/metrics.py`、`sql/migrations/042_*.sql`+`043_*.sql`、`scripts/init_db.py`、`backend/scripts/e2e_sql_closure.py`（新）。

## B. 生产入口调用图与统一 choke point

```
A. HTTP /sql、/sql/query      ─┐
B. chat → graph → SQLSkill     │                    ┌─ audit(042 表) best-effort
C. Supervisor Send → SQLSkill  ├→ SQLAgent 策略链 ──┼─ sql_agent_* metrics
D. Tool registry → sql_query_tool / execute_sql_tool ─→ (precheck→Guard→executor)
E. MCP /api/mcp/call → SQLMCPServer ─┘               └─ sql.guard span
F. execute_sql_tool（原旁路）→ 已收口：SQLPolicyGuard.validate_and_rewrite
G. evaluation runner / e2e_demo（policy=None 旧行为，非生产入口；kill switch 同样覆盖）
H. list_tables → schema_loader 白名单（不再触 DB）
```

**choke point = `backend/sql/sql_agent.py` 策略链**（`_ask_struct_with_policy`：precheck → select_tables → generate → `SQLPolicyGuard.validate_and_rewrite` → readonly executor → `_observe`）。四个生产入口全部汇聚于此；`SQLPolicyGuard.precheck` 在任何 LLM 调用之前执行权限/scope 检查。CS 域 service 直调 `execute_sql_struct` 属业务内部手写 SQL（非 NL2SQL 通道、参数化、无用户任意输入），归类为合法底层使用方，不在本阶段范围（且该目录正被并行会话修改）。

## C. HTTP /sql* 权限接线

`Depends(get_principal)` → `build_authorization_context` → `SQLPolicyContext(source_channel="http")` → `agent.ask_struct(policy=…)`。路由层仅两项预检，零角色字符串判断：①`SQL_AGENT_ENABLED=false → 503`；②`authz.has_permission("sql.read") → 403`（快速失败不进 LLM；agent 内 Guard 重复强制，纵深）。请求体 `current_user_id` 保持废弃忽略。

## D. Tool / MCP 收口

- Tool 通道：contextvars（graph bind / MCP 入口绑定）→ `_tool_policy_context()`（source=tool）→ 两 Tool 均过 Guard。新增 roles contextvar（`set_tool_roles/get_tool_roles`），`bind()` 同步写入。
- MCP：`/api/mcp/call` 补绑 tenant/roles → `_mcp_sql_policy_context()`（source=mcp）→ `ask_struct(policy=…)`。无可信身份 → 权限门拒绝（单测锁定「不存在匿名 SQL」）。无 MCP 专属授权体系。
- **无 bypass 残留**：全仓 grep `execute_sql_struct` 的非测试调用方 = SQLAgent、tools/sql.py（已收口）、CS service（业务内部查询，见 B）。

## E. execute_sql_tool 旁路审计

| 调用方 | 处理 |
|---|---|
| `workflow/skill_adapter.py`（workflow step 拉数） | 经收口后的 Tool：Guard 权限门+scope 注入生效；editor 查 personal/internal 将被拒（与 graph/HTTP 同语义） |
| 直接 `execute_sql(sql)`（旧 Markdown 出口，executor.py） | 无生产调用方残留（grep 验证）；保留为 tests/internal 底层 |
| CS service ×5（account/order/refund/logistics/after_sales） | 业务内部手写 SQL，非 SQL Agent 通道，不触碰（并行会话所有） |

## F. list_tables DB 修复

修复前：`psycopg2.connect(**DB_CONFIG)`（= **agent_memory 元数据库**，连错库）+ 直查 `information_schema`（内部表名泄露：auth.sessions 等）。修复后：直接返回 `schema_loader.get_all_table_names()`——与 SQL Agent 执行白名单同一数据源（schema_config.py），零 DB 查询、零 introspection 面。单测断言两集合**严格相等**且 7 个业务 schema 全覆盖、白名单外表（public.schema_migrations/auth.*）不出现。

## G. policy deny 终态

- `SQLPolicyError`：终态（generate=1、executor=0；单测 `test_terminal_deny_no_retry_no_executor` + `test_feedback_never_leaks_on_deny` 锁定 feedback 恒为 None）。
- **旧链终态化**：`ValidationError` 新增低基数 reason 分类——`TERMINAL_DENY_REASONS`（non_select/multi_statement/select_into/lock_clause/write_in_subquery/table_forbidden/schema_forbidden/column_forbidden/star_projection/dangerous_function）→ 终态不重试；`parse_error/empty_sql/alias_undefined/limit_exceeded` → 保留既有 ≤2 次带反馈修复（每次重走校验）。单测 `test_terminal_deny_no_retry_no_executor`（generate=1）与 `test_syntax_error_still_retryable`（generate=3、executor=0）双向锁定。
- 新增 `precheck`：权限拒绝发生在任何 LLM 调用之前（收口测试中发现并修复的缺口——原实现无权限用户也会触发 select_tables/generate）。

## H. Audit

- **migration 042** `sql_query_audits`（agent_memory，`MIGRATION_TARGETS["042_sql_query_audits.sql"]="memory"` 已登记，**真实执行 rc=0**）：id/session_id/user_id/tenant_id/department/data_scope/source_channel/tool_name/query_hash/tables/decision/deny_code/duration_ms/row_count/status/error_type/created_at + 3 索引。
- 脱敏：不存 SQL 原文（PII/literal 风险），只存 `sha256(规范化 SQL)`；禁存 Authorization/JWT/凭据。
- 写入点：agent 层统一 `_observe`（唯一一份审计逻辑）；**best-effort**——异步线程写入，失败仅日志+计数（`write_failure_count()`），主查询永不失败；决策枚举 ALLOW/DENY_PERMISSION/DENY_SCOPE/DENY_TABLE/DENY_VALIDATOR/EXECUTION_SUCCESS/EXECUTION_FAILED/TIMEOUT。
- **实库验证**：E2E 后 `SELECT decision, count(*) … WHERE source_channel='http'` → `{'DENY_TABLE': 2, 'EXECUTION_SUCCESS': 3}`。

## I. Metrics / Trace

- `sql_agent_requests_total{source,decision}`、`sql_agent_denied_total{reason}`、`sql_agent_execution_total{status}`、`sql_agent_execution_duration_seconds`（Histogram）、`sql_agent_rows_returned_total`。低基数 label：source ∈ http|graph|tool|mcp|unknown；reason ∈ permission|scope|table|validator|unknown_scope|timeout。禁 user_id/tenant/trace/表名/SQL 进 label。**/metrics 端点实测产出**（E2E 后 DENY_TABLE=2、execution success=2/no_data=1 等）。
- Span `sql.guard`：attributes 走 metrics dict（source_channel/data_scope/decision/reason_code/table_count，全低基数）；deny 以 status=error 收口（不 dangling）；`start_span` noop 语义保证无 trace 时零风险。
- 可回答：请求量✅ 权限拒绝数✅ scope 拒绝数✅ validator 拒绝数✅ 成功率✅ 执行耗时✅ timeout✅。

## J. DB 最小权限（migration 043 + 真实角色验证）

043（agent_business，已登记 MIGRATION_TARGETS、真实执行）：REVOKE agent_readonly 对 public schema 全部表/序列权限 + default privilege 兜底；7 业务 schema SELECT 保留。**以 agent_readonly 真实连接验证**：

| 操作 | 结果 |
|---|---|
| SELECT product.products | ✅ 10 行 |
| INSERT / UPDATE / DELETE products | ❌ permission denied ×3 |
| CREATE TABLE public.hack | ❌ permission denied for schema public |
| SELECT public.selection_tasks（白名单外） | ❌ permission denied |

修复前 public grants=10+、修复后 `role_table_grants(public, agent_readonly)=0`、业务 schema 16 条保留。migration 用户（postgres）不受影响。

## K. Kill Switch（SQL_AGENT_ENABLED，config 单一来源，默认 true）

| 入口 | 关闭行为 |
|---|---|
| HTTP /sql* | 503「SQL 查询服务暂不可用」（服务不可用语义，非权限语义） |
| graph SQLSkill | 终态 unavailable step_result（无重试、无 LLM） |
| Tool（两工具） | `tool_error_result(reason=service_unavailable)` |
| MCP（sql_query/list_tables） | `{"reason": "service_unavailable"}` |

单测证明 `SQL_AGENT_ENABLED=false → executor 调用次数=0`（policy 链与 legacy 链分别锁定）。注意：开关为启动期 env（跟随项目域开关风格），切换需重启生效。

## L. APISIX 真入口 E2E（:9080，`backend/scripts/e2e_sql_closure.py`，app 容器已 rebuild 加载新代码）

| Case | 身份 | 查询 | 预期 | 实际 | 触达 DB |
|---|---|---|---|---|---|
| C1 | e2e_sqlv(viewer) | 商品列表 | 403 | **403 PERMISSION_DENIED** | 否 |
| C2 | e2e_sqle(editor/hr) | 商品列表 | allow | **200 success, 10 rows** | 是 |
| C3 | editor/hr | 财务费用 | deny | **200 permission_denied**（文案无表名） | 否 |
| C4 | e2e_sqla(admin) | 财务费用 | allow | **200 no_data**（LLM 生成带条件查询，合法空结果） | 是 |
| C5 | admin | agent 任务 | allow | **200 success, 1 row** | 是 |
| C6 | self 查 orders | — | customer_id 注入 | HTTP 层 D3 角色映射下无 self+sql.read 组合（viewer 无权限 403）——**注入由 Guard 单测锁定**（test_sql_scope_self，含 int 类型回归） | — |
| C7 | editor/hr | 订单明细 | deny | **200 permission_denied** | 否 |
| C8 | viewer JWT + 伪造 X-User-Roles:admin | 商品列表 | 403 | **403**（伪造头未提权，角色只来自网关重注入） | 否 |
| C9 | kill switch 关闭 | — | executor=0 | **单测覆盖**（4 入口各自锁定）；共享容器不重启切换 env，见 M | 否 |
| C10 | — | — | deny 不重试 | 终态单测锁定 + **audit 实库验证**：DENY_TABLE=2 / EXECUTION_SUCCESS=3（http 归因正确） | — |

**8/8 PASS**。完整链验证：JWT 签发（login 经 :9080）→ 网关验签/剥伪造头/重注入 → FastAPI → Principal → AuthorizationContext → SQLPolicyContext → SQLPolicyGuard → readonly DB → audit+metrics。

## M. Regression / Git isolation

```
pytest tests/sql/ --no-cov                                                   → 233 passed（209 基线+24 新增，零回归）
pytest tests/test_registry_consistency.py tests/test_layer_consistency.py
       tests/test_adr0001_dual_registry_merge.py --no-cov                    → 36 passed（基线持平）
pytest tests/security/test_principal_authorization.py
       tests/orchestration/test_request_context.py
       tests/sql/test_sql_skill_structured.py tests/test_sql_user_context.py
       --no-cov                                                              → 80 passed（基线 51+25 sql_user_context）
```

- baseline existing failure（不修，与本阶段无关）：`tests/test_llm_span_fields.py` 5 例 minimax/deepseek pricing 断言。
- **部署注记**：app 容器已 `docker compose up -d --build app` 加载新代码（E2E 前提）；**worker 未 rebuild**（异步任务路径含 SQLSkill，当前无运行中异步 SQL 任务；随下次例行部署生效，已登记 STOP D 待办）。db-migrate rc=0。
- Git dirty 隔离：全程显式 pathspec；其他会话 dirty（tasks/Phase2、customer_service/缺陷9、cs_graph/cs_prefilter、docs/cs）零触碰、零混入；`backend/tests/test_task_phase2_step2_error_taxonomy.py`（他属未跟踪文件）不提交。

## N. STOP_C_PASS

**STOP_C_PASS=true** —— §一 17 项逐条：①HTTP 统一权限✅ ②graph 走既有 Guard✅ ③Tool 可信身份✅ ④MCP 同 Guard✅ ⑤execute_sql_tool 不再旁路✅ ⑥list_tables 同库同白名单✅ ⑦deny 终态（新旧链）✅ ⑧audit 持久化+实库验证✅ ⑨metrics✅ ⑩span✅ ⑪DB 最小权限（043+实测）✅ ⑫kill switch 四入口✅ ⑬APISIX :9080 E2E 8/8✅ ⑭⑮基线零回归✅ ⑯pricing baseline 未动✅ ⑰并行会话零混入✅

**STOP D 待办（只登记）**：worker 容器 rebuild 部署、/sql* 前端 UI 错误态适配（可选）、sql audit 管理端查询页（可选，规格 §一百零五 非 blocker）、DB role 收口后长期监控 denied 指标、UNION 放开评估（需先扩展 Guard 分支注入 E2E）、SQL 性能与 NL2SQL prompt 优化。
