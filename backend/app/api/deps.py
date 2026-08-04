"""依赖注入 — Agent 单例（惰性初始化，PR-2.x 工厂下沉到源模块）。

工厂函数已迁移到源模块（sql/sql_agent.py + rag/pipeline.py），
本模块封装惰性 import + 状态查询，避免启动时强制加载所有依赖。
"""
import threading

from backend.shared.logger import logger

_lock = threading.Lock()
_multi_agent = None
_rag_init_error = None
_rag_pipeline_ref = None


def get_multi_agent():
    global _multi_agent
    if _multi_agent is None:
        with _lock:
            if _multi_agent is None:
                from backend.orchestration.graph import MultiAgentSystem
                _multi_agent = MultiAgentSystem()
    return _multi_agent


def get_sql_agent():
    """惰性导入 SQLAgent 单例（避免启动时加载 sqlglot 等重依赖）。"""
    from backend.sql.sql_agent import get_sql_agent as _get
    return _get()


def get_rag_pipeline():
    """惰性导入 RAGPipeline 单例（避免启动时加载 HuggingFace 模型）。"""
    from backend.rag.pipeline import get_rag_pipeline as _get
    return _get()


def _ensure_pipeline_ref():
    global _rag_pipeline_ref, _rag_init_error
    if _rag_pipeline_ref is not None:
        return
    try:
        _rag_pipeline_ref = get_rag_pipeline()
        _rag_init_error = None
    except Exception as e:
        _rag_init_error = str(e)


def get_rag_status() -> dict:
    """返回 RAG 模块状态（供 health check 使用）"""
    _ensure_pipeline_ref()
    if _rag_pipeline_ref is not None:
        return {"ready": True, "status": "ready"}
    if _rag_init_error is not None:
        return {"ready": False, "status": "error", "error": _rag_init_error}
    return {"ready": False, "status": "initializing", "message": "模型加载中，请稍后重试"}


def require_rag_ready():
    """检查 RAG 是否就绪，未就绪则抛出 HTTPException 503。"""
    status = get_rag_status()
    if not status["ready"]:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=503,
            detail={
                "code": "SERVICE_NOT_READY",
                "status": status["status"],
                "message": status.get("message") or status.get("error", "未知错误"),
                "retry_after": 15,
            },
        )
