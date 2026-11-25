"""
backend/infra/llm/gateway/bound_proxy.py — 参数绑定包装与模型工具绑定
"""
from __future__ import annotations

import time
from typing import Any

from backend.infra.llm.gateway.accounting import (
    _last_call_meta_var,
    _last_tokens_var,
    _record_tokens,
    _wrap_result,
)
from backend.infra.llm.gateway.base import get_proxy_attr
from backend.infra.llm.gateway.budget_guard import (
    _apreflight_context,
    _enforce_rate_limit,
    _preflight_context,
)
from backend.infra.llm.gateway.context import (
    _get_override_llm,
    _notify_call_diagnostic,
    _role_fallback_generation_kwargs,
    _thread_local_user_id,
)
from backend.infra.llm.gateway.resilience import (
    _acall_with_resilience,
    _call_with_resilience,
)
from backend.infra.llm.resolved_model import set_current_resolved_model
from backend.shared.logger import logger


class _BoundLLMProxy:
    """绑定模型参数后的治理包装，保持调用和 LangChain 组合路径受控。"""

    def __init__(
        self,
        bound: Any,
        model_name: str | None = None,
        role: str = "main",
        tools: Any = None,
        generation_kwargs: dict[str, Any] | None = None,
    ):
        self._bound = bound
        self._model_name = model_name or ""
        self._role = role
        self._tools = tools
        self._generation_kwargs = dict(generation_kwargs or {})
        self._schema_tokens: int | None = None

    def _schema_reserved(self) -> int:
        """bind_tools 的工具 schema token 占用。"""
        if self._tools is None:
            return 0
        if self._schema_tokens is None:
            try:
                from backend.context_budget.token_counter import (
                    count_tool_schema_tokens,
                )
                self._schema_tokens = count_tool_schema_tokens(
                    self._tools, model=self._model_name or None)
            except Exception:
                self._schema_tokens = 0
        return self._schema_tokens

    def _bind_context(self) -> None:
        resolve_ctx_fn = get_proxy_attr("_resolve_call_context")
        if resolve_ctx_fn is not None:
            ctx = resolve_ctx_fn(
                role=self._role, model_name=self._model_name or None)
            if ctx is not None:
                set_current_resolved_model(ctx)

    def invoke(self, *args, **kwargs):
        user_id = kwargs.get("user_id") or get_proxy_attr("_thread_local_user_id", _thread_local_user_id)()
        get_proxy_attr("_enforce_rate_limit", _enforce_rate_limit)(user_id)
        _stage_started = time.monotonic()
        self._bind_context()
        get_proxy_attr("_notify_call_diagnostic", _notify_call_diagnostic)(
            "proxy_model_bind_ms",
            duration_ms=(time.monotonic() - _stage_started) * 1000,
        )
        _stage_started = time.monotonic()
        preflight_fn = get_proxy_attr("_preflight_context", _preflight_context)
        args = preflight_fn(args, kwargs,
            extra_reserved_tokens=self._schema_reserved(),
            call_method="invoke",
        )
        get_proxy_attr("_notify_call_diagnostic", _notify_call_diagnostic)(
            "price_preflight_ms",
            duration_ms=(time.monotonic() - _stage_started) * 1000,
        )
        _t0 = time.monotonic()
        token = _role_fallback_generation_kwargs.set(
            self._generation_kwargs or None)
        try:
            call_fn = get_proxy_attr("_call_with_resilience", _call_with_resilience)
            result = call_fn(self._bound.invoke, *args, **kwargs)
        finally:
            _role_fallback_generation_kwargs.reset(token)
        from backend.infra.llm.budget import current_reservation_id

        reservation_id = current_reservation_id()
        record_fn = get_proxy_attr("_record_tokens", _record_tokens)
        record_fn(
            result, duration_ms=(time.monotonic() - _t0) * 1000,
            model_name=self._model_name or None,
        )
        metadata = _last_call_meta_var.get() or {}
        get_proxy_attr("_notify_call_diagnostic", _notify_call_diagnostic)(
            "usage_accounting_finished",
            reservation_id=reservation_id,
            usage_received=bool(_last_tokens_var.get()),
            cost_status=metadata.get("cost_status", "unknown"),
            pricing_source=metadata.get("pricing_source", "unknown"),
            price_version=metadata.get("price_version", ""),
            settlement_status=metadata.get("settlement_status", "unknown"),
        )
        wrap_fn = get_proxy_attr("_wrap_result", _wrap_result)
        return wrap_fn(result)

    async def ainvoke(self, *args, **kwargs):
        user_id = kwargs.get("user_id") or get_proxy_attr("_thread_local_user_id", _thread_local_user_id)()
        get_proxy_attr("_enforce_rate_limit", _enforce_rate_limit)(user_id)
        self._bind_context()
        apreflight_fn = get_proxy_attr("_apreflight_context", _apreflight_context)
        args = await apreflight_fn(args, kwargs,
            extra_reserved_tokens=self._schema_reserved(),
            call_method="ainvoke",
        )
        _t0 = time.monotonic()
        token = _role_fallback_generation_kwargs.set(
            self._generation_kwargs or None)
        try:
            acall_fn = get_proxy_attr("_acall_with_resilience", _acall_with_resilience)
            result = await acall_fn(self._bound.ainvoke, *args, **kwargs)
        finally:
            _role_fallback_generation_kwargs.reset(token)
        record_fn = get_proxy_attr("_record_tokens", _record_tokens)
        record_fn(
            result, duration_ms=(time.monotonic() - _t0) * 1000,
            model_name=self._model_name or None,
        )
        wrap_fn = get_proxy_attr("_wrap_result", _wrap_result)
        return wrap_fn(result)

    def bind(self, **kwargs):
        return _BoundLLMProxy(
            self._bound.bind(**kwargs),
            model_name=self._model_name or None,
            role=self._role,
            tools=self._tools,
        )

    def with_config(self, config=None, **kwargs):
        return _BoundLLMProxy(
            self._bound.with_config(config, **kwargs),
            model_name=self._model_name or None,
            role=self._role,
            tools=self._tools,
        )

    def generate_prompt(self, prompts, stop=None, callbacks=None, **kwargs):
        from langchain_core.outputs import ChatGeneration, LLMResult

        config = {"callbacks": callbacks} if callbacks is not None else None
        generations = []
        for prompt in prompts:
            result = self.invoke(
                prompt,
                config=config,
                stop=stop,
                **kwargs,
            )
            generations.append([
                ChatGeneration(
                    message=result,
                    generation_info=getattr(result, "response_metadata", None),
                ),
            ])
        return LLMResult(generations=generations)

    async def agenerate_prompt(self, prompts, stop=None, callbacks=None, **kwargs):
        from langchain_core.outputs import ChatGeneration, LLMResult

        config = {"callbacks": callbacks} if callbacks is not None else None
        generations = []
        for prompt in prompts:
            result = await self.ainvoke(
                prompt,
                config=config,
                stop=stop,
                **kwargs,
            )
            generations.append([
                ChatGeneration(
                    message=result,
                    generation_info=getattr(result, "response_metadata", None),
                ),
            ])
        return LLMResult(generations=generations)

    def bind_tools(self, *args, **kwargs):
        return _BoundLLMProxy(
            self._bound.bind_tools(*args, **kwargs),
            model_name=self._model_name or None, role=self._role,
            tools=args[0] if args else kwargs.get("tools"),
            generation_kwargs=self._generation_kwargs,
        )

    def __getattr__(self, name: str):
        return getattr(self._bound, name)


