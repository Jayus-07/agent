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
  2. 按库应用迁移（business 为显式权限链清单；memory 从 init_db 的
     MIGRATION_TARGETS 唯一事实源动态派生，两入口不漂移）
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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 库 → 迁移顺序（与 docker/init-dbs.sh 对齐 + 补齐 006/007）
BUSINESS_MIGRATIONS = [
    "001_business_warehouse.sql",
    "003_agent_business_schema.sql",
    "005_schema_hardening.sql",
    "007_tool_approval.sql",
    "004_readonly_role.sql",  # 只读角色与授权（依赖前面的 schema）
    "043_readonly_public_revoke.sql",  # 收窄 public 后再启用只读口令
    "083_readonly_ai_grant_scope.sql",  # 收窄 ai 后再启用只读口令
    "093_revoke_readonly_default_password.sql",  # 清除历史默认口令
    "150_retail_erp_pilot_schema.sql",  # 零售 ERP 试点业务域
    "152_retire_crawler_read_access.sql",  # 竞品旧表保留但问数账号无读取权限
]


def _derive_memory_migrations() -> list[str]:
    """memory 库迁移清单：从 init_db.MIGRATION_TARGETS（唯一事实源）派生。

    手工清单曾与登记漂移：151_auth_sso 被登记，但它的 auth.users schema
    是 008→131 一串迁移逐步演化出来的，旧库补齐在 151 上必然失败
    （隔离旧库副本已复现）。改为动态派生后两个入口永不漂移；顺序与
    全新初始化一致（文件名序 + 尾置），运行时托管迁移（049，对象由
    应用启动 ensure_schema() 幂等建立）照 init_db 语义跳过不执行。
    """
    from scripts.init_db import RUNTIME_MANAGED_MIGRATIONS, discover_migrations

    ordered, _unregistered = discover_migrations()
    return [
        name for name, target in ordered
        if target == "memory" and name not in RUNTIME_MANAGED_MIGRATIONS
    ]


MEMORY_MIGRATIONS = _derive_memory_migrations()
_READONLY_TRACKED_MIGRATIONS = frozenset({
    "004_readonly_role.sql",
    "043_readonly_public_revoke.sql",
    "083_readonly_ai_grant_scope.sql",
    "093_revoke_readonly_default_password.sql",
})
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


def _apply_migration(
    conn,
    filename: str,
    *,
    password: str,
    user: str | None = None,
) -> str:
    """在给定连接上应用单个迁移文件。返回 'applied' / 'applied-partial' /
    'skipped' / 'error:…'。

    统一委托 init_db.apply_migration：跟踪表查 checksum（存量库重跑直接
    skipped，不裸重放 SQL）、冻结校验和、逐语句降级补齐全继承。此前的
    简化裸执行路径不登记跟踪表，存量库重跑 003/031 时因 ON CONFLICT
    约束被后续迁移重建、引用列被改而报错（隔离旧库副本已复现）。
    """
    path = MIGRATIONS_DIR / filename
    if not path.exists():
        return "error: 迁移文件不存在"

    sql = path.read_text(encoding="utf-8")
    # 旧迁移占位符只为既有 SQL 保留；凭据迁移走参数化 DDL。
    sql = sql.replace("${PG_READONLY_PASSWORD}", password)
    sql = sql.replace("${PG_READONLY_USER}", os.getenv("PG_READONLY_USER", "agent_readonly"))

    try:
        from scripts.init_db import apply_migration

        status, detail = apply_migration(
            conn,
            filename,
            sql,
            force=False,
            readonly_password=password,
            readonly_user=user or os.getenv("PG_READONLY_USER", "agent_readonly"),
        )
        if status == "error":
            return f"error: {detail}" if detail else f"error: {filename} failed"
        return status
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        return f"error: {type(exc).__name__}: {str(exc)[:120]}"


def _apply_business_migrations(
    conn,
    *,
    password: str,
    user: str,
    check: bool,
) -> int:
    """先关闭只读登录，再应用权限链；任何迁移失败都保持 NOLOGIN。"""
    if check:
        for mig in BUSINESS_MIGRATIONS:
            print(f"[ensure_dbs]   ({os.getenv('BUSINESS_PGDATABASE', 'agent_business')}) "
                  f"{mig}: 跳过（--check）")
        return 0

    from scripts.init_db import (
        block_readonly_roles_for_migrations,
        ensure_migration_tracking_table,
        heal_missing_readonly_role,
        pending_readonly_authorization_migrations,
    )

    db_label = os.getenv("BUSINESS_PGDATABASE", "agent_business")
    ensure_migration_tracking_table(conn)
    heal_status = heal_missing_readonly_role(
        conn, user=user, dbname=db_label,
    )
    print(f"[ensure_dbs]   ({db_label}) 只读角色探测: {heal_status}")
    if pending_readonly_authorization_migrations(conn):
        block_readonly_roles_for_migrations(conn, readonly_user=user)

    rc = 0
    readonly_migrations_ready = True
    for mig in BUSINESS_MIGRATIONS:
        if (
            mig == "093_revoke_readonly_default_password.sql"
            and not readonly_migrations_ready
        ):
            print(
                f"[ensure_dbs]   = ({db_label}) {mig}: "
                "跳过（前置迁移失败，保持 NOLOGIN）"
            )
            continue
        result = _apply_migration(conn, mig, password=password, user=user)
        mark = "✓" if result == "applied" else (
            "=" if result == "skipped" else "✗"
        )
        print(f"[ensure_dbs]   {mark} ({db_label}) {mig}: {result}")
        if result.startswith("error"):
            rc = 1
            readonly_migrations_ready = False

    if readonly_migrations_ready:
        print(
            f"[ensure_dbs]   ({db_label}) 只读角色密码对齐: "
            f"{_ensure_readonly_password(conn, user=user, password=password)}"
        )
    else:
        print(
            f"[ensure_dbs]   ({db_label}) 只读角色保持 NOLOGIN："
            "前置迁移未全部成功"
        )
    return rc


def _ensure_readonly_password(conn, *, user: str, password: str) -> str:
    """复用主初始化入口的只读账号持续对齐逻辑。"""
    from scripts.init_db import ensure_readonly_role

    return ensure_readonly_role(conn, user=user, password=password)


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
            if db == business_db:
                rc |= _apply_business_migrations(
                    conn,
                    password=ro_pw,
                    user=ro_user,
                    check=args.check,
                )
            else:
                # 委托 init_db.apply_migration 需要跟踪表记录 checksum
                from scripts.init_db import ensure_migration_tracking_table

                ensure_migration_tracking_table(conn)
                conn.commit()
                for mig in migrations:
                    if args.check:
                        print(f"[ensure_dbs]   ({db}) {mig}: 跳过（--check）")
                        continue
                    result = _apply_migration(
                        conn, mig, password=ro_pw, user=ro_user
                    )
                    mark = {"applied": "✓", "applied-partial": "~",
                            "skipped": "="}.get(result, "✗")
                    print(f"[ensure_dbs]   {mark} ({db}) {mig}: {result}")
                    if result.startswith("error"):
                        rc = 1
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
