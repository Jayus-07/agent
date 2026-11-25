"""
backend/infra/llm/gateway/accounting.py — Token 计费、用量累加与财务审计落账
"""
from __future__ import annotations

import contextvars as _contextvars
import re
import time
from typing import Any

from backend.config import LLM_MODEL
from backend.infra.llm.models import (
    canonical_model_id,
    compute_cost_usd,
    resolve_provider,
)
from backend.infra.llm.resolved_model import (
    ResolvedModelContext,
    get_current_resolved_model,
)
from backend.infra.llm.gateway.base import get_proxy_attr
from backend.infra.llm.gateway.context import _request_model_var
from backend.shared.logger import logger


def _get_provider_for(model: str) -> str:
    fn = get_proxy_attr("_get_provider_for")
    if fn is not None:
        return fn(model)
    return resolve_provider(model)


def _notify_call_diagnostic(event: str, **kwargs: Any) -> None:
    fn = get_proxy_attr("_notify_call_diagnostic")
    if fn is not None:
        fn(event, **kwargs)


def _strip_think(text: str, *, strip: bool = True) -> str:
    """剥离模型输出的 <think>...</think> 推理块"""
    import re
    text = re.sub(r'<think>[\s\S]*?</think>\s*', '', text)
    text = re.sub(r'<think>[\s\S]*', '', text)
    return text.strip() if strip else text


# =====================================================
# 最近一次 LLM 调用元数据（token / finish_reason / cost）
# =====================================================
# P1 并发隔离：模块级 dict 会被并发请求互相覆盖（A 的 token 被 B 覆盖，
# tracer 读到 B 的）。改用 ContextVar 按调用上下文隔离；
# 读写必须整体 set()/get()（不可变替换），禁止对 .get() 返回的 dict
# 做 clear()/update()（会改到共享对象）。

_last_tokens_var: _contextvars.ContextVar = _contextvars.ContextVar(
    "llm_last_tokens", default={},
)
_last_call_meta_var: _contextvars.ContextVar = _contextvars.ContextVar(
    "llm_last_call_meta", default={},
)

# P0 Token 看板：per-turn 用量累加器（按调用上下文隔离）。
# 一轮对话会产生多次 LLM 调用（planner/worker/reporter...），单次
# _last_tokens_var 会被覆盖，这里按模型累加整轮用量，trace finish 时兜底读取。
_turn_usage_var: _contextvars.ContextVar = _contextvars.ContextVar(
    "llm_turn_usage", default=None,
)

def _accumulate_turn_usage(model: str, p: int, c: int, t: int,
                           cached: int, reasoning: int, cost: float,
                           provider: str | None = None) -> None:
    """把单次 LLM 调用用量累加到当前上下文的 per-turn 汇总。

    provider 由调用方显式传入（STOP C）：此前这里恒按字符串推断，
    注册表 miss 时把豆包记成 ollama（STOP A 实机 R2）。
    """
    if not model:
        model = LLM_MODEL
    resolved_provider = provider or _get_provider_for(model)
    acc = dict(_turn_usage_var.get() or {})
    entry = dict(acc.get(model) or {
        "provider": resolved_provider,
        "calls": 0,
        "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
        "cached_tokens": 0, "reasoning_tokens": 0, "cost_cny": 0.0,
    })
    # 模型已在累计中但 provider 此前推断错误时，以本次权威值纠正
    entry.setdefault("provider", resolved_provider)
    if provider:
        entry["provider"] = provider
    entry["calls"] = int(entry["calls"]) + 1
    entry["prompt_tokens"] = int(entry["prompt_tokens"]) + int(p or 0)
    entry["completion_tokens"] = int(entry["completion_tokens"]) + int(c or 0)
    entry["total_tokens"] = int(entry["total_tokens"]) + int(t or 0)
    entry["cached_tokens"] = int(entry["cached_tokens"]) + int(cached or 0)
    entry["reasoning_tokens"] = int(entry["reasoning_tokens"]) + int(reasoning or 0)
    # cost 入参 = BillingResult.billed_cost_cny（恒 CNY，2026-10-07 收口）
    entry["cost_cny"] = float(entry["cost_cny"]) + float(cost or 0.0)
    acc[model] = entry
    _turn_usage_var.set(acc)


