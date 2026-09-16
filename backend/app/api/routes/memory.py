"""
api/routes/memory.py — 会话记忆 API

端点:
  GET    /memory/sessions              — 列出所有会话
  GET    /memory/sessions/{id}         — 获取会话详情（消息列表）
  GET    /memory/sessions/{id}/context — 获取 Agent 工作上下文
  DELETE /memory/sessions/{id}         — 删除会话
  PATCH  /memory/sessions/{id}         — 重命名会话

PR-2.x: 业务逻辑已迁移至 MemoryService，路由仅做参数提取和委托。

2026-09-16 按登录用户隔离：user_id 一律取网关验签后注入的身份头
（resolve_identity，IDENTITY_SOURCE=header），不再接受查询参数自报——
此前 ?user_id=xxx 谁都能传，所有用户实际共享 "default" 桶。
单会话读/删/改名额外做属主校验，跨用户访问按 404 处理（不泄露存在性）。
未认证（guest，即未经网关 JWT 的直连流量）一律 401。
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.app.api.identity import resolve_identity

from backend.memory.manager import memory_manager

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


def _require_user(request: Request) -> str:
    """记忆按登录用户隔离：解析身份，guest（无网关注入身份头）直接 401。

    注意必须在 memory_manager.run_tool 之外先解析——Request 头读取
    在线程池 lambda 里也可用，但统一在路由入口判定语义更清晰。
    """
    ident = resolve_identity(request)
    if not ident.authenticated:
        raise HTTPException(status_code=401, detail="未认证：记忆库按登录用户隔离")
    return ident.user_id


@router.get("/sessions")
def list_sessions(request: Request,
                  limit: int = 50, before: str | None = None):
    """列出当前登录用户的所有持久化会话（支持游标分页）。

    Query:
      limit: 单次返回上限（默认 50，最大 200）
      before: ISO timestamp 游标；只返回 updated_at < before 的会话

    注意必须经 memory_manager 后台 loop 桥接执行：DB engine 在该 loop
    上初始化后即绑死（asyncpg 连接不可跨 loop），路由若在主 loop 直接
    await 会抛 "attached to a different loop"（前端侧栏红色报错的根因）。
    同步 def 由 FastAPI 放线程池执行，阻塞等待桥接结果不占主 loop。
    """
    user_id = _require_user(request)
    return _raise_for_error(
        memory_manager.run_tool(
            lambda: _get_service().list_sessions(user_id=user_id, limit=limit, before=before)
        )
    )


@router.get("/sessions/{session_id}")
def get_session(session_id: str, request: Request):
    """获取指定会话的消息列表（仅属主）"""
    user_id = _require_user(request)
    return _raise_for_error(
        memory_manager.run_tool(
            lambda: _get_service().get_session_messages(session_id, user_id=user_id)
        )
    )


@router.get("/sessions/{session_id}/context")
def get_session_context(session_id: str, request: Request):
    """获取会话的 Agent 工作上下文（SQL结果/RAG文档/报告摘要，仅属主）"""
    user_id = _require_user(request)
    return _raise_for_error(
        memory_manager.run_tool(
            lambda: _get_service().get_session_context(session_id, user_id=user_id)
        )
    )


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, request: Request):
    """删除会话及其所有消息（仅属主）"""
    user_id = _require_user(request)
    return _raise_for_error(
        memory_manager.run_tool(
            lambda: _get_service().delete_session(session_id, user_id=user_id)
        )
    )


@router.patch("/sessions/{session_id}")
def rename_session(session_id: str, req: RenameRequest, request: Request):
    """重命名会话标题（仅属主）"""
    user_id = _require_user(request)
    return _raise_for_error(
        memory_manager.run_tool(
            lambda: _get_service().rename_session(session_id, req.title, user_id=user_id)
        )
    )
