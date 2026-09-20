#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ensure_dbs.py — 存量数据卷的数据库补齐入口（幂等）

背景（2026-09-15）：
  `docker/init-dbs.sh` 由 postgres 镜像的 docker-entrypoint-initdb.d 机制
  调用，**只在数据卷为空、首次启动时执行一次**。已存在的卷（升级/迁移/
  同事接手）不会重跑 → 业务库/只读角色/新增迁移缺失时无人补，表现是
  SQL 工具全线失败（本次实测：agent_business 库不存在、agent_readonly
  角色缺失）。

职责：
  1. 补齐数据库（agent_memory / agent_business）
  2. 按库应用迁移（含 001/002/003/005/006/007 与只读角色 004）
  3. 用 PG_READONLY_PASSWORD 对齐 agent_readonly 密码（**不经 shell/sed
     插值** —— 那会把含特殊字符的密码写坏，实测踩过）
  4. 校验并打印状态（表数量 / 角色可登录性），失败非零退出

用法：
  # 容器内（推荐，直接用 compose 的 env）
  docker compose exec app python scripts/ensure_dbs.py
  # 只体检不落库
  docker compose exec app python scripts/ensure_dbs.py --check
  # 宿主机（读 .env 或环境变量）
  ./.venv/Scripts/python.exe scripts/ensure_dbs.py

幂等性：CREATE DATABASE/SCHEMA/ROLE 前先探测；迁移里的重复对象错误
（duplicate_table/object/schema）按"已存在"跳过，不算失败。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = ROOT / "backend" / "sql" / "migrations"

# 库 → 迁移顺序（与 docker/init-dbs.sh 对齐 + 补齐 006/007）
BUSINESS_MIGRATIONS = [
    "001_business_warehouse.sql",
    "003_agent_business_schema.sql",
    "005_schema_hardening.sql",
    "007_tool_approval.sql",
    "004_readonly_role.sql",  # 只读角色与授权（放最后：依赖前面的 schema）
]
MEMORY_MIGRATIONS = [
    "002_agent_memory_schema.sql",
    "006_customer_service.sql",
    "003_agent_memory_seed.sql",
    "010_doc_registry_pg.sql",
    "011_doc_registry_version_governance.sql",
    "012_obs_trace_store_pg.sql",
    "013_obs_analytics_pg.sql",
    "014_rag_stores_pg.sql",
    "027_rag_processing_lineage.sql",
]

# "已存在 / 已导入"类错误码（幂等跳过，不算失败）
# 42P07 重复表 / 42710 重复对象 / 42P06 重复 schema / 42P04 重复库 /
# 42723 重复函数 / 42P16 重复约束 / 23505 唯一键冲突（种子数据重跑）
_ALREADY_EXISTS_SQLSTATES = {
    "42P07", "42710", "42P06", "42P04", "42723", "42P16", "23505",
}


def _load_env_file(path: Path) -> None:
    """把 .env 读进环境（不覆盖已存在的变量，容器内以进程 env 为准）。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def _conn_params(dbname: str) -> dict:
    return {
        "host": os.getenv("PGHOST", "localhost"),
        "port": int(os.getenv("PGPORT", "5432")),
        "user": os.getenv("PGUSER", "postgres"),
        "password": os.getenv("PGPASSWORD", ""),
        "dbname": dbname,
        "connect_timeout": 8,
    }


def _connect(dbname: str):
    import psycopg2

    return psycopg2.connect(**_conn_params(dbname))


def _db_exists(admin, name: str) -> bool:
    with admin.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))
        return cur.fetchone() is not None


def _apply_migration(conn, filename: str, *, password: str) -> str:
    """在给定连接上应用单个迁移文件。返回 'applied' / 'skipped' / 'error:…'。"""
    path = MIGRATIONS_DIR / filename
    if not path.exists():
        return "error: 迁移文件不存在"

    sql = path.read_text(encoding="utf-8")
    # 安全注入：Python 字符串替换（禁止 sed/shell 插值——特殊字符会被写坏）
    sql = sql.replace("${PG_READONLY_PASSWORD}", password)
    sql = sql.replace("${PG_READONLY_USER}", os.getenv("PG_READONLY_USER", "agent_readonly"))

    applied_any = False
    with conn.cursor() as cur:
        try:
            cur.execute(sql)
            applied_any = True
        except Exception as e:  # noqa: BLE001
            code = getattr(e, "pgcode", None)
            if code in _ALREADY_EXISTS_SQLSTATES:
                conn.rollback()
                return "skipped"
            conn.rollback()
            return f"error: {type(e).__name__}: {str(e)[:160]}"
    conn.commit()
    return "applied" if applied_any else "skipped"


def _ensure_readonly_password(conn, *, user: str, password: str) -> str:
    """对齐只读角色密码（参数化构造 DDL，避免任何字符串拼接风险）。"""
    from psycopg2 import sql

    if not password:
        return "skip: 未配置 PG_READONLY_PASSWORD"
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (user,))
        exists = cur.fetchone() is not None
        if not exists:
            return f"skip: 角色 {user} 不存在（004 迁移应先创建）"
        cur.execute(
            sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
                sql.Identifier(user), sql.Literal(password)
            )
        )
    conn.commit()
    return "ok"


def _verify_readonly(host_db: str) -> str:
    """用只读账号实测连接 + 查询（真实可用性验证）。"""
    user = os.getenv("PG_READONLY_USER", "agent_readonly")
    pw = os.getenv("PG_READONLY_PASSWORD", "")
    if not pw:
        return "skip: 未配置只读密码"
    try:
        import psycopg2

        params = _conn_params(host_db)
        params["user"], params["password"] = user, pw
        conn = psycopg2.connect(**params)
        with conn.cursor() as cur:
            cur.execute("SELECT current_user")
            who = cur.fetchone()[0]
        conn.close()
        return f"ok ({who})"
    except Exception as e:  # noqa: BLE001
        return f"FAIL: {type(e).__name__}: {str(e)[:120]}"


def _count_tables(conn, dbname: str) -> str:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_schema, count(*) FROM information_schema.tables
            WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
            GROUP BY 1 ORDER BY 1
            """
        )
        rows = cur.fetchall()
    return ", ".join(f"{s}={n}" for s, n in rows) or "(空)"


