"""用户反馈 API与评测候选审核入口。"""
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from backend.app.api.deps import OperatorIdentity, require_admin_user
from backend.app.api.identity import Identity, resolve_identity
from backend.evaluation.curator import append_case, list_cases
from backend.evaluation.models import TestCase
from backend.evaluation.trace_bridge import build_test_case_from_trace
from backend.feedback import add_feedback, init_db, stats as stats_query
from backend.feedback.candidates_pg import (
    create_candidate,
    get_candidate,
    list_candidates,
    mark_promoted,
    promotion_lock,
    transition_candidate,
)
from backend.observability.metrics import record_feedback
from backend.observability.tracer import trace_collector
from backend.shared.logger import logger

router = APIRouter()

# 启动时初始化（导入即建表）
init_db()


class FeedbackRequest(BaseModel):
    session_id: str = Field(..., min_length=1)
    vote: str = Field(..., pattern="^(positive|negative)$")
    msg_id: str = ""
    question: str = ""
    answer_preview: str = ""
    reason: str = ""
    trace_id: str = Field("", max_length=128)
    correction_text: str = Field("", max_length=2000)
    expected_answer: str = Field("", max_length=2000)


class CandidateReviewRequest(BaseModel):
    note: str = Field("", max_length=1000)


def _trace_value(trace: dict | object, key: str, default=""):
    if isinstance(trace, dict):
        return trace.get(key, default)
    return getattr(trace, key, default)


def _trace_owner_matches(trace: dict | object, identity: Identity) -> bool:
    """只接受带完整服务端归属标签的 Trace，缺标签按无法证明归属拒绝。"""
    tags = _trace_value(trace, "tags", {}) or {}
    if not isinstance(tags, dict):
        return False
    return (
        tags.get("tenant_id") == identity.tenant_id
        and tags.get("user_id") == identity.user_id
    )


def _find_trace(trace_id: str) -> dict | object | None:
    trace = trace_collector.get(trace_id)
    if trace is not None:
        return trace
    for item in trace_collector.list(limit=200):
        if _trace_value(item, "id") == trace_id:
            return item
    return None


def _candidate_case(trace: dict | object, req: FeedbackRequest) -> TestCase:
    expected_overrides = {}
    expected_answer = req.expected_answer.strip() or req.correction_text.strip()
    if expected_answer:
        expected_overrides["expected_answer"] = expected_answer
    return build_test_case_from_trace(
        trace,
        expected_overrides=expected_overrides or None,
        note=req.reason.strip() or req.correction_text.strip(),
    )


@router.post("/feedback")
async def post_feedback(req: FeedbackRequest, request: Request):
    """记录反馈；负反馈/纠错可生成租户隔离的 pending 候选。"""
    identity = resolve_identity(request)
    trace = None
    if req.trace_id:
        if not identity.authenticated or not identity.tenant_id:
            raise HTTPException(401, "Trace 反馈需要可信用户与租户身份")
        trace = _find_trace(req.trace_id)
        if trace is None:
            raise HTTPException(404, "Trace 不存在")
        if not _trace_owner_matches(trace, identity):
            raise HTTPException(403, "无权访问该 Trace")
        if _trace_value(trace, "session_id") != req.session_id:
            raise HTTPException(403, "反馈会话与 Trace 不匹配")
    try:
        new_id = add_feedback(
            session_id=req.session_id,
            vote=req.vote,
            msg_id=req.msg_id,
            question=req.question,
            answer_preview=req.answer_preview,
            reason=req.reason,
            trace_id=req.trace_id,
            user_id=identity.user_id,
            tenant_id=identity.tenant_id,
            correction_text=req.correction_text,
            expected_answer=req.expected_answer,
        )
        # 埋点运营指标
        record_feedback(req.vote)
        response = {"ok": True, "id": new_id}
        should_create_candidate = bool(
            trace is not None
            and (
                req.vote == "negative"
                or req.correction_text.strip()
                or req.expected_answer.strip()
            )
        )
        if should_create_candidate:
            case = _candidate_case(trace, req)
            candidate = create_candidate(
                feedback_id=new_id,
                tenant_id=identity.tenant_id,
                actor_id=identity.user_id,
                trace_id=req.trace_id,
                module=case.module,
                case_payload=case.model_dump(mode="json"),
            )
            response.update({
                "candidate_id": candidate["candidate_id"],
                "candidate_status": candidate["status"],
            })
        return response
    except Exception as e:
        logger.error(f"[Feedback] 写入失败: {e}")
        raise HTTPException(503, "反馈写入失败，请稍后重试") from e


