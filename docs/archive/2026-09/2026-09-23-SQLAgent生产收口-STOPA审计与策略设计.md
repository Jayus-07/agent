# SQL Agent Production Closure — STOP A 审计与策略设计

> 阶段：SQL Agent Production Closure｜STOP A（审计 + 策略设计，未改执行代码）
> 日期：2026-09-23
> 结论：**STOP_A_PASS=true**（审计与设计完成，4 个决策点待确认后进入 STOP B）
> 前置：AUTHORIZATION_PRODUCTION_READY=true（Principal / AuthorizationContext 已统一，commit 9a3581c）

---

## A. 现状 SQL 真实链路（规格书 §六 18 问逐答）

```
/chat/stream (APISIX :9080 → app :8000)
  → GraphRunner（routes/chat → runner.py:464 构建 RequestContext）
  → 三层 Router → planner 将 sql.query 编入 Capability DAG
  → supervisor 调度 sql_skill 节点（skills/sql/skill.py::sql_skill_node）
  → SQLSkill.execute → asyncio.to_thread(agent.ask_struct, question)   ← ⚠️ 不带身份
  → SQLAgent.ask_struct（sql/sql_agent.py）
      ① router.select_tables     关键词快路径 + LLM 选表（缓存 5min）
      ② generate_sql             LLM 生成（prompt: sql.generator，仅白名单表结构）
      ③ sql_validator.validate   sqlglot AST（postgres dialect）6 层硬校验
      ④ inject_row_filter        行级安全注入（SQL_ROW_SECURITY_ENABLED，默认关）
      ⑤ execute_sql_struct       连接池 + 只读事务 + statement_timeout + 脱敏
  → SQLResult(dataclass) → Pydantic SQLResult → step_results[step_id].output
  → L1 ToolResultBudgetGuard（_apply_tool_result_budget）
  → reporter 读 step_results 渲染 Markdown
```

旁路入口共 4 条（全部都要过 Guard，规格书 §135）：

| 入口 | 位置 | 过 validator | 行级注入 | 身份 |
|---|---|---|---|---|
| 图内 SQLSkill | `skills/sql/skill.py` | ✅ | ❌（未传 user_id） | ❌ 断裂 |
| REST `/sql`、`/sql/query` | `app/api/routes/sql.py` | ✅ | ✅（若开关开） | ⚠️ 仅 user_id 头 |
| Tool `sql_query_tool` | `tools/sql.py` | ✅ | ✅（若开关开） | ⚠️ contextvar user_id |
| Tool `execute_sql_tool`（workflow 原始 SQL） | `tools/sql.py` | ✅ | ❌ 无注入点 | ❌ 无 |
| MCP `sql_query` / `list_tables` | `mcp_servers/servers/sql.py` | ✅ / ❌ | ❌ | ❌ 无 |

18 问答案（关键项）：

