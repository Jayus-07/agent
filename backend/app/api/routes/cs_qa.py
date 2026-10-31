"""cs_qa.py — 质检中心（cs-workstation-v2 P6）

抽检任务 / AI 预填 / 评分 / 申诉 / 终审 / 日报的唯一生产入口：
  - GET  /cs/qa/tasks                        任务列表（agent 仅本人，supervisor 全部）
  - POST /cs/qa/tasks/{id}/score             评分提交/草稿（agent 仅本人任务）
  - POST /cs/qa/tasks/{id}/ai-prefill        AI 预填建议（缺失时计算并存储，幂等）
  - POST /cs/qa/tasks/{id}/appeal            申诉（被检坐席本人，scored + none）
  - POST /cs/qa/tasks/{id}/appeal/decision   终审维持/改判（supervisor，appealing）
  - GET  /cs/qa/report                       质检日报表（supervisor，含人工抽检数据）

状态机（不可回退，违规 409）：
  评分    pending/draft → draft|scored；scored 后锁定（改判仅走申诉终审）
  申诉    none → appealing → upheld|overturned（终态锁定）
身份口径与 cs_canned 一致：require_agent 服务端反查；全部查询带 tenant_id
（跨租户 id 404）。评分/终审属治理动作，audit_logs 留痕（actor_type=human，
表 CHECK 限 ai/human/system）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes.cs_agent_offers import (
    AgentContext,
    get_session,
    require_agent,
)
from backend.customer_service.models.qa import CSQAScore
from backend.customer_service.qa_sampling import compute_ai_prefill

router = APIRouter(tags=["智能客服-质检中心"])

_STATUS_VALUES = ("pending", "draft", "scored")
_APPEAL_VALUES = ("appealing", "upheld", "overturned")


def _require_supervisor(context: AgentContext) -> None:
    if not context.is_supervisor:
        raise HTTPException(403, detail="该操作需要客服主管或平台管理员权限")


def _visible_clause(context: AgentContext):
    """agent 仅本人任务（防枚举 404 口径一致）；supervisor 全部。"""
    if context.is_supervisor:
        return CSQAScore.tenant_id == context.tenant_id
    return (
        (CSQAScore.tenant_id == context.tenant_id)
        & (CSQAScore.agent_id == context.agent_id)
    )


async def _load_visible(
    session: AsyncSession, *, task_id: int, context: AgentContext,
) -> CSQAScore:
    row = (
        await session.execute(
            select(CSQAScore).where(
                CSQAScore.id == task_id,
                _visible_clause(context),
            ).limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail="质检任务不存在")
    return row


def _editable_by(context: AgentContext, row: CSQAScore) -> bool:
    """评分写权限：supervisor 任意；agent 仅被检本人。"""
    return context.is_supervisor or row.agent_id == context.agent_id


def _effective(row: CSQAScore) -> tuple[int | None, str | None]:
    """展示口径：改判后用终审分/结论，否则用原始评分。"""
    if row.appeal_status == "overturned" and row.final_score is not None:
        return row.final_score, row.final_verdict
    return (
        int(row.total_score) if row.total_score is not None else None,
        row.verdict,
    )


def _row_dto(row: CSQAScore) -> dict:
    total, verdict = _effective(row)
    return {
        "id": row.id,
        "conversation_id": row.conversation_id,
        "agent_id": row.agent_id,
        "batch_date": row.batch_date.isoformat() if row.batch_date else None,
        "source": row.source,
        "status": row.status,
        "scores": {
            "service": row.score_service,
            "accuracy": row.score_accuracy,
            "compliance": row.score_compliance,
            "timeliness": row.score_timeliness,
        },
        "total_score": total,
        "verdict": verdict,
        "note": row.note,
        "ai_prefill": row.ai_prefill,
        "scored_by": row.scored_by,
        "scored_at": row.scored_at.isoformat() if row.scored_at else None,
        "appeal_status": row.appeal_status,
        "appeal_reason": row.appeal_reason,
        "appeal_decision": row.appeal_decision,
        "appeal_opinion": row.appeal_opinion,
        "final_score": row.final_score,
        "final_verdict": row.final_verdict,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


class QaScoreBody(BaseModel):
    service: int = Field(ge=0, le=25)
    accuracy: int = Field(ge=0, le=25)
    compliance: int = Field(ge=0, le=25)
    timeliness: int = Field(ge=0, le=25)
    verdict: str = Field(pattern="^(pass|fail)$")
    note: str | None = Field(default=None, max_length=2000)
    draft: bool = False

    @field_validator("note")
    @classmethod
    def _note_blank_to_none(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v or None


class QaAppealBody(BaseModel):
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def _reason_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("申诉理由必填")
        return v


class QaAppealDecisionBody(BaseModel):
    decision: str = Field(pattern="^(upheld|overturned)$")
    opinion: str = Field(min_length=1, max_length=500)
    final_score: int | None = Field(default=None, ge=0, le=100)
    final_verdict: str | None = Field(default=None, pattern="^(pass|fail)$")

    @field_validator("opinion")
    @classmethod
    def _opinion_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("终审意见必填")
        return v


class QaTaskListResponse(BaseModel):
    items: list[dict]
    total: int
    scope: str  # self | all


class QaMutationResponse(BaseModel):
    task: dict


class QaReportRow(BaseModel):
    batch_date: str
    sampled: int
    scored: int
    pass_count: int
    pass_rate: float | None = None
    avg_score: float | None = None
    appeals: int
    top_deduction: str | None = None


class QaReportResponse(BaseModel):
    items: list[QaReportRow]


def _list_statement(
    context: AgentContext,
    *,
    status: str | None,
    appeal: str | None,
    limit: int,
) -> Select:
    """列表查询（全部条件经 ORM 绑定表达式，无字符串拼接）。"""
    clauses = [_visible_clause(context)]
    if status is not None:
        if status not in _STATUS_VALUES:
            raise HTTPException(422, detail="status 非法")
        clauses.append(CSQAScore.status == status)
    if appeal is not None:
        if appeal not in _APPEAL_VALUES:
            raise HTTPException(422, detail="appeal 非法")
        clauses.append(CSQAScore.appeal_status == appeal)
    return (
        select(CSQAScore)
        .where(*clauses)
        .order_by(CSQAScore.id.desc())
        .limit(limit)
    )


@router.get("/cs/qa/tasks", response_model=QaTaskListResponse)
async def list_qa_tasks(
    status: str | None = Query(default=None),
    appeal: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    context: AgentContext = Depends(require_agent),
    session: AsyncSession = Depends(get_session),
) -> QaTaskListResponse:
    """抽检任务列表（agent=我的任务，supervisor=全部）。"""
    rows = (
        await session.execute(
            _list_statement(
                context, status=status, appeal=appeal, limit=limit,
            )
        )
    ).scalars().all()
    return QaTaskListResponse(
        items=[_row_dto(row) for row in rows],
        total=len(rows),
        scope="all" if context.is_supervisor else "self",
    )


@router.post("/cs/qa/tasks/{task_id}/score", response_model=QaMutationResponse)
async def score_qa_task(
    task_id: int,
    body: QaScoreBody,
    context: AgentContext = Depends(require_agent),
    session: AsyncSession = Depends(get_session),
) -> QaMutationResponse:
    """评分提交/保存草稿：pending/draft → draft|scored；scored 后锁定（409）。"""
    async with session.begin():
        row = await _load_visible(
            session, task_id=task_id, context=context,
        )
        if not _editable_by(context, row):
            raise HTTPException(403, detail="仅可评分本人的抽检任务")
        if row.appeal_status != "none":
            raise HTTPException(
                409, detail="申诉流程中的任务不可再评分",
            )
        if row.status == "scored":
            raise HTTPException(
                409, detail="评分已提交（终态），如需调整请走申诉流程",
            )
        total = body.service + body.accuracy + body.compliance + body.timeliness
        row.score_service = body.service
        row.score_accuracy = body.accuracy
        row.score_compliance = body.compliance
        row.score_timeliness = body.timeliness
        row.total_score = total
        row.verdict = body.verdict
        row.note = body.note
        row.source = "human"
        row.scored_by = context.agent_id
        if body.draft:
            row.status = "draft"
        else:
            row.status = "scored"
            row.scored_at = datetime.now(timezone.utc)
        await session.flush()
        await _audit(
            session,
            context=context,
            action="qa_score_draft" if body.draft else "qa_score_submit",
            row=row,
            detail=(
                f"total={total}; verdict={body.verdict}; draft={body.draft}; "
                f"scored_by={context.agent_id}"
            ),
        )
        return QaMutationResponse(task=_row_dto(row))


@router.post("/cs/qa/tasks/{task_id}/ai-prefill", response_model=QaMutationResponse)
async def ai_prefill_qa_task(
    task_id: int,
    context: AgentContext = Depends(require_agent),
    session: AsyncSession = Depends(get_session),
) -> QaMutationResponse:
    """AI 预填建议：缺失时按会话事实计算并存储（幂等）；已评分任务 409。"""
    async with session.begin():
        row = await _load_visible(
            session, task_id=task_id, context=context,
        )
        if row.status == "scored" or row.appeal_status != "none":
            raise HTTPException(409, detail="任务已终态，AI 预填不可用")
        if row.ai_prefill is None:
            facts = await _conversation_facts(session, row.conversation_id)
            row.ai_prefill = compute_ai_prefill(facts)
            await session.flush()
        return QaMutationResponse(task=_row_dto(row))


async def _conversation_facts(
    session: AsyncSession, conversation_id: str,
) -> dict:
    """会话事实（评分参考）：满意度/首响/派单尝试/SLA 超时。绑定参数查询。"""
    from sqlalchemy import text

    row = (
        await session.execute(text("""
            SELECT c.rating,
                   EXTRACT(EPOCH FROM (c.first_reply_at - c.created_at))
                       AS first_reply_seconds,
                   (SELECT h.attempt_count
                      FROM customer_service.handoffs h
                     WHERE h.conversation_id = c.conversation_id
                     ORDER BY h.updated_at DESC LIMIT 1) AS attempt_count,
                   EXISTS (SELECT 1 FROM customer_service.handoffs h2
                            WHERE h2.conversation_id = c.conversation_id
                              AND h2.sla_level = 'breach') AS sla_breach
            FROM customer_service.conversations c
            WHERE c.conversation_id = :cid
            LIMIT 1
        """), {"cid": conversation_id})
    ).mappings().one_or_none()
    if row is None:
        return {}
    return {
        "rating": row["rating"],
        "first_reply_seconds": row["first_reply_seconds"],
        "attempt_count": row["attempt_count"],
        "sla_breach": bool(row["sla_breach"]),
    }


@router.post("/cs/qa/tasks/{task_id}/appeal", response_model=QaMutationResponse)
async def appeal_qa_task(
    task_id: int,
    body: QaAppealBody,
    context: AgentContext = Depends(require_agent),
    session: AsyncSession = Depends(get_session),
) -> QaMutationResponse:
    """申诉：被检坐席本人；已评分（scored）且无在途/终态申诉。"""
    async with session.begin():
        row = await _load_visible(
            session, task_id=task_id, context=context,
        )
        if row.agent_id != context.agent_id:
            raise HTTPException(403, detail="仅被检坐席本人可申诉")
        if row.status != "scored":
            raise HTTPException(409, detail="仅已提交评分的任务可申诉")
        if row.appeal_status != "none":
            raise HTTPException(409, detail="该任务申诉已存在或已终审")
        row.appeal_status = "appealing"
        row.appeal_reason = body.reason
        row.appeal_requested_by = context.agent_id
        row.appeal_requested_at = datetime.now(timezone.utc)
        await session.flush()
        await _audit(
            session, context=context, action="qa_appeal_request", row=row,
            detail=f"reason={body.reason}; requested_by={context.agent_id}",
        )
        return QaMutationResponse(task=_row_dto(row))


@router.post(
    "/cs/qa/tasks/{task_id}/appeal/decision", response_model=QaMutationResponse,
)
async def decide_qa_appeal(
    task_id: int,
    body: QaAppealDecisionBody,
    context: AgentContext = Depends(require_agent),
    session: AsyncSession = Depends(get_session),
) -> QaMutationResponse:
    """终审（supervisor）：维持=upheld；改判=overturned（终审分/结论必填）。"""
    _require_supervisor(context)
    if body.decision == "overturned" and (
        body.final_score is None or body.final_verdict is None
    ):
        raise HTTPException(422, detail="改判必须给出终审得分与结论")
    async with session.begin():
        row = await _load_visible(
            session, task_id=task_id, context=context,
        )
        if row.appeal_status != "appealing":
            raise HTTPException(409, detail="该任务不在申诉中，不可终审")
        row.appeal_status = body.decision
        row.appeal_decision = body.decision
        row.appeal_opinion = body.opinion
        row.appeal_decided_by = context.agent_id
        row.appeal_decided_at = datetime.now(timezone.utc)
        if body.decision == "overturned":
            row.final_score = body.final_score
            row.final_verdict = body.final_verdict
        await session.flush()
        await _audit(
            session, context=context, action="qa_appeal_decide", row=row,
            detail=(
                f"decision={body.decision}; opinion={body.opinion}; "
                f"final_score={body.final_score}; "
                f"final_verdict={body.final_verdict}; "
                f"decided_by={context.agent_id}"
            ),
        )
        return QaMutationResponse(task=_row_dto(row))


# 日报聚合（绑定参数，无任何字符串拼接输入）。
_REPORT_SQL = """
    SELECT batch_date,
           count(*)                                            AS sampled,
           count(*) FILTER (WHERE status = 'scored')           AS scored,
           count(*) FILTER (WHERE COALESCE(
               final_score,
               total_score) IS NOT NULL
               AND COALESCE(final_verdict, verdict) = 'pass')  AS pass_count,
           round(avg(COALESCE(final_score, total_score))::numeric, 1)
                                                               AS avg_score,
           count(*) FILTER (WHERE appeal_status <> 'none')     AS appeals,
           count(*) FILTER (WHERE score_service < 25)          AS d_service,
           count(*) FILTER (WHERE score_accuracy < 25)         AS d_accuracy,
           count(*) FILTER (WHERE score_compliance < 25)       AS d_compliance,
           count(*) FILTER (WHERE score_timeliness < 25)       AS d_timeliness
    FROM customer_service.cs_qa_scores
    WHERE tenant_id = :tenant
      AND batch_date >= :since AND batch_date <= :today
    GROUP BY batch_date
    ORDER BY batch_date DESC
