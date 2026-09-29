# 数据库迁移指南（Alembic）

> P1-13 引入。此前 schema 变更依赖 `scripts/rebuild_pg.py` 手动重放 SQL 文件
> （`backend/sql/migrations/00N_*.sql`），无版本记录、无法增量升级/回滚。
> 现由 alembic 统一管理，SQL 文件冻结为基线。

## 架构

双库双迁移线（对应 `config/database.py` 的两库分离设计）：

| 迁移线 | 目标库 | script_location | 版本表 |
|---|---|---|---|
| `business` | `agent_business`（业务数仓） | `backend/sql/alembic/business/` | `alembic_version_business` |
| `memory` | `agent_memory`（会话/记忆） | `backend/sql/alembic/memory/` | `alembic_version_memory` |

配置入口：仓库根 `alembic.ini`（两个 section：`[business]` / `[memory]`）。
连接串**不落盘**——`env.py` 复用 `backend.config.database` 的
`BUSINESS_DB_CONFIG` / `MEMORY_DB_CONFIG`（环境变量驱动），与应用运行时同一事实来源。

基线迁移 `0001_baseline.py` 固化了原手动 SQL：
- business：`001_business_warehouse.sql` + `DROP SCHEMA ai` + `005_schema_hardening.sql`
- memory：`002_agent_memory_schema.sql` + `003_agent_memory_seed.sql`

## 常用命令

在仓库根目录执行：

```bash
# 升级到最新（两库）
alembic -c alembic.ini -n business upgrade head
alembic -c alembic.ini -n memory   upgrade head

# 查看当前版本 / 历史
alembic -c alembic.ini -n business current
alembic -c alembic.ini -n business history

# 新增迁移（改完 SQL 或 autogenerate 后）
alembic -c alembic.ini -n business revision -m "add crawler.source_url"
alembic -c alembic.ini -n business revision --autogenerate -m "..."   # 需绑定 MetaData

# 回滚一步
alembic -c alembic.ini -n business downgrade -1

# 已存在的旧库（无版本表）打基线标记，不重放 SQL
alembic -c alembic.ini -n business stamp 0001
```

## 与 rebuild_pg.py 的关系

`scripts/rebuild_pg.py` 仍是「一键重建」入口（DROP + CREATE + 迁移 + 回归），
但其 Step 4/5 已改为调用 `alembic upgrade head`；
原「重导 002/003 schema dump」步骤已删除（schema 演进统一走 alembic revision）。

- **日常 schema 变更**：`alembic revision` → 编写/检查 upgrade/downgrade → `upgrade head` → 提交迁移文件入库
- **推倒重来**：`python scripts/rebuild_pg.py`

## Docker 环境

容器首启由 `docker/init-dbs.sh` 直接执行基线 SQL（幂等），
随后可用 `alembic stamp 0001` 对齐版本（见 `docker-compose.yml`）。

## 注意事项

1. 基线 `0001` 的 `downgrade` 只做 schema 级清理，不回滚种子数据。
2. 新迁移必须同时提供 `upgrade` 与 `downgrade`（至少 `pass` + 注释说明不可逆原因）。
3. 迁移文件一旦提交（合入主干）禁止修改历史版本，只能追加新版本。
4. `sqlalchemy.url` 不写入任何文件；密码经环境变量注入（见 `.env.example`）。
