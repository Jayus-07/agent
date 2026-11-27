# SQL Agent 业务域与智能查数架构指南

## 一、 执行链路与编排流水线

SQL Agent 核心任务是将用户的自然语言意图转换为可安全执行的只读 SQL，并在严格的租户与权限隔离下执行并返回结构化结果。执行流水线由 `backend/sql/sql_agent.py` 严格编排，整体分为 7 个关键阶段：

```
[用户提问]
   │
   ▼
1. precheck ───────────────────► 权限门拦截：校验 sql.read 与身份有效性（无权限直接拒答，不调 LLM）
   │
   ▼
2. _prepare_sql_question ──────► 问句预处理：提取时间范围、指标关键词与实体词
   │
   ▼
3. select_tables ──────────────► 候选表路由：依据 Schema 元数据与表域权限筛选可见物理表
   │
   ▼
4. generate_sql ───────────────► 结构化生成：LLM 基于白名单表结构与少样本生成只读 SQL
   │
   ▼
5. validate_and_rewrite ───────► 四层 AST 纵深防御：语法校验、表域判定与三维租户条件原地参数化注入
   │
   ▼
6. execute_sql_struct ─────────► 物理安全执行：ThreadedConnectionPool 只读账号 + 事务级超时 + 动态脱敏
   │
   ▼
7. _observe ───────────────────► 可观测归因：记录 sql.agent、sql.guard、sql.execute 细粒度 Span 与指标
```

### 1. API 与 Skill 调用入口
- **HTTP API 入口**：`POST /api/sql/ask`（位于 `backend/app/api/routes/sql.py`），通过 `_ask_sql_agent` 补齐 `sql_agent` Root Trace，要求请求携带合法的 Bearer Token，并在依赖注入阶段完成 `deps.get_authorization_context` 解析。
- **LangGraph Skill 入口**：`SQLSkill` 作为系统内置能力，从主图状态 `SQLSkillState` 中提取经过验证的 `Principal` 与 `AuthorizationContext`，统一调用 `build_sql_policy_context` 装配策略上下文，严禁客户端以请求体参数（如 `user_id`、`tenant_id`）绕过服务端身份注入。

---

## 二、 四层 AST 纵深安全防御机制（`SQLPolicyGuard`）

系统绝不采用脆弱的正则表达式或外部 `WHERE` 字符串拼接，而是完全基于 `sqlglot` 抽象语法树（AST）构建了**四层纵深防御体系**（源码：`backend/sql/policy.py`），实现编译级的安全保障。

```
┌────────────────────────────────────────────────────────────────────────┐
│                        SQLPolicyGuard 防御纵深                          │
├────────────────────────────────────────────────────────────────────────┤
│ Layer 1: 权限门（Precheck）                                            │
│   • 校验 sql.read 权限点                                               │
│   • user_id 必填性校验（空身份 Fail-Closed 阻断，拒绝旁路调用）        │
│   • data_scope 合法性校验（合法值: all / department / self）           │
├────────────────────────────────────────────────────────────────────────┤
│ Layer 2: 语法与 AST 6 层硬校验（sql_validator）                        │
│   • 根节点必须为只读 exp.Select，禁止单语句多执行                     │
│   • 拦截 DDL/DML、SELECT INTO、FOR UPDATE/SHARE 锁子句                 │
│   • 函数正向白名单（POSITIVE_FUNCTION_ALLOWLIST 拦截危险函数）         │
│   • 强制安全 LIMIT 门禁（默认 100，最大 1000）                          │
├────────────────────────────────────────────────────────────────────────┤
│ Layer 3: 真实物理表解析与表域判定                                       │
│   • 遍历 AST 中所有 exp.Table 节点                                     │
│   • 提取 CTE 集合，剥离 exp.CTE 别名伪装                               │
│   • 依 TablePolicy.data_domain 校验访问范围（shared / personal / internal）│
├────────────────────────────────────────────────────────────────────────┤
│ Layer 4: 基于 Select Scope 的三维租户条件原地注入                      │
│   • 递归遍历所有 exp.Select 作用域（涵盖子查询、UNION ALL、CTE body）   │
│   • 针对每个 Select 作用域，提取当前引用的物理表及对应别名             │
│   • sel.where(cond, copy=False) 原地参数化注入隔离条件                 │
│   • 提取 params 字典，杜绝二次注入与跨租户逃逸                         │
└────────────────────────────────────────────────────────────────────────┘
```