def bind_tools_for_model(model_name: str, tools) -> "_BoundLLMProxy | None":
    """为指定模型构建 bind_tools 包装（专用轻量模型场景，如 tool_selector）。"""
    if not model_name:
        return None
    try:
        from backend.infra.llm.models import (
            get_available_models,
            get_model_entry,
            model_supports,
            registry_declares,
        )
        if model_name not in {m["name"] for m in get_available_models()}:
            logger.warning(
                f"[LLM:proxy] 专用模型未注册: {model_name}，回退全局模型")
            return None
        entry = get_model_entry(model_name)
        if entry is not None and registry_declares(entry, "tools") \
                and not model_supports(entry, "tools"):
            logger.warning(
                f"[LLM:proxy] 模型 {model_name} 已登记不支持工具调用"
                f"(capabilities.tools=false)，回退全局模型")
            return None
        get_override_fn = get_proxy_attr("_get_override_llm", _get_override_llm)
        inst = get_override_fn(model_name)
        return _BoundLLMProxy(
            inst.bind_tools(tools), model_name=model_name, role="tool_selector",
            tools=tools,
        )
    except Exception as e:
        logger.warning(f"[LLM:proxy] 专用模型 bind_tools 失败，回退全局: {e}")
        return None


def bind_llm_for_role(
    role: str,
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> "_BoundLLMProxy":
    """按模型角色获取带参数绑定的治理化 LLM 包装（非 tools 场景）。"""
    from backend.config import model_roles

    effective = model_roles.resolve_effective(role)
    model_name = str(effective.get("value") or "").strip()
    if not model_name:
        raise RuntimeError(f"模型角色 {role!r} 没有可用模型")
    resolve_ctx_fn = get_proxy_attr("_resolve_call_context")
    if resolve_ctx_fn is not None:
        ctx = resolve_ctx_fn(role=role, model_name=model_name)
        if ctx is not None:
            set_current_resolved_model(ctx)
    get_override_fn = get_proxy_attr("_get_override_llm", _get_override_llm)
    bound = get_override_fn(model_name)
    bind_kwargs: dict[str, Any] = {}
    if temperature is not None:
        bind_kwargs["temperature"] = temperature
    if max_tokens is not None:
        bind_kwargs["max_tokens"] = max_tokens
    if bind_kwargs:
        bound = bound.bind(**bind_kwargs)
    return _BoundLLMProxy(
        bound, model_name=model_name, role=role,
        generation_kwargs=bind_kwargs,
    )
