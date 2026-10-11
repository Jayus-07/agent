# 数据库与迁移

本文只说明数据库开发入口与必须遵守的迁移、安全规则。表结构和已应用版本以代码、迁移文件及目标数据库为准，不在本文维护复制清单。

## 存储边界

- 当前持久化使用 PostgreSQL。默认业务库为 `agent_business`，记忆、会话及平台运行数据默认库为 `agent_memory`；实际连接由环境配置覆盖，见 [database.py](../backend/config/database.py) 和 [db.py](../backend/infra/db.py)。
- PostgreSQL DDL 位于 [backend/sql/migrations](../backend/sql/migrations)。新增迁移时，按 [init_db.py](../scripts/init_db.py) 中 `MIGRATION_TARGETS` 登记目标库；应用状态记录在目标库的 `public.schema_migrations`。
- RAG、记忆、业务域和会话使用哪个连接由各自配置决定。新增表前先查现有迁移、仓储实现和测试，避免重复建表或把数据写入错误的库。

## 修改数据库

1. 阅读相关仓储、模型、迁移和测试，确认数据归属、租户边界、索引及删除行为。
2. 新建向前迁移并登记数据库目标；不要改写已部署迁移来改变历史结果。
3. 在隔离数据库上验证迁移，再运行只读体检：`python scripts/init_db.py --check`。连接参数和服务操作见[运维命令](operations/commands.md)。
4. 生产变更按部署流程执行，并核对 `schema_migrations` 中的应用结果。

`init_db.py --reset` 会删除数据库数据；日常开发、验证和故障排查不得使用该选项。

## SQL 与数据安全

- NL2SQL 的查询校验、只读执行和租户过滤由 [executor.py](../backend/sql/executor.py)、[row_security.py](../backend/sql/row_security.py)、[schema_config.py](../backend/sql/data/schema_config.py) 与 [schema_loader.py](../backend/sql/schema_loader.py) 实现；改动前阅读对应测试。
- SQL 参数必须绑定，不能拼接用户输入；执行器使用只读边界，资源访问仍须做身份授权和租户隔离。
- Schema、列白名单和迁移是事实源。文档中的查询示例不能代替当前配置或实际权限检查。

## 开发入口

| 工作 | 入口 |
|---|---|
| 连接配置与 Engine | [backend/config/database.py](../backend/config/database.py)、[backend/infra/db.py](../backend/infra/db.py) |
| PostgreSQL 迁移与库归属 | [backend/sql/migrations](../backend/sql/migrations)、[scripts/init_db.py](../scripts/init_db.py) |
| SQL Agent 行为 | [SQL 领域说明](domains/sql.md) |
| 启停、体检与本地调试 | [运维命令](operations/commands.md) |
