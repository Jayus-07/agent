"""
backend/infra/llm/gateway/budget_guard.py — 速率限制与上下文预算输入门禁
"""
from __future__ import annotations

import time
from typing import Any

from backend.infra.llm.resolved_model import get_current_resolved_model
from backend.shared.logger import logger


class RateLimitError(RuntimeError):
    """LLM 限流拒绝时抛出。"""


def _try_distributed_rate_limit(user_id: str | None, qps: float, burst: float) -> bool | None:
    """尝试分布式限流。"""
    try:
        from backend.infra.redis.client import get_redis
        if get_redis() is None:
            return None
        from backend.infra.llm.distributed_rate_limiter import get_distributed_rate_limiter
        drl = get_distributed_rate_limiter()
        if not drl.acquire("global", "all", burst, qps):
            return False
        if user_id and not drl.acquire("user", user_id, max(1.0, burst / 10), max(1.0, qps / 10)):
            return False
        return True
    except Exception:
        return None


def _enforce_rate_limit(user_id: str | None) -> None:
    """根据 LLM_RATE_LIMIT_ENFORCE 配置执行限流。"""
    from backend.config.llm import (
        LLM_RATE_LIMIT_BURST,
        LLM_RATE_LIMIT_ENFORCE,
        LLM_RATE_LIMIT_QPS,
    )

    distributed = _try_distributed_rate_limit(user_id, LLM_RATE_LIMIT_QPS, LLM_RATE_LIMIT_BURST)
    if distributed is None:
        from backend.infra.llm.rate_limiter import get_rate_limiter
        passed = get_rate_limiter().acquire(user_id=user_id)
    else:
        passed = distributed

    if passed:
        return

    if LLM_RATE_LIMIT_ENFORCE == "reject":
        from backend.infra.llm.rate_limiter import get_rate_limiter
        limiter = get_rate_limiter()
        raise RateLimitError(
            f"LLM rate limit exceeded (user={user_id}), "
            f"retry after {limiter.retry_after_seconds(user_id):.1f}s"
        )

    if LLM_RATE_LIMIT_ENFORCE == "wait":
        from backend.infra.llm.rate_limiter import get_rate_limiter
        limiter = get_rate_limiter()
        wait = limiter.retry_after_seconds(user_id)
        logger.info(f"[RateLimit] wait mode: sleeping {wait:.1f}s (user={user_id})")
        time.sleep(wait)
        return

    logger.warning(f"[RateLimit] rate limited but enforce=off, proceeding (user={user_id})")


def _enforce_model_input_price_tier(
    args: tuple,
    kwargs: dict | None = None,
    extra_reserved_tokens: int = 0,
    *,
    model_name: str | None = None,
    call_method: str = "invoke",
) -> None:
    """在所有代理调用与预算预占之前限制到已登记的输入价格档位。"""
    from backend.context_budget.token_counter import (
        count_message_tokens,
        count_response_format_tokens,
        count_tokens,
        count_tool_schema_tokens,
    )
    from backend.infra.llm.pricing import model_input_price_tier_limit
    from backend.infra.llm.proxy import get_active_model_name

    if not model_name:
        resolved_model = get_current_resolved_model()
        model_name = (
            resolved_model.model_id if resolved_model else get_active_model_name()
        )
    limit = model_input_price_tier_limit(model_name)
    if limit is None:
        return

    if args:
        payload = args[0]
    else:
        payload = next(
            (kwargs[key] for key in ("input", "messages", "prompt")
             if kwargs and key in kwargs),
            None,
        )
        if payload is None:
            from backend.context_budget.errors import ContextBudgetExceededError

            raise ContextBudgetExceededError(
                used_tokens=limit + 1,
                input_budget=limit,
                stage="price_tier_unrecognized_input",
            )

    from langchain_core.messages import BaseMessage
    from langchain_core.messages.utils import convert_to_messages

    def _count_message_sequence(values) -> int:
        messages = convert_to_messages(values)
        return sum(
            count_message_tokens(message, model=model_name)
            for message in messages
        )

    def _count_payload(value) -> int:
        if isinstance(value, BaseMessage):
            return count_message_tokens(value, model=model_name)
        if isinstance(value, str):
            return count_tokens(value, model=model_name)
        if isinstance(value, dict) and isinstance(value.get("messages"), list):
            return _count_message_sequence(value["messages"])
        if isinstance(value, list):
            if not value:
                return 0
            if all(isinstance(item, str) for item in value):
                counts = [count_tokens(item, model=model_name) for item in value]
                return (
                    max(counts, default=0)
                    if call_method in {"batch", "generate", "agenerate"}
                    else sum(counts)
                )
            if all(isinstance(item, list) for item in value):
                return max(
                    (_count_message_sequence(batch) for batch in value),
                    default=0,
                )
            if all(
                isinstance(item, dict) and isinstance(item.get("messages"), list)
                for item in value
            ):
                return max(
                    (_count_payload(item) for item in value),
                    default=0,
                )
            return _count_message_sequence(value)
        if isinstance(value, tuple):
            if len(value) == 2 and isinstance(value[0], str):
                return _count_message_sequence([value])
            return _count_message_sequence(list(value))
        to_messages = getattr(value, "to_messages", None)
        if callable(to_messages):
            return _count_message_sequence(to_messages())
        to_string = getattr(value, "to_string", None)
        if callable(to_string):
            return count_tokens(str(to_string()), model=model_name)
        from backend.context_budget.errors import ContextBudgetExceededError

        raise ContextBudgetExceededError(
            used_tokens=limit + 1,
            input_budget=limit,
            stage="price_tier_unrecognized_input",
        )

    used_tokens = _count_payload(payload)

    reserved = max(0, int(extra_reserved_tokens or 0))
    if kwargs:
        reserved += count_tool_schema_tokens(kwargs.get("tools"))
        reserved += count_response_format_tokens(kwargs.get("response_format"))
    total_tokens = used_tokens + reserved
    if total_tokens > limit:
        from backend.context_budget.errors import ContextBudgetExceededError

        raise ContextBudgetExceededError(
            used_tokens=total_tokens,
            input_budget=limit,
            stage="price_tier_input_limit",
        )