### 1. Layer 1：前置权限门（Fail-Closed）
- **权限点校验**：通过 `policy.authz.has_permission("sql.read")` 显式判断权限。无权限直接抛出 `SQLPolicyError(SQL_PERMISSION_DENIED)`。
- **身份防穿透**：严格执行 Fail-Closed 准则。若 `policy.user_id` 为空，直接抛出 `SQL_SCOPE_UNAVAILABLE`。生产链路恒有 JWT/MCP/图状态身份，空 `user_id` 均被视作旁路恶意调用。

### 2. Layer 2：AST 语法与函数正向白名单（`backend/sql/sql_validator.py`）
- **单语句只读校验**：输入必须解析为单个 `exp.Select` 表达式，拦截 `;` 分号注入与批处理多语句。
- **锁语法拦截**：阻断任何带有 `FOR UPDATE`、`FOR SHARE`、`LOCK IN SHARE MODE` 的加锁语句，杜绝读请求对写事务造成死锁。
- **全函数正向白名单（`POSITIVE_FUNCTION_ALLOWLIST`）**：
  - *允许清单*：常用聚合函数（`COUNT`, `SUM`, `AVG`, `MIN`, `MAX`）、日期转换函数（`DATE_TRUNC`, `NOW`, `TO_CHAR`）、数学运算函数（`ROUND`, `COALESCE`, `ABS`）及安全字符串函数（`SUBSTRING`, `CONCAT`, `LOWER`）；
  - *严格拦截*：彻底拦截底层文件读取（`pg_read_file`, `pg_ls_dir`）、网络通信、系统状态变更以及时间盲注/DoS 延迟函数（`pg_sleep`, `sleep`, `benchmark`）。
- **物理强制 LIMIT**：遍历 AST 顶层节点，若缺失 `LIMIT` 自动注入 `LIMIT 100`；若模型生成的 `LIMIT` 超过系统安全上限 `MAX_LIMIT=1000`，直接将其改写回 1000，防止巨量扫描耗尽内存。

### 3. Layer 3：真实物理表解析与表域判定
- **CTE 别名伪装剥离**：
  ```python
  cte_names = {cte.alias.lower() for cte in stmt.find_all(exp.CTE)}
  table_refs = _collect_table_refs(stmt, cte_names)
  ```
  模型常通过 `WITH allowed_table AS (SELECT * FROM secret_table)` 进行混淆。Guard 遍历 AST 提取所有 `exp.CTE` 的别名，在收集表引用时彻底过滤掉 CTE 虚假别名，精准锁定底层物理表。
- **三维表域判定规则（`TablePolicy.data_domain`）**：
  - **`shared`（通用公共表）**：所有合法数据范围均可读取，但若表模型定义了 `tenant_column`，依然无条件追加租户参数隔离。
  - **`personal`（个人私有数据表）**：包含敏感个人数据（如订单流水、客户档案）。
    - 若用户范围为 `data_scope == "department"`，该表必须声明 `department_column`；若无部门列，因数据无法安全切分，触发 Fail-Closed 抛出 `SQL_TABLE_NOT_ALLOWED` 阻断。
    - 若用户范围为 `data_scope == "self"`，该表必须声明 `self_column`，否则拒绝访问。
  - **`internal`（系统内部敏感表）**：仅允许最高权限 `data_scope == "all"` 查询，其余任何范围直接拒绝。

### 4. Layer 4：基于 Select Scope 的三维参数化原地注入
#### 为什么外层 `WHERE` 字符串拼接是致命的？
外层简单追加 `WHERE tenant_id = 'xxx'` 存在灾难性漏洞：在面对 `UNION ALL` 联合查询、`FROM (SELECT ...)` 派生表或 `WHERE id IN (SELECT order_id FROM ...)` 深层子查询时，外层的 `WHERE` 条件根本无法下推至子查询内部，子查询将在无租户过滤的状态下全量读取跨租户数据。

