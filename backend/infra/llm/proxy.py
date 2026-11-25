"""
proxy.py — _LLMProxy 代理对象 + 模块级 llm 单例（Facade 门面）

核心设计:
  - 懒加载：首次调用时才初始化 LLM，根据 LLM_MODEL 自动选择 Provider
  - `llm` 是 _LLMProxy 代理；每次 .invoke()/.stream() 都委派给当前活跃模型
  - 架构重构：核心子系统物理拆解至 `backend/infra/llm/gateway/` 子包
    - context.py: 用户与请求上下文、诊断回调与取消监听
    - streaming.py: 流式 Sink 管理、增量分片提取与观测
    - accounting.py: Token 计费、用量累加与财务审计落账
    - resilience.py: 容灾韧性、重试退避与熔断降级
    - budget_guard.py: 速率限制、价格档位与上下文预算预检
    - bound_proxy.py: 参数绑定与模型工具绑定代理
  - 本模块作为门面（Facade），100% 保持向后兼容导出所有公共及内部契约符号。
"""
from __future__ import annotations

import inspect
import sys
import threading
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

from backend.config import LLM_MODEL
from backend.config.llm import (
    LLM_ALLOW_DEGRADED_ANSWER,
    LLM_CONTEXT_LENGTH,
    LLM_FALLBACK_MODEL,
    LLM_MAX_RETRIES,
    LLM_RETRY_BACKOFF_BASE,
    OLLAMA_ENABLED,
)
from backend.core.tool_runtime.deadline import (
    check_execution_deadline,
    provider_deadline_kwargs,
)
from backend.infra.llm.factory import get_llm_factory
from backend.infra.llm.models import (
    canonical_model_id,
    compute_cost_usd,
    get_available_models,
    get_model_entry,
    get_provider_driver,
    is_registered_model,
    model_identity_extras,
    resolve_output_token_cap,
    resolve_provider,
)
from backend.infra.llm.resolved_model import (
    ResolvedModelContext,
    get_current_resolved_model,
    set_current_resolved_model,
)
from backend.observability.llm_call_span import (
    note_prompt_snapshot,
    note_response_chunk,
)
from backend.shared.logger import logger

# ── 从 gateway 专职子包完整 re-export 符号（保证存量 100% 兼容）───────
from backend.infra.llm.gateway import (
    RateLimitError,
    _BoundLLMProxy,
    _DEGRADED_ANSWER,
    _RoleCallCancelled,
    _TRANSIENT_MARKERS,
    _acall_with_resilience,
    _accumulate_turn_usage,
    _ahandle_terminal_failure,
    _apreflight_context,
    _await_role_retry,
    _bind_role_fallback_generation_kwargs,
    _breaker_for_resolved_model,
    _call_diagnostic_observer,
    _call_with_resilience,
    _context_length_error,
    _degraded_answer,
    _enforce_model_input_price_tier,
    _enforce_rate_limit,
    _failed_attempt_http_status,
    _gate_fixed_shape,
    _get_override_llm,
    _handle_terminal_failure,
    _is_context_length_provider_error,
    _is_transient,
    _l5_trigger_ratio,
    _last_call_meta_var,
    _last_tokens_var,
    _notify_call_diagnostic,
    _notify_degradation,
    _notify_stream_usage_missing,
    _override_llm_cache,
    _override_llm_lock,
    _preflight_context,
    _prepare_context_budget,
    _prepare_context_budget_async,
    _raise_if_role_call_cancelled,
    _record_failed_attempt,
    _record_preflight_failure,
    _record_stream_attempt,
    _record_tokens,
    _reject_overbudget,
    _request_model_var,
    _resilience_call_identity,
    _role_call_cancelled,
    _role_cancellation_event,
    _role_fallback_generation_kwargs,
    _role_max_retries,
    _role_runtime_policy,
    _role_skips_fallback,
    _settle_estimated_stream_usage,
    _soft_overflow_enabled,
    _stream_sink_var,
    _strip_think,
    _thread_local_user_id,
    _truncate_messages_to_budget,
    _try_distributed_rate_limit,
    _turn_usage_var,
    _usage_component,
    _user_id_var,
    _wait_for_role_retry,
    _wrap_result,
    bind_llm_for_role,
    bind_tools_for_model,
    emit_stream_delta,
    enforce_model_input_price_tier,
    extract_chunk_reasoning,
    extract_chunk_text,
    get_request_model_name,
    get_turn_usage,
    invalidate_runtime_caches,
    is_degraded_response,
    record_llm_result,
    reset_stream_sink,
    reset_turn_usage,
    run_with_role_cancellation,
    scoped_call_diagnostics,
    scoped_current_user_id,
    set_current_user_id,
    set_request_model,
    set_stream_sink,
)

