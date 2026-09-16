"""依赖注入 — Agent 单例（惰性初始化，PR-2.x 工厂下沉到源模块）。

工厂函数已迁移到源模块（sql/sql_agent.py + rag/pipeline.py），
本模块封装惰性 import + 状态查询，避免启动时强制加载所有依赖。
"""
import hmac
import os
import threading
from dataclasses import dataclass

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


# ── 运营角色解析（prompts 等治理类接口的唯一入口）──────────────
#
# 2026-09-15 S0-2：此前的实现是**每个端点各自声明**
#   `x_operator_role: str = Header(default="viewer"|"editor")`
# 即角色完全由**客户端自设请求头**决定，且未被任何网关剥离 —— 任意客户端带
# `X-Operator-Role: admin` 即可发布/回滚高风险 Prompt。同时写端点默认值为
# `editor`、`/seed` 为 `admin`，意味着**剥头后无凭据调用仍然放行**。
#
# 本次收敛为单一入口，要点：
#   ① 角色只能由本函数产出，**任何地方不得再读 X-Operator-Role**；
#   ② 双通道（2026-09-16 兑现演进点③）：
#      a) JWT 用户：网关 gateway-auth 验签后注入 X-User-Roles（roles claim，
#         客户端伪造会被剥离），管理端浏览器链路由此打通；
#      b) 服务凭据：X-Internal-Token（机器凭据，映射 admin），服务间调用不变。
#
# 角色枚举与 `backend/app/api/routes/prompts.py::_check_permission` 的权限矩阵对应：
#   viewer / editor / admin


@dataclass(frozen=True)
class OperatorIdentity:
    """运营操作者身份：`role` 决定能做什么，`actor` 进审计留痕。

    - role：viewer / editor / admin（权限矩阵见 prompts.py::_check_permission）
    - actor：审计用操作者标识。服务凭据调用固定为 `service:internal-token`；
      JWT 用户为 `user:<X-User-Id>`——该头由网关验签后注入（enforce 下不可
      伪造，信任边界=网络边界：app:8000 不对外暴露），与早期「直连可伪造」
      的前提已不同。
    - kind（2026-09-16 方案 A）：user（JWT 通道，网关验签注入）|
      service（API Key / 内部令牌凭据）。**JWT 是唯一用户身份来源**——
      敏感端点治理只认 kind，不再逐处解析 actor 字符串前缀。
    """

    role: str
    actor: str

    @property
    def kind(self) -> str:
        return "user" if self.actor.startswith("user:") else "service"


_KNOWN_ROLES = ("viewer", "editor", "admin")
_ROLE_RANK = {"viewer": 0, "editor": 1, "admin": 2}


def _highest_known_role(roles: tuple[str, ...]) -> str | None:
    """取 roles 中已知的最高角色；全未知/为空返回 None（多角色按高权限生效）。"""
    known = [r for r in roles if r in _ROLE_RANK]
    if not known:
        return None
    return max(known, key=lambda r: _ROLE_RANK[r])


async def resolve_operator_role(request: Request) -> OperatorIdentity:
    """运营角色的**唯一解析入口**（S0-2 起；2026-09-16 双通道）。

    通道 a（JWT 用户）：网关验签后注入的 X-User-Id + X-User-Roles（roles
    claim，逗号分隔）。多角色取最高；全部未知视为无角色，落到通道 b。
    通道 b（服务凭据）：X-Internal-Token → role = admin。凭据缺失/错误 → 401；
    令牌未配置且未显式豁免 → 503（由 require_internal_token 决定）。

    为什么服务凭据映射为 admin：`_check_permission` 的矩阵里 `high` 风险的
    publish/rollback 仅 admin 可做。若映射为 editor，则高风险 Prompt 将**无人可发布**
    （服务调用方没有用户上下文），功能性上等于锁死；而服务凭据本身是不外发的
    服务端机密，映射为 admin 既恢复合法运营能力、又彻底关闭不可信通道。
    """
    from backend.app.api.identity import resolve_identity  # 局部导入避免循环依赖

    ident = resolve_identity(request)
    if ident.authenticated:
        role = _highest_known_role(ident.roles)
        if role is not None:
            return OperatorIdentity(role=role, actor=f"user:{ident.user_id}")
    await require_internal_token(request)
    return OperatorIdentity(role="admin", actor="service:internal-token")


# ── 敏感端点统一守卫（2026-09-16 方案 A）──────────────────────
#
# 规则下沉：JWT 是唯一用户身份来源，API Key / 内部令牌一律映射 service
# 身份，敏感端点只认 kind == "user"。此前该规则散落在各路由自带的守卫里
# （如 observability.require_admin_operator 以 actor 字符串前缀判定），
# 本依赖是收敛后的唯一实现，新敏感端点一律挂它。
#
# 灰度开关 SENSITIVE_API_GUARD_MODE（与 observability 既有开关同源）：
#   audit   —— 规则生效但仅记日志不拦截（灰度观察 1-2 周）
#   enforce —— 拦截 service 身份请求，403
# 切换方式：.env 设 SENSITIVE_API_GUARD_MODE=enforce 后重启 app 容器。


async def require_user_actor(request: Request):
    """敏感端点统一依赖：仅允许 actor.kind == "user"（JWT 通道）通过。

    不限角色——角色门槛（如 admin）由调用方在本依赖之后叠加判断
    （operator.role）。audit 模式放行 service 请求但记 warning，
    enforce 模式 403（错误体与 observability 既有守卫同构）。
    """
    ident = await resolve_operator_role(request)
    if ident.kind == "user":
        return ident
    mode = os.getenv("SENSITIVE_API_GUARD_MODE", "enforce").strip().lower()
    if mode == "audit":
        logger.warning(
            "[SensitiveGuard] audit 放行 service 身份访问敏感端点: "
            f"actor={ident.actor} role={ident.role}"
        )
        return ident
    raise HTTPException(
        status_code=403,
        detail={
            "error": "Forbidden",
            "message": "敏感端点仅限 JWT 用户身份访问；"
                       "API Key / 内部令牌属服务间凭据，不映射用户身份",
        },
    )


async def require_admin_user(request: Request):
    """敏感端点统一依赖（管理员档）：kind == "user" 且 role == admin。

    语义与 observability.require_admin_operator 等价，作为收敛后的
    统一实现提供；observability 迁移本依赖前两者并存（行为一致）。
    kind 门槛由 require_user_actor 处理（audit 下会先记一次 service
    放行日志），这里只叠加角色判定。
    """
    ident = await require_user_actor(request)
    if ident.role == "admin":
        return ident
    mode = os.getenv("SENSITIVE_API_GUARD_MODE", "enforce").strip().lower()
    if mode == "audit":
        logger.warning(
            "[SensitiveGuard] audit 放行非管理员访问敏感端点: "
            f"actor={ident.actor} role={ident.role}"
        )
        return ident
    raise HTTPException(
        status_code=403,
        detail={
            "error": "Forbidden",
            "message": "该端点仅限管理员（role=admin）访问；"
                       "服务级凭据通道不可访问",
        },
    )
