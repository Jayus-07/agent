"""observability/health.py — 健康检查端点

提供 /health 用于负载均衡器/监控系统探测服务存活。
"""
from fastapi import APIRouter

router = APIRouter()


@router.get("/health", tags=["系统"])
async def health():
    """健康检查：返回服务状态 + RAG 模块状态 + 会话上下文 backend 状态"""
    from backend.app.api.deps import get_rag_status
    return {
        "status": "ok",
        "rag": get_rag_status(),
        # STOP G5：ConversationContext backend 观测（healthy/degraded/
        # disabled + backend 名称）。软失败：观测缺失不影响存活判定。
        "conversation_context": _context_backend_status(),
    }


def _context_backend_status() -> dict:
    try:
        from backend.orchestration.context.context_repository import (
            get_conversation_context_repository,
        )

        return get_conversation_context_repository().status
    except Exception:  # noqa: BLE001
        return {"backend": "unknown", "status": "unknown"}