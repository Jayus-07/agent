"""PostgresSelectionStore — selection 两表的 PostgreSQL 连接层（迁移计划 Batch D）。

与 SQLite 版 `SelectionStore` 对外接口完全一致。
PG 为唯一实现（2026-09-17 SQLite 轨删除）。

库归属：agent_business（业务数据，对 NL2SQL 可见）。
schema 与 backend/sql/migrations/016_business_stores_pg.sql 保持一致。
"""

from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator, Optional

import psycopg2
import psycopg2.extras

from backend.config.database import SELECTION_PG_CONFIG
from backend.selection.store import DEFAULT_WEIGHTS, SelectionStore
from backend.shared.logger import logger

_PREFIX = os.getenv("SELECTION_PG_TABLE_PREFIX", "")
_T_SCORES = f"{_PREFIX}selection_scores"
_T_WEIGHTS = f"{_PREFIX}selection_weights"

_SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {_T_SCORES} (
    url         TEXT PRIMARY KEY,
    score_json  TEXT NOT NULL,
    snapshot_id BIGINT,
    computed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS {_T_WEIGHTS} (
    key        TEXT PRIMARY KEY,
    value      DOUBLE PRECISION NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class PostgresSelectionStore(SelectionStore):
    """选品引擎存储 — PostgreSQL 实现（isinstance 兼容）。"""

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path or "data/selection.db"  # 兼容保留
        self._lock = threading.Lock()
        self._init_db()
        logger.info("[PostgresSelectionStore] 初始化完成（agent_business）")

    @contextmanager
    def _conn(self) -> Iterator[Any]:
        conn = psycopg2.connect(**SELECTION_PG_CONFIG)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _exec(self, conn: Any, sql: str, params: tuple = ()) -> Any:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params)
        return cur

    def _init_db(self):
        with self._lock, self._conn() as conn:
            conn.cursor().execute(_SCHEMA_SQL)

    # ── 评分缓存 ─────────────────────────────────

    def save_score(self, url: str, score_json: dict[str, Any],
                   snapshot_id: Optional[int]) -> None:
        """UPSERT 一条评分结果"""
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock, self._conn() as conn:
            self._exec(
                conn,
                f"""INSERT INTO {_T_SCORES} (url, score_json, snapshot_id, computed_at)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (url) DO UPDATE SET
                        score_json = EXCLUDED.score_json,
                        snapshot_id = EXCLUDED.snapshot_id,
                        computed_at = EXCLUDED.computed_at""",
                (url, json.dumps(score_json, ensure_ascii=False), snapshot_id, now),
            )

    def get_score(self, url: str) -> Optional[dict[str, Any]]:
        """读取评分缓存（score_json 已反序列化）"""
        with self._conn() as conn:
            row = self._exec(
                conn, f"SELECT * FROM {_T_SCORES} WHERE url = %s", (url,)
            ).fetchone()
        if row is None:
            return None
        return {
            "url": row["url"],
            "score_json": json.loads(row["score_json"]),
            "snapshot_id": row["snapshot_id"],
            "computed_at": row["computed_at"],
        }

    def all_scores(self) -> list[dict[str, Any]]:
        """全部评分缓存"""
        with self._conn() as conn:
            rows = self._exec(conn, f"SELECT * FROM {_T_SCORES}").fetchall()
        return [
            {
                "url": r["url"],
                "score_json": json.loads(r["score_json"]),
                "snapshot_id": r["snapshot_id"],
                "computed_at": r["computed_at"],
            }
            for r in rows
        ]

    # ── 权重配置 ─────────────────────────────────

    def get_weights(self) -> dict[str, float]:
        """当前权重（未配置的 key 用默认值补齐）"""
        weights = dict(DEFAULT_WEIGHTS)
        with self._conn() as conn:
            for row in self._exec(
                conn, f"SELECT key, value FROM {_T_WEIGHTS}"
            ).fetchall():
                if row["key"] in weights:
                    weights[row["key"]] = row["value"]
        return weights

    def set_weights(self, weights: dict[str, float]) -> None:
        """更新权重（仅接受已知 key）并清空评分缓存"""
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock, self._conn() as conn:
            for key, value in weights.items():
                if key not in DEFAULT_WEIGHTS:
                    continue
                self._exec(
                    conn,
                    f"""INSERT INTO {_T_WEIGHTS} (key, value, updated_at)
                       VALUES (%s, %s, %s)
                       ON CONFLICT (key) DO UPDATE SET
                            value = EXCLUDED.value, updated_at = EXCLUDED.updated_at""",
                    (key, float(value), now),
                )
            self._exec(conn, f"DELETE FROM {_T_SCORES}")
        logger.info(f"[PostgresSelectionStore] 权重更新: {weights}")
