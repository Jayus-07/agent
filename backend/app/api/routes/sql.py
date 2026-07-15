"""SQL 路由 — 自然语言 → SQL 安全查询（6层硬校验）"""
import asyncio
from fastapi import APIRouter
from backend.app.api.schemas import SQLAskRequest, ErrorResponse
from backend.app.api.deps import get_sql_agent

router = APIRouter(prefix="/sql", tags=["SQL查询"])


@router.post("", responses={500: {"model": ErrorResponse}})
async def sql_ask(req: SQLAskRequest):
    """自然语言转 SQL 查询，自动行级安全注入 + 敏感列拦截 + 脱敏"""
    agent = get_sql_agent()
    answer = await asyncio.to_thread(agent.ask, req.question, req.current_user_id)
    return {"answer": answer}
