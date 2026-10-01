"""RAG 索引运行记录（rag_index_runs）— 候选版本模型的状态权威（B 阶段）。

一次 HTTP 上传 = 一条运行记录（upload_id 主键）。record 承载：
  - generation：本次候选代次（向量 collection / BM25 staging 目录共用）；
  - staging_path：不可变暂存文件（Worker 只读它，永不在候选期触碰正式文件）；
  - base_generation：认领时观测到的 registry active_generation——发布 CAS 的
    比较基准，保证同一逻辑文档任意时刻至多一个发布者，过期 Worker 无法
    覆盖新发布结果；
  - status 状态机：claimed → indexing → publishing → published；
    失败终态 failed / superseded。终态一次写：published/failed/superseded
    之后的任何回写都被拒绝（Celery 重试/恢复任务不能重复发布或洗白失败）。

崩溃恢复语义：Worker 崩溃后任务经 Celery acks_late / tasks 租约 sweeper
重投，重投任务读取本记录按状态续跑（publishing 态的发布步骤全部幂等）；
候选阶段的中间产物（候选 collection、BM25 staging 目录）按 generation
命名，重跑前先清同名残留即可，不存在半写状态被误读。
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from typing import Any, Iterator

import psycopg2
import psycopg2.extras

from backend.config.database import DOC_REGISTRY_PG_CONFIG
from backend.infra.db import engine_for
from backend.shared.logger import logger

_NOW_SQL = "to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS {table} (
    upload_id    TEXT PRIMARY KEY,
    generation   TEXT NOT NULL DEFAULT '',
    file_path    TEXT NOT NULL DEFAULT '',
    doc_id       TEXT NOT NULL DEFAULT '',
    kb_id        TEXT NOT NULL DEFAULT '',
    department   TEXT NOT NULL DEFAULT '',
    file_hash    TEXT NOT NULL DEFAULT '',
    staging_path TEXT NOT NULL DEFAULT '',
    tenant_id    TEXT NOT NULL DEFAULT '',
    actor_id     TEXT NOT NULL DEFAULT '',
    base_generation TEXT NOT NULL DEFAULT '',
    celery_task_id  TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'claimed',
    stage        TEXT NOT NULL DEFAULT '',
    error        TEXT NOT NULL DEFAULT '',
    created_at   TEXT DEFAULT ({now}),
    updated_at   TEXT DEFAULT ({now})
);
CREATE INDEX IF NOT EXISTS idx_{table}_file_path ON {table}(file_path);
CREATE INDEX IF NOT EXISTS idx_{table}_status ON {table}(status);
"""

# 状态机（终态一次写）
ACTIVE_STATUSES = ("claimed", "indexing", "publishing")
TERMINAL_STATUSES = ("published", "failed", "superseded")
ALL_STATUSES = ACTIVE_STATUSES + TERMINAL_STATUSES

# 非终态运行持有的暂存文件（启动清理跳过它们，防止长延迟任务断源）
_NON_TERMINAL = ACTIVE_STATUSES + ("publishing",)


class RunStateConflict(RuntimeError):
    """状态迁移违反终态一次写约束（调用方按幂等重放处理）。"""


def _resolve_table(default: str = "rag_index_runs") -> str:
    """表名解析（显式 env 优先；默认继承 doc_registry 表的测试前缀）。"""
    explicit = os.getenv("RAG_INDEX_RUNS_TABLE")
    if explicit:
        return explicit
    reg_table = os.getenv("DOC_REGISTRY_PG_TABLE", "")
    if reg_table.endswith("doc_registry"):
        return reg_table[: -len("doc_registry")] + default
    return default


