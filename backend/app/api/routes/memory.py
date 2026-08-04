"""
api/routes/memory.py — 会话记忆 API

端点:
  GET    /memory/sessions              — 列出所有会话
  GET    /memory/sessions/{id}         — 获取会话详情（消息列表）
  GET    /memory/sessions/{id}/context — 获取 Agent 工作上下文
  DELETE /memory/sessions/{id}         — 删除会话
  PATCH  /memory/sessions/{id}         — 重命名会话

PR-2.x: 业务逻辑已迁移至 MemoryService，路由仅做参数提取和委托。
"""
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/memory", tags=["记忆"])

# 惰性初始化 MemoryService 单例（避免启动时加载 DB 连接池）
_memory_service = None


def _get_service():
    global _memory_service
    if _memory_service is None:
        from backend.memory.service import MemoryService
        _memory_service = MemoryService()
    return _memory_service


class RenameRequest(BaseModel):
    title: str


@router.get("/sessions")
async def list_sessions(user_id: str = "default"):
    """列出用户的所有持久化会话"""
    return await _get_service().list_sessions(user_id=user_id)


@router.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """获取指定会话的消息列表"""
    return await _get_service().get_session_messages(session_id)


@router.get("/sessions/{session_id}/context")
async def get_session_context(session_id: str):
    """获取会话的 Agent 工作上下文（SQL结果/RAG文档/报告摘要）"""
    return await _get_service().get_session_context(session_id)


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """删除会话及其所有消息"""
    return await _get_service().delete_session(session_id)


@router.patch("/sessions/{session_id}")
async def rename_session(session_id: str, req: RenameRequest):
    """重命名会话标题"""
    return await _get_service().rename_session(session_id, req.title)