#### 语法树原地参数化注入源码剖析
`SQLPolicyGuard` 按 SELECT Scope 进行分层处理，并使用参数化占位符规避注入：
```python
# 1. 遍历 AST 中所有 Select 节点（逐组处理子查询与 UNION 分支）
for sel in list(stmt.find_all(exp.Select)):
    scope_refs = _collect_scope_table_refs(sel, cte_names)
    conditions = []
    for qualified_name, alias in scope_refs:
        tp = schema_loader.get_table_policy(qualified_name)
        conds = self._conditions_for_table(tp, alias, scope, policy, params, applied)
        conditions.extend(conds)
    for cond in conditions:
        # ⚠️ 关键细节：copy=False
        # sqlglot 的 Select.where 默认 copy=True（返回修改后的副本而丢弃），
        # 必须显式传入 copy=False 才能在原语法树节点上原地追加 AND 条件！
        sel.where(cond, copy=False)
```

#### 隔离参数命名通道与大整数守卫
- **参数占位符**：使用 psycopg2 命名字典通道：
  - `_PARAM_TENANT = "sql_scope_tenant_id"`
  - `_PARAM_DEPARTMENT = "sql_scope_department"`
  - `_PARAM_SELF = "sql_scope_self_value"`
- **`resolve_self_value` 整型溢出防御与 64 位雪花 ID 支持**：
  ```python
  PG_INTEGER_MAX = 2_147_483_647  # 32 位整型上限 (2^31 - 1)
  PG_BIGINT_MAX = 9_223_372_036_854_775_807  # 64 位整型上限 (2^63 - 1，支持雪花 ID)

  def resolve_self_value(
      user_id: str | None,
      *,
      max_value: int = PG_BIGINT_MAX,
  ) -> int | None:
      s = str(user_id or "").strip()
      if not s.isdigit():
          return None
      value = int(s)
      if value < 0 or value > max_value:
          return None
      return value
  ```
  业务仓库中 `orders.customer_id` 为整数型。为避免微服务与分布式体系中标准 64 位雪花算法 Snowflake ID（如 `1850123456789012345`）被 32 位阈值阻断，系统原生支持 64 位 `PG_BIGINT_MAX` 边界守护，并允许指定具体列类型的物理上限。非纯数字、负数或越界值立即返回 `None` 并在 Guard 中 Fail-Closed 阻断，杜绝类型转换异常穿透到数据库执行期。

---

## 三、 执行层只读隔离与连接池自愈

数据库执行层收敛于 `backend/sql/executor.py`，配置了严密的执行期防线：

1. **连接池借出探活与自愈（`ThreadedConnectionPool`）**：
   - 生产环境面临空闲连接超时被防火墙或 PG `idle_in_transaction_session_timeout` 切断的风险；
   - 连接池在借出（`getconn`）时执行快速健康探测（`SELECT 1`），若连接失效自动销毁并重建，实现连接断线自愈；
   - 遭遇并发峰值时，捕获 `PoolExhaustedError` 抛出标准限流异常，防止大量并发请求打垮数据库套接字。
2. **三重会话级只读与超时保护**：
   - 专用只读账号：使用 `agent_readonly` 角色执行，在数据库权限体系中已被 `REVOKE ALL PRIVILEGES` 且仅被赋予目标表的 `SELECT` 权限；
   - 事务级声明：执行前显式执行 `SET TRANSACTION READ ONLY;`；
   - 语句级硬超时：每次查询显式注入 `SET LOCAL statement_timeout = 5000;`（5 秒超时熔断），防止由于复杂笛卡尔积或全表扫描造成数据库 CPU 尖刺；
   - 连接归还复位防护：`_get_conn` 归还连接池前，除执行 `conn.rollback()` 外，在未损坏连接上显式执行 `RESET statement_timeout;`，防止非事务或降级模式下将超时参数泄漏污染后续借出该连接的其他请求。
3. **动态列级数据脱敏**：
   - 执行返回的字段元数据经过 `schema_loader` 列级脱敏规则过滤，对手机号、身份证、邮箱等 PII 字段根据角色动态打码（Masking），确保敏感数据不落明文日志与前端展示。

