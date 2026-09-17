"""P0 地基：结构化分析层 — trace_summary（本地 SQLite 实现）。

定位（增量升级路线图 P0，免 Docker 本地开发版）：
  - SQLite trace_store 仍是主持久化与详情兜底（保留不动）
  - 本层在 finish() 时额外落一份「结构化摘要」，为后续阶段提供聚合查询地基：
      P1 Sessions   → sessions() GROUP BY session_id
      P2 Cost 面板  → cost_summary() 按模型/日聚合
      P4 评测数据集 → list(only_rejected=...) 真实流量提取
  - SQL 保持 Postgres 兼容（简单类型 + 标准聚合），将来切换
    OBS_BACKEND=postgres 时只需替换方言层，业务代码零改动。

软失败原则（与 alerts / trace_store 一致）：
  - 写入/查询失败只记日志，绝不向上抛异常，不阻塞业务链路。

环境变量：
  OBS_ANALYTICS_ENABLED  true|false（默认 true；测试环境置 false 防污染）
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any

from backend.infra.sqlite import get_connection
from backend.shared.logger import logger

# 默认路径（与 trace_store.db / doc_registry 同级）
DEFAULT_ANALYTICS_DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "analytics.db"
)

_MAX_ROWS = 20000  # 摘要行保留上限（比详情库 5000 大，聚合需要更长窗口）


def _cfg_enabled() -> bool:
    return os.getenv("OBS_ANALYTICS_ENABLED", "true").strip().lower() not in (
        "0", "false", "no", "off",
    )


class AnalyticsStore:
    """trace_summary 结构化存储。线程安全（单锁，SQLite 单连接短事务）。"""

    def __init__(self, db_path: str = DEFAULT_ANALYTICS_DB_PATH):
        self._db_path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    @property
    def enabled(self) -> bool:
        return _cfg_enabled()

    # =====================================================
    # 建表
    # =====================================================

    def _conn(self):
        return get_connection(self._db_path)

    def _init_db(self):
        with self._lock, self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trace_summary (
                    trace_id          TEXT PRIMARY KEY,
                    ts                TEXT NOT NULL,      -- trace 开始时间 (ISO8601 UTC)
                    session_id        TEXT NOT NULL DEFAULT '',
                    workflow_name     TEXT NOT NULL DEFAULT '',
                    workflow_kind     TEXT NOT NULL DEFAULT 'other',
                    status            TEXT NOT NULL DEFAULT 'success',
                    question          TEXT NOT NULL DEFAULT '',
                    answer_preview    TEXT NOT NULL DEFAULT '',
                    duration_ms       INTEGER NOT NULL DEFAULT 0,
                    model             TEXT NOT NULL DEFAULT '',
                    provider          TEXT NOT NULL DEFAULT '',
                    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
                    completion_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens      INTEGER NOT NULL DEFAULT 0,
                    cost_usd          REAL NOT NULL DEFAULT 0,
                    kb_id             TEXT NOT NULL DEFAULT '',
                    rejected          INTEGER NOT NULL DEFAULT 0,  -- Evidence Gate 拒答
                    tags              TEXT NOT NULL DEFAULT '{}',  -- JSON
                    created_at        TEXT NOT NULL
                )
            """)
            # P1 Sessions / 列表页 / 时间窗筛选的查询路径
            conn.execute("CREATE INDEX IF NOT EXISTS idx_as_session "
                         "ON trace_summary(session_id, ts DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_as_ts "
                         "ON trace_summary(ts DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_as_workflow "
                         "ON trace_summary(workflow_name, ts DESC)")

    # =====================================================
    # 写入
    # =====================================================

    def save(self, record: Any) -> bool:
        """从 TraceRecord 抽取结构化字段写入。失败只记日志，返回 False。"""
        if not self.enabled:
            return False
        try:
            data = self._record_to_dict(record)
            return self.save_dict(data)
        except Exception as e:
            logger.warning(f"[AnalyticsStore] 写入失败 "
                           f"{getattr(record, 'id', '?')}: {e}")
            return False

    def save_dict(self, data: dict) -> bool:
        """接受已序列化的 trace dict 写入（供 trace_writer 异步路径调用）。"""
        if not self.enabled:
            return False
        try:
            usage = data.get("usage") or {}
            tags = data.get("tags") or {}
            metadata = data.get("metadata") or {}
            rejection = metadata.get("rejection") or {}
            spans = data.get("spans") or []
            cost = sum(
                (s.get("metrics") or {}).get("cost_usd", 0)
                for s in spans if isinstance(s, dict)
            ) or (data.get("cost") or {}).get("total_usd", 0) or 0

            now = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            with self._lock, self._conn() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO trace_summary (
                        trace_id, ts, session_id, workflow_name, workflow_kind,
                        status, question, answer_preview, duration_ms,
                        model, provider, prompt_tokens, completion_tokens,
                        total_tokens, cost_usd, kb_id, rejected, tags, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    data.get("id", ""),
                    data.get("timestamp", "") or now,
                    data.get("session_id", "") or "",
                    data.get("workflow_name", "") or "",
                    data.get("workflow_kind", "other") or "other",
                    data.get("status", "success") or "success",
                    data.get("question", "") or "",
                    data.get("answer_preview", "") or "",
                    int(data.get("total_ms", 0) or data.get("duration_ms", 0) or 0),
                    data.get("model", "") or "",
                    data.get("provider", "") or "",
                    int(usage.get("prompt_tokens", 0) or 0),
                    int(usage.get("completion_tokens", 0) or 0),
                    int(usage.get("total_tokens", 0) or 0),
                    float(cost),
                    str(tags.get("kb_id", "") or metadata.get("kb_id", "") or ""),
                    1 if rejection.get("rejected") else 0,
                    json.dumps(tags, ensure_ascii=False, default=str),
                    now,
                ))
                count = conn.execute(
                    "SELECT COUNT(*) FROM trace_summary").fetchone()[0]
                if count > _MAX_ROWS:
                    conn.execute(
                        "DELETE FROM trace_summary WHERE trace_id IN "
                        "(SELECT trace_id FROM trace_summary "
                        "ORDER BY created_at ASC LIMIT ?)",
                        (count - _MAX_ROWS + 200,),
                    )
            return True
        except Exception as e:
            logger.warning(f"[AnalyticsStore] save_dict 失败 "
                           f"{data.get('id', '?')}: {e}")
            return False

    @staticmethod
    def _record_to_dict(record: Any) -> dict:
        """TraceRecord → dict（跳过 _ 前缀内部属性）。"""
        d = {}
        for k, v in record.__dict__.items():
            if k.startswith("_"):
                continue
            if isinstance(v, dict):
                d[k] = {dk: dv for dk, dv in v.items()
                        if not (isinstance(dk, str) and dk.startswith("_"))}
            elif isinstance(v, list):
                d[k] = [AnalyticsStore._record_to_dict(x) if hasattr(x, "__dict__") else x
                        for x in v]
            elif hasattr(v, "__dict__"):
                d[k] = AnalyticsStore._record_to_dict(v)
            else:
                d[k] = v
        return d

    # =====================================================
    # 查询（PG 兼容：标准 SQL，无 SQLite 方言特性）
    # =====================================================

    def list(self, limit: int = 50, workflow_name: str | None = None,
             session_id: str | None = None) -> list[dict]:
        """结构化摘要列表（服务端过滤 — 前端不再拉 200 条本地 filter）。"""
        try:
            sql = "SELECT * FROM trace_summary"
            where, params = [], []
            if workflow_name:
                where.append("workflow_name = ?")
                params.append(workflow_name)
            if session_id:
                where.append("session_id = ?")
                params.append(session_id)
            if where:
                sql += " WHERE " + " AND ".join(where)
            sql += " ORDER BY ts DESC LIMIT ?"
            params.append(limit)
            with self._lock, self._conn() as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(sql, params).fetchall()
            return [self._row_to_dict(r) for r in rows]
        except Exception as e:
            logger.warning(f"[AnalyticsStore] list 失败: {e}")
            return []

    def sessions(self, limit: int = 50) -> list[dict]:
        """P1 Sessions 聚合：按 session_id 汇总轮次/耗时/成本。"""
        try:
            with self._lock, self._conn() as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute("""
                    SELECT session_id,
                           COUNT(*)            AS turns,
                           MAX(ts)             AS last_ts,
                           MIN(ts)             AS first_ts,
                           AVG(duration_ms)    AS avg_duration_ms,
                           SUM(total_tokens)   AS total_tokens,
                           SUM(cost_usd)       AS total_cost_usd,
                           SUM(rejected)       AS rejected_count
                    FROM trace_summary
                    WHERE session_id != ''
                    GROUP BY session_id
                    ORDER BY last_ts DESC
                    LIMIT ?
                """, (limit,)).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.warning(f"[AnalyticsStore] sessions 失败: {e}")
            return []

    def cost_summary(self, days: int = 7) -> list[dict]:
        """P2 Cost 面板：近 N 天按 日×模型 聚合用量与成本。"""
        try:
            cutoff = time.strftime(
                "%Y-%m-%d 00:00:00",
                time.localtime(time.time() - days * 86400),
            )
            with self._lock, self._conn() as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute("""
                    SELECT substr(ts, 1, 10)  AS day,
                           model,
                           COUNT(*)           AS traces,
                           SUM(prompt_tokens)     AS prompt_tokens,
                           SUM(completion_tokens) AS completion_tokens,
                           SUM(total_tokens)      AS total_tokens,
                           SUM(cost_usd)          AS total_cost_usd
                    FROM trace_summary
                    WHERE created_at >= ?
                    GROUP BY substr(ts, 1, 10), model
                    ORDER BY day DESC
                """, (cutoff,)).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.warning(f"[AnalyticsStore] cost_summary 失败: {e}")
            return []

    def count(self) -> int:
        try:
            with self._lock, self._conn() as conn:
                return conn.execute(
                    "SELECT COUNT(*) FROM trace_summary").fetchone()[0]
        except Exception:
            return 0

    # =====================================================
    # 内部
    # =====================================================

    @staticmethod
    def _row_to_dict(r) -> dict:
        d = dict(r)
        d["id"] = d.pop("trace_id")
        d["timestamp"] = d.pop("ts")
        d["usage"] = {
            "prompt_tokens": d.pop("prompt_tokens"),
            "completion_tokens": d.pop("completion_tokens"),
            "total_tokens": d.pop("total_tokens"),
        }
        try:
            d["tags"] = json.loads(d.get("tags") or "{}")
        except Exception:
            d["tags"] = {}
        d["rejected"] = bool(d.get("rejected"))
        return d


# 模块级单例
_analytics_store: AnalyticsStore | None = None


def get_analytics_store() -> AnalyticsStore:
    """存储工厂：OBS_DB_BACKEND=postgres 时返回 PG 实现（接口/语义一致）。"""
    global _analytics_store
    if _analytics_store is None:
        if os.getenv("OBS_DB_BACKEND", "sqlite").strip().lower() == "postgres":
            from backend.observability.analytics_store_pg import PostgresAnalyticsStore
            _analytics_store = PostgresAnalyticsStore()
        else:
            _analytics_store = AnalyticsStore()
    return _analytics_store