def enforce_model_input_price_tier(
    args: tuple,
    *,
    model_name: str,
    kwargs: dict | None = None,
) -> None:
    """为尚未迁入统一代理的兼容调用提供相同的价格档位硬门禁。"""
    _enforce_model_input_price_tier(args, kwargs, model_name=model_name)


def _soft_overflow_enabled() -> bool:
    """D-9：压缩后仍超预算时是否优雅降级（默认 true；false=旧硬拒）。"""
    import os
    return os.getenv("CONTEXT_BUDGET_SOFT_OVERFLOW", "true").lower() == "true"


def _truncate_messages_to_budget(payload: list, budget: int) -> list:
    """D-9 优雅降级：保留首条（system）+ 最新消息；最新消息 content
    超预算则按字符比例截断（≈ tokens×4 的保守换算，中文偏安全）。"""
    from backend.memory.token_budget import count_message_tokens

    messages = list(payload)
    if not messages:
        return messages
    head, tail = messages[0], messages[-1]
    tail_tokens = count_message_tokens(tail)
    head_tokens = count_message_tokens(head) if len(messages) > 1 else 0
    allowance = max(budget - head_tokens - 256, 512)
    if tail_tokens > allowance:
        content = tail.content if isinstance(tail.content, str) else str(tail.content)
        keep_chars = max(int(len(content) * allowance / max(tail_tokens, 1) * 0.95), 256)
        truncated = content[:keep_chars] + chr(10) + "…（上下文过长，已截断尾部）"
        try:
            tail = tail.__class__(content=truncated,
                                  additional_kwargs=tail.additional_kwargs)
        except TypeError:
            import copy as _copy

            tail = _copy.copy(tail)
            tail.content = truncated
    return [head, tail] if len(messages) > 1 else [tail]


def _l5_trigger_ratio() -> float:
    """L5 触发阈值（config 活读；proxy 快路径与 manager 共用同一常量）。"""
    try:
        import backend.config as _config
        return max(0.0, min(1.0, float(getattr(
            _config, "CONTEXT_L5_TRIGGER_RATIO", 0.90))))
    except Exception:
        return 0.90


def _reject_overbudget(used: int, budget: int, stage: str) -> None:
    """最终硬门禁：低基数计数 + error 日志 + 稳定错误码（provider 0 调用）。"""
    from backend.context_budget.errors import ContextBudgetExceededError
    from backend.context_budget.metrics import record_overflow
    record_overflow(stage)
    logger.error(
        f"[LLM:preflight] {stage}: 输入无法容纳于预算 "
        f"used={used} budget={budget}，拒绝调用 provider")
    raise ContextBudgetExceededError(
        used_tokens=used, input_budget=budget, stage=stage)


def _record_preflight_failure() -> None:
    """预检自身故障计数（低基数，无 payload 信息）。"""
    try:
        from backend.observability.metrics import degradation_alerts_total
        degradation_alerts_total.labels(
            code="context_preflight_failed", level="warn").inc()
    except Exception:
        pass


