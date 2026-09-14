"""SQL 路由 — 自然语言 → SQL 安全查询（6层硬校验）"""
import asyncio
from typing import Optional

from fastapi import APIRouter, Request

from backend.app.api.schemas import SQLAskRequest, SQLQueryResponse, ErrorResponse
from backend.app.api.deps import get_sql_agent
from backend.config import USER_ID_HEADER
from backend.shared.logger import logger

router = APIRouter(prefix="/sql", tags=["SQL查询"])


def _resolve_user_id(request: Request) -> Optional[int]:
    """从服务端可信来源推导当前用户 ID（P1-11；P3 起统一走 identity.py）。

    安全语义：
      - 请求体中的 current_user_id 已废弃（客户端可伪造，一律忽略）
      - header/strict 模式：网关身份头即权威（IDENTITY_SOURCE）
      - legacy 模式：仅当 TRUST_USER_HEADER=true（表明反向代理/网关已完成
        认证并注入 USER_ID_HEADER 头）时，从头中读取用户身份
      - 无身份时返回 None —— 行级安全严格模式下，
        受保护表的查询会被拒绝（fail-closed），而非放行
    """
    from backend.app.api.identity import resolve_identity

    ident = resolve_identity(request)  # 不传 body_user_id：SQL 侧从未采信请求体
    if not ident.authenticated:
        if ident.source == "guest" and ident.auth_type == "guest":
            # legacy 关闭信任头 → 原样静默返回 None；header/strict 缺头 → 点名
            from backend.config.auth import identity_source
            if identity_source() in ("header", "strict"):
                logger.warning(
                    f"[SQL] IDENTITY_SOURCE={identity_source()} 但请求缺少 {USER_ID_HEADER} 头，"
                    "用户上下文为空（受保护表将被拒绝）"
                )
        return None

    raw = ident.user_id
    try:
        return int(raw)
    except ValueError:
        logger.warning(f"[SQL] {USER_ID_HEADER} 头不是合法整数: {raw!r}，忽略")
        return None


@router.post("", responses={500: {"model": ErrorResponse}})
async def sql_ask(req: SQLAskRequest, request: Request):
    """自然语言转 SQL 查询，自动行级安全注入 + 敏感列拦截 + 脱敏

    用户身份（行级安全参数）由服务端从可信头推导，客户端请求体中的
    current_user_id 字段被忽略（防伪造）。
    """
    if req.current_user_id is not None:
        logger.warning(
            f"[SQL] 客户端在请求体中传递了 current_user_id={req.current_user_id}，"
            "该字段已废弃（可伪造），已忽略；用户身份由服务端可信头推导"
        )

    agent = get_sql_agent()
    user_id = _resolve_user_id(request)
    answer = await asyncio.to_thread(agent.ask, req.question, user_id)
    return {"answer": answer}


@router.post("/query", response_model=SQLQueryResponse,
             responses={500: {"model": ErrorResponse}})
async def sql_query(req: SQLAskRequest, request: Request) -> SQLQueryResponse:
    """结构化查询端点：返回 status / 行列数据 / 耗时 / 错误分类。

    与 POST /sql 的区别：后者只返回 Markdown 字符串；本端点返回
    结构化 JSON，供前端表格渲染和程序化调用。安全语义一致
    （行级安全、敏感列拦截、脱敏均由 SQLAgent 内部完成）。
    """
    agent = get_sql_agent()
    user_id = _resolve_user_id(request)
    result = await asyncio.to_thread(agent.ask_struct, req.question, user_id)
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
