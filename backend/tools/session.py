"""Session contextvar — 跨 Tool 传递会话 ID 与用户身份。

统一上下文重构（2026-09-14）：ContextVar 定义收编至
backend.core.request_context（统一 bind() 的唯一定义点），本模块保留
原读写函数作薄封装——tools 层调用点与对象同一性（测试直接
set/reset ContextVar）均不受影响。
"""
from backend.core.request_context import (
    _current_department,
    _current_session_id,
    _current_user_id,
    _get_session_id,
    get_tool_department,
    get_tool_user_id,
    set_session_id,
    set_tool_department,
    set_tool_user_id,
)

__all__ = [
    "_current_department",
    "_current_session_id",
    "_current_user_id",
    "_get_session_id",
    "get_tool_department",
    "get_tool_user_id",
    "set_session_id",
    "set_tool_department",
    "set_tool_user_id",
]