def get_turn_usage() -> dict:
    """读取当前上下文本轮 LLM 用量汇总：{model: {calls, tokens..., cost_cny}}。

    cost_cny = BillingResult.billed_cost_cny 累加（记账本位币唯一口径）。
    """
    return dict(_turn_usage_var.get() or {})


def reset_turn_usage() -> None:
    """清空本轮用量（trace start/finish 时调用，防线程池复用线程串味）。"""
    _turn_usage_var.set(None)


def _usage_component() -> str:
    """usage 明细行的 component 归因（2026-09-18 窗口级 token 核算）。

    客服域轮次（cs_prefilter 命中时已在 trace.tags 打 cs_target）→
    "customer_service"；其余主问答/工具链路沿用 PG 列默认 "llm"。
    token 看板 list_calls/dashboard 已支持按 component 过滤，无需改 store。
    软失败：trace 不可达时回退默认值，不影响主链路。
    """
    try:
        from backend.observability.tracer import trace_collector
        t = trace_collector.current()
        if t is not None and t.tags.get("cs_target"):
            return "customer_service"
    except Exception:
        pass
    return "llm"


def _failed_attempt_http_status(err: BaseException) -> int | None:
    """提取失败 attempt 的 HTTP 状态码（requests/httpx/openai 兼容），无则 None。

    openai.APIStatusError / httpx.HTTPStatusError 带 status_code 属性；
    requests.HTTPError 经 .response.status_code 暴露。仅接受 int，
    避免把字符串/非状态码属性误拼进 finish_reason。
    """
    status = getattr(err, "status_code", None)
    if not isinstance(status, int):
        status = getattr(getattr(err, "response", None), "status_code", None)
    return status if isinstance(status, int) else None


def _record_failed_attempt(
    model_name: str, err: BaseException,
    decision: str = "primary", duration_ms: float = 0.0,
) -> None:
    """失败 attempt 也落一行 usage（STOP C / C11）。

    重试/终态失败的 attempt 在 provider 侧可能已产生真实计费（tokens 未知，
    记 0），此前完全不留痕 —— fallback 分析与成本对账都缺第一跳。行语义：
    tokens/cost 全 0、finish_reason 带异常类型、cost_status=unpriced、
    decision 由韧性链标注（primary/retry/fallback）。
    绝不抛错（usage 软失败原则，与 _record_tokens 一致）。
    """
    ledger_written = False
    try:
        canonical = canonical_model_id(model_name) or model_name
        provider = _get_provider_for(canonical)
        from backend.infra.llm.error_taxonomy import classify_model_error
        from backend.infra.llm.pricing import COST_STATUS_UNPRICED

        error_type = classify_model_error(err)
        # HTTP 状态拼入 finish_reason（如 error:APIConnectionError:429）：
        # 失败 attempt 无 span，这是 HTTP 响应口径唯一可归因的落点。
        http_status = _failed_attempt_http_status(err)
        finish_reason = f"error:{type(err).__name__}"
        if http_status is not None:
            finish_reason = f"{finish_reason}:{http_status}"
        try:
            from backend.observability.llm_usage_store import (
                current_usage_attribution,
                get_llm_usage_store,
            )
            attribution = current_usage_attribution()
            ledger_written = bool(get_llm_usage_store().record({
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
                             + f".{int(time.time() % 1 * 1000):03d}Z",
                "trace_id": attribution["trace_id"],
                "request_id": attribution["request_id"],
                "session_id": attribution["session_id"],
                "user_id": attribution["user_id"],
                "tenant_id": attribution["tenant_id"],
                "component": _usage_component(),
                "model": canonical,
                "provider": provider,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                # ── Billing V2 失败留痕行：调用可能已计费但用量不可知 ──
                "billing_schema_version": 2,
                "native_cost": 0.0,
                "native_currency": "",
                "billed_cost_cny": 0.0,
                "price_version": "",
                "pricing_source": "unpriced",
                "usage_source": "unavailable",
                "cost_usd": 0.0,
                "total_cost": 0.0,
                "cost_status": COST_STATUS_UNPRICED,
                "currency": "",
                "requested_model": "",
                "upstream_model_id": "",
                "binding_source": "failed_attempt",
                "finish_reason": finish_reason,
                "decision": decision,
                "duration_ms": round(duration_ms, 1),
                "run_id": attribution["run_id"],
                "step_id": attribution["step_id"],
                "role": attribution["role"],
                "stage": attribution["stage"],
            }))
        except Exception:
            pass
        _notify_call_diagnostic(
            "usage_ledger_write_finished", written=ledger_written,
        )
        try:
            from backend.observability.metrics import (
                llm_failures_total,
                llm_requests_total,
            )
            llm_failures_total.labels(model=canonical, error_type=error_type).inc()
            llm_requests_total.labels(
                model=canonical, provider=provider, status="error",
            ).inc()
        except Exception:
            pass
    except Exception:
        logger.debug("[LLM:proxy] 失败 attempt 用量记录异常（忽略）", exc_info=True)