# =====================================================
# 默认主模型与备用模型实例管理（单例/懒加载）
# =====================================================

_default_llm = None
_default_llm_model: str | None = None
_default_lock = threading.Lock()

_fallback_llm = None
_fallback_lock = threading.Lock()


def _get_provider_for(model_name: str) -> str:
    """根据模型名查找所属 provider。"""
    return resolve_provider(model_name)


def _upstream_model_name(model_name: str) -> str:
    """登记名 → 上游真实模型名。"""
    try:
        entry = get_model_entry(model_name)
    except Exception:
        return model_name
    if not entry:
        return model_name
    return str(entry.get("upstream_name") or "").strip() or model_name


def _resolve_credentials_or_none(provider: str, model_name: str):
    """解析某 provider 当前生效的数据库凭据。"""
    from backend.infra.llm.credentials import resolve_credentials
    try:
        return resolve_credentials(provider, model_name=model_name)
    except Exception as e:
        logger.warning(
            f"[LLM:proxy] 凭据解析失败 provider={provider} model={model_name}: {e} "
            "→ 保持未配置状态"
        )
        return None


def _build_llm_for(model_name: str) -> BaseChatModel:
    """按模型名构建 Provider 实例（不缓存 — 缓存由调用方管理）。"""
    provider = _get_provider_for(model_name)
    credentials = _resolve_credentials_or_none(provider, model_name)
    upstream = _upstream_model_name(model_name)

    if provider == "deepseek":
        from backend.infra.llm.providers.deepseek import build_deepseek
        return build_deepseek(upstream, credentials)
    if provider == "minimax":
        from backend.infra.llm.providers.minimax import build_minimax
        return build_minimax(upstream, credentials)
    if provider == "qwen":
        from backend.infra.llm.providers.qwen import build_qwen
        return build_qwen(upstream, credentials)
    if provider == "qwen_tp":
        from backend.infra.llm.providers.qwen_tp import build_qwen_tp
        return build_qwen_tp(upstream, credentials)
    if provider == "vllm":
        from backend.infra.llm.providers.vllm import build_vllm
        return build_vllm(upstream, credentials)
    if provider == "siliconflow":
        from backend.infra.llm.providers.siliconflow import build_siliconflow
        return build_siliconflow(upstream, credentials)

    driver = (get_provider_driver(provider) or "").strip().lower()
    if driver in ("openai", "anthropic"):
        from backend.infra.llm.providers.driver_compat import build_by_driver
        _entry = get_model_entry(model_name) or {}
        return build_by_driver(
            driver, upstream, credentials,
            max_tokens=resolve_output_token_cap(_entry, LLM_CONTEXT_LENGTH),
        )
    if driver and driver != "ollama":
        logger.warning(
            "[LLM:proxy] provider=%s 的协议驱动 %s 不受支持，落 ollama 兜底",
            provider, driver,
        )

    from backend.infra.llm.providers.ollama import build_ollama
    return build_ollama(upstream, credentials)  # type: ignore[return-value]


