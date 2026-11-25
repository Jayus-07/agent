# SQL Agent

## 当前执行链

`backend/sql/sql_agent.py` 按 `precheck -> _prepare_sql_question -> select_tables -> generate_sql -> SQLPolicyGuard.validate_and_rewrite -> execute_sql_struct -> _observe` 编排执行；API/Skill 调用入口以 `backend/app/api/routes/sql.py`（`_ask_sql_agent` 补齐 `sql_agent` Root Trace）和对应 Skill 实现为准。

## 安全约束与核心实现

- **只读与最小权限**：执行层（`backend/sql/executor.py`）使用 `ThreadedConnectionPool`（借出探活自愈、`PoolExhaustedError` 限流）、`agent_readonly` 只读账号、`SET TRANSACTION READ ONLY`、`SET LOCAL statement_timeout` 与列级脱敏。
- **服务端策略与原地注入（`backend/sql/policy.py`）**：`SQLPolicyContext` 仅能由 `Principal`/`AuthorizationContext` 装配，查询必须 fail closed；`precheck` 在调用 LLM 前拦截无 `sql.read` 或缺失身份/合法 scope 的请求；`SQLPolicyGuard.validate_and_rewrite` 按 `TablePolicy.data_domain`（`shared`/`personal`/`internal`）对 AST 内所有 `exp.Select`（含 CTE/子查询/UNION）原地注入 `%(sql_scope_*)s` 命名参数，取代旧 `row_security.inject_row_filter` 避免双重注入。
- **AST 六层校验与终态分类（`backend/sql/sql_validator.py`）**：基于 `sqlglot` 执行单语句 SELECT 校验、拒绝 `SELECT INTO` 与 `FOR UPDATE|SHARE` 锁子句、表/Schema/列白名单（含 CTE 列白名单收紧）、**全函数正向白名单（`POSITIVE_FUNCTION_ALLOWLIST`）**、PG 保留字表名自动补双引号、别名未定义拦截与强制 `LIMIT`；通过 `TERMINAL_DENY_REASONS` 严禁安全拒绝进入重试循环，区分权限/校验拒绝与空结果。
- **延迟优化**：`backend/sql/router.py` 与 `backend/sql/sql_generator.py` 对支持 thinking 的云端模型显式禁用 thinking（`extra_body={"thinking": {"type": "disabled"}}`、`temperature=0.0`），显著降低端到端问数耗时。

## 零售 ERP 单店试点

- 新业务域为 `retail`，迁移为 `backend/sql/migrations/150_retail_erp_pilot_schema.sql`；包含门店、仓库、SKU/条码、供应商、采购申请/订单/明细、收货验收、库存快照/流水、调拨、盘点、批次效期及 POS 销售/退货。
- SQL schema 描述和权限白名单由 `backend/sql/data/schema_config.py` 维护。新表均有 `tenant_id`，SQL 策略会对每张表注入当前租户条件；这些表暂为 `internal`，只有服务端授权为 `data_scope=all` 的账号可读。门店级角色授权尚未实现，不能把单店测试结果当成门店隔离验收。
- 合成试点数据位于 `backend/sql/seeds/retail_erp_pilot.sql`，租户固定为 `default`，只含一间虚构门店和 USD 样例金额。先将迁移应用到隔离的 `agent_business` 开发/测试库，再使用有写权限的连接手动执行 seed；seed 不随应用启动自动写库，也不清空既有数据。
- 20 条企业常用问数候选位于 `backend/evaluation/datasets/sql_retail_erp_v1/`。离线校验检查问题、SQL 白名单、表覆盖和租户策略；要宣称结果正确，还必须在隔离库加载 seed 后核对查询结果、金额/数量口径和前端表格展示。
- 试点只模拟店内后仓与卖场之间的调拨。跨店调拨、总部汇总多门店和店长/店员授权范围，需要后续补齐真实门店关系、服务端门店授权映射与多门店数据后再验收。

## 维护与验证

- 策略、校验、执行和 schema 源码：backend/sql/。
- 表描述的事实源：backend/sql/data/schema_config.py 与当前 schema loader。
- 定向测试入口：backend/tests/sql/；权限变化还需选择租户隔离、失败关闭和 API 鉴权用例。
