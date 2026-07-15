"""observability/health.py — 健康检查端点

提供 /health 用于负载均衡器/监控系统探测服务存活。
"""
from fastapi import APIRouter

router = APIRouter()


@router.get("/health", tags=["系统"])
async def health():
    """健康检查：返回服务状态 + RAG 模块状态"""
    from backend.app.api.deps import get_rag_status
    return {
        "status": "ok",
        "rag": get_rag_status(),
    }