def _gate_fixed_shape(
    payload: Any, budget: int, *, call_method: str = "invoke",
) -> bool:
    """不可安全改写形态的用量门禁（同步/异步入口共用，STOP B）。"""
    from backend.context_budget.token_counter import count_tokens

    def _count_text(t: Any) -> int:
        return count_tokens(t)

    if isinstance(payload, str):
        used = _count_text(payload)
        if used > budget:
            _reject_overbudget(used, budget, "str_input")
        return True

    from langchain_core.prompt_values import PromptValue
    if isinstance(payload, PromptValue):
        used = sum(count_tokens(m.content) for m in payload.to_messages())
        if used > budget:
            _reject_overbudget(used, budget, "prompt_value_input")
        return True

    if isinstance(payload, list) and payload and all(isinstance(x, str) for x in payload):
        counts = [_count_text(x) for x in payload]
        batch_like = call_method in {"batch", "generate", "agenerate"}
        used = max(counts) if batch_like else sum(counts)
        if used > budget:
            _reject_overbudget(used, budget, "batch_str_input")
        return True

    if isinstance(payload, list) and payload and all(
            isinstance(d, dict) and "role" in d and "content" in d for d in payload):
        used = sum(_count_text(d["content"]) for d in payload if isinstance(d.get("content"), str))
        if used > budget:
            _reject_overbudget(used, budget, "openai_dict_input")
        return True

    logger.debug(
        f"[LLM:preflight] 未知输入类型 {type(payload).__name__}，"
        f"跳过用量改写直接放行")
    return True


def _prepare_context_budget(
    args: tuple,
    kwargs: dict | None = None,
    extra_reserved_tokens: int = 0,
    *,
    call_method: str = "invoke",
) -> tuple:
    """统一 Prompt Preflight：LLM 调用前对 messages 走 ContextBudgetManager。"""
    from langchain_core.messages import BaseMessage

    from backend.config import CONTEXT_BUDGET_ENABLED
    if not CONTEXT_BUDGET_ENABLED or not args:
        return args
    payload = args[0]

    from backend.context_budget import context_budget
    from backend.context_budget.errors import ContextBudgetExceededError

    reserved = max(0, int(extra_reserved_tokens or 0))
    if kwargs:
        try:
            from backend.context_budget.token_counter import (
                count_response_format_tokens,
                count_tool_schema_tokens,
            )
            reserved += count_tool_schema_tokens(kwargs.get("tools"))
            reserved += count_response_format_tokens(
                kwargs.get("response_format"))
        except Exception:
            pass

    budget = context_budget.get_input_budget(extra_reserved_tokens=reserved)

    # 形态 1：list[BaseMessage]
    if isinstance(payload, list) and payload \
            and all(isinstance(m, BaseMessage) for m in payload):
        from backend.memory.token_budget import count_message_tokens
        total = sum(count_message_tokens(m) for m in payload)
        l5_ratio = _l5_trigger_ratio()
        if total <= budget * l5_ratio:
            return args
        try:
            prepared = context_budget.prepare_llm_context(
                messages=payload, extra_reserved_tokens=reserved)
        except ContextBudgetExceededError:
            raise
        except Exception:
            _record_preflight_failure()
            logger.error(
                "[LLM:preflight] 预检失败，无法证明预算内，拒绝发送（fail-closed）", exc_info=True)
            raise
        if prepared.overflow:
            if _soft_overflow_enabled():
                messages = _truncate_messages_to_budget(payload, budget)
                from backend.context_budget.metrics import record_overflow
                record_overflow("soft_truncate")
                logger.warning(
                    f"[LLM:preflight] overflow 优雅降级: {total} tokens → "
                    f"截断至预算 {budget}（保留 system+最新消息）")
                return (messages, *args[1:])
            _reject_overbudget(
                prepared.usage.used_tokens if prepared.usage else total,
                budget, "final_gate")
        logger.info(
            f"[LLM:preflight] 上下文超预算已压缩: {total}→"
            f"{prepared.usage.used_tokens} tokens (budget={budget})")
        return (prepared.messages, *args[1:])

    # 形态 2：批量 list[list[BaseMessage]]
    if isinstance(payload, list) and payload and all(
            isinstance(item, list) and item
            and all(isinstance(m, BaseMessage) for m in item)
            for item in payload):
        gated: list[list] = []
        for item in payload:
            prepared = context_budget.prepare_llm_context(
                messages=item, extra_reserved_tokens=reserved)
            if prepared.overflow:
                _reject_overbudget(
                    prepared.usage.used_tokens if prepared.usage else 0,
                    budget, "final_gate_batch")
            gated.append(prepared.messages)
        return (gated, *args[1:])

    # 形态 3~5：不可安全改写的输入
    if _gate_fixed_shape(payload, budget, call_method=call_method):
        return args
    return args


