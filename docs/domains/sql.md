# SQL Agent

## 当前执行链

backend/sql/sql_agent.py 按授权策略准备问题和可访问表，再组合选表、SQL 生成、验证、行级范围处理与只读执行；API/Skill 调用入口以 backend/app/api/routes/sql.py 和对应 Skill 实现为准。

## 安全约束

- 只执行只读查询，数据库账号保持最小权限。
- 授权表清单和租户/用户/部门数据范围由服务端 SQLPolicyContext 提供；查询必须 fail closed。
- 所有生成 SQL 经当前 Validator 与执行器边界；禁止模型直连数据库、拼接未参数化输入或绕过校验。
- 权限拒绝、安全校验拒绝、Schema 生成错误和服务不可用须区分处理；不得把拒绝伪装为空结果。

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