def _get_fallback_llm() -> BaseChatModel | None:
    """获取备用模型实例（未配置 LLM_FALLBACK_MODEL 时返回 None）。"""
    global _fallback_llm
    fallback_model = _configured_fallback_model()
    if not fallback_model:
        return None
    if _fallback_llm is not None:
        return _fallback_llm
    with _fallback_lock:
        if _fallback_llm is None:
            try:
                logger.info(f"[LLM:resilience] 初始化备用模型: {fallback_model}")
                _fallback_llm = _build_llm_for(fallback_model)
            except Exception as exc:
                logger.warning(
                    "[LLM:resilience] 备用模型初始化失败: %s",
                    type(exc).__name__,
                )
                return None
    return _fallback_llm


def _build_default_llm():
    """根据当前 ``main`` 角色构建默认 Provider 实例。"""
    global _default_llm, _default_llm_model
    model_name = _configured_main_model()
    if _default_llm is not None and _default_llm_model == model_name:
        return _default_llm

    with _default_lock:
        if _default_llm is not None and _default_llm_model == model_name:
            return _default_llm

        logger.info(f"正在初始化默认 LLM: {model_name}")
        _default_llm = _build_llm_for(model_name)
        _default_llm_model = model_name
        logger.info(
            f"LLM init OK: {model_name} (provider={_get_provider_for(model_name)})"
        )
        return _default_llm


def _configured_main_model() -> str:
    """取 main 角色的当前生效模型；DB 覆盖优先于旧模块常量。"""
    try:
        from backend.config import model_roles

        value = model_roles.resolve_effective("main").get("value")
        return str(value or LLM_MODEL)
    except Exception:
        logger.warning("[LLM:proxy] 读取 main 模型角色失败，回落 LLM_MODEL", exc_info=True)
        return LLM_MODEL


def get_llm_for_role(role: str) -> BaseChatModel:
    """按模型角色获取带缓存的 LLM 实例。"""
    from backend.config import model_roles

    effective = model_roles.resolve_effective(role)
    model_name = str(effective.get("value") or "").strip()
    if not model_name:
        raise RuntimeError(f"模型角色 {role!r} 没有可用模型")
    ctx = _resolve_call_context(role=role, model_name=model_name)
    if ctx is not None:
        set_current_resolved_model(ctx)
    return _get_override_llm(model_name)


def _configured_fallback_model() -> str:
    """取 fallback 角色的字面值；空值保持「不启用备用模型」语义。"""
    try:
        from backend.config import model_roles

        info = model_roles.resolve_raw("fallback")
        if info.get("source") == model_roles.SOURCE_DB:
            return str(info.get("value") or "")
    except Exception:
        logger.warning(
            "[LLM:proxy] 读取 fallback 模型角色失败，回落旧常量", exc_info=True
        )
    return LLM_FALLBACK_MODEL


def _get_role_fallback_llm(role: str) -> tuple[str, Any | None]:
    """优先使用角色策略指定的备用模型，否则继承全局 fallback 角色。"""
    mod = sys.modules.get("backend.infra.llm.proxy")
    policy_fn = getattr(mod, "_role_runtime_policy", _role_runtime_policy) if mod else _role_runtime_policy
    policy = policy_fn(role)
    role_model = str(getattr(policy, "fallback_model", "") or "").strip()
    if role_model:
        try:
            override_fn = getattr(mod, "_get_override_llm", _get_override_llm) if mod else _get_override_llm
            return role_model, override_fn(role_model)
        except Exception as exc:
            logger.warning(
                "[LLM:resilience] 角色备用模型初始化失败 (%s)",
                type(exc).__name__,
            )
            return role_model, None
    fallback_model_fn = getattr(mod, "_configured_fallback_model", _configured_fallback_model) if mod else _configured_fallback_model
    model_name = fallback_model_fn()
    fallback_llm_fn = getattr(mod, "_get_fallback_llm", _get_fallback_llm) if mod else _get_fallback_llm
    return model_name, fallback_llm_fn()