class IndexRunStore:
    """rag_index_runs 的 PG 仓储（与 doc_registry 同库同引擎）。"""

    def __init__(self, table: str = "rag_index_runs"):
        # 测试隔离（pgtest_ 前缀纪律）：默认表名继承 doc_registry 表名的
        # 前缀（pgtest_biz_doc_registry → pgtest_biz_rag_index_runs），
        # 显式 RAG_INDEX_RUNS_TABLE 优先
        self._table = _resolve_table(table)
        self._lock = threading.Lock()
        self._init_db()

    @contextmanager
    def _conn(self) -> Iterator[Any]:
        conn = engine_for(DOC_REGISTRY_PG_CONFIG).raw_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._lock, self._conn() as conn:
            conn.cursor().execute(_SCHEMA_SQL.format(table=self._table, now=_NOW_SQL))

    # ---- 写 ----

    def create_run(
        self, *, upload_id: str, generation: str, file_path: str,
        doc_id: str = "", kb_id: str = "", department: str = "",
        file_hash: str = "", staging_path: str = "",
        tenant_id: str = "", actor_id: str = "",
        base_generation: str = "", celery_task_id: str = "",
    ) -> dict:
        """登记运行。同 upload_id 重传（同键幂等重放/失败重试）→ 重置为新一轮
        候选：新 generation、状态回 claimed（终态记录允许被同 id 新内容重开，
        「同键不同内容 409」在 HTTP 幂等层已拦截，到达这里的同 id 必是同内容）。"""
        with self._lock, self._conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(
                f"""INSERT INTO {self._table}
                   (upload_id, generation, file_path, doc_id, kb_id, department,
                    file_hash, staging_path, tenant_id, actor_id,
                    base_generation, celery_task_id, status, stage)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'claimed','')
                   ON CONFLICT (upload_id) DO UPDATE SET
                     generation = EXCLUDED.generation,
                     file_path = EXCLUDED.file_path,
                     doc_id = EXCLUDED.doc_id,
                     kb_id = EXCLUDED.kb_id,
                     department = EXCLUDED.department,
                     file_hash = EXCLUDED.file_hash,
                     staging_path = EXCLUDED.staging_path,
                     tenant_id = EXCLUDED.tenant_id,
                     actor_id = EXCLUDED.actor_id,
                     base_generation = EXCLUDED.base_generation,
                     celery_task_id = EXCLUDED.celery_task_id,
                     status = 'claimed', stage = '', error = '',
                     updated_at = {_NOW_SQL}
                   RETURNING *""",
                (upload_id, generation, file_path, doc_id, kb_id, department,
                 file_hash, staging_path, tenant_id, actor_id,
                 base_generation, celery_task_id),
            )
            row = cur.fetchone()
        return dict(row) if row else {}

    def get_run(self, upload_id: str) -> dict | None:
        with self._lock, self._conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(
                f"SELECT * FROM {self._table} WHERE upload_id = %s", (upload_id,))
            row = cur.fetchone()
        return dict(row) if row else None

    def mark_status(self, upload_id: str, status: str,
                    stage: str = "", error: str = "") -> dict:
        """状态推进（终态一次写：终态→任何状态都拒绝并抛 RunStateConflict）。"""
        if status not in ALL_STATUSES:
            raise ValueError(f"非法运行状态: {status}")
        with self._lock, self._conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            # 终态守卫：仅当当前状态非终态（或与目标相同=幂等重放）时更新
            cur.execute(
                f"""UPDATE {self._table}
                   SET status = %s, stage = %s, error = %s,
                       updated_at = {_NOW_SQL}
                   WHERE upload_id = %s
                     AND (status = %s OR status NOT IN {TERMINAL_STATUSES!r})
                   RETURNING *""",
                (status, stage, error, upload_id, status),
            )
            row = cur.fetchone()
        if row is None:
            existing = self.get_run(upload_id)
            raise RunStateConflict(
                f"运行 {upload_id} 状态迁移被拒绝: current="
                f"{(existing or {}).get('status')} → {status}")
        return dict(row)

    # ---- 读 ----

    def list_non_terminal_staging_paths(self) -> set[str]:
        """非终态运行的暂存文件集合（启动清理的豁免名单）。"""
        with self._lock, self._conn() as conn:
            cur = conn.cursor()
            cur.execute(
                f"SELECT staging_path FROM {self._table} "
                f"WHERE status IN {ACTIVE_STATUSES!r} AND staging_path <> ''")
            rows = cur.fetchall()
        return {r[0] for r in rows}

    def list_non_terminal_by_file(self, file_path: str) -> list[dict]:
        with self._lock, self._conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(
                f"SELECT * FROM {self._table} WHERE file_path = %s "
                f"AND status IN {ACTIVE_STATUSES!r} ORDER BY created_at",
                (file_path,))
            rows = cur.fetchall()
        return [dict(r) for r in rows]

    # ---- 待处理入库失败（管理端「入库失败」信号唯一出口，2026-10-02）----
    #
    # 派生口径（不加状态列，G2）：failed 且同 file_path 尚未出现更新的
    # published 运行——重传成功即自动消数。created_at 为 UTC 字符串
    # （'YYYY-MM-DD HH24:MI:SS'），字典序即时间序，可直接比较。

    def list_pending_failures(self, *, limit: int = 50) -> list[dict]:
        """待处理入库失败清单（每文件取最新一次失败，新失败在前）。"""
        limit = max(1, min(int(limit), 200))
        with self._lock, self._conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(
                f"""SELECT * FROM (
                        SELECT DISTINCT ON (file_path)
                               upload_id, file_path, doc_id, kb_id, department,
                               error, created_at, actor_id
                        FROM {self._table}
                        WHERE status = 'failed'
                          AND NOT EXISTS (
                              SELECT 1 FROM {self._table} p
                              WHERE p.file_path = {self._table}.file_path
                                AND p.status = 'published'
                                AND p.created_at > {self._table}.created_at)
                        ORDER BY file_path, created_at DESC
                    ) t ORDER BY created_at DESC LIMIT %s""",
                (limit,))
            rows = cur.fetchall()
        return [dict(r) for r in rows]

    def count_pending_failures(self) -> int:
        """待处理入库失败计数（与 list_pending_failures 同一派生口径）。"""
        with self._lock, self._conn() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""SELECT COUNT(*) FROM (
                        SELECT DISTINCT ON (file_path) file_path
                        FROM {self._table}
                        WHERE status = 'failed'
                          AND NOT EXISTS (
                              SELECT 1 FROM {self._table} p
                              WHERE p.file_path = {self._table}.file_path
                                AND p.status = 'published'
                                AND p.created_at > {self._table}.created_at)
                        ORDER BY file_path, created_at DESC
                    ) t""")
            row = cur.fetchone()
        return int(row[0]) if row else 0


_run_stores: dict[str, IndexRunStore] = {}
_run_store_lock = threading.Lock()


def get_index_run_store() -> IndexRunStore:
    """按表名缓存实例（表结构惰性创建，失败快速抛出——运行记录是发布
    协议的状态权威，不可用必须让上传链路显式失败，而不是静默降级）。

    按表名而非全局单例：测试隔离以 pgtest_ 前缀切表名，全局单例会把
    上一测试的表名带进下一测试（表被 teardown 删除后 DDL 不会重跑）。
    """
    global _run_stores
    table = _resolve_table()
    with _run_store_lock:
        if not isinstance(_run_stores, dict):
            # pg_env._reset_singletons 会把它置 None → 惰性重建
            _run_stores = {}
        store = _run_stores.get(table)
        if store is None:
            store = _run_stores[table] = IndexRunStore()
        return store
