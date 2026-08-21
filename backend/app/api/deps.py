"""依赖注入 — Agent 单例（惰性初始化，PR-2.x 工厂下沉到源模块）。

工厂函数已迁移到源模块（sql/sql_agent.py + rag/pipeline.py），
本模块封装惰性 import + 状态查询，避免启动时强制加载所有依赖。
"""
import threading

from backend.shared.logger import logger

_lock = threading.Lock()
_multi_agent = None


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


def _kick_pipeline_init() -> None:
    """在后台线程触发 pipeline 初始化（幂等：已有初始化在进行则不重复启动）。

    用于 not_started 状态下的首次访问兼容：保证即使启动预热未覆盖，
    初始化也会被触发，且绝不阻塞当前（事件循环）线程。
    """
    from backend.rag import pipeline as _p
    if _p._pipeline_singleton is not None or _p._pipeline_initializing:
        return
    def _bg_init():
        try:
            _p.get_rag_pipeline()
        except Exception as e:
            logger.warning(f"[deps] 后台 pipeline 初始化失败: {e}")
    threading.Thread(target=_bg_init, name="rag-pipeline-init", daemon=True).start()


def get_rag_status() -> dict:
    """返回 RAG 模块状态（供 health check 使用）。

    【非阻塞】：只读状态标志，不等 _pipeline_lock —— RAGPipeline 构造含
    全量同步（数十分钟），若在事件循环线程同步等待会冻结整个 API。
    """
    from backend.rag.pipeline import get_rag_pipeline_state
    state = get_rag_pipeline_state()
    if state["state"] == "ready":
        return {"ready": True, "status": "ready"}
    if state["state"] == "error":
        return {"ready": False, "status": "error", "error": state["error"]}
    if state["state"] == "not_started":
        _kick_pipeline_init()
    return {
        "ready": False,
        "status": "initializing",
        "message": state.get("message", "模型加载中，请稍后重试"),
    }


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


def warmup_multi_agent() -> bool:
    """启动期预热 MultiAgent 运行时：构造 LangGraph、tool_registry、memory_manager。

    目的：首次 chat 请求不再触发 5-15s 的图编译与依赖链加载。
    返回 True 表示成功，False 表示失败（启动期失败不阻塞服务运行，首请求时会再尝试）。
    """
    try:
        get_multi_agent()
        return True
    except Exception as e:
        logger.warning(f"[Warmup] MultiAgent 预热失败（首请求会重试）: {e}")
        return False