@router.get("/feedback/stats")
async def get_feedback_stats(days: int = 7):
    """最近 N 天的反馈统计（供 /ops 看板或独立周报）。"""
    try:
        return stats_query(days=days)
    except Exception as e:
        logger.error(f"[Feedback] stats 失败: {e}")
        raise HTTPException(503, "反馈统计暂不可用，请稍后重试") from e


@router.get("/feedback/candidates")
async def get_feedback_candidates(
    request: Request,
    status: str = Query("", description="pending/approved/rejected/promoted"),
    limit: int = Query(50, ge=1, le=200),
    operator: OperatorIdentity = Depends(require_admin_user),
):
    """管理员查看当前租户的评测候选；不跨租户返回。"""
    identity = resolve_identity(request)
    if not identity.tenant_id:
        raise HTTPException(401, "候选查询需要可信租户身份")
    try:
        return {"items": list_candidates(identity.tenant_id, status=status, limit=limit)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/feedback/candidates/{candidate_id}/approve")
async def approve_feedback_candidate(
    candidate_id: str,
    body: CandidateReviewRequest,
    request: Request,
    operator: OperatorIdentity = Depends(require_admin_user),
):
    """pending → approved；只允许管理员审核。"""
    identity = resolve_identity(request)
    if not identity.tenant_id:
        raise HTTPException(401, "候选审核需要可信租户身份")
    try:
        candidate = transition_candidate(
            candidate_id, identity.tenant_id, "approved", operator.actor, body.note,
        )
        return {"ok": True, "candidate": candidate}
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(409, str(e))


@router.post("/feedback/candidates/{candidate_id}/reject")
async def reject_feedback_candidate(
    candidate_id: str,
    body: CandidateReviewRequest,
    request: Request,
    operator: OperatorIdentity = Depends(require_admin_user),
):
    """pending → rejected；拒绝候选永远不能 promotion。"""
    identity = resolve_identity(request)
    if not identity.tenant_id:
        raise HTTPException(401, "候选审核需要可信租户身份")
    try:
        candidate = transition_candidate(
            candidate_id, identity.tenant_id, "rejected", operator.actor, body.note,
        )
        return {"ok": True, "candidate": candidate}
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(409, str(e))


@router.post("/feedback/candidates/{candidate_id}/promote")
async def promote_feedback_candidate(
    candidate_id: str,
    request: Request,
    operator: OperatorIdentity = Depends(require_admin_user),
):
    """approved → promoted；只有此入口才调用 append_case 写入评测集。"""
    identity = resolve_identity(request)
    if not identity.tenant_id:
        raise HTTPException(401, "候选 promotion 需要可信租户身份")
    candidate = get_candidate(candidate_id, identity.tenant_id)
    if candidate is None:
        raise HTTPException(404, "候选不存在")
    if candidate["status"] == "promoted":
        return {
            "ok": True,
            "status": "promoted",
            "case_id": candidate.get("promoted_case_id", ""),
            "appended": False,
        }
    if candidate["status"] != "approved":
        raise HTTPException(409, "只有 approved 候选可以 promotion")
    try:
        with promotion_lock(identity.tenant_id, candidate["trace_id"]):
            # 在锁内重新读取，避免等待期间另一实例已经完成 promotion。
            candidate = get_candidate(candidate_id, identity.tenant_id)
            if candidate is None:
                raise HTTPException(404, "候选不存在")
            if candidate["status"] == "promoted":
                return {
                    "ok": True,
                    "status": "promoted",
                    "case_id": candidate.get("promoted_case_id", ""),
                    "appended": False,
                }
            if candidate["status"] != "approved":
                raise HTTPException(409, "只有 approved 候选可以 promotion")

            case = TestCase.model_validate(json.loads(candidate["case_json"]))
            result = append_case(case)
            case_id = case.id
            if not result.get("appended"):
                if not result.get("duplicate", False):
                    raise HTTPException(503, "评测集导出失败，请稍后重试")
                existing = next(
                    (
                        item for item in list_cases(case.module)
                        if item.metadata.get("trace_id") == candidate["trace_id"]
                    ),
                    None,
                )
                case_id = existing.id if existing else case.id
            promoted = mark_promoted(
                candidate_id, identity.tenant_id, case_id, operator.actor,
            )
            return {
                "ok": True,
                "status": promoted["status"],
                "case_id": promoted.get("promoted_case_id", case_id),
                "appended": bool(result.get("appended")),
            }
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(409, str(e))
