"""Session contextvar — 跨 Tool 传递会话 ID。"""
from contextvars import ContextVar

# 当前会话 ID，由 LangGraph workflow 在每次请求时设置
_current_session_id: ContextVar[str] = ContextVar("session_id", default="multi-agent-default")


def set_session_id(sid: str):
    _current_session_id.set(sid)


def _get_session_id() -> str:
    return _current_session_id.get()