def _resolve_active_llm() -> BaseChatModel:
    """返回当前生效的 LLM 实例：请求覆盖 > factory 缓存 > 懒加载默认。"""
    override = _request_model_var.get()
    if override:
        factory = get_llm_factory()
        if factory is not None:
            cached = factory._instance_cache.get(override)
            if cached is not None:
                return cached
        return _get_override_llm(override)
    try:
        from backend.config import model_roles

        if model_roles.resolve_raw("main").get("source") == model_roles.SOURCE_DB:
            return _build_default_llm()
    except Exception:
        logger.warning("[LLM:proxy] 判断 main DB 覆盖失败，继续旧 factory 路径", exc_info=True)
    factory = get_llm_factory()
    if factory is not None:
        cached = factory._instance_cache.get(factory._current_model)
        if cached is not None:
            return cached
    return _build_default_llm()


def get_active_model_name() -> str:
    """返回当前生效模型名（解析顺序与 _resolve_active_llm 一致）。"""
    override = _request_model_var.get()
    if override:
        return override
    try:
        from backend.config import model_roles

        main = model_roles.resolve_raw("main")
        if main.get("source") == model_roles.SOURCE_DB:
            return _configured_main_model()
    except Exception:
        logger.warning("[LLM:proxy] 读取 main DB 覆盖失败，继续旧 factory 路径", exc_info=True)
    factory = get_llm_factory()
    if factory is not None:
        return factory._current_model
    return _configured_main_model()


def _request_attribution_ids() -> tuple[str, str]:
    """取当前请求的 request_id / trace_id（软失败返回空串）。"""
    try:
        from backend.observability.llm_usage_store import current_usage_attribution

        attr_d = current_usage_attribution()
        return str(attr_d.get("request_id") or ""), str(attr_d.get("trace_id") or "")
    except Exception:
        return "", ""


def _resolve_call_context(
    role: str = "main", model_name: str | None = None,
) -> ResolvedModelContext | None:
    """生成本次调用的 ResolvedModelContext（P1 模型归属修复）。"""
    try:
        request_id, trace_id = _request_attribution_ids()

        # 1. 请求级覆盖（仅 main 角色语义）
        if role == "main":
            override = _request_model_var.get()
            if override:
                return ResolvedModelContext(
                    model_id=override, provider=_get_provider_for(override),
                    role=role, binding_source="request_override",
                    request_id=request_id, trace_id=trace_id,
                    **model_identity_extras(override),
                )

        # 2. 显式指定模型
        resolved_name = str(model_name or "").strip()
        source = "code_default"
        if resolved_name:
            try:
                from backend.config import model_roles

                src = model_roles.resolve_raw(role).get("source")
                if src == model_roles.SOURCE_DB:
                    source = "db_binding"
                elif src == model_roles.SOURCE_ENV:
                    source = "env_default"
            except Exception:
                pass
        else:
            # 3. main 默认解析
            resolved_name = get_active_model_name()
            try:
                from backend.config import model_roles

                if model_roles.resolve_raw("main").get("source") == model_roles.SOURCE_DB:
                    source = "db_binding"
            except Exception:
                pass
            factory = get_llm_factory()
            if factory is not None and source != "db_binding":
                if resolved_name == getattr(factory, "_current_model", None):
                    source = "factory"
                else:
                    source = "env_default"

        if not resolved_name:
            return None
        return ResolvedModelContext(
            model_id=resolved_name, provider=_get_provider_for(resolved_name),
            role=role, binding_source=source,
            request_id=request_id, trace_id=trace_id,
            **model_identity_extras(resolved_name),
        )
    except Exception:
        return None


# =====================================================
# 核心门面代理对象 _LLMProxy
# =====================================================