1. **Router 怎么选择 SQL**：主图三层 Router（rule→vector→LLM）→ planner 把 `sql.query` 编入 DAG → supervisor Send 调度；`sql.query` 在 `capabilities.yaml` 有映射，direct 模式经 tool_selector 也可达。
2. **模型**：全局 `backend.infra.llm.llm`（DeepSeek/硅基流动 Qwen3-32B，按 .env），无 SQL 专用模型。
3. **Prompt**：`prompts/registry.py` 注册 `sql.generator` / `sql.router`；默认模板 `prompts/defaults/sql_generator.yaml`、`sql_router.yaml`。⚠️ **DB 永远赢 YAML**（prompt_service 已发布版本优先），改 prompt 必须走 create_draft→publish + 重启 backend。
4. **Schema 来源**：`backend/sql/data/schema_config.py::SCHEMA_CONFIG`（代码内静态配置，非 DB introspection）；敏感列对 LLM 隐藏（`get_table_info` 过滤）。
5. **生成后处理**：`sql_validator.validate`（AST 6 层）→ `inject_row_filter`（默认关）→ `execute_sql_struct`。
6. **Parser**：有，sqlglot，`read="postgres"`，AST 遍历（非 regex 边界）。
7. **只读验证**：三层 —— ①AST：单语句 + 仅 SELECT + 递归禁写（含 CTE）+ 禁 SELECT INTO/FOR UPDATE；②事务：`SET TRANSACTION READ ONLY` + 连接级 `set_session(readonly=True)`；③DB 角色 `agent_readonly`。
8. **Table whitelist**：有，`schema.table` 全限定名 + schema 白名单，AST 提取全部 FROM/JOIN（含子查询/CTE），未登记即拒（fail-closed）。
9. **参数化**：行级注入值走 psycopg2 `%(name)s` 参数通道；LLM 生成 SQL 中的 literal 仅只读执行（无写路径），现状可接受。
10. **DB 用户**：`agent_readonly`（`PG_READONLY_USER`，config/database.py:60），角色实存、仅 SELECT（7 业务 schema ✅ + public 20 张应用表 ⚠️ 纵深缺口）。
11. **Executor 位置**：app（/chat/stream 同步）与 worker（异步任务续跑同一图代码）都会执行 → 发布需同时 rebuild app + worker。
12. **step_results**：`SQLSkill.execute` → Pydantic `SQLResult.model_dump()` → `sr["output"]`，且过 L1 预算（`_apply_tool_result_budget`）→ 规格书 §53/§54 已满足。
13. **previous_outputs**：supervisor 调度时注入 `step_results`；`business.analyze` 从 `previous_outputs` 读 SQLResult。
14. **Reporter**：读 `all_step_results` 渲染 Markdown（数字一致性 E2E 在 STOP D 验证）。
15. **SQL Audit**：**不存在**。无 sql_query_audits 表、无决策审计写入（029_rbac_audit 是 RBAC 管理面审计，另一码事；trace_store 是通用 trace，不含 guard 决策语义）。
16. **超时**：`schema_config.query_timeout = 5.0s` → `SET LOCAL statement_timeout`（DB 层真实取消）+ SQLSkill 层 policy 注册表 15s + Deadline 治理。
17. **LIMIT**：`max_limit = 100` 强制添加/覆写（含 LIMIT ALL 等非数字字面量强制覆写为 max）。
18. **tenant/department 字段**：**业务表全部没有**（109 列逐一核对）。身份侧：Principal/AuthorizationContext 有 tenant_id/department/data_scope（data_scope 未消费）；图通道 `RequestContext` 有 user_id/tenant_id/department 但**无 roles → 推不出 data_scope**。

## B. 已有能力 vs 缺口（对照规格书）

### 已满足（STOP B 只需补测试，不需要重写）

| 规格书条目 | 现状实现 |
|---|---|
| §18 只允许 SELECT / §19 禁多语句 / §20 CTE 写检测 | `sql_validator._check_statement_type` + `_check_no_write_in_subqueries`（walk 全 AST） |
| §14/88/89 表白名单 + schema 限定 + 引号/保留字归一 | `_check_table_allowlist`（AST 名称不受引号影响）+ f19 保留字补引号 |
| §16/86/87 敏感列全表达式拒绝（SELECT/WHERE/GROUP/ORDER/HAVING） | `_check_sensitive_columns`：`find_all(exp.Column)` 覆盖一切列引用，别名回溯，无限定名 fail-closed |
| §17 SELECT * 防泄露 | `_check_star_projection`（敏感表拒绝星号投影） |
| §21/22 sqlglot + postgres dialect | 已用（ILIKE/JSONB/::cast 不误判） |
| §25/42 只读事务 | `set_session(readonly=True)` + `BEGIN` + `SET TRANSACTION READ ONLY` |
| §26/27 agent_readonly 只读角色 | 已存在且仅 SELECT（public 授权待收口） |
| §29 参数化注入 | `%(name)s` 占位符 + params 通道 |
| §31/32/33/34 UNION/子查询/JOIN/alias 逐表注入 | `inject_row_filter` 按 `(表,别名)` 逐引用注入（自连接逐别名），`find_all(exp.Table)` 天然覆盖 UNION 分支/子查询/JOIN |
| §37 缺参数 fail-closed | 严格模式抛 RowSecurityError（不静默跳过） |
| §39/81/82 LIMIT 强制/覆写 | `_ensure_limit`（顶层 LIMIT 语义，子查询 LIMIT 不误判） |
| §41 statement_timeout | `SET LOCAL statement_timeout`（5s，DB 层真取消，非仅 asyncio） |
| §44/45 危险函数黑名单 | pg_sleep/dblink/lo_import/lo_export/pg_read_file 等 11 个 |
| §46 Schema 最小暴露 | `get_table_info` 对 LLM 隐藏敏感列、只给白名单表（MCP list_tables 例外，见 G7） |
| §52→134/135 所有尝试重走 Guard | 重试都经 validate；ValidationError 带反馈重试仍重走 Guard |
| §53/54 L1 Context Budget + 结构化保留 | SQLSkill 已接 `_apply_tool_result_budget`，保留 columns/row_count/rows |
| §61/62 错误分类与对外文案脱敏 | `_classify_pg_error`（SQLSTATE 优先）+ `_public_error_text`（原文只进日志） |
| §55 结果层脱敏 | `masked_columns` + lineage 别名回溯（`SELECT name AS n` 仍打码） |

