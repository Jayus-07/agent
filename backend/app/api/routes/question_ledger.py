"""question_ledger 路由 — 线上问题台账读/处置（2026-10-08 #13）

管理端 data-explorer「问题收集」tab 的数据入口：分页列表（按域/状态筛）、
accept/dismiss 状态机、批量转候选评测集（复用 evaluation_datasets 候选
管道，approve 后进不可变版本目录）。管理员闸（require_admin_user），
与评测治理端点同口径。
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.app.api.deps import OperatorIdentity, require_admin_user
from backend.observability import question_ledger as ledger

router = APIRouter(prefix="/question-ledger", tags=["question-ledger"])


@router.get("")
async def list_questions(
    domain: str = Query("", description="按域筛选（travel/sql/planner/cs/rag/ai_assistant）"),
    status: str = Query("", description="按状态筛选（pending/accepted/dismissed）"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _operator: OperatorIdentity = Depends(require_admin_user),
):
    """分页列表 + 近 24h 各域收集量（tab 头计数一次带回）。"""
    data = ledger.list_questions(
        domain=domain, status=status, page=page, page_size=page_size)
    data["stats_by_domain"] = ledger._stats_by_domain(24)
    return data


class LedgerStatusBody(BaseModel):
    status: str = Field(description="目标状态：accepted / dismissed")


@router.post("/{question_id}/status")
async def update_status(
    question_id: int,
    body: LedgerStatusBody,
    _operator: OperatorIdentity = Depends(require_admin_user),
):
    """pending → accepted / dismissed（幂等；accepted 通常伴随转候选）。"""
    if not ledger.set_status(question_id, body.status):
        raise HTTPException(status_code=400, detail="非法状态或条目不存在")
    return {"ok": True, "id": question_id, "status": body.status}


class ToCandidatesBody(BaseModel):
    ids: list[int] = Field(min_length=1, description="台账 id 列表")


@router.post("/to-candidates")
async def to_candidates(
    body: ToCandidatesBody,
    _operator: OperatorIdentity = Depends(require_admin_user),
):
    """批量转候选评测集（source_type=online_ledger，redacted=True）。"""
    reviewer = getattr(_operator, "actor", "") or "evaluation-platform"
    return ledger.to_candidates(body.ids, reviewer=reviewer)