class _LLMProxy:
    """代理对象：每次调用都实时委派给当前活跃 LLM，并协调韧性与用量记录。"""

    __slots__ = ()
    __name__ = "llm_proxy"
    __code__ = None
    __defaults__ = None
    __kwdefaults__ = None
    __annotations__ = None
    _WRAP_METHODS = {'invoke', 'ainvoke', 'generate', 'agenerate', 'batch', 'stream', 'astream'}

    def __getattr__(self, name: str):
        target = _resolve_active_llm()
        attr = getattr(target, name)
        if name == "bind" and callable(attr):
            def bind_wrapper(*args, **kwargs):
                return _BoundLLMProxy(attr(*args, **kwargs), role="main")
            return bind_wrapper

        if name == "bind_tools" and callable(attr):
            def bind_tools_wrapper(*args, **kwargs):
                return _BoundLLMProxy(
                    attr(*args, **kwargs),
                    tools=args[0] if args else kwargs.get("tools"),
                )
            return bind_tools_wrapper

        if name in self._WRAP_METHODS and callable(attr):
            if inspect.isasyncgenfunction(attr):
                async def astream_wrapper(*args, **kwargs):
                    from backend.infra.llm.budget import (
                        release_model_reservation,
                        reserve_model_call,
                        settle_or_review_model_reservation,
                    )

                    user_id = kwargs.get("user_id") or _thread_local_user_id()
                    _enforce_rate_limit(user_id)
                    ctx = _resolve_call_context()
                    if ctx is not None:
                        set_current_resolved_model(ctx)
                    args = await _apreflight_context(
                        args, kwargs, call_method=name,
                    )
                    note_prompt_snapshot(args)
                    reserve_model_call(
                        "primary", model_name=get_active_model_name(),
                    )
                    _t0 = time.monotonic()
                    usage_chunk = None
                    from backend.infra.llm.usage_estimator import StreamTextMeter
                    meter = StreamTextMeter()
                    kwargs = provider_deadline_kwargs(attr, kwargs)
                    try:
                        async for chunk in attr(*args, **kwargs):
                            check_execution_deadline()
                            if getattr(chunk, "usage_metadata", None):
                                usage_chunk = chunk
                            meter.add(extract_chunk_text(chunk))
                            note_response_chunk(extract_chunk_text(chunk))
                            yield _wrap_result(chunk)
                    except Exception as e:
                        if _is_context_length_provider_error(e):
                            release_model_reservation()
                            raise _context_length_error() from e
                        settle_or_review_model_reservation("stream_failed_possibly_billed")
                        raise
                    finally:
                        if usage_chunk is not None:
                            _record_tokens(
                                usage_chunk,
                                duration_ms=(time.monotonic() - _t0) * 1000,
                            )
                        else:
                            if not _settle_estimated_stream_usage(
                                meter, args, (time.monotonic() - _t0) * 1000,
                                "astream",
                            ):
                                settle_or_review_model_reservation("stream_usage_missing")
                                _notify_stream_usage_missing("astream")
                return astream_wrapper

            if inspect.isgeneratorfunction(attr):
                def stream_wrapper(*args, **kwargs):
                    from backend.infra.llm.budget import (
                        release_model_reservation,
                        reserve_model_call,
                        settle_or_review_model_reservation,
                    )

                    user_id = kwargs.get("user_id") or _thread_local_user_id()
                    _enforce_rate_limit(user_id)
                    ctx = _resolve_call_context()
                    if ctx is not None:
                        set_current_resolved_model(ctx)
                    args = _preflight_context(args, kwargs, call_method=name)
                    note_prompt_snapshot(args)
                    _t0 = time.monotonic()
                    usage_chunk = None
                    yielded_content = False
                    from backend.infra.llm.usage_estimator import StreamTextMeter
                    meter = StreamTextMeter()
                    try:
                        for attempt in range(LLM_MAX_RETRIES + 1):
                            check_execution_deadline()
                            kwargs = provider_deadline_kwargs(attr, kwargs)
                            reserve_model_call(
                                "primary" if attempt == 0 else "retry",
                                model_name=get_active_model_name(),
                            )
                            _attempt_t0 = time.monotonic()
                            _first_chunk_ms: float | None = None
                            try:
                                for chunk in attr(*args, **kwargs):
                                    if _first_chunk_ms is None:
                                        _first_chunk_ms = (
                                            time.monotonic() - _attempt_t0
                                        ) * 1000
                                    check_execution_deadline()
                                    if getattr(chunk, "usage_metadata", None):
                                        usage_chunk = chunk
                                    wrapped = _wrap_result(chunk)
                                    if extract_chunk_text(wrapped):
                                        yielded_content = True
                                    meter.add(extract_chunk_text(chunk))
                                    note_response_chunk(extract_chunk_text(chunk))
                                    yield wrapped
                                _record_stream_attempt(
                                    attempt,
                                    (time.monotonic() - _attempt_t0) * 1000,
                                    _first_chunk_ms,
                                )
                                break
                            except Exception as e:
                                _record_stream_attempt(
                                    attempt,
                                    (time.monotonic() - _attempt_t0) * 1000,
                                    _first_chunk_ms,
                                    error_type=type(e).__name__,
                                )
                                if yielded_content or _is_transient(e):
                                    settle_or_review_model_reservation(
                                        "stream_failed_possibly_billed",
                                    )
                                else:
                                    release_model_reservation()
                                if _is_context_length_provider_error(e):
                                    raise _context_length_error() from e
                                if yielded_content or not _is_transient(e) \
                                        or attempt >= LLM_MAX_RETRIES:
                                    raise
                                delay = LLM_RETRY_BACKOFF_BASE ** (attempt + 1)
                                logger.warning(
                                    f"[LLM:stream] 首 chunk 前瞬时错误，重试 "
                                    f"{attempt + 1}/{LLM_MAX_RETRIES} "
                                    f"({type(e).__name__}, {delay:.1f}s 后)")
                                time.sleep(delay)
                    finally:
                        if usage_chunk is not None:
                            _record_tokens(
                                usage_chunk,
                                duration_ms=(time.monotonic() - _t0) * 1000,
                            )
                        else:
                            if not _settle_estimated_stream_usage(
                                meter, args, (time.monotonic() - _t0) * 1000,
                                "stream",
                            ):
                                settle_or_review_model_reservation("stream_usage_missing")
                                _notify_stream_usage_missing("stream")
                return stream_wrapper

            if inspect.iscoroutinefunction(attr):
                async def async_wrapper(*args, **kwargs):
                    user_id = kwargs.get("user_id") or _thread_local_user_id()
                    _enforce_rate_limit(user_id)
                    ctx = _resolve_call_context()
                    if ctx is not None:
                        set_current_resolved_model(ctx)
                    args = await _apreflight_context(
                        args, kwargs, call_method=name,
                    )
                    _t0 = time.monotonic()
                    result = await _acall_with_resilience(attr, *args, **kwargs)
                    _record_tokens(result, duration_ms=(time.monotonic() - _t0) * 1000)
                    return _wrap_result(result)
                return async_wrapper

            def wrapper(*args, **kwargs):
                user_id = kwargs.get("user_id") or _thread_local_user_id()
                _enforce_rate_limit(user_id)
                ctx = _resolve_call_context()
                if ctx is not None:
                    set_current_resolved_model(ctx)
                args = _preflight_context(args, kwargs, call_method=name)
                _t0 = time.monotonic()
                result = _call_with_resilience(attr, *args, **kwargs)
                _record_tokens(result, duration_ms=(time.monotonic() - _t0) * 1000)
                return _wrap_result(result)
            return wrapper
        return attr

    def __call__(self, *args, **kwargs):
        user_id = kwargs.get("user_id") or _thread_local_user_id()
        _enforce_rate_limit(user_id)
        ctx = _resolve_call_context()
        if ctx is not None:
            set_current_resolved_model(ctx)
        args = _preflight_context(args, kwargs)
        _t0 = time.monotonic()
        result = _call_with_resilience(_resolve_active_llm().invoke, *args, **kwargs)
        _record_tokens(result, duration_ms=(time.monotonic() - _t0) * 1000)
        return _wrap_result(result)

    def __repr__(self) -> str:
        try:
            target = _resolve_active_llm()
            return f"<LLMProxy -> {type(target).__name__}({getattr(target, 'model', '?')})>"
        except Exception:
            return "<LLMProxy>"

    def __str__(self) -> str:
        return self.__repr__()


# 模块级唯一代理单例
llm = _LLMProxy()


def get_llm() -> BaseChatModel:
    """显式获取当前 LLM 实例。"""
    return _resolve_active_llm()