### 缺口清单（STOP B/C 工作项来源）

| # | 缺口 | 证据 | 对应规格书 |
|---|---|---|---|
| G1 | **权限门缺失**：无 `sql.read` 权限点，4 个入口都不做权限检查 | `authorization.py::ROLE_PERMISSION_CODES` 无 sql.read | §八/九 |
| G2 | **图通道身份断裂**：`SQLSkill.execute` 调 `ask_struct(question)` 不传身份；`RequestContext` 无 roles/data_scope 字段 | skill.py:196、request_context.py:114 | §九十五/96 |
| G3 | **data_scope 未消费**：AuthorizationContext.data_scope 已算出（viewer=self/editor=department/admin=all）但 SQL 层无消费点 | authorization.py:38 | §十/十一 |
| G4 | **Table Policy Registry 缺位**：row_security 仅 1 表 1 参数（order.orders.customer_id），无 tenant/department 维度、无数据域分类 | schema_config.py:269 | §十三 |
| G5 | **业务表无 tenant/department 列**：租户/部门隔离只能走「数据域分类 + 结构隔离」，或加列（扩 scope） | 109 列逐一核对，见 §C 矩阵 | §十二/六十四 |
| G6 | **execute_sql_tool 绕过 scope 注入**：workflow 原始 SQL 直调无注入点（仍过 validator+只读事务） | tools/sql.py:32 | §91/135 |
| G7 | **MCP 缺陷**：`list_tables` 直查 information_schema 且 `DB_CONFIG` 实为 **agent_memory 元数据库**（连错库 + 内部表名泄露）；`sql_query` 不带身份 | mcp_servers/servers/sql.py:39-55 | §15/46/135 |
| G8 | **审计缺失**：无 sql_query_audits 表与写入 | 全库 grep 无 | §56-58 |
| G9 | **Metrics 缺失**：无 sql_agent_* 指标 | observability/metrics.py | §59-60 |
| G10 | **DB 纵深缺口**：agent_readonly 对 public schema 20 张应用表有 SELECT（白名单外） | information_schema.role_table_grants | §十四 |
| G11 | **Prompt 未声明「权限 WHERE 由系统注入」** | sql_generator.yaml | §49 |
| G12 | **安全拒绝重试语义**：安全拒绝（forbidden_table 等）目前与语法错误一样允许带反馈重试 2 次；规格书 §52 要求安全拒绝为终态 | sql_agent.py:133-140 | §51/52 |
| G13 | **self 身份类型断裂**：`orders.customer_id` 是 integer，而网关 user_id 是字符串（auth 主体 ID）；现配置直接等值比较必然类型错误 → self 值需要显式映射策略 | schema_config.py:269 + tools/sql.py:82 | §三十五 |
| G14 | **Kill switch 缺失**：无 SQL_AGENT_ENABLED 总闸 | config/__init__.py | §126 |

## C. 真实 Schema 与 Table Policy Matrix

真实仓库是**跨境电商业务数据仓库**（agent_business，7 schema × 18 业务表 + ai.tool_approval_requests），规格书 §七示例的 users/departments/projects/project_members **不存在**，矩阵按实库填。

实测数据量（demo 规模）：orders=19、order_items=23、customers=4、products=10、inventory=12、competitor_products=2、daily_profit=3、agent_tasks=1。

