"""Session contextvar — 跨 Tool 传递会话 ID 与用户身份。"""
from contextvars import ContextVar

# 当前会话 ID，由 LangGraph workflow 在每次请求时设置
_current_session_id: ContextVar[str] = ContextVar("session_id", default="multi-agent-default")
# 当前请求用户身份：RequestContext.bind() 时设置，工具层读它做权限校验/审计归属。
# 与 proxy 的 _user_id_var（限流用）分离：工具层不依赖 infra 细节。
_current_user_id: ContextVar[str] = ContextVar("tool_user_id", default="")


def set_session_id(sid: str):
    _current_session_id.set(sid)


def _get_session_id() -> str:
    return _current_session_id.get()


def set_tool_user_id(user_id: str):
    _current_user_id.set(user_id or "")


def get_tool_user_id() -> str:
    """当前请求用户 ID；无上下文（MCP 直调/脚本）返回空串。"""
    return _current_user_id.get()

