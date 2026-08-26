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

echo "[init-dbs] 完成。验证只读角色："
$PSQL -d postgres -c "SELECT rolname, rolcanlogin, rolsuper FROM pg_roles WHERE rolname = 'agent_readonly';"
