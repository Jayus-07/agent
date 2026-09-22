"""SQL 路由 — 自然语言 → SQL 安全查询（6层硬校验 + SQLPolicyGuard）

STOP C 接线（2026-09-23）：身份链完全复用统一授权体系——

    APISIX JWT → 可信身份头 → deps.get_principal → AuthorizationContext
        → SQLPolicyContext(source_channel="http") → SQLAgent 策略链
        → SQLPolicyGuard（权限门/表域/scope 注入）→ readonly executor

路由层只做两件事，不自行判断角色/作用域：
  1. kill switch（SQL_AGENT_ENABLED=false → 503 服务不可用语义）
  2. sql.read 权限预检（authz.has_permission → 403，快速失败不进 LLM；
     agent 内 Guard 仍强制检查，纵深防御）
"""
import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, Request

from backend.app.api.deps import get_sql_agent, get_principal
from backend.app.api.schemas import SQLAskRequest, SQLQueryResponse, ErrorResponse
from backend.shared.logger import logger
from backend.security.principal import Principal
from backend.sql.policy import SQLPolicyContext

router = APIRouter(prefix="/sql", tags=["SQL查询"])


def _resolve_user_id(request: Request) -> Optional[int]:
    """[legacy] P1-11 时代的头身份推导，STOP C 起路由改走 SQLPolicyContext。

    保留供 tests/test_sql_user_context.py 的身份收敛语义回归；
    生产路径不再调用——身份经 deps.get_principal → AuthorizationContext。
    """
    from backend.app.api.identity import resolve_identity

    ident = resolve_identity(request)  # 不传 body_user_id：SQL 侧从未采信请求体
    if not ident.authenticated:
        return None

    raw = ident.user_id
    try:
        return int(raw)
    except ValueError:
        return None


def _build_policy(principal: Principal) -> SQLPolicyContext:
    """HTTP 通道 SQLPolicyContext 装配（复用 AuthorizationContext，不重推导）。

    Principal → AuthorizationContext 的构建在 deps.get_principal 链上
    已完成；此处把 HTTP 通道显式标注为 source_channel="http"（审计归因）。
    """
    from backend.security.authorization import build_authorization_context

    authz = build_authorization_context(principal)
    return SQLPolicyContext(
        principal=principal, authz=authz, source_channel="http")


def _require_sql_enabled() -> None:
    """kill switch（规格 §十四）：关闭时统一 503 服务不可用语义。"""
    from backend.config import SQL_AGENT_ENABLED

    if not SQL_AGENT_ENABLED:
        from fastapi import HTTPException

        raise HTTPException(status_code=503, detail="SQL 查询服务暂不可用，请稍后重试。")


def _require_sql_read(policy: SQLPolicyContext) -> None:
    """sql.read 权限预检（快速失败，不进 LLM 生成链）。

    消费 AuthorizationContext.has_permission，不读角色字符串；
    agent 内 SQLPolicyGuard 会再次强制检查（纵深，规格 §三）。
    """
    from fastapi import HTTPException

    if not policy.authz.has_permission("sql.read"):
        raise HTTPException(status_code=403, detail="当前查询超出你的数据访问范围。")


@router.post("", responses={500: {"model": ErrorResponse}})
async def sql_ask(
    req: SQLAskRequest,
    request: Request,
    principal: Principal = Depends(get_principal),
):
    """自然语言转 SQL 查询：敏感列拦截 + 数据范围注入 + 脱敏 + 审计。

    用户身份/权限/数据范围全部来自服务端可信链（网关验签头 → Principal
    → AuthorizationContext），请求体中的 current_user_id 已废弃（可伪造，
    一律忽略）。
    """
    _require_sql_enabled()
    policy = _build_policy(principal)
    _require_sql_read(policy)

    if req.current_user_id is not None:
        logger.warning(
            f"[SQL] 客户端在请求体中传递了 current_user_id={req.current_user_id}，"
            "该字段已废弃（可伪造），已忽略；用户身份由服务端可信链推导")

    agent = get_sql_agent()
    answer = await asyncio.to_thread(agent.ask_struct, req.question, policy=policy)
    return {"answer": answer.to_markdown()}


@router.post("/query", response_model=SQLQueryResponse,
             responses={500: {"model": ErrorResponse}})
async def sql_query(
    req: SQLAskRequest,
    request: Request,
    principal: Principal = Depends(get_principal),
) -> SQLQueryResponse:
    """结构化查询端点：返回 status / 行列数据 / 耗时 / 错误分类。

    安全语义与 POST /sql 一致（权限预检 + SQLPolicyGuard + 审计）。
    """
    _require_sql_enabled()
    policy = _build_policy(principal)
    _require_sql_read(policy)

    agent = get_sql_agent()
    result = await asyncio.to_thread(agent.ask_struct, req.question, policy=policy)
    return SQLQueryResponse(
        status=result.status,
        answer=result.to_markdown(),
        columns=result.columns or [],
        rows=result.rows or [],
        row_count=result.row_count,
        elapsed_sec=result.elapsed_sec,
        error=result.error,
        error_type=result.error_type,
    )
