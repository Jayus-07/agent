"""
api/routes/memory.py — 会话记忆 API

端点:
  GET    /memory/sessions              — 列出所有会话
  GET    /memory/sessions/{id}         — 获取会话详情（消息列表）
  GET    /memory/sessions/{id}/context — 获取 Agent 工作上下文
  DELETE /memory/sessions/{id}         — 删除会话
  PATCH  /memory/sessions/{id}         — 重命名会话
"""
from fastapi import APIRouter
from pydantic import BaseModel
from backend.memory.database import AsyncSessionLocal
from backend.memory.repository.session_repo import SessionRepository
from backend.utils.logger import logger

router = APIRouter(prefix="/memory", tags=["记忆"])


class RenameRequest(BaseModel):
    title: str


@router.get("/sessions")
async def list_sessions(user_id: str = "default"):
    """列出用户的所有持久化会话"""
    async with AsyncSessionLocal() as db_session:
        try:
            repo = SessionRepository(db_session)
            sessions = await repo.list_all(user_id=user_id)
            await db_session.commit()
            return {"sessions": sessions, "total": len(sessions)}
        except Exception as e:
            await db_session.rollback()
            logger.error(f"[Memory API] list_sessions 失败: {e}")
            return {"sessions": [], "total": 0, "error": str(e)}


@router.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """获取指定会话的消息列表"""
    async with AsyncSessionLocal() as db_session:
        try:
            repo = SessionRepository(db_session)
            msgs = await repo.load_messages(session_id)
            await db_session.commit()
            return {
                "session_id": session_id,
                "messages": [
                    {"role": m.role, "content": m.content, "created_at": m.created_at.isoformat()}
                    for m in msgs
                ],
            }
        except Exception as e:
            await db_session.rollback()
            logger.error(f"[Memory API] get_session 失败: {e}")
            return {"session_id": session_id, "messages": [], "error": str(e)}


@router.get("/sessions/{session_id}/context")
async def get_session_context(session_id: str):
    """获取会话的 Agent 工作上下文（SQL结果/RAG文档/报告摘要）"""
    async with AsyncSessionLocal() as db_session:
        try:
            repo = SessionRepository(db_session)
            ctx = await repo.get_context(session_id)
            await db_session.commit()
            if ctx:
                import json as _json
                try:
                    return {"session_id": session_id, "context": _json.loads(ctx)}
                except Exception:
                    return {"session_id": session_id, "context": {"raw": ctx}}
            return {"session_id": session_id, "context": None}
        except Exception as e:
            await db_session.rollback()
            return {"session_id": session_id, "context": None, "error": str(e)}


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """删除会话及其所有消息"""
    async with AsyncSessionLocal() as db_session:
        try:
            repo = SessionRepository(db_session)
            ok = await repo.delete(session_id)
            await db_session.commit()
            if ok:
                logger.info(f"[Memory API] 已删除会话: {session_id}")
                return {"ok": True, "session_id": session_id}
            return {"ok": False, "error": "会话不存在"}
        except Exception as e:
            await db_session.rollback()
            logger.error(f"[Memory API] delete_session 失败: {e}")
            return {"ok": False, "error": str(e)}


@router.patch("/sessions/{session_id}")
async def rename_session(session_id: str, req: RenameRequest):
    """重命名会话标题"""
    async with AsyncSessionLocal() as db_session:
        try:
            repo = SessionRepository(db_session)
            ok = await repo.rename(session_id, req.title)
            await db_session.commit()
            if ok:
                logger.info(f"[Memory API] 已重命名: {session_id} → {req.title}")
                return {"ok": True, "session_id": session_id, "title": req.title}
            return {"ok": False, "error": "会话不存在"}
        except Exception as e:
            await db_session.rollback()
            logger.error(f"[Memory API] rename_session 失败: {e}")
            return {"ok": False, "error": str(e)}