def _preflight_context(
    args: tuple,
    kwargs: dict | None = None,
    extra_reserved_tokens: int = 0,
    *,
    call_method: str = "invoke",
) -> tuple:
    """先按上下文预算裁剪，再执行统一价格档位的硬输入上限。"""
    from backend.config import CONTEXT_BUDGET_ENABLED

    if not CONTEXT_BUDGET_ENABLED or not args:
        _enforce_model_input_price_tier(
            args, kwargs, extra_reserved_tokens, call_method=call_method,
        )
        return args
    prepared = _prepare_context_budget(
        args, kwargs, extra_reserved_tokens, call_method=call_method,
    )
    _enforce_model_input_price_tier(
        prepared, kwargs, extra_reserved_tokens, call_method=call_method,
    )
    return prepared


async def _prepare_context_budget_async(
    args: tuple,
    kwargs: dict | None = None,
    extra_reserved_tokens: int = 0,
    *,
    call_method: str = "ainvoke",
) -> tuple:
    """异步版统一 Prompt Preflight：L5 异步压缩。"""
    from langchain_core.messages import BaseMessage

    from backend.config import CONTEXT_BUDGET_ENABLED
    if not CONTEXT_BUDGET_ENABLED or not args:
        return args
    payload = args[0]

    from backend.context_budget import context_budget
    from backend.context_budget.errors import ContextBudgetExceededError

    reserved = max(0, int(extra_reserved_tokens or 0))
    if kwargs:
        try:
            from backend.context_budget.token_counter import (
                count_response_format_tokens,
                count_tool_schema_tokens,
            )
            reserved += count_tool_schema_tokens(kwargs.get("tools"))
            reserved += count_response_format_tokens(
                kwargs.get("response_format"))
        except Exception:
            pass

    budget = context_budget.get_input_budget(extra_reserved_tokens=reserved)

    if isinstance(payload, list) and payload \
            and all(isinstance(m, BaseMessage) for m in payload):
        from backend.memory.token_budget import count_message_tokens
        total = sum(count_message_tokens(m) for m in payload)
        l5_ratio = _l5_trigger_ratio()
        if total <= budget * l5_ratio:
            return args
        try:
            prepared = await context_budget.aprepare_llm_context(
                messages=payload, extra_reserved_tokens=reserved)
        except ContextBudgetExceededError:
            raise
        except Exception:
            _record_preflight_failure()
            logger.error(
                "[LLM:preflight:async] 预检失败，无法证明预算内，拒绝发送（fail-closed）", exc_info=True)
            raise
        if prepared.overflow:
            if _soft_overflow_enabled():
                messages = _truncate_messages_to_budget(payload, budget)
                from backend.context_budget.metrics import record_overflow
                record_overflow("soft_truncate_async")
                logger.warning(
                    f"[LLM:preflight:async] overflow 优雅降级: {total} tokens → "
                    f"截断至预算 {budget}（保留 system+最新消息）")
                return (messages, *args[1:])
            _reject_overbudget(
                prepared.usage.used_tokens if prepared.usage else total,
                budget, "final_gate_async")
        logger.info(
            f"[LLM:preflight:async] 上下文超预算已压缩: {total}→"
            f"{prepared.usage.used_tokens} tokens (budget={budget})")
        return (prepared.messages, *args[1:])

    if isinstance(payload, list) and payload and all(
            isinstance(item, list) and item
            and all(isinstance(m, BaseMessage) for m in item)
            for item in payload):
        gated: list[list] = []
        for item in payload:
            prepared = await context_budget.aprepare_llm_context(
                messages=item, extra_reserved_tokens=reserved)
            if prepared.overflow:
                _reject_overbudget(
                    prepared.usage.used_tokens if prepared.usage else 0,
                    budget, "final_gate_batch_async")
            gated.append(prepared.messages)
        return (gated, *args[1:])

    if _gate_fixed_shape(payload, budget, call_method=call_method):
        return args
    return args


async def _apreflight_context(
    args: tuple,
    kwargs: dict | None = None,
    extra_reserved_tokens: int = 0,
    *,
    call_method: str = "ainvoke",
) -> tuple:
    """异步版先按预算裁剪，再校验价格档位输入上限。"""
    from backend.config import CONTEXT_BUDGET_ENABLED

    if not CONTEXT_BUDGET_ENABLED or not args:
        _enforce_model_input_price_tier(
            args, kwargs, extra_reserved_tokens, call_method=call_method,
        )
        return args
    prepared = await _prepare_context_budget_async(
        args, kwargs, extra_reserved_tokens, call_method=call_method,
    )
    _enforce_model_input_price_tier(
        prepared, kwargs, extra_reserved_tokens, call_method=call_method,
    )
    return prepared
