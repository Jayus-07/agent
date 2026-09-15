"""依赖注入 — Agent 单例（惰性初始化，PR-2.x 工厂下沉到源模块）。

工厂函数已迁移到源模块（sql/sql_agent.py + rag/pipeline.py），
本模块封装惰性 import + 状态查询，避免启动时强制加载所有依赖。
"""
import hmac
import threading

from fastapi import HTTPException, Request

from backend.config import ALLOW_UNAUTHENTICATED, ENVIRONMENT
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
    from backend.config.rag import RAG_MODE
    if RAG_MODE == "remote":
        # 远端模式：本地无索引可预热，rag-service 自己负责启动初始化
        return
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
    if state["state"] == "remote":
        # 远端模式：rag-service 就绪与否由其 /readyz 决定，此处只透传模式信息
        return {
            "ready": True,
            "status": "remote",
            "endpoint": state["endpoint"],
            "message": "RAG 运行于远端服务模式，实际就绪状态见 rag-service /readyz",
        }
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


# ── 内部服务凭据（X-Internal-Token）────────────────────────────
#
# 2026-09-15 S0-4：由 fail-open 改为 fail-closed，对齐 middleware/auth.py 的既有
# 约定（显式 ALLOW_UNAUTHENTICATED 开关 + 生产环境防御性拒绝）。
#
# 原实现位于 routes/internal_ai.py，在 AI_INTERNAL_TOKEN 未配置时直接 return 放行
# —— 等于「服务间网关无凭据即开放」。现上提到本模块，供 prompts 等路由复用，
# 避免 routes → routes 的横向导入。
#
# 实测背景（2026-09-15）：容器内 AI_INTERNAL_TOKEN 为空、AI_TOOLS_ENABLED=true，
# 即该网关处于「已启用 + 无凭据」状态；同时 prompts 的写权限也准备收敛到本依赖，
# 因此这条通道必须与 API Key 中间件同为 fail-closed，否则等于换个门继续开。

_INTERNAL_TOKEN_EXEMPT_WARNED = False


async def require_internal_token(request: Request) -> None:
    """路由级鉴权依赖：校验 X-Internal-Token（常量时间比较，防时序侧信道）。

    fail-closed 语义：
    - 未配置 AI_INTERNAL_TOKEN：
        · ENVIRONMENT=production → 503 拒绝（即使开了豁免开关，defense-in-depth）
        · ALLOW_UNAUTHENTICATED=true → 放行（仅本地开发显式豁免，只告警一次）
        · 否则 → 503 拒绝（不再静默跳过）
    - 已配置：缺头或不匹配 → 401

    失败响应沿用本仓既有错误壳（`{"error": ..., "detail": ...}`），与
    middleware/auth.py 的 `AuthNotConfigured` / `Unauthorized` 保持同构。
    """
    global _INTERNAL_TOKEN_EXEMPT_WARNED

    from backend.config.messaging import AI_INTERNAL_TOKEN

    if not AI_INTERNAL_TOKEN:
        if ENVIRONMENT == "production":
            logger.error(
                "[InternalToken] 生产环境未配置 AI_INTERNAL_TOKEN → 拒绝请求"
                "（服务间网关不得无凭据开放；defense-in-depth）"
            )
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "InternalTokenNotConfigured",
                    "message": "生产环境必须配置 AI_INTERNAL_TOKEN",
                },
            )
        if ALLOW_UNAUTHENTICATED:
            if not _INTERNAL_TOKEN_EXEMPT_WARNED:
                logger.warning(
                    "[InternalToken] ALLOW_UNAUTHENTICATED=true：内部令牌校验已显式豁免"
                    "（仅限本地开发调试，生产禁止开启）"
                )
                _INTERNAL_TOKEN_EXEMPT_WARNED = True
            return
        raise HTTPException(
            status_code=503,
            detail={
                "error": "InternalTokenNotConfigured",
                "message": (
                    "服务端未配置 AI_INTERNAL_TOKEN，已拒绝请求（fail-closed）。"
                    "请设置 AI_INTERNAL_TOKEN；仅本地开发可显式设置 "
                    "ALLOW_UNAUTHENTICATED=true"
                ),
            },
        )

    provided = request.headers.get("X-Internal-Token", "")
    if not hmac.compare_digest(
        AI_INTERNAL_TOKEN.encode("utf-8"), provided.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="invalid internal token")