def main() -> int:
    ap = argparse.ArgumentParser(description="存量数据卷的数据库补齐（幂等）")
    ap.add_argument("--check", action="store_true", help="只体检不落库")
    args = ap.parse_args()

    _load_env_file(ROOT / ".env")

    maintenance = "postgres"
    business_db = os.getenv("BUSINESS_PGDATABASE", "agent_business")
    memory_db = os.getenv("PGDATABASE", "agent_memory")
    ro_user = os.getenv("PG_READONLY_USER", "agent_readonly")
    ro_pw = os.getenv("PG_READONLY_PASSWORD", "")

    print(f"[ensure_dbs] 目标: host={os.getenv('PGHOST')}:{os.getenv('PGPORT')} "
          f"business={business_db} memory={memory_db} readonly_user={ro_user}")

    try:
        admin = _connect(maintenance)
    except Exception as e:  # noqa: BLE001
        print(f"[ensure_dbs] ✗ 无法连接数据库: {type(e).__name__}: {e}")
        return 2

    rc = 0
    try:
        # ── 1. 库 ──
        for db in (business_db, memory_db):
            if _db_exists(admin, db):
                print(f"[ensure_dbs] 库已存在: {db}")
            elif args.check:
                print(f"[ensure_dbs] ✗ 缺库: {db}（--check 模式不创建）")
                rc = 1
            else:
                admin.autocommit = True
                with admin.cursor() as cur:
                    cur.execute(f'CREATE DATABASE "{db}"')
                print(f"[ensure_dbs] ✓ 已创建库: {db}")
        admin.close()

        # ── 2. 迁移 ──
        for db, migrations in ((business_db, BUSINESS_MIGRATIONS),
                              (memory_db, MEMORY_MIGRATIONS)):
            try:
                conn = _connect(db)
            except Exception as e:  # noqa: BLE001
                print(f"[ensure_dbs] ✗ 连接 {db} 失败: {e}")
                rc = 1
                continue
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema NOT IN ('pg_catalog','information_schema')"
                )
                before = cur.fetchone()[0]
            for mig in migrations:
                if args.check:
                    print(f"[ensure_dbs]   ({db}) {mig}: 跳过（--check）")
                    continue
                result = _apply_migration(conn, mig, password=ro_pw)
                mark = "✓" if result == "applied" else ("=" if result == "skipped" else "✗")
                print(f"[ensure_dbs]   {mark} ({db}) {mig}: {result}")
                if result.startswith("error"):
                    rc = 1
            if not args.check:
                print(f"[ensure_dbs]   ({db}) 只读角色密码对齐: "
                      f"{_ensure_readonly_password(conn, user=ro_user, password=ro_pw)}")
            conn.commit()
            print(f"[ensure_dbs] {db} 表分布: {_count_tables(conn, db)}（迁移前 {before} 张）")
            conn.close()

        # ── 3. 只读账号实测 ──
        print(f"[ensure_dbs] 只读账号连通性: {_verify_readonly(business_db)}")
    finally:
        pass

    print(f"[ensure_dbs] 完成 rc={rc}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
