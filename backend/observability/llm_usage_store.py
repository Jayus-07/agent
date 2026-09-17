"""LLM 用量明细存储 — llm_usage 表（每次 LLM 调用一行）。

定位（Token 统计看板的数据地基）：
  - proxy._record_tokens 每次成功调用后写入一行，模型/成本按实际模型计价
  - 看板聚合（总量/日趋势/按模型）直接在本表 GROUP BY，与 trace 聚合解耦：
    避免 agent 根 trace 与嵌套 RAG 子 trace 的 token 双计问题
  - trace_id/session_id 用于按轮次（per-turn）追溯

软失败原则（与 analytics_store 一致）：
  写入/查询失败只记日志，绝不向上抛异常，不阻塞 LLM 主链路。
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from typing import Any

from backend.infra.sqlite import get_connection
from backend.shared.logger import logger

# 与 analytics.db 同库不同表（同一家族的结构化分析层）
DEFAULT_LLM_USAGE_DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "analytics.db"
)

_MAX_ROWS = 500_000  # 明细行保留上限（每次调用一行，量大；超限删最旧）


def _now_iso() -> str:
    """ISO8601 UTC（与 tracer._now_iso 同格式：YYYY-MM-DDTHH:MM:SS.mmmZ）。"""
    t = time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{int((t % 1) * 1000):03d}Z"


def _cfg_enabled() -> bool:
    """与 analytics_store 同一开关：测试环境可整体关闭防污染。"""
    return os.getenv("OBS_ANALYTICS_ENABLED", "true").strip().lower() not in (
        "0", "false", "no", "off",
    )


class LLMUsageStore:
    """llm_usage 明细存储。线程安全（单锁 + SQLite 短事务）。"""

    def __init__(self, db_path: str = DEFAULT_LLM_USAGE_DB_PATH):
        self._db_path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._lock = threading.Lock()
        self._write_count = 0
        self._init_db()

    def _conn(self):
        return get_connection(self._db_path)

    def _init_db(self):
        with self._lock, self._conn() as conn:
            # 检查是否需要迁移：旧表没有 component 字段
            needs_migration = False
            try:
                cols = [row[1] for row in conn.execute(
                    "PRAGMA table_info(llm_usage)").fetchall()]
                if "component" not in cols:
                    needs_migration = True
            except Exception:
                pass

            if needs_migration:
                logger.info("[LLMUsageStore] 检测到旧表结构，开始迁移添加 component 字段...")
                conn.execute("""
                    ALTER TABLE llm_usage ADD COLUMN component TEXT NOT NULL DEFAULT 'llm'
                """)
                logger.info("[LLMUsageStore] 迁移完成：所有历史数据标记为 component='llm'")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS llm_usage (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts                TEXT NOT NULL,      -- ISO8601 UTC（与 trace ts 同格式，支持字典序过滤）
                    trace_id          TEXT NOT NULL DEFAULT '',
                    session_id        TEXT NOT NULL DEFAULT '',
                    component         TEXT NOT NULL DEFAULT 'llm',  -- llm | embedding | rerank
                    model             TEXT NOT NULL DEFAULT '',
                    provider          TEXT NOT NULL DEFAULT '',
                    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
                    completion_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens      INTEGER NOT NULL DEFAULT 0,
                    cached_tokens     INTEGER NOT NULL DEFAULT 0,
                    reasoning_tokens  INTEGER NOT NULL DEFAULT 0,
                    cost_usd          REAL NOT NULL DEFAULT 0,
                    duration_ms       REAL NOT NULL DEFAULT 0,
                    finish_reason     TEXT NOT NULL DEFAULT '',
                    created_at        TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_lu_ts ON llm_usage(ts)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_lu_model ON llm_usage(model, ts)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_lu_trace ON llm_usage(trace_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_lu_component ON llm_usage(component, ts)")

    def record(self, event: dict[str, Any]) -> bool:
        """写入单次调用用量（LLM / Embedding / Reranker）。失败只记日志返回 False。"""
        if not _cfg_enabled():
            return False
        try:
            # ts 必须用 UTC ISO "T" 格式（与 trace ts 同构），否则 dashboard 的
            # 字符序时间窗过滤（"..." >= "YYYY-MM-DDT00:00:00"）会漏掉数据
            now = _now_iso()
            component = event.get("component", "llm")  # 默认 llm 保持向后兼容
            with self._lock, self._conn() as conn:
                conn.execute("""
                    INSERT INTO llm_usage (
                        ts, trace_id, session_id, component, model, provider,
                        prompt_tokens, completion_tokens, total_tokens,
                        cached_tokens, reasoning_tokens, cost_usd,
                        duration_ms, finish_reason, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    event.get("timestamp") or now,
                    str(event.get("trace_id") or ""),
                    str(event.get("session_id") or ""),
                    str(component),
                    str(event.get("model") or ""),
                    str(event.get("provider") or ""),
                    int(event.get("prompt_tokens") or 0),
                    int(event.get("completion_tokens") or 0),
                    int(event.get("total_tokens") or 0),
                    int(event.get("cached_tokens") or 0),
                    int(event.get("reasoning_tokens") or 0),
                    float(event.get("cost_usd") or 0.0),
                    float(event.get("duration_ms") or 0.0),
                    str(event.get("finish_reason") or ""),
                    now,
                ))
                self._write_count += 1
                # 每 2000 次写入做一次裁剪检查（避免每次写都 COUNT）
                if self._write_count % 2000 == 0:
                    count = conn.execute(
                        "SELECT COUNT(*) FROM llm_usage").fetchone()[0]
                    if count > _MAX_ROWS:
                        conn.execute(
                            "DELETE FROM llm_usage WHERE id IN "
                            "(SELECT id FROM llm_usage ORDER BY id ASC LIMIT ?)",
                            (count - _MAX_ROWS,),
                        )
            return True
        except Exception as e:
            logger.warning(f"[LLMUsageStore] 写入失败: {e}")
            return False

    def by_trace(self, trace_id: str, limit: int = 200) -> list[dict[str, Any]]:
        """按 trace_id 取该 trace 的全部调用明细（ts 升序）。

        供 tracer.finish() 回填 usage/model/llm span 指标使用：
        proxy 层每次调用同步写入本表，finish 时数据已就绪。
        """
        if not trace_id:
            return []
        try:
            with self._lock, self._conn() as conn:
                rows = conn.execute(
                    """SELECT ts, trace_id, session_id, component, model, provider,
                              prompt_tokens, completion_tokens, total_tokens,
                              cached_tokens, reasoning_tokens, cost_usd,
                              duration_ms, finish_reason
                       FROM llm_usage WHERE trace_id = ? ORDER BY ts ASC LIMIT ?""",
                    (trace_id, limit),
                ).fetchall()
            cols = ["ts", "trace_id", "session_id", "component", "model", "provider",
                    "prompt_tokens", "completion_tokens", "total_tokens",
                    "cached_tokens", "reasoning_tokens", "cost_usd",
                    "duration_ms", "finish_reason"]
            return [dict(zip(cols, r)) for r in rows]
        except Exception as e:
            logger.warning(f"[LLMUsageStore] by_trace({trace_id}) 查询失败: {e}")
            return []

    def list_calls(self, days: int = 7, model: str | None = None,
                   component: str | None = None,
                   limit: int = 20, offset: int = 0) -> dict:
        """调用明细（分页，最新在前）。返回 {calls: [...], total: n}。"""
        try:
            cutoff = self._cutoff_iso(days)
            where, params = ["ts >= ?"], [cutoff]
            if model:
                where.append("model = ?")
                params.append(model)
            if component and component != "all":
                where.append("component = ?")
                params.append(component)
            where_sql = " AND ".join(where)
            with self._lock, self._conn() as conn:
                conn.row_factory = sqlite3.Row
                total = conn.execute(
                    f"SELECT COUNT(*) FROM llm_usage WHERE {where_sql}",
                    params).fetchone()[0]
                rows = conn.execute(f"""
                    SELECT ts, trace_id, session_id, component, model, provider,
                           prompt_tokens, completion_tokens, total_tokens,
                           cached_tokens, reasoning_tokens, cost_usd,
                           duration_ms, finish_reason
                    FROM llm_usage WHERE {where_sql}
                    ORDER BY id DESC LIMIT ? OFFSET ?
                """, [*params, int(limit), int(offset)]).fetchall()
            return {"calls": [dict(r) for r in rows], "total": total}
        except Exception as e:
            logger.warning(f"[LLMUsageStore] list_calls 失败: {e}")
            return {"calls": [], "total": 0}

    # =====================================================
    # 看板聚合查询
    # =====================================================

    @staticmethod
    def _cutoff_iso(days: int) -> str:
        """N 天前（含当天）的 UTC 零点，ISO 格式；llm_usage.ts 为 ISO 字典序可比。"""
        return time.strftime("%Y-%m-%dT00:00:00", time.gmtime(time.time() - (days - 1) * 86400))

    def dashboard(self, days: int = 7, component: str | None = None) -> dict:
        """Token 看板聚合：总量 / 日趋势 / 按模型。失败返回空骨架。"""
        empty = {
            "totals": {
                "requests": 0, "calls": 0,
                "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                "cached_tokens": 0, "reasoning_tokens": 0, "cost_usd": 0.0,
            },
            "daily": [],
            "models": [],
        }
        try:
            cutoff = self._cutoff_iso(days)
            where_base = ["ts >= ?"]
            params_base = [cutoff]
            if component and component != "all":
                where_base.append("component = ?")
                params_base.append(component)
            where_sql = " AND ".join(where_base)

            with self._lock, self._conn() as conn:
                conn.row_factory = sqlite3.Row

                # ① 总量（requests = 去重轮次；calls = 调用次数）
                totals = dict(conn.execute(f"""
                    SELECT COUNT(DISTINCT CASE WHEN trace_id != '' THEN trace_id END) AS requests,
                           COUNT(*) AS calls,
                           COALESCE(SUM(prompt_tokens), 0)     AS prompt_tokens,
                           COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                           COALESCE(SUM(total_tokens), 0)      AS total_tokens,
                           COALESCE(SUM(cached_tokens), 0)     AS cached_tokens,
                           COALESCE(SUM(reasoning_tokens), 0)  AS reasoning_tokens,
                           COALESCE(SUM(cost_usd), 0)          AS cost_usd
                    FROM llm_usage WHERE {where_sql}
                """, params_base).fetchone())

                # ② 日趋势（日 × 输入/输出/成本）
                daily = [dict(r) for r in conn.execute(f"""
                    SELECT substr(ts, 1, 10)             AS day,
                           COUNT(*)                      AS calls,
                           COALESCE(SUM(prompt_tokens), 0)     AS prompt_tokens,
                           COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                           COALESCE(SUM(total_tokens), 0)      AS total_tokens,
                           COALESCE(SUM(cost_usd), 0)          AS cost_usd
                    FROM llm_usage WHERE {where_sql}
                    GROUP BY substr(ts, 1, 10)
                    ORDER BY day ASC
                """, params_base).fetchall()]

                # ③ 按模型细分（Provider/Model 维度）
                models = [dict(r) for r in conn.execute(f"""
                    SELECT provider, model,
                           COUNT(*) AS calls,
                           COUNT(DISTINCT CASE WHEN trace_id != '' THEN trace_id END) AS requests,
                           COALESCE(SUM(prompt_tokens), 0)     AS prompt_tokens,
                           COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                           COALESCE(SUM(total_tokens), 0)      AS total_tokens,
                           COALESCE(SUM(cached_tokens), 0)     AS cached_tokens,
                           COALESCE(SUM(reasoning_tokens), 0)  AS reasoning_tokens,
                           COALESCE(SUM(cost_usd), 0)          AS cost_usd
                    FROM llm_usage WHERE {where_sql}
                    GROUP BY provider, model
                    ORDER BY total_tokens DESC
                """, params_base).fetchall()]

            totals["cost_usd"] = round(totals.get("cost_usd", 0) or 0, 6)
            for d in daily:
                d["cost_usd"] = round(d.get("cost_usd", 0) or 0, 6)
            for m in models:
                m["cost_usd"] = round(m.get("cost_usd", 0) or 0, 6)
            return {"totals": totals, "daily": daily, "models": models}
        except Exception as e:
            logger.warning(f"[LLMUsageStore] dashboard 聚合失败: {e}")
            return empty


# 模块级单例
_llm_usage_store: LLMUsageStore | None = None
_store_lock = threading.Lock()


def get_llm_usage_store() -> LLMUsageStore:
    """存储工厂：OBS_DB_BACKEND=postgres 时返回 PG 实现（接口/语义一致）。"""
    global _llm_usage_store
    if _llm_usage_store is None:
        with _store_lock:
            if _llm_usage_store is None:
                if os.getenv("OBS_DB_BACKEND", "sqlite").strip().lower() == "postgres":
                    from backend.observability.llm_usage_store_pg import PostgresLLMUsageStore
                    _llm_usage_store = PostgresLLMUsageStore()
                else:
                    _llm_usage_store = LLMUsageStore()
    return _llm_usage_store
