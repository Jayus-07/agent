"""selection/store.py — 选品引擎存储（SQLite）

两表设计:
  - selection_scores   — 评分结果缓存（快照无更新时命中缓存）
  - selection_weights  — 评分权重配置（可调）

镜像 backend/competitor/store.py 的模式（线程安全单例 + threading.Lock）。
"""
import json
import os
import sqlite3
import threading
from datetime import datetime
from typing import Any, Optional

from backend.shared.logger import logger

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PROJECT_ROOT = os.path.dirname(_BACKEND_DIR)
_DEFAULT_DB_PATH = os.path.join(_PROJECT_ROOT, "data", "selection.db")


def _resolve_db_path() -> str:
    """惰性读取环境变量（测试可通过 monkeypatch.setenv 隔离）"""
    return os.getenv("SELECTION_DB_PATH", _DEFAULT_DB_PATH)


SELECTION_DB_PATH = _resolve_db_path()

# 与 spec §5.1 一致的默认权重
DEFAULT_WEIGHTS: dict[str, float] = {
    "reputation": 0.25,
    "heat": 0.25,
    "price": 0.20,
    "differentiation": 0.15,
    "stability": 0.15,
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS selection_scores (
    url         TEXT PRIMARY KEY,
    score_json  TEXT NOT NULL,
    snapshot_id INTEGER,
    computed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS selection_weights (
    key        TEXT PRIMARY KEY,
    value      REAL NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class SelectionStore:
    """线程安全的选品引擎存储"""

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path or _resolve_db_path()
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
        logger.info(f"[SelectionStore] 初始化: {self._db_path}")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ── 评分缓存 ─────────────────────────────────

    def save_score(self, url: str, score_json: dict[str, Any],
                   snapshot_id: Optional[int]) -> None:
        """UPSERT 一条评分结果"""
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO selection_scores (url, score_json, snapshot_id, computed_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(url) DO UPDATE SET
                        score_json=excluded.score_json,
                        snapshot_id=excluded.snapshot_id,
                        computed_at=excluded.computed_at""",
                (url, json.dumps(score_json, ensure_ascii=False), snapshot_id, now),
            )

    def get_score(self, url: str) -> Optional[dict[str, Any]]:
        """读取评分缓存（score_json 已反序列化）"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM selection_scores WHERE url = ?", (url,)
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
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM selection_scores").fetchall()
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
        with self._connect() as conn:
            for row in conn.execute("SELECT key, value FROM selection_weights"):
                if row["key"] in weights:
                    weights[row["key"]] = row["value"]
        return weights

    def set_weights(self, weights: dict[str, float]) -> None:
        """更新权重（仅接受已知 key，忽略未知 key）

        权重变更后评分语义即变，因此同时清空评分缓存（selection_scores），
        避免旧权重算出的分数继续被命中。
        """
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock, self._connect() as conn:
            for key, value in weights.items():
                if key not in DEFAULT_WEIGHTS:
                    continue
                conn.execute(
                    """INSERT INTO selection_weights (key, value, updated_at)
                       VALUES (?, ?, ?)
                       ON CONFLICT(key) DO UPDATE SET
                            value=excluded.value, updated_at=excluded.updated_at""",
                    (key, float(value), now),
                )
            conn.execute("DELETE FROM selection_scores")
        logger.info(f"[SelectionStore] 权重更新: {weights}")


_store: Optional[SelectionStore] = None


def get_selection_store() -> SelectionStore:
    """全局单例"""
    global _store
    if _store is None:
        _store = SelectionStore()
    return _store


def reset_selection_store() -> None:
    """重置全局单例（测试隔离）"""
    global _store
    _store = None
