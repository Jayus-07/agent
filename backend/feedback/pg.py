"""feedback/pg.py — feedback 表的 PostgreSQL 实现（迁移计划 Batch D）。

接口与 SQLite 版 `backend/feedback/__init__.py` 完全一致（init_db / add_feedback / stats）。
PG 为唯一实现（2026-09-17 SQLite 轨删除）。

库归属：agent_business（业务数据，对 NL2SQL 可见）。
schema 与 backend/sql/migrations/016_business_stores_pg.sql 保持一致。

时间语义：SQLite 版 created_at DEFAULT datetime('now') 为 UTC 文本
（"YYYY-MM-DD HH:MM:SS"），PG 版由应用侧生成同格式 UTC 文本写入，
不依赖 PG 服务器时区；stats 的 N 天窗口 cutoff 同为应用侧 UTC 文本（字典序可比）。
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from typing import Any, Iterator

import psycopg2

from backend.config.database import FEEDBACK_PG_CONFIG
from backend.shared.logger import logger

_TABLE = os.getenv("FEEDBACK_PG_TABLE", "feedback")

_SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    id             BIGSERIAL PRIMARY KEY,
    session_id     TEXT NOT NULL,
    msg_id         TEXT,
    question       TEXT,
    answer_preview TEXT,
    vote           TEXT NOT NULL CHECK (vote IN ('positive', 'negative')),
    reason         TEXT,
    created_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_feedback_session ON {_TABLE}(session_id);
CREATE INDEX IF NOT EXISTS idx_feedback_created ON {_TABLE}(created_at);
CREATE INDEX IF NOT EXISTS idx_feedback_vote ON {_TABLE}(vote);
"""


def _utc_now_text() -> str:
    """等价 SQLite datetime('now') 的 UTC 文本格式"""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


@contextmanager
def _conn() -> Iterator[Any]:
    """per-op 连接：成功 commit、异常 rollback、退出必关。"""
    conn = psycopg2.connect(**FEEDBACK_PG_CONFIG)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """创建 feedback 表（幂等）"""
    with _conn() as conn:
        conn.cursor().execute(_SCHEMA_SQL)


def add_feedback(
    session_id: str,
    vote: str,
    msg_id: str = "",
    question: str = "",
    answer_preview: str = "",
    reason: str = "",
) -> int:
    """写入反馈，返回新 id"""
    if vote not in ("positive", "negative"):
        raise ValueError(f"vote 必须是 positive/negative，得到: {vote}")
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""INSERT INTO {_TABLE}
            (session_id, msg_id, question, answer_preview, vote, reason, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (
                session_id, msg_id, question[:500], answer_preview[:500],
                vote, reason[:500], _utc_now_text(),
            ),
        )
        return cur.fetchone()[0]


def stats(days: int = 7) -> dict:
    """最近 N 天的反馈统计"""
    # created_at 为 UTC 文本，cutoff 同格式（字典序比较等价时间比较）
    cutoff = time.strftime(
        "%Y-%m-%d %H:%M:%S",
        time.gmtime(time.time() - days * 86400),
    )
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            f"SELECT COUNT(*) FROM {_TABLE} WHERE created_at >= %s", (cutoff,)
        )
        total = cur.fetchone()[0]
        cur.execute(
            f"SELECT COUNT(*) FROM {_TABLE} "
            "WHERE vote='positive' AND created_at >= %s",
            (cutoff,),
        )
        positive = cur.fetchone()[0]
        negative = total - positive
        rate = (positive / total) if total > 0 else None
        # Top 失败 query
        cur.execute(
            f"""SELECT question, COUNT(*) as cnt
               FROM {_TABLE}
               WHERE vote='negative' AND created_at >= %s
               GROUP BY question
               ORDER BY cnt DESC LIMIT 10""",
            (cutoff,),
        )
        top_failed = cur.fetchall()
    return {
        "days": days,
        "total": total,
        "positive": positive,
        "negative": negative,
        "positive_rate": rate,
        "top_failed_queries": [{"question": q, "count": c} for q, c in top_failed],
    }


logger.debug("[feedback.pg] PG 实现模块加载完成")
