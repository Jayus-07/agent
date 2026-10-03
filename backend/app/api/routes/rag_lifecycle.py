"""知识生命周期 API（2026-10-03，演进分析 C1/C2）。

端点（挂在 /rag 前缀下，经 rag.py include）：
  POST /rag/knowledge/{doc_id}/lifecycle        状态迁移（deprecate/restore/send-to-review）
  POST /rag/knowledge/{doc_id}/expire-at        设置/清除有效期（expire_at，按日粒度）
  GET  /rag/knowledge/{doc_id}/lifecycle/events 流转审计查询

fail-closed：状态机裁决在 backend/rag/indexing/lifecycle.py；本路由只做
身份/归属校验与转发。审核通过（pending_review→active）不在本路由——必须走
POST /rag/pending/{doc_id}/approve（代次发布语义）。
"""

from datetime import date as _date

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from backend.app.api.deps import require_rag_editor, require_rag_user
from backend.app.api.routes._rag_shared import (
    _extract_source,
    _get_registry,
    _safe_log_op,
)
from backend.rag.indexing.lifecycle import (
    KnowledgeLifecycleService,
    LifecycleAuditLog,
    default_audit_conn,
)
from backend.shared.logger import logger

router = APIRouter(dependencies=[Depends(require_rag_user)])

_service: KnowledgeLifecycleService | None = None


def _get_lifecycle_service() -> KnowledgeLifecycleService:
    global _service
    if _service is None:
        _service = KnowledgeLifecycleService(
            _get_registry(),
            LifecycleAuditLog(default_audit_conn),
        )
    return _service


def _require_authz(request: Request):
    """与 rag_documents 同口径的路由级授权（fail-closed，403 不降级）。"""
    from backend.app.api.identity import require_principal
    from backend.rag.authz import RagAuthorization, RagAuthorizationError

    principal = require_principal(request)
    try:
        return RagAuthorization.build(principal), principal
    except RagAuthorizationError as e:
        logger.error(f"[RAG] 授权失败: {e}")
        raise HTTPException(status_code=403, detail="授权服务暂不可用，已拒绝操作")


def _deny_manage(reason: str):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=403, content={"ok": False, "error": reason})


def _actor_of(principal) -> str:
    return f"{principal.subject_type}:{principal.user_id or 'anonymous'}"


def _check_manageable(request: Request, doc_id: str):
    """归属校验 + 返回 (doc, actor, source)；文档不存在/越权统一在此拦截。"""
    authz, principal = _require_authz(request)
    doc = _get_registry().get_by_doc_id(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    ok, reason = authz.can_manage_row(doc)
    if not ok:
        return None, _deny_manage(reason), None
    return doc, None, (_actor_of(principal), _extract_source(request))


class LifecycleTransitionRequest(BaseModel):
    to_status: str
    reason: str = ""


class ExpireAtRequest(BaseModel):
    # ISO 日期（YYYY-MM-DD）；null/空串 = 清除有效期
    expire_at: str | None = None
    reason: str = ""


@router.post("/knowledge/{doc_id}/lifecycle", dependencies=[Depends(require_rag_editor)])
async def transition_lifecycle(doc_id: str, body: LifecycleTransitionRequest, request: Request):
    """知识生命周期状态迁移。

    合法迁移：active→deprecated（下线）｜deprecated→active（恢复）｜
    active→pending_review（改版回审）。其余（含一切 →active 直跳、
    →deleted 裸切）一律 422 拒绝并说明原因。
    """
    doc, deny, ctx = _check_manageable(request, doc_id)
    if deny is not None:
        return deny
    actor, source = ctx
    try:
        result = _get_lifecycle_service().transition(
            doc_id, body.to_status, actor=actor, reason=body.reason,
        )
        if result.get("ok"):
            _safe_log_op(
                doc_id, doc.get("file_name", ""), f"lifecycle:{body.to_status}", source,
                detail={"from": doc.get("status"), "to": result.get("to")},
            )
            _invalidate_retrieval_cache()
        return result
    except Exception as e:
        logger.error(f"[RAG] lifecycle 迁移失败 {doc_id}: {e}", exc_info=True)
        return {"ok": False, "error": str(e)}


@router.post("/knowledge/{doc_id}/expire-at", dependencies=[Depends(require_rag_editor)])
async def set_expire_at(doc_id: str, body: ExpireAtRequest, request: Request):
    """设置/清除时效知识的有效期（expire_at，仅 active 文档）。

    到期知识的检索过滤在 rag 检索链内实时生效（hybrid._pending_review_doc_ids
    并入 list_expired 结果，60s 进程内缓存）。
    """
    doc, deny, ctx = _check_manageable(request, doc_id)
    if deny is not None:
        return deny
    actor, source = ctx
    expire_at = (body.expire_at or "").strip()
    if expire_at:
        try:
            _date.fromisoformat(expire_at)
        except ValueError:
            return {"ok": False, "error": f"expire_at 需为 ISO 日期（YYYY-MM-DD），收到: {expire_at}"}
    try:
        result = _get_lifecycle_service().set_expire_at(
            doc_id, expire_at or None, actor=actor, reason=body.reason,
        )
        if result.get("ok"):
            _safe_log_op(
                doc_id, doc.get("file_name", ""), "lifecycle:expire_at_set", source,
                detail={"from": doc.get("status"), "expire_at": expire_at},
            )
            _invalidate_retrieval_cache()
        return result
    except Exception as e:
        logger.error(f"[RAG] expire-at 设置失败 {doc_id}: {e}", exc_info=True)
        return {"ok": False, "error": str(e)}


@router.get("/knowledge/{doc_id}/lifecycle/events")
async def list_lifecycle_events(doc_id: str, request: Request):
    """流转审计查询（含 doc 不存在与越权的统一不可见语义）。"""
    authz, _principal = _require_authz(request)
    doc = _get_registry().get_by_doc_id(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    ok, reason = authz.can_read_row(doc)
    if not ok:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=403, content={"ok": False, "error": reason})
    return {"ok": True, "doc_id": doc_id, "events": _get_lifecycle_service().list_events(doc_id)}


def _invalidate_retrieval_cache() -> None:
    """状态/有效期变更后清本进程检索过滤缓存（rag-service 进程靠 60s TTL 兜底）。"""
    try:
        from backend.rag.retrieval.hybrid import invalidate_pending_review_cache
        invalidate_pending_review_cache()
    except Exception:
        logger.debug("[RAG] 检索过滤缓存清理失败", exc_info=True)
