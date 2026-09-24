#!/usr/bin/env python
"""verify_migration_state.py — 发布前迁移一致性 preflight（Platform Readiness STOP B3）。

回答任务书 §2.1 的事故不再发生：
    git 有迁移文件 → db-migrate 镜像未重建 → migration 静默跳过

本脚本校验三层中的两层（镜像层由构建同一性保证——db-migrate 必须与 app
同批构建，见 release checklist；构建后可用 --image <name> 抽检容器内文件）：

  层1 repo    backend/sql/migrations 目录 × scripts/init_db.py 登记表
  层2 DB      agent_memory / agent_business 的 schema_migrations

判定（任一命中即 exit 1，阻断发布）：
  - unregistered：目录里有、登记表没有 → 运行期 db-migrate 将 rc=2 整栈拒绝启动
  - missing_in_db：登记表有、DB 未应用 → 镜像漂移静默跳过的直接证据
  - checksum_mismatch：DB 记录与文件 checksum 不一致 → 文件被改未重放

用法：
    cd backend && PYTHONPATH=.. python scripts/verify_migration_state.py
    python scripts/verify_migration_state.py --image agent-db-migrate   # 加验镜像层
"""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # backend/scripts/x.py → 仓库根
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from scripts.init_db import MIGRATION_TARGETS  # noqa: E402  (单一事实源，禁复制)

MIGRATIONS_DIR = ROOT / "backend" / "sql" / "migrations"


def _checksum(path: Path) -> str:
    """与 scripts/init_db.py::checksum_of 完全同口径：sha256(文件 utf-8 文本)[:16]。"""
    return hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()[:16]


def repo_layer() -> tuple[list[str], list[str]]:
    """返回 (registered_files, unregistered_files)。"""
    on_disk = sorted(p.name for p in MIGRATIONS_DIR.glob("*.sql"))
    registered = sorted(f for f in on_disk if f in MIGRATION_TARGETS)
    unregistered = sorted(f for f in on_disk if f not in MIGRATION_TARGETS)
    return registered, unregistered


def db_layer() -> dict[tuple[str, str], dict]:
    """读两库 schema_migrations：{(target_db, filename): {status, checksum}}。

    两库各自的 schema_migrations 可能残留同名异值行（历史上登记表变更过），
    必须按 MIGRATION_TARGETS 指定的目标库定位，禁止跨库按文件名合并。

    连接目标自动解析为权威库（docker agent-postgres-1 的宿主映射端口，
    默认 127.0.0.1:5433）——仓库 .env 的 PGHOST=localhost/PGPORT=5432 会
    命中宿主机原生 PG 的同名旧库（多会话纪律 §六 已知坑），此处显式覆盖。
    """
    import psycopg2

    from backend.config.database import BUSINESS_DB_CONFIG, MEMORY_DB_CONFIG

    host, port = resolve_authority_pg()
    print(f"[db] 连接权威库 {host}:{port}"
          f"（MEMORY={MEMORY_DB_CONFIG['dbname']} / BUSINESS={BUSINESS_DB_CONFIG['dbname']}）")
    rows: dict[tuple[str, str], dict] = {}
    for name, cfg in (("memory", MEMORY_DB_CONFIG), ("business", BUSINESS_DB_CONFIG)):
        conn = psycopg2.connect(
            host=host, port=port, dbname=cfg["dbname"],
            user=cfg["user"], password=cfg["password"], connect_timeout=5)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT filename, checksum, status FROM schema_migrations")
                for filename, checksum, status in cur.fetchall():
                    rows[(name, filename)] = {"status": status,
                                              "checksum": checksum or ""}
        finally:
            conn.close()
    return rows


def resolve_authority_pg() -> tuple[str, str]:
    """权威库端点：docker port agent-postgres-1 → (127.0.0.1, 5433)；
    docker 不可用时回退显式 env PG_AUTHORITY_HOST/PG_AUTHORITY_PORT。"""
    override = (os.environ.get("PG_AUTHORITY_HOST"),
                os.environ.get("PG_AUTHORITY_PORT"))
    if all(override):
        return override  # type: ignore[return-value]
    try:
        out = subprocess.run(["docker", "port", "agent-postgres-1", "5432"],
                             capture_output=True, text=True, timeout=10)
        line = out.stdout.strip().splitlines()[0] if out.stdout.strip() else ""
        # 形如 "0.0.0.0:5433" 或 ":::5433"
        port = line.rsplit(":", 1)[-1]
        if port.isdigit():
            return "127.0.0.1", port
    except Exception:
        pass
    return "127.0.0.1", "5433"


def image_layer(image: str) -> set[str]:
    out = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "ls", image,
         "/app/backend/sql/migrations"],
        capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise RuntimeError(f"镜像层读取失败: {out.stderr[:200]}")
    return {line.strip() for line in out.stdout.splitlines() if line.endswith(".sql")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default="", help="可选：抽检指定镜像内迁移目录")
    args = ap.parse_args()

    problems: list[str] = []

    registered, unregistered = repo_layer()
    print(f"[repo] 登记 {len(registered)} / 目录 {len(registered) + len(unregistered)} 个迁移")
    if unregistered:
        for f in unregistered:
            problems.append(f"unregistered: {f}（目录有、init_db.py 登记表没有 → db-migrate 将 rc=2）")
        print(f"[repo][FAIL] 未登记迁移: {unregistered}")
    else:
        print("[repo][OK] 目录内迁移全部登记")

    db_rows = db_layer()
    # applied / applied-partial / skipped（runtime-managed 或已一致的 skip）
    # 都视为「已到位」；error 或缺行才判定缺失。
    present = {key for key, row in db_rows.items()
               if row["status"] in ("applied", "applied-partial", "skipped")}
    missing = [f for f in registered
               if (MIGRATION_TARGETS[f], f) not in present]
    if missing:
        for f in missing:
            problems.append(f"missing_in_db: {f}（登记有、DB 未应用 → 镜像漂移静默跳过）")
        print(f"[db][FAIL] 未应用迁移: {missing}")
    else:
        print(f"[db][OK] 登记迁移 {len(registered)} 个全部已应用")

    mismatch = []
    for f in registered:
        row = db_rows.get((MIGRATION_TARGETS[f], f))
        if row and row["checksum"] and row["checksum"] != _checksum(MIGRATIONS_DIR / f):
            mismatch.append(f)
    if mismatch:
        for f in mismatch:
            problems.append(f"checksum_mismatch: {f}（文件已改、DB 记录未更新）")
        print(f"[db][FAIL] checksum 不一致: {mismatch}")

    if args.image:
        in_image = image_layer(args.image)
        absent = sorted(set(registered) - in_image)
        if absent:
            for f in absent:
                problems.append(f"missing_in_image: {f}（镜像 {args.image} 缺该迁移 → 静默跳过）")
            print(f"[image][FAIL] {args.image} 缺迁移: {absent}")
        else:
            print(f"[image][OK] {args.image} 含全部 {len(registered)} 个登记迁移")

    print()
    if problems:
        print("result: MIGRATION_STATE_FAIL")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("result: MIGRATION_STATE_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