def _record_tokens(
    result,
    duration_ms: float | None = None,
    model_name: str | None = None,
):
    """从 LLM 返回值提取 token + finish_reason + cost，存为 dict 供 tracer 读取。

    无 token_usage 时清空 _last_tokens_var 和 _last_call_meta_var。
    duration_ms: 调用方（wrapper）计得的本次 LLM 调用耗时（含重试/熔断等待）。
    """
    try:
        # C4 统一解析层：usage 数量提取只此一份实现（tracer.parse_tokens 同源委托）
        from backend.observability.usage_parse import extract_usage_payload

        tu = extract_usage_payload(result)
        # ChatAnthropic 用 input_tokens/output_tokens，ChatOpenAI 用 prompt_tokens/completion_tokens
        p = tu.get("prompt_tokens", tu.get("input_tokens", 0))
        c = tu.get("completion_tokens", tu.get("output_tokens", 0))
        t = tu.get("total_tokens", p + c)
        if not t:
            _last_tokens_var.set({})
            _last_call_meta_var.set({})
            from backend.infra.llm.budget import current_reservation_id

            reservation_id = current_reservation_id()
            try:
                from backend.infra.llm.budget import record_model_usage

                settlement_status = record_model_usage(
                    total_tokens=0,
                    review_reason="provider_usage_missing",
                )
            except Exception:
                settlement_status = "accounting_error"
            _notify_call_diagnostic(
                "usage_unavailable",
                reservation_id=reservation_id,
                settlement_status=settlement_status,
            )
            return

        # 细粒度用量：缓存命中 / 推理 token。
        # LangChain 统一在 usage_metadata.input_token_details（cache_read/cache_creation）
        # 与 output_token_details（reasoning）透传；上游未返回时为 0。
        # ⚠️ 计费口径（2026-09-22 成本计量第一阶段）：LangChain 三家 Provider
        # （OpenAI/Anthropic/DashScope）的 input_token_details.cache_read 均为
        # prompt/input tokens 的**子集** —— 按 input 价计费的部分必须是
        # billable = input - cached，否则缓存部分会被 input 与 cache_read 双算。
        in_details = tu.get("input_token_details") or {}
        out_details = tu.get("output_token_details") or {}
        cached = int(in_details.get("cache_read", 0) or 0)
        reasoning = int(out_details.get("reasoning", 0) or 0)
        billable_input = max(int(p) - cached, 0)
        _last_tokens_var.set({
            "prompt_tokens": p, "completion_tokens": c, "total_tokens": t,
            "cached_tokens": cached, "reasoning_tokens": reasoning,
        })

        # 模型身份归属（STOP C 重排 / C2：调用方显式声明最权威）：
        # 显式 model_name 参数（补计量链声明的登记名）> ResolvedModelContext
        #（调用时解析）> response 回传名（纯观测值，只落 upstream_model_id）。
        # 此前 response 最优先，旁路补计量链被上游回传名（如豆包
        # doubao-seed-2-0-mini-260428）带偏：归一 miss → provider 误判
        # ollama → cost=0（实机 1174 行错位）。
        # 显式名双匹配归一失败（是残缺 upstream 别名而非登记名）时才信 ctx。
        ctx = get_current_resolved_model()
        observed_model = ""
        if hasattr(result, "response_metadata") and result.response_metadata:
            observed_model = (result.response_metadata.get("model_name", "")
                              or result.response_metadata.get("model", ""))
        explicit_name = str(model_name or "").strip()
        if explicit_name:
            canonical = canonical_model_id(explicit_name)
            if canonical == explicit_name and ctx is not None and ctx.model_id \
                    and ctx.model_id != explicit_name:
                canonical = ctx.model_id
        elif ctx is not None:
            canonical = canonical_model_id(ctx.model_id)
        else:
            canonical = canonical_model_id(observed_model)
        model = canonical or LLM_MODEL
        upstream_model = observed_model or explicit_name or (
            ctx.model_id if ctx is not None else "")
        # provider 归属：ctx 在场且与最终归属一致时用 ctx.provider（调用时
        # 解析的权威值）；否则按 canonical 双匹配直查（内部不再有 ollama 盲猜
        # 优先——已登记别名可正确归一）。
        if ctx is not None and ctx.model_id == model:
            provider_resolved = ctx.provider
        else:
            provider_resolved = _get_provider_for(model)
        requested_override = ""
        try:
            requested_override = str(_request_model_var.get() or "")
        except Exception:
            requested_override = ""

        # Prometheus 指标：LLM token 用量
        try:
            from backend.observability.metrics import llm_tokens_total
            if model and p:
                llm_tokens_total.labels(model=model, direction="prompt").inc(p)
            if model and c:
                llm_tokens_total.labels(model=model, direction="completion").inc(c)
        except Exception:
            pass  # 指标记录失败不影响核心链路

        finish_reason = "unknown"
        if hasattr(result, "response_metadata") and result.response_metadata:
            finish_reason = result.response_metadata.get(
                "finish_reason",
                result.response_metadata.get("stop_reason", "unknown"),
            )
        from backend.infra.llm.budget import current_request_budget
        from backend.infra.llm.pricing import (
            PRICING_SOURCE_UNPRICED,
            USAGE_SOURCE_PROVIDER,
            NormalizedUsage,
            price_usage,
        )

        budget_state = current_request_budget()
        enforce = bool(
            budget_state
            and budget_state.mode == "enforce"
            and budget_state.quota_store is not None
        )
        # 唯一计费入口（2026-10-07 收口）：observe/enforce 同算法同金额，
        # 差异只在 cost_status（缺价时 price_unknown vs estimated）与门禁行为。
        # BillingResult.billed_cost_cny 恒为 CNY——预算结算、llm_usage、
        # trace、看板全部消费同一结果，任何一层不得重新算钱或二次换汇。
        billing = price_usage(
            model, "llm",
            NormalizedUsage(
                input_tokens=int(p),
                cached_input_tokens=cached,
                cache_write_tokens=int(in_details.get("cache_creation", 0) or 0),
                output_tokens=int(c),
                reasoning_tokens=reasoning,
                tool_calls=int(tu.get("tool_calls", 0) or 0),
            ),
            enforce=enforce,
            usage_source=USAGE_SOURCE_PROVIDER,
        )
        cost_decimal = billing.billed_cost_cny
        cost_status = billing.cost_status
        # currency 列语义（V2）= 供应商原生报价币种；记账金额看 billed_cost_cny
        currency = billing.native_currency
        cost_breakdown = {
            "input_cost": float(billing.input_cost_cny),
            "cached_input_cost": float(billing.cached_input_cost_cny),
            "output_cost": float(billing.output_cost_cny),
            # 单价快照 = 原生币种；无价（fallback/unpriced）为 None → 落库 NULL
            "input_unit_price": (
                float(billing.input_unit_price)
                if billing.input_unit_price is not None else None
            ),
            "output_unit_price": (
                float(billing.output_unit_price)
                if billing.output_unit_price is not None else None
            ),
            "cache_input_unit_price": (
                float(billing.cache_input_unit_price)
                if billing.cache_input_unit_price is not None else None
            ),
        }
        cost = float(cost_decimal)
        native_cost = float(billing.native_cost)
        try:
            from backend.infra.llm.budget import record_model_usage

            settlement_status = record_model_usage(
                prompt_tokens=p,
                completion_tokens=c,
                total_tokens=t,
                cost=(
                    None if billing.pricing_source == PRICING_SOURCE_UNPRICED
                    else cost_decimal
                ),
                review_reason=(
                    "billing_price_unavailable"
                    if billing.pricing_source == PRICING_SOURCE_UNPRICED
                    else ""
                ),
            )
        except Exception:
            # 预算记录是观测/门禁辅助，不能反向破坏模型主链路。
            settlement_status = "accounting_error"
        _last_call_meta_var.set({
            "prompt_tokens": p,
            "completion_tokens": c,
            "total_tokens": t,
            "billable_input_tokens": billable_input,
            "cached_tokens": cached,
            "reasoning_tokens": reasoning,
            "finish_reason": finish_reason,
            # 币种契约（V2）：cost_usd 只在原生币种确为 USD 时有值；
            # 记账金额一律看 cost_cny / billed_cost_cny。
            "cost_usd": native_cost if billing.native_currency == "USD" else 0.0,
            "cost_cny": cost,
            "native_cost": native_cost,
            "native_currency": billing.native_currency,
            "fx_rate": float(billing.fx_rate) if billing.fx_rate is not None else None,
            "price_version": billing.price_version or "",
            "pricing_source": billing.pricing_source,
            "usage_source": billing.usage_source,
            "input_cost": cost_breakdown["input_cost"],
            "cached_input_cost": cost_breakdown["cached_input_cost"],
            "output_cost": cost_breakdown["output_cost"],
            "input_unit_price": cost_breakdown["input_unit_price"],
            "output_unit_price": cost_breakdown["output_unit_price"],
            "cache_input_unit_price": cost_breakdown["cache_input_unit_price"],
            "cost_status": cost_status,
            "settlement_status": settlement_status,
            "currency": currency,
            "model": model,
            "canonical_model_id": model,
            "upstream_model_id": upstream_model or "",
            "requested_model": requested_override,
            "provider": provider_resolved,
            "configured_model_id": ctx.model_id if ctx is not None else "",
            "model_role": ctx.role if ctx is not None else "",
            "binding_source": ctx.binding_source if ctx is not None else "",
            "duration_ms": round(duration_ms, 1) if duration_ms is not None else 0.0,
        })
        _accumulate_turn_usage(model, p, c, t, cached, reasoning, cost,
                               provider=provider_resolved)

        # 统一 llm_call span 出口（2026-10-30）：与上面 llm_usage 行同源同值，
        # 一个成功调用 = 恰一行 usage + 恰一个 span，不双计（口径见模块注释）。
        from backend.observability.llm_call_span import record_llm_call_span

        record_llm_call_span(
            metrics=dict(_last_call_meta_var.get() or {}),
            result=result,
            duration_ms=duration_ms or 0.0,
        )

        # Token 看板明细落库（每调用一行，软失败不影响主链路）
        usage_ledger_written = False
        try:
            from backend.observability.llm_usage_store import (
                current_usage_attribution,
                get_llm_usage_store,
            )
            from backend.infra.llm.budget import current_call_decision
            attribution = current_usage_attribution()
            usage_ledger_written = bool(get_llm_usage_store().record({
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
                             + f".{int(time.time() % 1 * 1000):03d}Z",
                "trace_id": attribution["trace_id"],
                "request_id": attribution["request_id"],
                "session_id": attribution["session_id"],
                "user_id": attribution["user_id"],
                "tenant_id": attribution["tenant_id"],
                "component": _usage_component(),
                "model": model,
                # ctx 在场 = 调用时解析的权威 provider；缺失才按 canonical 直查
                "provider": provider_resolved,
                "prompt_tokens": p,
                "completion_tokens": c,
                "total_tokens": t,
                "billable_input_tokens": billable_input,
                "cached_tokens": cached,
                "reasoning_tokens": reasoning,
                # ── Billing V2（2026-10-07 冻结契约）：唯一权威字段 ──
                "billing_schema_version": 2,
                "native_cost": native_cost,
                "native_currency": billing.native_currency,
                "billed_cost_cny": cost,
                "fx_rate": (float(billing.fx_rate)
                            if billing.fx_rate is not None else None),
                "price_version": billing.price_version or "",
                "pricing_source": billing.pricing_source,
                "usage_source": billing.usage_source,
                "input_cost": cost_breakdown["input_cost"],
                "cached_input_cost": cost_breakdown["cached_input_cost"],
                "output_cost": cost_breakdown["output_cost"],
                "reasoning_cost_cny": float(billing.reasoning_cost_cny),
                "cache_write_cost_cny": float(billing.cache_write_cost_cny),
                "tool_call_cost_cny": float(billing.tool_call_cost_cny),
                "cost_status": cost_status,
                # 兼容列过渡语义：currency=原生币种；cost_usd 仅原生 USD 时有值，
                # CNY 原生行为 0（真相在 billed_cost_cny，读层 V2-aware）。
                "cost_usd": native_cost if billing.native_currency == "USD" else 0.0,
                "currency": currency,
                "total_cost": cost,
                # 身份链（STOP C C2）+ 单价快照（原生币种）
                "requested_model": requested_override,
                "upstream_model_id": upstream_model or "",
                "binding_source": ctx.binding_source if ctx is not None else "",
                "input_unit_price": cost_breakdown["input_unit_price"],
                "output_unit_price": cost_breakdown["output_unit_price"],
                "cache_input_unit_price": cost_breakdown["cache_input_unit_price"],
                "reasoning_unit_price": (
                    float(billing.reasoning_unit_price)
                    if billing.reasoning_unit_price is not None else None
                ),
                "cache_write_unit_price": (
                    float(billing.cache_write_unit_price)
                    if billing.cache_write_unit_price is not None else None
                ),
                "tool_call_unit_price": (
                    float(billing.tool_call_unit_price)
                    if billing.tool_call_unit_price is not None else None
                ),
                "finish_reason": finish_reason,
                "decision": current_call_decision(),
                "duration_ms": round(duration_ms, 1) if duration_ms is not None else 0.0,
                # 业务归因（M5）：skill/tool/域维度，随 attribution 落库
                "skill_id": attribution["skill_id"],
                "tool_id": attribution["tool_id"],
                "agent_domain": attribution["agent_domain"],
                "run_id": attribution["run_id"],
                "step_id": attribution["step_id"],
                "role": attribution["role"],
                "stage": attribution["stage"],
            }))
            # C15：调用计数（低基数 label：model/provider/status）
            try:
                from backend.observability.metrics import llm_requests_total
                llm_requests_total.labels(
                    model=model, provider=provider_resolved, status="ok",
                ).inc()
            except Exception:
                pass
        except Exception:
            pass
        _notify_call_diagnostic(
            "usage_ledger_write_finished",
            written=usage_ledger_written,
        )
    except Exception:
        _last_tokens_var.set({})
        _last_call_meta_var.set({})


