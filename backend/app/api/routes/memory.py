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
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/memory", tags=["记忆"])

# 惰性初始化 MemoryService 单例（避免启动时加载 DB 连接池）
_memory_service = None

# MemoryService 用 {"error": "会话不存在"} 表达业务性缺失，其余 error 都来自
# except 分支（DB 连不上、SQL 失败等基础设施故障），两者 HTTP 语义不同
_NOT_FOUND_MESSAGE = "会话不存在"


def _get_service():
    global _memory_service
    if _memory_service is None:
        from backend.memory.service import MemoryService
        _memory_service = MemoryService()
    return _memory_service


def _raise_for_error(result: dict) -> dict:
    """把 service 层的 error 字段映射为对应 HTTP 状态码。

    service 保持「返回 error 字段」的契约（Agent 侧依赖它，不能改成抛异常），
    但 HTTP 边界必须区分「会话不存在」(404) 和「记忆库故障」(503)：
    否则 DB 挂掉会以 200 + 空列表返回，前端只能显示成"没有会话"，
    真因得去翻 PostgreSQL 日志才找得到。
    """
    error = result.get("error")
    if not error:
        return result
    if error == _NOT_FOUND_MESSAGE:
        raise HTTPException(status_code=404, detail=error)
    raise HTTPException(status_code=503, detail=f"记忆库不可用: {error}")


class RenameRequest(BaseModel):
    title: str


@router.get("/sessions")
async def list_sessions(user_id: str = "default",
                        limit: int = 50, before: str | None = None):
    """列出用户的所有持久化会话（支持游标分页）。

    Query:
      limit: 单次返回上限（默认 50，最大 200）
      before: ISO timestamp 游标；只返回 updated_at < before 的会话
    """
    return _raise_for_error(
        await _get_service().list_sessions(user_id=user_id, limit=limit, before=before)
    )


@router.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """获取指定会话的消息列表"""
    return _raise_for_error(await _get_service().get_session_messages(session_id))


@router.get("/sessions/{session_id}/context")
async def get_session_context(session_id: str):
    """获取会话的 Agent 工作上下文（SQL结果/RAG文档/报告摘要）"""
    return _raise_for_error(await _get_service().get_session_context(session_id))


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """删除会话及其所有消息"""
    return _raise_for_error(await _get_service().delete_session(session_id))


@router.patch("/sessions/{session_id}")
async def rename_session(session_id: str, req: RenameRequest):
    """重命名会话标题"""
    return _raise_for_error(await _get_service().rename_session(session_id, req.title))
