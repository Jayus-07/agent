"""
backend/infra/llm/gateway/context.py — 请求上下文变量与诊断回调管理
"""
from __future__ import annotations

import asyncio
import contextvars as _contextvars
import threading
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Callable, Iterator

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

from backend.config.llm import OLLAMA_ENABLED
from backend.infra.llm.models import (
    get_available_models,
    is_registered_model,
)
from backend.shared.logger import logger

# PR-0.4: 当前请求的 user_id（限流用）— 调用方可通过 set_current_user_id() 设置。
_user_id_var: _contextvars.ContextVar[str | None] = _contextvars.ContextVar(
    "llm_current_user_id", default=None,
)


def set_current_user_id(user_id: str | None) -> None:
    """设置当前请求上下文的 user_id（限流用）。FastAPI 路由层在每次请求开始时调用。"""
    _user_id_var.set(user_id)


@contextmanager
def scoped_current_user_id(user_id: str | None) -> Iterator[None]:
    """在短期后台作用域绑定限流用户，并在退出时恢复原值。"""
    token = _user_id_var.set(user_id or None)
    try:
        yield
    finally:
        _user_id_var.reset(token)


def _thread_local_user_id() -> str | None:
    """读取当前请求的 user_id（限流用）。"""
    return _user_id_var.get()


_role_cancellation_event: _contextvars.ContextVar[Any] = _contextvars.ContextVar(
    "llm_role_cancellation_event", default=None,
)
_call_diagnostic_observer: _contextvars.ContextVar[Callable | None] = (
    _contextvars.ContextVar("llm_call_diagnostic_observer", default=None)
)
_role_fallback_generation_kwargs: _contextvars.ContextVar[dict[str, Any] | None] = (
    _contextvars.ContextVar("llm_role_fallback_generation_kwargs", default=None)
)


def run_with_role_cancellation(
    event: Any,
    function: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    """在调用上下文中绑定可由等待方触发的角色取消信号。"""
    token = _role_cancellation_event.set(event)
    try:
        return function(*args, **kwargs)
    finally:
        _role_cancellation_event.reset(token)


@contextmanager
def scoped_call_diagnostics(observer: Callable[[str, dict[str, Any]], None]):
    """为单次调用绑定轻量诊断回调，不改变 Proxy 的治理与结算路径。"""
    token = _call_diagnostic_observer.set(observer)
    try:
        yield
    finally:
        _call_diagnostic_observer.reset(token)


def _notify_call_diagnostic(stage: str, **values: Any) -> None:
    observer = _call_diagnostic_observer.get()
    if observer is None:
        return
    try:
        observer(stage, values)
    except Exception:  # noqa: BLE001 — 诊断回调不得影响模型调用
        logger.debug("[LLM:proxy] 调用诊断回调失败", exc_info=True)


def _role_call_cancelled(role: str) -> bool:
    if role != "multi_query":
        return False
    event = _role_cancellation_event.get()
    return bool(event is not None and event.is_set())


class _RoleCallCancelled(TimeoutError):
    """区分 Provider 超时和 Provider 尚未启动前的逻辑取消。"""


def _raise_if_role_call_cancelled(role: str) -> None:
    if _role_call_cancelled(role):
        raise _RoleCallCancelled("multi_query role deadline expired")


def _bind_role_fallback_generation_kwargs(fallback):
    """让 fallback 继承角色入口设置的生成约束。"""
    generation_kwargs = _role_fallback_generation_kwargs.get()
    if generation_kwargs:
        return fallback.bind(**generation_kwargs)
    return fallback


def _wait_for_role_retry(role: str, delay: float) -> None:
    event = _role_cancellation_event.get() if role == "multi_query" else None
    if event is not None:
        if event.wait(delay):
            _raise_if_role_call_cancelled(role)
        return
    time.sleep(delay)


async def _await_role_retry(role: str, delay: float) -> None:
    event = _role_cancellation_event.get() if role == "multi_query" else None
    if event is None:
        await asyncio.sleep(delay)
        return
    deadline = time.monotonic() + delay
    while not event.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        await asyncio.sleep(min(remaining, 0.05))
    _raise_if_role_call_cancelled(role)


# 按请求模型覆盖：RequestContext.model → bind() 时设置（空 = 用全局模型）。
_request_model_var: _contextvars.ContextVar[str] = _contextvars.ContextVar(
    "llm_request_model", default="",
)


def set_request_model(model: str) -> None:
    """设置当前请求的模型覆盖。空串清除；非法模型名忽略（回退全局 LLM_MODEL）。"""
    model = (model or "").strip()
    if not model:
        _request_model_var.set("")
        return
    from backend.infra.llm.models import validate_override_model
    from backend.infra.llm.proxy import get_active_model_name

    ok, reason = validate_override_model(model, ollama_enabled=OLLAMA_ENABLED)
    if not ok:
        if not is_registered_model(model):
            reason = f"{reason} (可用: {[m['name'] for m in get_available_models()]})"
        logger.warning(f"[LLM:proxy] 忽略模型覆盖 {model}: {reason} "
                       f"(回退全局 {get_active_model_name()})")
        _request_model_var.set("")
        return
    _request_model_var.set(model)


def get_request_model_name() -> str:
    """读取当前请求的模型覆盖（空串 = 无覆盖，走全局模型）。"""
    return _request_model_var.get()


# 覆盖模型的实例缓存（独立于全局默认模型，按需构建）
_override_llm_cache: dict[str, BaseChatModel] = {}
_override_llm_lock = threading.Lock()


def _get_override_llm(model_name: str) -> BaseChatModel:
    """获取按请求覆盖模型的实例（懒构建 + 缓存）。"""
    with _override_llm_lock:
        inst = _override_llm_cache.get(model_name)
        if inst is None:
            from backend.infra.llm.proxy import _build_llm_for
            logger.info(f"[LLM:proxy] 按请求模型覆盖生效: {model_name}")
            inst = _build_llm_for(model_name)
            _override_llm_cache[model_name] = inst
        return inst


def invalidate_runtime_caches() -> None:
    """清空 proxy/factory 的模型实例缓存，使配置变更立即进入新实例。"""
    import backend.infra.llm.proxy as proxy_module

    with proxy_module._default_lock:
        proxy_module._default_llm = None
        proxy_module._default_llm_model = None
    with proxy_module._fallback_lock:
        proxy_module._fallback_llm = None
    with _override_llm_lock:
        _override_llm_cache.clear()
    try:
        from backend.infra.llm import factory as factory_module

        factory = factory_module._factory
        if factory is not None:
            factory.invalidate()
    except Exception:
        logger.warning("[LLM:proxy] 清理工厂实例缓存失败", exc_info=True)
