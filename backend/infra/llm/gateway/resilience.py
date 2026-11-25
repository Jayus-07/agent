"""
backend/infra/llm/gateway/resilience.py — 容灾韧性、重试退避与熔断降级
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from backend.config.llm import (
    LLM_ALLOW_DEGRADED_ANSWER,
    LLM_MAX_RETRIES,
    LLM_RETRY_BACKOFF_BASE,
)
from backend.core.tool_runtime.deadline import provider_deadline_kwargs
from backend.infra.llm.gateway.accounting import _record_failed_attempt
from backend.infra.llm.gateway.base import get_proxy_attr
from backend.infra.llm.gateway.context import (
    _RoleCallCancelled,
    _await_role_retry,
    _bind_role_fallback_generation_kwargs,
    _notify_call_diagnostic,
    _raise_if_role_call_cancelled,
    _role_call_cancelled,
    _wait_for_role_retry,
)
from backend.infra.llm.models import canonical_model_id, model_identity_extras
from backend.infra.llm.resolved_model import (
    ResolvedModelContext,
    get_current_resolved_model,
    set_current_resolved_model,
)
from backend.observability.llm_call_span import note_prompt_snapshot
from backend.shared.logger import logger

_TRANSIENT_MARKERS = (
    "timeout", "timeouterror", "connecterror", "connectionerror",
    "ratelimit", "apierror", "internalerror", "serviceunavailable",
    "badgateway", "gatewaytimeout", "503", "502", "504", "429",
)

_DEGRADED_ANSWER = (
    "抱歉，AI 服务当前暂时不可用（上游模型故障），请稍后重试。"
    "你稍后也可以在「模型管理」页切换到其他可用模型。"
)


def _is_transient(err: BaseException) -> bool:
    name = type(err).__name__.lower()
    return any(m in name for m in _TRANSIENT_MARKERS)


def _is_context_length_provider_error(err: BaseException) -> bool:
    """provider 异常是否为输入超长（判定逻辑收口在 error_taxonomy）。"""
    from backend.infra.llm.error_taxonomy import is_context_length_error
    return is_context_length_error(err)


def _context_length_error() -> Exception:
    """构造统一的超长输入错误（稳定错误码，SSE/API 层据此映射）。"""
    from backend.context_budget.errors import ContextBudgetExceededError
    return ContextBudgetExceededError(stage="provider")


def _degraded_answer(
    reason: str = "",
    error_type: str = "",
    provider: str = "",
    model: str = "",
):
    """构造降级 AIMessage（结构与正常 LLM 返回一致）。"""
    from langchain_core.messages import AIMessage
    degraded_meta = {
        "llm_degraded": True,
        "llm_degrade_reason": reason[:200],
        "llm_error_type": error_type,
        "llm_provider": provider,
        "llm_model": model,
    }
    return AIMessage(
        content=_DEGRADED_ANSWER,
        additional_kwargs=dict(degraded_meta),
        response_metadata=dict(degraded_meta),
    )


def is_degraded_response(msg: Any) -> bool:
    """判断 LLM 返回是否为降级话术（结构化消费者应据此 fail-fast）。"""
    if msg is None:
        return False
    for attr in ("additional_kwargs", "response_metadata"):
        meta = getattr(msg, attr, None)
        if isinstance(meta, dict) and meta.get("llm_degraded"):
            return True
    content = getattr(msg, "content", None)
    if isinstance(content, str):
        return content.strip().startswith(_DEGRADED_ANSWER[:24])
    return False


def _notify_degradation(code: str, detail: dict) -> None:
    """降级事件告警（best-effort，失败不影响主流程）。"""
    try:
        from backend.observability.alerts import log_degradation, make_alert
        log_degradation(make_alert(code, detail))
    except Exception:
        logger.debug("降级告警发送失败", exc_info=True)


def _handle_terminal_failure(
    err: BaseException,
    args: Any,
    kwargs: Any,
    *,
    model_name: str | None = None,
    role: str = "main",
):
    """重试耗尽/熔断开路后的统一兜底：备用模型 → 降级话术 → 模型错误。"""
    from backend.infra.llm.proxy import (
        _enforce_model_input_price_tier,
        _get_provider_for,
        _get_role_fallback_llm,
        get_active_model_name,
    )

    reason = (
        type(err).__name__ if role == "multi_query"
        else f"{type(err).__name__}: {str(err)[:120]}"
    )
    from backend.infra.llm.error_taxonomy import ModelProviderError, classify_model_error

    error_type = classify_model_error(err)
    active_model = canonical_model_id(model_name or "") or (
        model_name or get_proxy_attr("get_active_model_name", get_active_model_name)()
    )
    provider_fn = get_proxy_attr("_get_provider_for", _get_provider_for)
    provider = provider_fn(active_model)
    error_detail = {
        "reason": reason, "error_type": error_type,
        "provider": provider, "model": active_model,
    }
    skip_fallback_fn = get_proxy_attr("_role_skips_fallback", _role_skips_fallback)
    skip_fallback = skip_fallback_fn(role)

    # 1) 备用模型
    role_fallback_fn = get_proxy_attr("_get_role_fallback_llm", _get_role_fallback_llm)
    fallback_model, fb = ("", None) if skip_fallback else role_fallback_fn(role)
    if not skip_fallback:
        _raise_if_role_call_cancelled(role)
    if fb is not None:
        from backend.infra.circuit_breaker import CircuitBreakerOpenError
        from backend.infra.llm.budget import (
            RequestBudgetExceeded,
            release_model_reservation,
            reserve_model_call,
            settle_or_review_model_reservation,
        )
        from backend.infra.llm.quota import QuotaConfigurationError, QuotaExceeded

        enforce_price_fn = get_proxy_attr("_enforce_model_input_price_tier", _enforce_model_input_price_tier)
        enforce_price_fn(
            args, kwargs, model_name=fallback_model,
        )
        try:
            reserve_model_call("fallback", model_name=fallback_model)
        except (
            RequestBudgetExceeded, QuotaConfigurationError, QuotaExceeded,
        ) as exc:
            if not isinstance(err, CircuitBreakerOpenError):
                exc.provider_attempted = True
            raise
        try:
            _raise_if_role_call_cancelled(role)
            get_ctx_fn = get_proxy_attr("get_current_resolved_model", get_current_resolved_model)
            set_ctx_fn = get_proxy_attr("set_current_resolved_model", set_current_resolved_model)
            extras_fn = get_proxy_attr("model_identity_extras", model_identity_extras)
            _prev_ctx = get_ctx_fn()
            set_ctx_fn(ResolvedModelContext(
                model_id=fallback_model, provider=provider_fn(fallback_model),
                role=role, binding_source="fallback",
                request_id=(_prev_ctx.request_id if _prev_ctx else ""),
                trace_id=(_prev_ctx.trace_id if _prev_ctx else ""),
                **extras_fn(fallback_model),
            ))
            _raise_if_role_call_cancelled(role)
            kwargs = provider_deadline_kwargs(fb, kwargs)
            result = _bind_role_fallback_generation_kwargs(fb).invoke(
                *args, **kwargs)
            logger.info(f"[LLM:resilience] 备用模型接管成功 ({reason})")
            try:
                from backend.observability.metrics import llm_fallback_total
                llm_fallback_total.labels(
                    primary_model=active_model, fallback_model=fallback_model,
                ).inc()
            except Exception:
                pass
            _notify_degradation("LLM_FALLBACK_USED",
                                {**error_detail, "fallback_model": fallback_model})
            return result
        except _RoleCallCancelled:
            release_model_reservation()
            raise
        except Exception as e:
            if _is_transient(e):
                settle_or_review_model_reservation("fallback_failed_possibly_billed")
            else:
                release_model_reservation()
            fallback_error = type(e).__name__ if role == "multi_query" else e
            logger.warning("[LLM:resilience] 备用模型也失败: %s", fallback_error)
            record_failed_fn = get_proxy_attr("_record_failed_attempt", _record_failed_attempt)
            record_failed_fn(fallback_model, e, decision="fallback")

    # 2) 降级话术
    allow_degraded = get_proxy_attr("LLM_ALLOW_DEGRADED_ANSWER", LLM_ALLOW_DEGRADED_ANSWER)
    if (
        allow_degraded
        and not skip_fallback
        and role != "multi_query"
    ):
        logger.warning(f"[LLM:resilience] 最终降级为拒答话术 ({reason})")
        _notify_degradation("LLM_DEGRADED_ANSWER", error_detail)
        return _degraded_answer(reason, error_type=error_type,
                                provider=provider, model=active_model)

    # 3) 明确模型错误
    raise ModelProviderError(
        f"模型调用失败 [{error_type}] provider={provider} model={active_model}: {reason}",
        error_type=error_type, provider=provider, model=active_model,
        origin=err,
    ) from err


async def _ahandle_terminal_failure(
    err: BaseException,
    args: Any,
    kwargs: Any,
    *,
    model_name: str | None = None,
    role: str = "main",
):
    """async 版兜底：备用模型 → 降级话术 → 模型错误（分类同同步版）。"""
    from backend.infra.llm.proxy import (
        _enforce_model_input_price_tier,
        _get_provider_for,
        _get_role_fallback_llm,
        get_active_model_name,
    )

    reason = (
        type(err).__name__ if role == "multi_query"
        else f"{type(err).__name__}: {str(err)[:120]}"
    )
    from backend.infra.llm.error_taxonomy import ModelProviderError, classify_model_error

    error_type = classify_model_error(err)
    active_model = canonical_model_id(model_name or "") or (
        model_name or get_proxy_attr("get_active_model_name", get_active_model_name)()
    )
    provider_fn = get_proxy_attr("_get_provider_for", _get_provider_for)
    provider = provider_fn(active_model)
    error_detail = {
        "reason": reason, "error_type": error_type,
        "provider": provider, "model": active_model,
    }
    skip_fallback_fn = get_proxy_attr("_role_skips_fallback", _role_skips_fallback)
    skip_fallback = skip_fallback_fn(role)
    role_fallback_fn = get_proxy_attr("_get_role_fallback_llm", _get_role_fallback_llm)
    fallback_model, fb = ("", None) if skip_fallback else role_fallback_fn(role)
    if not skip_fallback:
        _raise_if_role_call_cancelled(role)
    if fb is not None:
        from backend.infra.llm.budget import (
            release_model_reservation,
            reserve_model_call,
            settle_or_review_model_reservation,
        )

        enforce_price_fn = get_proxy_attr("_enforce_model_input_price_tier", _enforce_model_input_price_tier)
        enforce_price_fn(
            args, kwargs, model_name=fallback_model,
        )
        reserve_model_call("fallback", model_name=fallback_model)
        try:
            _raise_if_role_call_cancelled(role)
            get_ctx_fn = get_proxy_attr("get_current_resolved_model", get_current_resolved_model)
            set_ctx_fn = get_proxy_attr("set_current_resolved_model", set_current_resolved_model)
            extras_fn = get_proxy_attr("model_identity_extras", model_identity_extras)
            _prev_ctx = get_ctx_fn()
            set_ctx_fn(ResolvedModelContext(
                model_id=fallback_model, provider=provider_fn(fallback_model),
                role=role, binding_source="fallback",
                request_id=(_prev_ctx.request_id if _prev_ctx else ""),
                trace_id=(_prev_ctx.trace_id if _prev_ctx else ""),
                **extras_fn(fallback_model),
            ))
            fallback = _bind_role_fallback_generation_kwargs(fb)
            _raise_if_role_call_cancelled(role)
            kwargs = provider_deadline_kwargs(fb, kwargs)
            result = await fallback.ainvoke(*args, **kwargs)
            logger.info(f"[LLM:resilience] 备用模型接管成功 ({reason})")
            try:
                from backend.observability.metrics import llm_fallback_total
                llm_fallback_total.labels(
                    primary_model=active_model, fallback_model=fallback_model,
                ).inc()
            except Exception:
                pass
            _notify_degradation("LLM_FALLBACK_USED",
                                {**error_detail, "fallback_model": fallback_model})
            return result
        except _RoleCallCancelled:
            release_model_reservation()
            raise
        except asyncio.CancelledError:
            settle_or_review_model_reservation("fallback_cancelled_possibly_billed")
            raise
        except Exception as e:
            if _is_transient(e):
                settle_or_review_model_reservation("fallback_failed_possibly_billed")
            else:
                release_model_reservation()
            fallback_error = type(e).__name__ if role == "multi_query" else e
            logger.warning("[LLM:resilience] 备用模型也失败: %s", fallback_error)
            record_failed_fn = get_proxy_attr("_record_failed_attempt", _record_failed_attempt)
            record_failed_fn(fallback_model, e, decision="fallback")

    allow_degraded = get_proxy_attr("LLM_ALLOW_DEGRADED_ANSWER", LLM_ALLOW_DEGRADED_ANSWER)
    if (
        allow_degraded
        and not skip_fallback
        and role != "multi_query"
    ):
        logger.warning(f"[LLM:resilience] 最终降级为拒答话术 ({reason})")
        _notify_degradation("LLM_DEGRADED_ANSWER", error_detail)
        return _degraded_answer(reason, error_type=error_type,
                                provider=provider, model=active_model)
    raise ModelProviderError(
        f"模型调用失败 [{error_type}] provider={provider} model={active_model}: {reason}",
        error_type=error_type, provider=provider, model=active_model,
        origin=err,
    ) from err


def _resilience_call_identity() -> tuple[str, str, str]:
    """取本次韧性调用已解析的模型/角色/Provider。"""
    from backend.infra.llm.proxy import _get_provider_for, get_active_model_name

    provider_fn = get_proxy_attr("_get_provider_for", _get_provider_for)
    active_model_fn = get_proxy_attr("get_active_model_name", get_active_model_name)
    context = get_current_resolved_model()
    if context is not None and context.model_id:
        model_name = canonical_model_id(context.model_id) or context.model_id
        provider = context.provider or provider_fn(model_name)
        return model_name, context.role or "main", provider
    model_name = active_model_fn()
    return model_name, "main", provider_fn(model_name)


def _role_runtime_policy(role: str):
    """读取由 Proxy 消费的角色运行策略。"""
    if role not in {"multi_query", "chunking"}:
        return None
    from backend.config import model_roles

    try:
        policy = model_roles.resolve_runtime_policy(role)
    except Exception:
        return model_roles.runtime_defaults(role)
    if role in {"multi_query", "chunking"} and policy.failure_policy not in {"skip", "fallback"}:
        logger.warning(
            "[LLM:proxy] 忽略轻量兜底角色的非支持 failure_policy，"
            "回落到代码默认策略"
        )
        defaults = model_roles.runtime_defaults(role)
        return model_roles.RoleRuntimeDefaults(
            fallback_model=policy.fallback_model,
            timeout_seconds=policy.timeout_seconds,
            max_retries=policy.max_retries,
            failure_policy=defaults.failure_policy,
        )
    return policy


def _role_max_retries(role: str) -> int:
    policy_fn = get_proxy_attr("_role_runtime_policy", _role_runtime_policy)
    policy = policy_fn(role)
    if policy is None:
        return int(get_proxy_attr("LLM_MAX_RETRIES", LLM_MAX_RETRIES))
    return max(0, int(policy.max_retries))


def _role_skips_fallback(role: str) -> bool:
    policy_fn = get_proxy_attr("_role_runtime_policy", _role_runtime_policy)
    policy = policy_fn(role)
    return bool(policy and policy.failure_policy != "fallback")


def _breaker_for_resolved_model(model_name: str, role: str, provider: str):
    """main 保持既有熔断器；其他角色按实际模型与 Provider 隔离。"""
    from backend.infra.circuit_breaker import (
        get_llm_model_circuit_breaker,
        llm_circuit_breaker,
    )

    if role == "main":
        return llm_circuit_breaker
    return get_llm_model_circuit_breaker(provider, model_name)


def _call_with_resilience(attr, *args, **kwargs):
    """同步韧性调用：按绑定模型预占/记录，重试与熔断按角色隔离。"""
    from backend.infra.circuit_breaker import CircuitBreakerOpenError
    from backend.infra.llm.budget import (
        RequestBudgetExceeded,
        release_model_reservation,
        reserve_model_call,
        settle_or_review_model_reservation,
    )
    from backend.infra.llm.quota import QuotaConfigurationError, QuotaExceeded

    last_err: BaseException | None = None
    note_prompt_snapshot(args)
    resilience_id_fn = get_proxy_attr("_resilience_call_identity", _resilience_call_identity)
    model_name, role, provider = resilience_id_fn()
    get_proxy_attr("_notify_call_diagnostic", _notify_call_diagnostic)(
        "call_identity", model=model_name, role=role, provider=provider,
    )
    breaker_fn = get_proxy_attr("_breaker_for_resolved_model", _breaker_for_resolved_model)
    breaker = breaker_fn(model_name, role, provider)
    max_retries_fn = get_proxy_attr("_role_max_retries", _role_max_retries)
    max_retries = max_retries_fn(role)
    backoff_base = float(get_proxy_attr("LLM_RETRY_BACKOFF_BASE", LLM_RETRY_BACKOFF_BASE))
    terminal_failure_fn = get_proxy_attr("_handle_terminal_failure", _handle_terminal_failure)
    record_failed_fn = get_proxy_attr("_record_failed_attempt", _record_failed_attempt)

    for attempt in range(max_retries + 1):
        _raise_if_role_call_cancelled(role)
        kwargs = provider_deadline_kwargs(attr, kwargs)
        call_id = uuid.uuid4().hex
        reserve_started = time.monotonic()
        try:
            reserve_model_call(
                "primary" if attempt == 0 else "retry",
                model_name=model_name,
            )
        except (
            RequestBudgetExceeded, QuotaConfigurationError, QuotaExceeded,
        ) as exc:
            if attempt > 0:
                exc.provider_attempted = True
            raise
        finally:
            from backend.infra.llm.budget import current_reservation_id

            get_proxy_attr("_notify_call_diagnostic", _notify_call_diagnostic)(
                "budget_reserve_ms",
                duration_ms=(time.monotonic() - reserve_started) * 1000,
                reservation_id=current_reservation_id(),
                attempt=attempt,
            )
        if _role_call_cancelled(role):
            release_model_reservation()
            _raise_if_role_call_cancelled(role)
        provider_started = time.monotonic()
        from backend.infra.llm.budget import current_reservation_id

        get_proxy_attr("_notify_call_diagnostic", _notify_call_diagnostic)(
            "provider_call_started",
            call_id=call_id,
            reservation_id=current_reservation_id(),
            attempt=attempt,
        )
        try:
            result = breaker.call(attr, *args, **kwargs)
            get_proxy_attr("_notify_call_diagnostic", _notify_call_diagnostic)(
                "provider_completed",
                duration_ms=(time.monotonic() - provider_started) * 1000,
                call_id=call_id,
                reservation_id=current_reservation_id(),
                attempt=attempt,
            )
            return result
        except CircuitBreakerOpenError as e:
            get_proxy_attr("_notify_call_diagnostic", _notify_call_diagnostic)(
                "provider_not_sent",
                call_id=call_id,
                error_type=type(e).__name__,
                reservation_id=current_reservation_id(),
                attempt=attempt,
            )
            release_model_reservation()
            logger.warning(f"[LLM:resilience] 熔断开路: {e}")
            if _role_call_cancelled(role):
                _raise_if_role_call_cancelled(role)
            _notify_degradation("LLM_CIRCUIT_OPEN", {"retry_in": round(e.retry_in, 1)})
            return terminal_failure_fn(
                e, args, kwargs, model_name=model_name, role=role,
            )
        except Exception as e:
            from backend.infra.llm.budget import current_reservation_id

            reservation_id = current_reservation_id()
            get_proxy_attr("_notify_call_diagnostic", _notify_call_diagnostic)(
                "provider_error",
                duration_ms=(time.monotonic() - provider_started) * 1000,
                call_id=call_id,
                error_type=type(e).__name__,
                timeout=isinstance(e, TimeoutError)
                or "timeout" in type(e).__name__.lower(),
                reservation_id=current_reservation_id(),
                attempt=attempt,
            )
            if _is_transient(e):
                settle_or_review_model_reservation("call_failed_possibly_billed")
                settlement_status = "needs_review"
            else:
                release_model_reservation()
                settlement_status = "released"
            record_failed_fn(
                model_name, e,
                decision="primary" if attempt == 0 else "retry",
            )
            get_proxy_attr("_notify_call_diagnostic", _notify_call_diagnostic)(
                "usage_unavailable",
                reservation_id=reservation_id,
                settlement_status=settlement_status,
            )
            last_err = e
            if _is_context_length_provider_error(e):
                raise _context_length_error() from e
            if _role_call_cancelled(role):
                _raise_if_role_call_cancelled(role)
            if not _is_transient(e):
                break
            if attempt >= max_retries:
                break
            delay = backoff_base ** (attempt + 1)
            logger.warning(
                f"[LLM:resilience] 瞬时错误重试 {attempt + 1}/{max_retries} "
                f"({type(e).__name__}, {delay:.1f}s 后重试)"
            )
            _wait_for_role_retry(role, delay)
    _raise_if_role_call_cancelled(role)
    return terminal_failure_fn(
        last_err, args, kwargs, model_name=model_name, role=role,
    )


async def _acall_with_resilience(attr, *args, **kwargs):
    """异步韧性调用：与同步路径使用同一模型角色隔离和用量归属。"""
    from backend.infra.circuit_breaker import CircuitBreakerOpenError
    from backend.infra.llm.budget import (
        release_model_reservation,
        reserve_model_call,
        settle_or_review_model_reservation,
    )

    last_err: BaseException | None = None
    note_prompt_snapshot(args)
    resilience_id_fn = get_proxy_attr("_resilience_call_identity", _resilience_call_identity)
    model_name, role, provider = resilience_id_fn()
    breaker_fn = get_proxy_attr("_breaker_for_resolved_model", _breaker_for_resolved_model)
    breaker = breaker_fn(model_name, role, provider)
    max_retries_fn = get_proxy_attr("_role_max_retries", _role_max_retries)
    max_retries = max_retries_fn(role)
    backoff_base = float(get_proxy_attr("LLM_RETRY_BACKOFF_BASE", LLM_RETRY_BACKOFF_BASE))
    aterminal_failure_fn = get_proxy_attr("_ahandle_terminal_failure", _ahandle_terminal_failure)
    record_failed_fn = get_proxy_attr("_record_failed_attempt", _record_failed_attempt)

    for attempt in range(max_retries + 1):
        _raise_if_role_call_cancelled(role)
        kwargs = provider_deadline_kwargs(attr, kwargs)
        reserve_model_call(
            "primary" if attempt == 0 else "retry",
            model_name=model_name,
        )
        if _role_call_cancelled(role):
            release_model_reservation()
            _raise_if_role_call_cancelled(role)
        try:
            return await breaker.acall(attr, *args, **kwargs)
        except asyncio.CancelledError:
            settle_or_review_model_reservation("call_cancelled_possibly_billed")
            raise
        except CircuitBreakerOpenError as e:
            release_model_reservation()
            logger.warning(f"[LLM:resilience] 熔断开路: {e}")
            if _role_call_cancelled(role):
                _raise_if_role_call_cancelled(role)
            _notify_degradation("LLM_CIRCUIT_OPEN", {"retry_in": round(e.retry_in, 1)})
            return await aterminal_failure_fn(
                e, args, kwargs, model_name=model_name, role=role,
            )
        except Exception as e:
            if _is_transient(e):
                settle_or_review_model_reservation("call_failed_possibly_billed")
            else:
                release_model_reservation()
            record_failed_fn(
                model_name, e,
                decision="primary" if attempt == 0 else "retry",
            )
            last_err = e
            if _is_context_length_provider_error(e):
                raise _context_length_error() from e
            if _role_call_cancelled(role):
                _raise_if_role_call_cancelled(role)
            if not _is_transient(e):
                break
            if attempt >= max_retries:
                break
            delay = backoff_base ** (attempt + 1)
            logger.warning(
                f"[LLM:resilience] 瞬时错误重试 {attempt + 1}/{max_retries} "
                f"({type(e).__name__}, {delay:.1f}s 后重试)"
            )
            await _await_role_retry(role, delay)
    _raise_if_role_call_cancelled(role)
    return await aterminal_failure_fn(
        last_err, args, kwargs, model_name=model_name, role=role,
    )
