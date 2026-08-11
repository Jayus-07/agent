"""feedback 表初始化（2026-08-11 P1 反馈循环）"""
import os
import sqlite3
from pathlib import Path

DB_PATH = os.getenv("FEEDBACK_DB_PATH", "data/feedback.db")


def _ensure_dir():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)


def init_db() -> None:
    """创建 feedback 表（幂等）"""
    _ensure_dir()
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            msg_id TEXT,
            question TEXT,
            answer_preview TEXT,
            vote TEXT NOT NULL CHECK (vote IN ('positive', 'negative')),
            reason TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_feedback_session ON feedback(session_id);
        CREATE INDEX IF NOT EXISTS idx_feedback_created ON feedback(created_at);
        CREATE INDEX IF NOT EXISTS idx_feedback_vote ON feedback(vote);
        """)


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
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            """INSERT INTO feedback
            (session_id, msg_id, question, answer_preview, vote, reason)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (session_id, msg_id, question[:500], answer_preview[:500], vote, reason[:500]),
        )
        return cur.lastrowid


def stats(days: int = 7) -> dict:
    """最近 N 天的反馈统计"""
    with sqlite3.connect(DB_PATH) as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM feedback WHERE created_at >= datetime('now', ?)",
            (f"-{days} days",),
        ).fetchone()[0]
        positive = conn.execute(
            "SELECT COUNT(*) FROM feedback WHERE vote='positive' AND created_at >= datetime('now', ?)",
            (f"-{days} days",),
        ).fetchone()[0]
        negative = total - positive
        rate = (positive / total) if total > 0 else None
        # Top 失败 query
        top_failed = conn.execute(
            """SELECT question, COUNT(*) as cnt
               FROM feedback
               WHERE vote='negative' AND created_at >= datetime('now', ?)
               GROUP BY question
               ORDER BY cnt DESC LIMIT 10""",
            (f"-{days} days",),
        ).fetchall()
        return {
            "days": days,
            "total": total,
            "positive": positive,
            "negative": negative,
            "positive_rate": rate,
            "top_failed_queries": [{"question": q, "count": c} for q, c in top_failed],
        }