def record_llm_result(
    result,
    *,
    duration_ms: float | None = None,
    model_name: str | None = None,
) -> None:
    """记录绕过 ``_LLMProxy`` 的专用角色调用。

    入库阶段按角色获取的是底层 LangChain 实例，不能假设所有调用都经过
    全局 ``llm`` 代理；专用富化入口用这个显式适配器复用同一计量口径。
    """
    _record_tokens(result, duration_ms=duration_ms, model_name=model_name)


def _settle_estimated_stream_usage(
    meter, call_args, duration_ms: float, mode: str,
) -> bool:
    """流式 usage 缺失时按本地估算结算（2026-10-02 企业口径）。

    能估算（有输出文本或可识别的 prompt 入参）→ 按 estimated 结算并落
    llm_usage（binding_source='estimated'），预占正常结算，不进待对账；
    无法估算（零输出且 prompt 不可识别）→ 返回 False，调用方维持原
    needs_review 路径。全程软失败：任何异常都返回 False 不反噬主链路。
    """
    try:
        from backend.config.llm import LLM_USAGE_ESTIMATION_ENABLED
        if not LLM_USAGE_ESTIMATION_ENABLED:
            return False
        from backend.infra.llm.usage_estimator import (
            StreamTextMeter, estimate_prompt_tokens,
        )
        if not isinstance(meter, StreamTextMeter):
            return False
        prompt_tokens = estimate_prompt_tokens(call_args[0] if call_args else None)
        completion_tokens = meter.completion_tokens
        if prompt_tokens <= 0 and completion_tokens <= 0:
            return False
        total_tokens = prompt_tokens + completion_tokens

        # 模型归属与 _record_tokens 同口径：调用时解析的 ctx 最权威
        ctx = get_current_resolved_model()
        canonical = canonical_model_id(ctx.model_id) if ctx is not None else ""
        model = canonical or LLM_MODEL
        if ctx is not None and ctx.model_id == model:
            provider_resolved = ctx.provider
        else:
            provider_resolved = _get_provider_for(model)

        from backend.infra.llm.pricing import (
            USAGE_SOURCE_ESTIMATED, NormalizedUsage, price_usage,
        )
        from backend.infra.llm.budget import current_request_budget

        _budget_state = current_request_budget()
        _enforce = bool(
            _budget_state
            and _budget_state.mode == "enforce"
            and _budget_state.quota_store is not None
        )
        billing = price_usage(
            model, "llm",
            NormalizedUsage(input_tokens=prompt_tokens, output_tokens=completion_tokens),
            enforce=_enforce,
            usage_source=USAGE_SOURCE_ESTIMATED,
        )
        cost_decimal = billing.billed_cost_cny
        cost_status = billing.cost_status
        _currency = billing.native_currency

        # 预算结算：估算值视为本次调用的实际用量（保守性由 reserved 上限保证）；
        # 结算金额 = BillingResult.billed_cost_cny（恒 CNY，与 observe/enforce 同源）
        from backend.infra.llm.budget import (
            current_call_decision, record_model_usage,
        )
        record_model_usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost=cost_decimal,
        )

        _last_tokens_var.set({
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "cached_tokens": 0, "reasoning_tokens": 0,
        })
        cost = float(cost_decimal)
        _native_cost = float(billing.native_cost)
        _last_call_meta_var.set({
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "billable_input_tokens": prompt_tokens,
            "cached_tokens": 0, "reasoning_tokens": 0,
            "finish_reason": "estimated_no_usage",
            # 币种契约（V2）：记账金额看 cost_cny；cost_usd 仅原生 USD 时有值
            "cost_usd": _native_cost if billing.native_currency == "USD" else 0.0,
            "cost_cny": cost,
            "native_cost": _native_cost,
            "native_currency": billing.native_currency,
            "fx_rate": float(billing.fx_rate) if billing.fx_rate is not None else None,
            "price_version": billing.price_version or "",
            "pricing_source": billing.pricing_source,
            "usage_source": billing.usage_source,
            "input_cost": float(billing.input_cost_cny),
            "cached_input_cost": 0.0,
            "output_cost": float(billing.output_cost_cny),
            "input_unit_price": (
                float(billing.input_unit_price)
                if billing.input_unit_price is not None else None
            ),
            "output_unit_price": (
                float(billing.output_unit_price)
                if billing.output_unit_price is not None else None
            ),
            "cache_input_unit_price": None,
            "cost_status": cost_status,
            "currency": _currency,
            "model": model,
            "canonical_model_id": model,
            "upstream_model_id": ctx.model_id if ctx is not None else "",
            "requested_model": "",
            "provider": provider_resolved,
            "configured_model_id": ctx.model_id if ctx is not None else "",
            "model_role": ctx.role if ctx is not None else "",
            "binding_source": "estimated",
            "duration_ms": round(duration_ms, 1) if duration_ms is not None else 0.0,
        })

        # 估算结算行同样出 llm_call span（usage_source=estimated 标注来源；
        # StreamTextMeter 不缓存文本，response 快照如实留空）
        from backend.observability.llm_call_span import record_llm_call_span

        record_llm_call_span(
            metrics=dict(_last_call_meta_var.get() or {}),
            result=None,
            duration_ms=duration_ms or 0.0,
        )

        # llm_usage 明细落库（estimated 行：聚合口径与供应商 diff 的锚点）
        try:
            from backend.observability.llm_usage_store import (
                current_usage_attribution, get_llm_usage_store,
            )
            attribution = current_usage_attribution()
            get_llm_usage_store().record({
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
                             + f".{int(time.time() % 1 * 1000):03d}Z",
                "trace_id": attribution["trace_id"],
                "request_id": attribution["request_id"],
                "session_id": attribution["session_id"],
                "user_id": attribution["user_id"],
                "tenant_id": attribution["tenant_id"],
                "component": _usage_component(),
                "model": model,
                "provider": provider_resolved,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "billable_input_tokens": prompt_tokens,
                "cached_tokens": 0, "reasoning_tokens": 0,
                # ── Billing V2（估算行：usage_source=estimated）──
                "billing_schema_version": 2,
                "native_cost": _native_cost,
                "native_currency": billing.native_currency,
                "billed_cost_cny": cost,
                "fx_rate": (float(billing.fx_rate)
                            if billing.fx_rate is not None else None),
                "price_version": billing.price_version or "",
                "pricing_source": billing.pricing_source,
                "usage_source": billing.usage_source,
                "input_cost": float(billing.input_cost_cny),
                "cached_input_cost": 0.0,
                "output_cost": float(billing.output_cost_cny),
                "reasoning_cost_cny": 0.0,
                "cache_write_cost_cny": 0.0,
                "tool_call_cost_cny": 0.0,
                "cost_status": cost_status,
                "cost_usd": _native_cost if billing.native_currency == "USD" else 0.0,
                "currency": _currency,
                "total_cost": cost,
                "requested_model": "",
                "upstream_model_id": ctx.model_id if ctx is not None else "",
                "binding_source": "estimated",
                "input_unit_price": (
                    float(billing.input_unit_price)
                    if billing.input_unit_price is not None else None
                ),
                "output_unit_price": (
                    float(billing.output_unit_price)
                    if billing.output_unit_price is not None else None
                ),
                "cache_input_unit_price": None,
                "finish_reason": "estimated_no_usage",
                "decision": current_call_decision(),
                "duration_ms": round(duration_ms, 1) if duration_ms is not None else 0.0,
                "run_id": attribution["run_id"],
                "step_id": attribution["step_id"],
                "role": attribution["role"],
                "stage": attribution["stage"],
                "skill_id": attribution["skill_id"],
                "tool_id": attribution["tool_id"],
                "agent_domain": attribution["agent_domain"],
            })
        except Exception:
            pass  # 用量明细软失败，预算结算已发生

        try:
            from backend.observability.metrics import (
                llm_usage_estimated_total, llm_usage_missing_total,
            )
            llm_usage_missing_total.inc()
            llm_usage_estimated_total.inc()
        except Exception:
            pass
        logger.info(
            "[LLM:proxy] %s 流式缺 usage，已按本地估算结算 "
            "model=%s prompt~%s completion~%s tokens cost=%s",
            mode, model, prompt_tokens, completion_tokens, cost,
        )
        return True
    except Exception:
        logger.debug("[LLM:proxy] 估算结算失败，回退待对账路径", exc_info=True)
        return False



def _wrap_result(result, *, strip: bool = True):
    """递归剥离 LLM 返回值中的 <think> 块，兼容 str / AIMessage / list / dict"""
    if isinstance(result, str):
        return _strip_think(result, strip=strip)
    if hasattr(result, 'content') and isinstance(result.content, str):
        result.content = _strip_think(result.content, strip=strip)
    if isinstance(result, list):
        return [_wrap_result(r, strip=strip) for r in result]
    if isinstance(result, dict):
        return {k: _wrap_result(v, strip=strip) for k, v in result.items()}
    return result


# =====================================================
# 限流执行
# =====================================================

