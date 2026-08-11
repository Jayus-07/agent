"""用户反馈 API（2026-08-11 P1 反馈循环）。"""
from fastapi import APIRouter
from pydantic import BaseModel, Field
from backend.feedback import init_db, add_feedback, stats as stats_query
from backend.observability.metrics import record_feedback
from backend.shared.logger import logger

router = APIRouter()

# 启动时初始化（导入即建表）
init_db()


class FeedbackRequest(BaseModel):
    session_id: str = Field(..., min_length=1)
    vote: str = Field(..., regex="^(positive|negative)$")
    msg_id: str = ""
    question: str = ""
    answer_preview: str = ""
    reason: str = ""


@router.post("/feedback")
async def post_feedback(req: FeedbackRequest):
    """记录用户 👍 / 👎 反馈。"""
    try:
        new_id = add_feedback(
            session_id=req.session_id,
            vote=req.vote,
            msg_id=req.msg_id,
            question=req.question,
            answer_preview=req.answer_preview,
            reason=req.reason,
        )
        # 埋点运营指标
        record_feedback(req.vote)
        return {"ok": True, "id": new_id}
    except Exception as e:
        logger.error(f"[Feedback] 写入失败: {e}")
        return {"ok": False, "error": str(e)}


@router.get("/feedback/stats")
async def get_feedback_stats(days: int = 7):
    """最近 N 天的反馈统计（供 /ops 看板或独立周报）。"""
    try:
        return stats_query(days=days)
    except Exception as e:
        logger.error(f"[Feedback] stats 失败: {e}")
        return {"days": days, "total": 0, "positive": 0, "negative": 0, "error": str(e)}
