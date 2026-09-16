"""migrate_doc_registry_to_pg.py — R1/C19：doc_registry 历史 SQLite 数据一次性迁移到 PostgreSQL。

用法（在仓库根目录，使用项目 .venv）：
    python backend/scripts/migrate_doc_registry_to_pg.py
    python backend/scripts/migrate_doc_registry_to_pg.py --sqlite data/doc_registry.db --sample 20
    python backend/scripts/migrate_doc_registry_to_pg.py --truncate   # 先清空 PG 表再全量迁（重跑用）

行为：
  1. 幂等建表（与 backend/sql/migrations/010_doc_registry_pg.sql 一致）；
  2. 全量批量 upsert 迁移（按 file_path 冲突覆盖，可安全重跑）；
  3. 对账：行数（总数 + 分状态）+ file_hash（内容 SHA256）抽样逐行比对；
  4. 对账失败 exit 1，全部通过 exit 0。

PG 连接：backend/config/database.py::DOC_REGISTRY_PG_CONFIG
（PGHOST/PGPORT/PGUSER/PGPASSWORD + DOC_REGISTRY_PGDATABASE，默认 agent_memory）。
表名：env DOC_REGISTRY_PG_TABLE（默认 doc_registry）。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from backend.config.database import DOC_REGISTRY_PATH, DOC_REGISTRY_PG_CONFIG  # noqa: E402
from backend.rag.indexing.doc_registry_pg import PostgresDocumentRegistry  # noqa: E402
from backend.shared.logger import logger  # noqa: E402


def _open_sqlite_ro(sqlite_path: str) -> sqlite3.Connection:
    p = Path(sqlite_path)
    if not p.exists():
        raise FileNotFoundError(f"SQLite 源库不存在: {p}")
    conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _fetch_sqlite_rows(conn: sqlite3.Connection) -> tuple[list[str], list[sqlite3.Row]]:
    cur = conn.execute("SELECT * FROM doc_registry ORDER BY file_path")
    cols = [d[0] for d in cur.description]
    return cols, cur.fetchall()


def _migrate(pg_conn, cols: list[str], rows: list[sqlite3.Row], batch: int, table: str) -> int:
    """批量 upsert（含 created_at 在内全列拷贝，保留历史时间戳）。"""
    col_list = ", ".join(cols)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != "file_path")
    # execute_values 约定：SQL 中仅一个 %s 占位（由其自行填充 VALUES 元组）
    sql = (
        f"INSERT INTO {table} ({col_list}) VALUES %s "
        f"ON CONFLICT (file_path) DO UPDATE SET {updates}"
    )
    migrated = 0
    with pg_conn.cursor() as cur:
        for i in range(0, len(rows), batch):
            chunk = [tuple(r[c] for c in cols) for r in rows[i : i + batch]]
            psycopg2.extras.execute_values(cur, sql, chunk, page_size=batch)
            migrated += len(chunk)
            logger.info(f"[migrate] 已写入 {migrated}/{len(rows)}")
    pg_conn.commit()
    return migrated


def _table_name() -> str:
    from backend.config.database import DOC_REGISTRY_PG_TABLE
    import os
    return os.getenv("DOC_REGISTRY_PG_TABLE", DOC_REGISTRY_PG_TABLE)


def _reconcile(sqlite_conn: sqlite3.Connection, pg_dsn_cfg: dict, table: str, sample: int) -> bool:
    """对账：行数 + 分状态计数 + file_hash 抽样。"""
    ok = True

    sq_total = sqlite_conn.execute("SELECT COUNT(*) FROM doc_registry").fetchone()[0]
    pg = psycopg2.connect(**pg_dsn_cfg)
    try:
        with pg.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            pg_total = cur.fetchone()[0]

            print(f"[reconcile] 行数: sqlite={sq_total} pg={pg_total}", end="")
            if sq_total != pg_total:
                print("  ❌ MISMATCH")
                ok = False
            else:
                print("  ✅")

            cur.execute(f"SELECT status, COUNT(*) FROM {table} GROUP BY status ORDER BY status")
            pg_status = dict(cur.fetchall())
        sq_status = dict(sqlite_conn.execute("SELECT status, COUNT(*) FROM doc_registry GROUP BY status").fetchall())
        print(f"[reconcile] 分状态: sqlite={sq_status}")
        print(f"[reconcile]          pg={pg_status}")
        if sq_status != pg_status:
            print("[reconcile] ❌ 分状态计数不一致")
            ok = False

        # file_hash（内容 SHA256）抽样逐行比对
        sample = min(sample, sq_total)
        sq_rows = sqlite_conn.execute(
            "SELECT file_path, file_hash, chunk_count, doc_id, status FROM doc_registry "
            "ORDER BY file_path LIMIT ? OFFSET ?",  # 取前 N 条已按 file_path 排序，配合迁移排序稳定抽样
            (sample, 0),
        ).fetchall()
        mismatch = 0
        with pg.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            for r in sq_rows:
                cur.execute(
                    f"SELECT file_hash, chunk_count, doc_id, status FROM {table} WHERE file_path = %s",
                    (r["file_path"],),
                )
                p = cur.fetchone()
                if p is None or p["file_hash"] != r["file_hash"] or p["chunk_count"] != r["chunk_count"] \
                        or p["doc_id"] != r["doc_id"] or p["status"] != r["status"]:
                    mismatch += 1
                    print(f"[reconcile] ❌ 抽样不一致: {r['file_path']} sqlite={dict(r)} pg={dict(p) if p else None}")
        print(f"[reconcile] file_hash 抽样对账: {sample - mismatch}/{sample} 一致")
        if mismatch:
            ok = False
    finally:
        pg.close()
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", default=DOC_REGISTRY_PATH, help="SQLite 源库路径（默认 DOC_REGISTRY_PATH）")
    parser.add_argument("--sample", type=int, default=20, help="file_hash 抽样对账条数（默认 20）")
    parser.add_argument("--batch", type=int, default=500, help="批量 upsert 每批行数（默认 500）")
    parser.add_argument("--truncate", action="store_true", help="迁移前清空 PG 目标表（幂等重跑用）")
    args = parser.parse_args()

    table = _table_name()
    logger.info(f"[migrate] 源: {args.sqlite} → 目标: {DOC_REGISTRY_PG_CONFIG['dbname']}.{table}")

    # 1. 幂等建表
    PostgresDocumentRegistry(args.sqlite)  # _init_db 建表；db_path 参数仅为签名兼容

    sq = _open_sqlite_ro(args.sqlite)
    cols, rows = _fetch_sqlite_rows(sq)
    if not rows:
        print(f"[migrate] SQLite 源库为空（0 行）——如确需以空表覆盖 PG，请用 --truncate 配合手工清理")
        return _reconcile(sq, DOC_REGISTRY_PG_CONFIG, table, args.sample)

    pg = psycopg2.connect(**DOC_REGISTRY_PG_CONFIG)
    try:
        if args.truncate:
            with pg.cursor() as cur:
                cur.execute(f"TRUNCATE TABLE {table}")
            pg.commit()
            print(f"[migrate] 已清空 PG 目标表 {table}")
    finally:
        pg.close()

    # 2. 迁移（execute_values 需要独立连接）
    pg = psycopg2.connect(**DOC_REGISTRY_PG_CONFIG)
    try:
        n = _migrate(pg, cols, rows, args.batch, table)
    finally:
        pg.close()
    print(f"[migrate] 迁移完成: {n} 行")

    # 3. 对账
    passed = _reconcile(sq, DOC_REGISTRY_PG_CONFIG, table, args.sample)
    sq.close()
    print(f"[migrate] 对账结果: {'✅ PASS' if passed else '❌ FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
