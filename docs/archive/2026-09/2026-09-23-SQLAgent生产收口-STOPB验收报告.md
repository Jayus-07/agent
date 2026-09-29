# SQL Agent Production Closure — STOP B 验收报告（Guard + Scope 单测）

> 阶段：SQL Agent Production Closure｜STOP B
> 日期：2026-09-23
> 前置：STOP A（b6a196a）4 项决策 D1-D4 已由用户定版
> 结论：**STOP_B_PASS=true**（14 项完成标准全部满足；未进入 STOP C）

## A. 修改文件 / Commit

| Commit | 内容 |
|---|---|
| `2cd4987` feat(sql) | 核心实现 12 文件（+705/-6） |
| `6ab85d0` test(sql) | 测试 12 文件（+978） |

核心：`backend/sql/policy.py`（新）、`sql/sql_agent.py`（policy 链）、`sql/schema_loader.py`（TablePolicy 加载）、`sql/data/schema_config.py`（18 表策略登记）、`security/authorization.py`（sql.read + widest_data_scope + build_tool_authorization_context）、`security/principal.py`（resolve_tool_principal 加 roles）、`core/request_context.py`（roles/data_scope + checkpoint_safe）、`orchestration/request_context.py`（dict 还原同步）、`orchestration/graph/runner.py` + `graph/system.py` + `app/api/routes/chat.py`（入口透传）、`skills/sql/skill.py`（策略上下文装配）。

Git 隔离：全程双重 pathspec 限定；剩余 dirty（tasks/Phase2、customer_service/缺陷9、docs/cs）均为其他会话所有，未触碰。

## B. SQLPolicyContext

复用不新建（规格书 §十）：`SQLPolicyContext(principal: Principal, authz: AuthorizationContext)`，字段全部从既有授权对象派生（user_id/tenant_id/department/data_scope properties）。

装配链（三通道同源，权限/ scope 推导唯一发生在 security/authorization.py）：
- 图通道：`SQLSkill._build_sql_policy_context(state)` → `get_context_from_state` → `build_sql_policy_context(user_id/department/tenant_id/roles/data_scope)` → `build_tool_authorization_context`（principal.py + authorization.py）
- HTTP 通道：`deps.get_authorization_context`（已有，STOP C 接到 /sql* 路由）
- policy=None（评测 runner/脚本等未接上下文调用）：旧行为，授权未启用语义（与 allowed_kb_ids=None 一致），生产图路径恒有 request_context 故恒走策略链

## C. TablePolicy Matrix 最终版

schema_config.py `table_policies`（18 表全显式登记，未登记默认 shared）：

| 表 | data_domain | tenant_column | department_column | self_column |
|---|---|---|---|---|
| product.products / categories / product_tags | shared | - | - | - |
| inventory.inventory / warehouses / purchase_orders | shared | - | - | - |
| crawler.competitor_products / competitor_price / product_reviews | shared | - | - | - |
| customer.customers / customer_behavior | shared | - | - | - |
| order.orders | personal | - | - | **customer_id** |
| order.order_items / refunds | personal | - | - | -（self→DENY） |
| finance.expenses / daily_profit | **internal** | - | - | - |
| ai.agent_tasks / agent_trace | **internal** | - | - | - |

真实 18 表均无 tenant/department 列（D1 结构隔离）；三个 *_column 为注入能力位，未来表加列只需登记。ai.tool_approval_requests 不在白名单（validator Layer2 已拒）。

## D. sql.read 权限矩阵

| 角色 | sql.read | 行为 |
|---|---|---|
| viewer | ❌ | `SQL_PERMISSION_DENIED`（对外文案「当前查询超出你的数据访问范围。」） |
| editor | ✅ | data_scope=department |
| admin | ✅ | data_scope=all |
| 未知角色/guest | ❌ | fail-closed 拒绝 |

映射唯一来源 `ROLE_PERMISSION_CODES`；权限门是 Guard 第一步，先于 scope/表域判定（不向无权者泄露任何信息）。SQL 层零角色判断。

## E. shared / personal / internal 行为

```
internal  → 仅 all；department/self 拒（SQL_TABLE_NOT_ALLOWED）
personal  → all 全量；self 按 self_column 注入；department 无部门列可表达 → DENY
shared    → all/department 放行不注入（D2）；self 无 self_column → DENY
tenant_column 声明位：任何 scope 声明即注入（all 也不例外，tenant 永远优先）
```

## F. tenant fixture rewrite 示例

fixture `demo.tenant_projects`（tenant_column=tenant_id）+ data_scope=all + tenant_id="tenant-a"：

```sql
-- 输入: SELECT id FROM demo.tenant_projects
-- 输出:
SELECT id FROM demo.tenant_projects
WHERE tenant_id = %(sql_scope_tenant_id)s LIMIT 100
-- params = {"sql_scope_tenant_id": "tenant-a"}
```

tenant_id 缺失 → `SQL_SCOPE_UNAVAILABLE`；值走参数通道，文本中无字面值。

## G. department rewrite 示例

fixture `demo.dept_projects`（personal + department_column=department）+ department scope + dept=hr，用户自带越权谓词：

```sql
-- 输入: SELECT project_name FROM demo.dept_projects WHERE department = 'finance'
-- 输出（安全谓词 AND 叠加，交集语义，不可被用户谓词覆盖）:
SELECT project_name FROM demo.dept_projects
WHERE department = 'finance' AND dept_projects.department = %(sql_scope_department)s
LIMIT 100
-- params = {"sql_scope_department": "hr"}
```