"""

_DIM_NAMES = {
    "d_service": "服务规范",
    "d_accuracy": "准确性",
    "d_compliance": "合规",
    "d_timeliness": "处理时效",
}


@router.get("/cs/qa/report", response_model=QaReportResponse)
async def qa_report(
    days: int = Query(default=7, ge=1, le=90),
    context: AgentContext = Depends(require_agent),
    session: AsyncSession = Depends(get_session),
) -> QaReportResponse:
    """质检日报表（supervisor）：近 N 天抽检聚合（含人工抽检数据）。

    直接按 batch_date 聚合 cs_qa_scores，不依赖 beat 日报是否已跑；
    top_deduction = 当日最常见扣分维度。
    """
    _require_supervisor(context)
    today = datetime.now(timezone.utc).date()
    since = today - timedelta(days=days - 1)

    from sqlalchemy import text

    rows = (
        await session.execute(
            text(_REPORT_SQL),
            {"tenant": context.tenant_id, "since": since, "today": today},
        )
    ).mappings().all()

    items: list[QaReportRow] = []
    for row in rows:
        scored = int(row["scored"] or 0)
        pass_count = int(row["pass_count"] or 0)
        counts = {key: int(row[key] or 0) for key in _DIM_NAMES}
        top_key = (
            max(counts, key=lambda key: counts[key])
            if any(counts.values()) else None
        )
        items.append(QaReportRow(
            batch_date=row["batch_date"].isoformat(),
            sampled=int(row["sampled"] or 0),
            scored=scored,
            pass_count=pass_count,
            pass_rate=round(pass_count / scored, 4) if scored else None,
            avg_score=(
                float(row["avg_score"]) if row["avg_score"] is not None else None
            ),
            appeals=int(row["appeals"] or 0),
            top_deduction=(
                f"{_DIM_NAMES[top_key]}（{counts[top_key]}）" if top_key else None
            ),
        ))
    return QaReportResponse(items=items)


async def _audit(
    session: AsyncSession,
    *,
    context: AgentContext,
    action: str,
    row: CSQAScore,
    detail: str,
) -> None:
    """治理动作审计留痕（关键旁路：失败不阻断主流程，与 P5 口径一致）。"""
    try:
        from backend.customer_service.audit import build_audit_entry
        from backend.customer_service.repository.audit_repo import AuditRepository

        await AuditRepository(session).insert_audit_log({
            **build_audit_entry(
                context.user_id,
                action,
                "success",
                target_type="qa_score",
                target_id=str(row.id),
                detail=detail,
                conversation_id=row.conversation_id,
            ),
            "actor_type": "human",
            "actor_id": context.agent_id,
        })
    except Exception:
        from backend.shared.logger import logger

        logger.error(
            "[QA] audit persist failed: task=%s action=%s", row.id, action,
            exc_info=True,
        )