| 表 | data_domain（建议） | tenant 列 | department 列 | self 列 | 敏感列 | 脱敏列 | scope 生效语义 |
|---|---|---|---|---|---|---|---|
| product.products | shared | 无 | 无 | 无 | 无（cost_price 列为商业敏感候选，待业务确认） | 无 | 全部 scope 可读（tenant 结构隔离） |
| product.categories | shared | 无 | 无 | 无 | 无 | 无 | 同上 |
| product.product_tags | shared | 无 | 无 | 无 | 无 | 无 | 同上 |
| order.orders | personal | 无 | 无 | customer_id | 无 | 无 | **self: customer_id = 自我映射值**（G13）；department/all：本表无部门列 → all 可读；department scope 用户按 D2 语义 |
| order.order_items | personal(经 join) | 无 | 无 | 无（经 order_id 归属 orders） | cost（利润成本，候选） | 无 | 无直接 self 列 → 按 §三十六：第一版对 self 用户 deny，或经 orders 半连接（不做，安全优先 → deny） |
| order.refunds | personal(经 join) | 无 | 无 | 无 | 无 | 无 | 同 order_items |
| inventory.inventory | shared | 无 | 无 | 无 | 无 | 无 | 全部 scope 可读 |
| inventory.warehouses | shared | 无 | 无 | 无 | 无 | 无 | 同上 |
| inventory.purchase_orders | shared | 无 | 无 | 无 | 无 | 无 | 同上 |
| customer.customers | shared(P II) | 无 | 无 | id（候选） | 无实名列（phone/email 预留注释已在） | **name → "张\*\*\*"（已生效）** | 全部 scope 可读（脱敏兜底） |
| customer.customer_behavior | shared(P II 关联) | 无 | 无 | 无（经 customer_id 归属） | 无 | 无 | 无 self 列 → self 用户 deny（同 §三十六） |
| crawler.competitor_products | shared | 无 | 无 | 无 | 无 | 无 | 全部 scope 可读 |
| crawler.competitor_price | shared | 无 | 无 | 无 | 无 | 无 | 同上 |
| crawler.product_reviews | shared | 无 | 无 | 无 | 无 | 无 | 同上 |
| finance.expenses | internal（建议） | 无 | 无 | 无 | amount 属商业敏感候选 | 无 | **仅 all 可读**（D4 待确认） |
| finance.daily_profit | internal（建议） | 无 | 无 | 无 | 同上 | 无 | 同上 |
| ai.agent_tasks | internal | 无 | 无 | 无 | **user_query 含他人提问内容** | 无 | **仅 all 可读**（跨用户内容，建议收紧） |
| ai.agent_trace | internal | 无 | 无 | 无 | **input/output JSONB 含他人会话与内部 prompt** | 无 | **仅 all 可读**（同上） |
| （不在白名单）ai.tool_approval_requests | — | — | — | — | 审批工单（含指纹/reviewer） | — | validator 层已不可达 ✅；建议 DB 层回收 agent_readonly 的 SELECT（G10） |

说明：
- **tenant 列全空** → 当前租户隔离是**部署级结构隔离**（一套 agent_business = 一个租户），Guard 保留 tenant 注入能力（TablePolicy.tenant_column 登记后才注入），规格书 §十二的「tenant 永远优先」在此语义下成立且不虚构字段。
- public schema 20 张应用表（selection/feedback/competitor_watch 等）不在 SQL 白名单，validator 层不可达；G10 建议回收 DB 层授权使两层一致。

## D. Data Scope 设计（STOP B 实施蓝图）

### D1. SQLPolicyContext（规格书 §十，复用不新建）

```python
# backend/sql/policy.py（新模块；策略数据仍以 schema_config.py 为唯一事实源，G2）
@dataclass(frozen=True)
class SQLPolicyContext:
    principal: Principal            # 复用 security/principal.py，不造第二套
    authz: AuthorizationContext     # 复用 security/authorization.py
    tenant_id: str                  # authz.tenant_id
    department: str                 # authz.department
    user_id: str                    # principal.user_id
    data_scope: Literal["all", "department", "self"]   # 未知值 → fail-closed DENY
```

构建点（三通道同源，不重复解析身份）：
- HTTP：`deps.get_authorization_context` 已有 → `/sql*` 路由直接消费；
- 图通道：入口构建 AuthorizationContext 后把 `roles`/`data_scope` 增补进 `RequestContext.checkpoint_safe()`（规格书 §96 字段集，新增两字段，向后兼容）；
- Tool/MCP 直调：contextvars（`get_tool_user_id/department/tenant_id`）→ `resolve_tool_principal` → 同一构建函数。