department=NULL + 声明 department_column → `SQL_SCOPE_UNAVAILABLE`。

## H. self rewrite 示例

`order.orders`（self_column=customer_id）+ self scope + user_id="3"（demo 显式数字映射，超 PG integer 范围/非数字一律 DENY）：

```sql
-- 输入: SELECT order_no FROM "order".orders
-- 输出:
SELECT order_no FROM "order".orders
WHERE customer_id = %(sql_scope_self_value)s LIMIT 100
-- params = {"sql_scope_self_value": 3}   ← int（类型回归锁）
```

user_id="u-admin"/空 → `SQL_SCOPE_UNAVAILABLE`；order_items/refunds/customer_behavior 无 self_column → `SQL_TABLE_NOT_ALLOWED`（不做跨表 JOIN 改写）。

## I. fail-closed case（全部有测试锁定）

unknown scope（foo/""/None/"ALL" 大小写）→ DENY；viewer/未知角色/guest → DENY；缺 tenant_id → DENY；department=NULL+有部门列 → DENY；self 不可映射/非数字/超范围 → DENY；self+shared → DENY；internal+非 all → DENY；policy 拒绝为**终态**（SQLAgent 策略链 generate 只调 1 次，不携带 feedback 重试，§五十二）；对外文案为固定安全话术，不含表名/参数。

## J. UNION / subquery / JOIN / alias 测试

- **UNION**：现状 fail-closed——sqlglot 解析为 exp.Union（非 exp.Select），既有 validator Layer1 整条拒绝（分支不可能绕过注入）；同时直测引擎 scope 分组：两分支各自独立收集表引用（未来放开时语义就绪）
- **子查询**：IN/EXISTS 内外两个 SELECT scope 各自注入，别名不串用；子查询里的 internal/personal 表同样触发拒绝；CTE 别名不误当真实表（body 真实表独立成 scope）
- **JOIN**：两表各自注入；shared JOIN personal 对 department 用户整条拒绝；自连接逐别名注入（a/b 各一条，共享同值参数）；无别名表用表名限定
- 注入位置：`Select.where(cond, copy=False)` 原地 AND 追加（修复了 copy=True 返回副本导致注入静默丢失的缺陷，测试锁定）

## K. RequestContext 身份透传

- `RequestContext` 新增 `roles: tuple` / `data_scope: str`（""=未声明，消费方 fail-closed）
- `checkpoint_safe()` 同步两字段（契约测试更新 + 新增 round-trip 测试）；dict 还原路径（checkpointer 开启）同步
- 入口：chat 路由传 `ident.roles`（网关验签头）→ `stream_events/ask` → `iter_events` → `widest_data_scope(roles)` 折算 → RequestContext；每次新请求以当前可信身份覆盖，旧 checkpoint 不提权（规格书 §九十七）
- direct / supervisor 两条调度路径均经 `skill_func(state)`，同一接线自动覆盖
- 未保存 Request/AuthzService/DB Session/ORM 对象（仅可序列化标量与 tuple）

## L. 测试命令与结果

```
pytest tests/sql/ --no-cov                                  → 209 passed（新增 121 + 既有 8 组无回归）
pytest tests/test_registry_consistency.py
        tests/test_layer_consistency.py
        tests/test_adr0001_dual_registry_merge.py --no-cov  → 36 passed
pytest tests/security/test_principal_authorization.py
        tests/orchestration/test_request_context.py
        tests/sql/test_sql_skill_structured.py --no-cov     → 51 passed
```

既有失败（与本阶段无关，不扩 scope）：`tests/test_llm_span_fields.py` 5 例 minimax/deepseek 定价断言（本阶段未触碰计价代码，属基线存量失败）。

## M. Git dirty 隔离情况

工作区剩余 dirty 全部归属并行会话：`backend/tasks/*`、`orchestration/checkpoint/`、`services/task*`、`config/tasks.py`（任务 Phase2 会话）；`customer_service/*`、`cs_graph_node.py`、`cs_prefilter.py`、docs/cs（CS 缺陷会话）。本阶段两个 commit 文件清单与上述零交集；提交前逐一核对 12 个已改文件 diff 规模与本次改动吻合，无他人改动混入。

## N. STOP_B_PASS

**STOP_B_PASS=true** —— 对照 14 项完成标准逐条满足：sql.read 入统一权限体系✅ 权限矩阵锁定✅ 完全复用 Principal/AuthorizationContext✅ TablePolicy 落地✅ internal 仅 all✅ tenant/department/self 能力可测试✅ 无 ownership 表达即 fail-closed✅ 注入全参数化✅ UNION/subquery/JOIN/alias scope 不丢✅ RequestContext 不断链✅ 既有 validator/readonly/LIMIT/timeout 零回归（209 全绿）✅ 守卫测试无新增失败✅

按规格停止，不进入 STOP C。STOP C 待办（已登记）：/sql* 路由与 Tool/MCP 通道接权限门、execute_sql_tool 旁路收口、MCP list_tables 连错库修复、sql_query_audits 迁移（042 + MIGRATION_TARGETS）、sql_agent_* metrics、sql.guard trace span、安全拒绝重试终态化（旧链）、DB public SELECT grant 回收、SQL_AGENT_ENABLED kill switch、APISIX 真入口 E2E。
