#!/bin/bash
# =====================================================
# init-dbs.sh — 容器首次初始化数据库（P1-12）
#
# 由 postgres 官方镜像的 docker-entrypoint-initdb.d 机制调用，
# 仅在数据卷为空（首次启动）时执行一次；重跑安全（全部幂等 SQL）。
#
# 职责：
#   1. 创建业务库 agent_business（POSTGRES_DB 本身是 agent_memory）
#   2. 业务库：schema + 种子数据（001）+ 加固（005）+ 只读角色（004）
#   3. 记忆库：schema（002）+ 种子数据（003）
#
# 说明：
#   - 004_readonly_role.sql 中的角色密码为 dev 默认值；
#     生产环境启动后请立即 ALTER ROLE ... PASSWORD 修改，
#     或在首次初始化前用 sed 替换注入（见 compose 注释）。
#   - 应用侧业务查询使用 agent_readonly（见 compose 的
#     PG_READONLY_USER / PG_READONLY_PASSWORD）。
# =====================================================
set -e

PSQL="psql -v ON_ERROR_STOP=1 -U $POSTGRES_USER"

echo "[init-dbs] 1/3 创建业务库 agent_business ..."
$PSQL -d postgres -c "CREATE DATABASE agent_business;"

echo "[init-dbs] 2/3 初始化业务库（schema + 种子 + 加固 + 只读角色）..."
$PSQL -d agent_business -f /docker-migrations/001_business_warehouse.sql
$PSQL -d agent_business -f /docker-migrations/005_schema_hardening.sql
$PSQL -d agent_business -f /docker-migrations/004_readonly_role.sql

echo "[init-dbs] 3/3 初始化记忆库（schema + 种子）..."
$PSQL -d agent_memory -f /docker-migrations/002_agent_memory_schema.sql
$PSQL -d agent_memory -f /docker-migrations/003_agent_memory_seed.sql

echo "[init-dbs] 4/4 初始化自建认证（auth schema，幂等）..."
$PSQL -d agent_memory -f /docker-migrations/008_local_auth.sql
$PSQL -d agent_memory -f /docker-migrations/009_auth_roles.sql

echo "[init-dbs] 5/5 初始化 PG 化存储（doc_registry + 可观测层，幂等）..."
# 迁移计划 2026-09-17：doc_registry（010/011）与可观测层（012/013）补进首启链路
# （此前 010/011 仅靠应用侧幂等建表兜底，此处补齐权威 schema 双写）
$PSQL -d agent_memory -f /docker-migrations/010_doc_registry_pg.sql
$PSQL -d agent_memory -f /docker-migrations/011_doc_registry_version_governance.sql
$PSQL -d agent_memory -f /docker-migrations/012_obs_trace_store_pg.sql
$PSQL -d agent_memory -f /docker-migrations/013_obs_analytics_pg.sql
$PSQL -d agent_memory -f /docker-migrations/014_rag_stores_pg.sql

echo "[init-dbs] 6/6 初始化编排族与业务族 PG 存储（迁移计划 Batch C/D，幂等）..."
# 库归属：workflow_runs → agent_memory；inventory 4 表 + 业务族 11 表 → agent_business
$PSQL -d agent_memory -f /docker-migrations/015_workflow_runs_pg.sql
$PSQL -d agent_business -f /docker-migrations/016_inventory_alerts_pg.sql
$PSQL -d agent_business -f /docker-migrations/017_business_stores_pg.sql

echo "[init-dbs] 完成。验证只读角色："
$PSQL -d postgres -c "SELECT rolname, rolcanlogin, rolsuper FROM pg_roles WHERE rolname = 'agent_readonly';"