### D2. data_scope 注入语义（真实字段版）

沿用 row_security 已验证的「逐 (表,别名) AST 注入 + psycopg2 参数化」引擎，扩展为三维：

```
对 SQL 引用的每张表（含 UNION 分支/子查询/CTE/JOIN，逐别名）：
  data_domain == internal  → data_scope != all → DENY（SQL_TABLE_NOT_ALLOWED）
  tenant_column 存在       → 恒注入  alias.tenant_id = :tenant_id（all 也不例外，§十二）
  data_scope == department 且表有 department_column：
      department 为空      → DENY（SQL_SCOPE_UNAVAILABLE，§三十七/七十）
      否则注入 alias.department = :department
  data_scope == self 且表有 self_column：
      self 值映射失败（G13）→ DENY（SQL_SCOPE_UNAVAILABLE）
      否则注入 alias.self_column = :self_value
  data_domain == shared    → 不注入（无部门/个人归属的业务数据）
  用户 SQL 已写 scope 谓词 → 照常叠加注入（AND 语义，结果为 0 行，§三十）
```

department/self 值一律参数绑定（`%(name)s`），禁止字符串拼接（§二十九）。

**决策点 D2**：department scope 用户查 shared 表（如库存、商品）——本仓库存量表无 department 列、也不该虚构。
推荐语义：**shared 表对所有持 sql.read 者可读**（数据本身无部门归属）；「部门隔离」在未来表真带 department 列时按上表自动生效（只改 policy 登记不改代码）。备选：department 用户对 shared 表也 deny（demo 不可用，不推荐）。

### D3. 权限点与角色映射（规格书 §八/九）

- 新增 `sql.read` 进 `ROLE_PERMISSION_CODES`（单一来源 security/authorization.py，SQL 层不自判角色）。
- **推荐映射：editor + admin 持有 sql.read；viewer 无**（SQL 数据分析属编辑者能力；viewer 保持 RAG 只读）。未持权 → 403 `SQL_PERMISSION_DENIED`，用户文案「当前查询超出你的数据访问范围」（§六十二），不泄露表存在性（§六十三）。
- enforcement 点：`/sql`、`/sql/query`、SQLSkill（图）、MCP sql_query、execute_sql_tool——全部统一在一个 `require_sql_read(policy_ctx)` 入口函数。

### D4. 数据域分类确认（C 矩阵「建议」列）

- 推荐：finance.* 与 ai.* 划 internal（仅 all）——ai.* 含他人提问/会话内容，跨用户暴露于 self/department 用户属真实泄露面；finance 利润成本数据同理。
- 影响：editor/department 用户的「利润分析」场景将被拒（改为提示无权限）。**这是产品语义决策，需要确认**；若业务要求 editor 可读 finance，则 finance 归 shared，仅 ai.* 收 internal。

### 其余设计定版

