"""app/api/routes/plan_revisions.py — 运行中任务的多轮计划修订 API（决策 #29）。

多用户隔离照 tasks.py 同款：thread_id → tasks 归属校验（user_id/tenant_id），
非本人/他租户一律 404（不泄露存在性）。

v1 API-first：不接前端（PR body 声明），前端接入留下一步。

语义边界（与 plan_revision.py 服务层一致，对外显式声明）：
  - 修订只影响 pending/failed 步骤；success 步骤结果只能保留复用或作废引用，
    **已完成的外部写入不会被撤销或伪装回滚**；
  - apply 仅在任务静止（PAUSED/WAITING_USER/FAILED）时可用；
  - 并发版本检查：base_plan_version 不匹配当前已应用版本 → 409。

网关映射：APISIX 剥 /api 前缀 → 对外 /api/plan-revisions/*，后端 /plan-revisions/*。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from backend.app.api.identity import resolve_identity
from backend.orchestration.discovery.plan_revision import (
    PlanRevisionError,
    apply_revision,
    draft_revision,
    list_revisions,
)
from backend.shared.logger import logger

router = APIRouter(prefix="/plan-revisions", tags=["计划修订"])


class PlanRevisionCreateRequest(BaseModel):
    thread_id: str = Field(..., min_length=1, description="任务 checkpoint 线程（task-<task_id>）")
    instruction: str = Field(..., min_length=1, description="自然语言修改指令")
    base_plan_version: int = Field(..., ge=0, description="当前已应用计划版本（并发检查基准）")


class PlanRevisionApplyRequest(BaseModel):
    base_plan_version: int = Field(..., ge=0, description="并发检查基准（须与修订记录一致）")


def _identity(request: Request):
    """统一身份解析；未认证直接 401（修订必须绑定用户）。"""
    ident = resolve_identity(request, None)
    if not ident.user_id:
        raise HTTPException(status_code=401,
                            detail="未认证：计划修订接口要求用户身份（网关身份头）")
    return ident


def _tenant(request: Request, identity) -> str:
    """租户取可信 header 身份（网关模式）；legacy 无租户时缺省 default。"""
    if identity.source == "header":
        return identity.tenant_id or "default"
    return request.headers.get("X-Tenant-Id", "") or "default"


def _revision_error(exc: PlanRevisionError) -> HTTPException:
    return HTTPException(status_code=exc.http_status, detail=exc.message)


# ═══════════════════════════════════════════════════
# POST /plan-revisions — 起草修订（LLM + 服务端强制校验）
# ═══════════════════════════════════════════════════

@router.post("")
async def create_plan_revision(body: PlanRevisionCreateRequest, request: Request):
    ident = _identity(request)
    tenant_id = _tenant(request, ident)
    try:
        draft = draft_revision(
            body.thread_id, tenant_id, ident.user_id,
            body.instruction, body.base_plan_version)
    except PlanRevisionError as exc:
        raise _revision_error(exc)
    except Exception as exc:  # noqa: BLE001 — 未预期异常按内部错误出协议封套语义
        logger.error("[PlanRevisionsAPI] draft failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="修订起草失败，请稍后重试")
    return draft


# ═══════════════════════════════════════════════════
# GET /plan-revisions?thread_id= — 修订历史
# ═══════════════════════════════════════════════════

@router.get("")
async def list_plan_revisions(request: Request,
                              thread_id: str = Query(..., min_length=1),
                              limit: int = Query(50, ge=1, le=100)):
    ident = _identity(request)
    tenant_id = _tenant(request, ident)
    revisions = list_revisions(thread_id, tenant_id, ident.user_id, limit=limit)
    return {"revisions": revisions, "count": len(revisions)}


# ═══════════════════════════════════════════════════
# POST /plan-revisions/{revision_id}/apply — 应用修订并重新入图
# ═══════════════════════════════════════════════════

@router.post("/{revision_id}/apply")
async def apply_plan_revision(revision_id: str, body: PlanRevisionApplyRequest,
                              request: Request):
    ident = _identity(request)
    tenant_id = _tenant(request, ident)
    try:
        result = apply_revision(
            revision_id, tenant_id, ident.user_id, body.base_plan_version)
    except PlanRevisionError as exc:
        raise _revision_error(exc)
    except Exception as exc:  # noqa: BLE001
        logger.error("[PlanRevisionsAPI] apply failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="修订应用失败，请稍后重试")
    return result
