"""approvals.py — 写操作工具审批管理 API

供管理员（或运维后台）查看/批准/驳回审批单。
鉴权: 走全局 api_key_middleware（X-API-Key），与其他管理路由一致。
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.security.tool_approval import decide_request, is_degraded, list_requests

router = APIRouter(prefix="/approvals", tags=["工具审批"])


class ApprovalDecision(BaseModel):
    reviewer: str = Field("admin", description="审批人标识")
    reason: str = Field("", description="批准/驳回理由")


@router.get("")
async def list_approvals(status: str = "", limit: int = 50):
    """审批单列表。status=pending/approved/rejected/executed，空=全部。"""
    limit = max(1, min(limit, 200))
    return {
        "degraded": is_degraded(),
        "items": list_requests(status=status or None, limit=limit),
    }


@router.post("/{request_id}/approve")
async def approve(request_id: str, body: ApprovalDecision):
    """批准审批单。批准后 TTL 内重试相同操作即可执行。"""
    rec = decide_request(request_id, approve=True,
                         reviewer=body.reviewer, reason=body.reason)
    if rec is None:
        raise HTTPException(404, "审批单不存在或已处理")
    return {"ok": True, "request": rec}


@router.post("/{request_id}/reject")
async def reject(request_id: str, body: ApprovalDecision):
    """驳回审批单。被驳回的指纹再次触发不会重复建单前需先处理 pending。"""
    rec = decide_request(request_id, approve=False,
                         reviewer=body.reviewer, reason=body.reason)
    if rec is None:
        raise HTTPException(404, "审批单不存在或已处理")
    return {"ok": True, "request": rec}