- **GuardedSQL / SQLPolicyGuard**（§二十三）：`validate_and_rewrite(sql, policy) -> GuardedSQL(original_sql, executable_sql, referenced_tables, applied_scopes, row_limit)`；内部 = 现有 sql_validator + 新 scope 注入层；**executor 只收 GuardedSQL**（§九十一），`execute_sql(sql:str)` 底层函数保留给 tests/internal（§九十二），生产路径改走 `execute_guarded(guarded, policy_ctx)`。
- **安全拒绝终态化**（G12/§五十二）：ValidationError 按 layer/reason 分两类——`forbidden_table / forbidden_column / dangerous_function / non_select / multi_statement` → 终态（error_code=SQL_UNSAFE_QUERY，不重试）；`解析失败/别名未定义` 等语法类 → 保留现有带反馈重试（≤2 次，每次重走 Guard，§一百三十四）。
- **审计**（G8/§五十六~五十八）：新表 `sql_query_audits`（migration **042**，登记 `MIGRATION_TARGETS`——吸取 041 教训）；落 **agent_memory**（observability 族惯例，OBS_DB_PG_CONFIG）；字段按 §一百二十九（query_hash=sha256(normalized_sql)，SQL 全文按现有隐私策略先存 executed_sql、original_sql 截断）；执行前决策内存判定不可失败开放，audit 持久化软降级（失败→日志+metrics）。
- **Metrics**（G9/§五十九）：`sql_agent_queries_total{status,decision}`、`sql_agent_guard_rejections_total{reason}`（固定低基数 reason 枚举）、`sql_agent_query_duration_seconds`、`sql_agent_rows_returned_total`；禁 user_id/tenant/表名/SQL hash 进 label（§一百三十一）。Trace span `sql.guard`（attributes: table_count/scope/rewrite_applied/decision/reason）。
- **错误码**（§六十一）：SQL_PERMISSION_DENIED / SQL_UNSAFE_QUERY / SQL_TABLE_NOT_ALLOWED / SQL_COLUMN_NOT_ALLOWED / SQL_SCOPE_UNAVAILABLE / SQL_TIMEOUT / SQL_EXECUTION_FAILED / SQL_EMPTY_RESULT，复用 shared/error_protocol envelope。
- **Kill switch**（G14/§一百二十六）：`SQL_AGENT_ENABLED`（config/__init__.py，默认 true——存量生产功能；false 时 SQLSkill/路由/Tool 返回 not_configured，风格同域开关）。
- **LIMIT/超时复用**（§一百二十五「已有配置则复用」）：保留 `max_limit=100`、`query_timeout=5.0s`（schema_config.py 单一来源），不另设 SQL_MAX_ROWS/SQL_QUERY_TIMEOUT_MS 第二配置。
- **Prompt 增补**（G11/§四十九）：sql.generator 增加「不要在 SQL 中写租户/部门/用户权限 WHERE，数据范围由系统安全层统一注入」；走 DB 发布流程（draft→publish）+ 重启 backend（prompt 陷阱已知）。
- **E2E 数据**（§六十五）：demo 体量小（4 客户/19 订单）。self-scope E2E 采用**映射约定**：SQLPolicyContext.self_value 解析器将数字型 user_id（"3"）映射为 customer_id=3，非数字型 fail-closed；在 auth 侧造 viewer/editor/admin+ 数字 ID 测试号，订单/客户数据带 marker（order_no 后缀 TENANT-A-HR-111 等）。**不做跨租户数据级 E2E**（表无 tenant 列，虚构即造假）——租户隔离以「策略登记检查 + tenant 注入逻辑单测（fixture 表带 tenant_id 列）+ 结构隔离声明」验证，报告中如实标注。

## E. 决策点汇总（进入 STOP B 前需确认）

| # | 决策 | 推荐 | 备选 |
|---|---|---|---|
| D1 | 租户隔离形态 | 结构隔离（单租户仓库）+ Guard 预留 tenant_column 注入，不做跨租户数据级 E2E | 给 18 表加 tenant_id 列 + 迁移造数（扩 scope，违背 §四） |
| D2 | department scope 对 shared 表 | 放行（shared=无部门归属数据） | deny（department 用户几乎无表可查） |
| D3 | sql.read 角色映射 | editor+admin；viewer 403 | 三角色全放（viewer=self 几乎查不到数据） |
| D4 | internal 域划分 | finance.* + ai.* 仅 all | finance 保持 shared（editor 利润场景保留），仅 ai.* 收 internal |

## F. STOP B 工作项预告（Guard + Scope 单测，下一阶段）

1. `backend/sql/policy.py`：SQLPolicyContext / GuardedSQL / SQLPolicyGuard / scope 注入引擎（扩展 row_security 三维）。
2. schema_config.py：TablePolicy 登记（data_domain/self_column/tenant_column/department_column）+ internal 域。
3. security/authorization.py：ROLE_PERMISSION_CODES += sql.read（D3）。
4. RequestContext 增补 roles/data_scope（checkpoint_safe 同步）。
5. 测试（`backend/tests/sql/` 新增，全部 `--no-cov` 局部跑）：test_sql_guard_readonly / tables / columns / scope_tenant / scope_department / scope_self / guard_bypass（UNION/subquery/CTE 写/multi-statement/system table/dangerous function/quoted/schema 限定/comments）——fixture 表带 tenant/department 列验证注入逻辑，真实 18 表回归现有 demo queries（§一百三十二）。
6. 既有 8 个 SQL 测试全绿回归。

## G. Git 状态

- 工作区唯一 dirty：`docs/customer-service/客服验收测试场景清单-2026-09-22.md`（另一会话所有，本阶段不触碰、不提交）。
- 本报告为 STOP A 唯一产物，路径限定提交；未改任何执行代码（符合 §一百五十六 STOP A 边界）。
