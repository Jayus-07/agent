"""feedback 表初始化（2026-08-11 P1 反馈循环，PG 实现）

2026-09-17 SQLite 轨已删除，唯一实现为 backend/feedback/pg.py。
"""
import os


def init_db() -> None:
    """创建 feedback 表（幂等）"""
    from backend.feedback.pg import init_db as _pg_init_db
    _pg_init_db()


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
    from backend.feedback.pg import add_feedback as _pg_add_feedback
    return _pg_add_feedback(
        session_id, vote, msg_id=msg_id, question=question,
        answer_preview=answer_preview, reason=reason,
    )


def stats(days: int = 7) -> dict:
    """最近 N 天的反馈统计"""
    from backend.feedback.pg import stats as _pg_stats
    return _pg_stats(days)