---

## 四、 终态分类与错误码契约

`SQLPolicyGuard` 定义了严格低基数的错误码标准，杜绝任何敏感表结构反向泄漏：

| 错误码 | 触发根因 | 对外安全回显文案（`_USER_SAFE_TEXT`） | 重试策略 |
| :--- | :--- | :--- | :--- |
| `SQL_PERMISSION_DENIED` | 用户未持有 `sql.read` 权限点 | “当前查询超出你的数据访问范围。” | **终态阻断**，禁止 LLM 重试 |
| `SQL_TABLE_NOT_ALLOWED` | 访问 `internal` 表或个人表缺少对应范围归属列 | “该数据不在当前可访问范围内。” | **终态阻断**，禁止 LLM 重试 |
| `SQL_SCOPE_UNAVAILABLE` | `user_id` 为空、`data_scope` 非法或范围映射失败 | “暂时无法确定你的数据访问范围，查询被拒绝。” | **终态阻断**，禁止 LLM 重试 |

- **详细审计归因**：详细的拒绝原因（如缺少哪个字段、具体的物理表名）仅记录在 OpenTelemetry Trace 的 `sql.guard` Span 与结构化日志中，绝对不返回给客户端或模型。
- **严禁安全拒绝进入重试循环**：`TERMINAL_DENY_REASONS` 包含上述错误码，Agent 捕获后立即收敛至终态报错，防止 LLM 通过反复改写 Prompt 探测系统内部库表结构（Prompt Injection / Blind Probing）。

---

## 五、 延迟优化：思考型模型的隐式思维链抑制

### 1. 痛点：高频结构化生成遭遇思考延迟爆炸
在早期将支持深度思考的模型（如 DeepSeek-R1、GLM-4-Thinking）接入 SQL 生成与路由节点时，发现原本只需生成简单 `SELECT count(*) FROM orders` 的请求，端到端耗时常飙升至 **12~18 秒**。分析 Trace Span 发现，模型在输出最终 SQL 前，自主开启了长达上千 Token 的 CoT 思维链分析，生成了大量关于语法推演的冗余思考 Token。

### 2. 生产解决方案与参数锁定
智能查数属于**高确定性、强结构化、强语法受限**的业务场景，不需要开放式发散推理。平台在 `backend/sql/router.py` 与 `backend/sql/sql_generator.py` 中显式固化了底层推理载荷：
```python
# 显式关闭思维链推理，锁定贪婪采样
llm_kwargs = {
    "extra_body": {"thinking": {"type": "disabled"}},
    "temperature": 0.0,
}
```
通过请求层强制下发 `thinking: {"type": "disabled"}`，将选表与 SQL 生成耗时由原本的 15s 直接压降到 **1.8~2.5 秒**，端到端 P99 响应速度提升超 **80%**。

---

## 六、 零售 ERP 单店试点与多租户测试资产

为验证 SQL Agent 在真实企业级复杂多租户环境下的鲁棒性，平台落地了**零售 ERP 单店试点业务架构**（迁移 `150_retail_erp_pilot_schema.sql`，测试数据种子位于 `backend/sql/seeds/retail_erp_pilot.sql`）：

1. **核心试点表与隔离列声明**：
   - `retail_stores`（门店档案）：`data_domain="shared"`, `tenant_column="tenant_id"`；
   - `retail_inventory`（实时库存）：`data_domain="shared"`, `tenant_column="tenant_id"`；
   - `retail_sales_orders`（销售流水）：`data_domain="personal"`, `tenant_column="tenant_id"`, `department_column="store_code"`, `self_column="cashier_id"`；
   - `retail_shift_logs`（交接班记录）：`data_domain="internal"`，仅限全量数据管理员审计可见。
2. **验证保障**：
   - 定向测试用例集成于 `backend/tests/sql/test_sql_policy_guard.py` 与 `test_sql_agent_retail.py`，完整覆盖多表 JOIN、嵌套子查询与跨门店越权攻击场景，确保安全防线 100% 可用。
