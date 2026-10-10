# SQL Agent

## 当前执行链

backend/sql/sql_agent.py 按授权策略准备问题和可访问表，再组合选表、SQL 生成、验证、行级范围处理与只读执行；API/Skill 调用入口以 backend/app/api/routes/sql.py 和对应 Skill 实现为准。

## 安全约束

- 只执行只读查询，数据库账号保持最小权限。
- 授权表清单和租户/用户/部门数据范围由服务端 SQLPolicyContext 提供；查询必须 fail closed。
- 所有生成 SQL 经当前 Validator 与执行器边界；禁止模型直连数据库、拼接未参数化输入或绕过校验。
- 权限拒绝、安全校验拒绝、Schema 生成错误和服务不可用须区分处理；不得把拒绝伪装为空结果。

## 维护与验证

- 策略、校验、执行和 schema 源码：backend/sql/。
- 表描述的事实源：backend/sql/data/schema_config.py 与当前 schema loader。
- 定向测试入口：backend/tests/sql/；权限变化还需选择租户隔离、失败关闭和 API 鉴权用例。