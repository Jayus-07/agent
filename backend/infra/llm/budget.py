"""请求级模型预算。 

本模块只负责一次请求内的调用预占和 token 计数：

* ``off``：不建立请求状态，保持现有行为；
* ``observe``：记录超限状态但不阻断，供上线前校准；
* ``enforce``：每次 primary/retry/fallback 前先预占，超限抛出
  ``RequestBudgetExceeded``，由统一错误协议映射为 ``BUDGET_EXCEEDED``。

用户/租户日月额度和跨进程预占不放在这里，待产品额度与价格口径确认后再接入
Redis/PG 配额服务。
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from contextvars import ContextVar
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from backend.shared.logger import logger


_VALID_CALL_KINDS = frozenset({"primary", "retry", "fallback"})
_MAX_TRACKED_REQUESTS = 4096
_current_request_id: ContextVar[str] = ContextVar(
    "llm_budget_request_id", default=""
)
_current_call_decision: ContextVar[str] = ContextVar(
    "llm_budget_call_decision", default="primary"
)
_current_quota_reservation: ContextVar[Any] = ContextVar(
    "llm_budget_quota_reservation", default=None
)
_states: OrderedDict[str, "RequestBudget"] = OrderedDict()
_states_lock = threading.RLock()


def _record_budget_request(mode: str, result: str) -> None:
    """记录请求预算门禁结果；观测失败不能改变门禁结果。"""
    try:
        from backend.observability import metrics

        metrics.budget_request_total.labels(mode=mode, result=result).inc()
    except Exception as exc:
        logger.debug("[Budget] request metric write failed: %s", exc)


@dataclass(frozen=True)
class RequestBudgetLimits:
    """请求级限制；小于等于 0 表示该项不限制。"""

    max_calls: int = 8
    max_total_tokens: int = 32000
    max_cost: Decimal = Decimal("0.50")
    max_retries: int = 2
    max_fallbacks: int = 1

    def __post_init__(self):
        object.__setattr__(
            self,
            "max_cost",
            Decimal(str(self.max_cost)).quantize(Decimal("0.000001")),
        )


@dataclass(frozen=True)
class RequestBudgetSnapshot:
    calls: int
    retries: int
    fallbacks: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost: Decimal
    exceeded: tuple[str, ...] = ()


class RequestBudgetExceeded(RuntimeError):
    """请求级预算阻断。"""

    code = "BUDGET_EXCEEDED"
    retryable = False

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"请求预算已超限: {reason}")


@dataclass
class RequestBudget:
    """单请求预算状态；跨线程节点通过 request_id 共享同一实例。"""

    limits: RequestBudgetLimits = field(default_factory=RequestBudgetLimits)
    mode: str = "off"
    calls: int = 0
    retries: int = 0
    fallbacks: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost: Decimal = Decimal("0")
    user_id: str = ""
    tenant_id: str = ""
    quota_store: Any = field(default=None, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def reserve(
        self,
        kind: str,
        *,
        model_name: str = "",
        component: str = "llm",
    ) -> Any:
        """在实际模型调用前预占一次调用额度。"""
        if kind not in _VALID_CALL_KINDS:
            raise ValueError(f"非法预算调用类型: {kind}")
        with self._lock:
            reason = self._exceeded_reason(kind)
            if reason and self.mode == "enforce":
                _record_budget_request(self.mode, "rejected")
                raise RequestBudgetExceeded(reason)
            if self.mode == "enforce" and model_name and self.quota_store is not None:
                from backend.infra.llm.pricing import get_current_price_table

                # P0 治理修正（2026-09-22）：价格未知（缺价/价格库不可用）
                # 不再阻断调用 —— 模型推理可用性优先于结算精度。此前
                # 「拒绝首调」曾让 general_chat 等主链路直接降级静态话术。
                # 放行后由 proxy._record_tokens 结算侧以 price_unknown
                # 状态标记，按注册表估价记账（不静默算 0），token 上限
                # 保护照常生效。
                try:
                    get_current_price_table(model_name, component).require(
                        model_name, component, enforce=True
                    )
                except Exception as exc:
                    _record_budget_request(self.mode, "price_unknown")
                    logger.warning(
                        "[Budget][price_unknown] model=%s component=%s "
                        "价格未知，放行调用（结算侧将标记 price_unknown）: %s",
                        model_name, component, exc,
                    )
            reservation = None
            if (
                self.mode == "enforce"
                and self.quota_store is not None
                and self.user_id
                and self.tenant_id
            ):
                try:
                    reservation = self.quota_store.reserve(
                        user_id=self.user_id,
                        tenant_id=self.tenant_id,
                        # 预占本次请求级成本上限；成功后按真实价格结算并
                        # 释放差额，避免并发请求在未知 completion token 时超卖。
                        amount_cny=self.limits.max_cost,
                        request_id=_current_request_id.get(),
                    )
                except Exception:
                    _record_budget_request(self.mode, "rejected")
                    raise
            self.calls += 1
            if kind == "retry":
                self.retries += 1
            elif kind == "fallback":
                self.fallbacks += 1
            _record_budget_request(self.mode, "allowed")
            return reservation

    def record_usage(
        self,
        *,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
        cost: Decimal | float | str | None = None,
    ) -> None:
        """记录一次调用返回的真实 token；未知用量不伪造。"""
        with self._lock:
            prompt = max(0, int(prompt_tokens or 0))
            completion = max(0, int(completion_tokens or 0))
            total = max(0, int(total_tokens or prompt + completion))
            self.prompt_tokens += prompt
            self.completion_tokens += completion
            self.total_tokens += total
            self.cost += Decimal(str(cost or 0)).quantize(
                Decimal("0.000001")
            )

    def snapshot(self) -> RequestBudgetSnapshot:
        with self._lock:
            exceeded: list[str] = []
            for kind in ("primary", "retry", "fallback"):
                reason = self._exceeded_reason(kind)
                if reason and reason not in exceeded:
                    exceeded.append(reason)
            return RequestBudgetSnapshot(
                calls=self.calls,
                retries=self.retries,
                fallbacks=self.fallbacks,
                prompt_tokens=self.prompt_tokens,
                completion_tokens=self.completion_tokens,
                total_tokens=self.total_tokens,
                cost=self.cost,
                exceeded=tuple(exceeded),
            )

    def _exceeded_reason(self, kind: str) -> str:
        if self.limits.max_calls > 0 and self.calls >= self.limits.max_calls:
            return "request_calls"
        if (
            self.limits.max_total_tokens > 0
            and self.total_tokens >= self.limits.max_total_tokens
        ):
            return "request_tokens"
        if (
            self.limits.max_cost > 0
            and self.cost >= self.limits.max_cost
        ):
            return "request_cost"
        if (
            kind == "retry"
            and self.limits.max_retries > 0
            and self.retries >= self.limits.max_retries
        ):
            return "request_retries"
        if (
            kind == "fallback"
            and self.limits.max_fallbacks > 0
            and self.fallbacks >= self.limits.max_fallbacks
        ):
            return "request_fallbacks"
        return ""


def _config_limits() -> RequestBudgetLimits:
    """从配置读取请求级建议默认值；不读取用户/租户金额配置。"""
    from backend.config import llm as config

    return RequestBudgetLimits(
        max_calls=int(config.LLM_REQUEST_MAX_CALLS),
        max_total_tokens=int(config.LLM_REQUEST_MAX_TOKENS),
        max_cost=Decimal(str(config.LLM_REQUEST_MAX_COST)),
        max_retries=int(config.LLM_REQUEST_MAX_RETRIES),
        max_fallbacks=int(config.LLM_REQUEST_MAX_FALLBACKS),
    )


def _config_mode() -> str:
    from backend.config import llm as config

    return config.LLM_BUDGET_MODE


def bind_request_budget(
    request_id: str,
    *,
    user_id: str = "",
    tenant_id: str = "",
) -> None:
    """按 Trace/request_id 绑定预算；相同请求在并行线程复用同一状态。"""
    request_id = str(request_id or "").strip()
    if not request_id:
        clear_request_budget()
        return
    mode = _config_mode()
    if mode == "off":
        # 默认关闭时连请求状态也不建立，避免给现有请求引入无意义的
        # 计数和 LRU 生命周期；显式 observe/enforce 才进入预算链。
        clear_request_budget()
        return
    with _states_lock:
        state = _states.get(request_id)
        if state is None:
            quota_store = None
            if user_id and tenant_id:
                from backend.infra.llm.quota import PostgresQuotaStore

                quota_store = PostgresQuotaStore()
            state = RequestBudget(
                _config_limits(), mode, user_id=user_id, tenant_id=tenant_id,
                quota_store=quota_store,
            )
            _states[request_id] = state
            while len(_states) > _MAX_TRACKED_REQUESTS:
                _states.popitem(last=False)
        else:
            _states.move_to_end(request_id)
    _current_request_id.set(request_id)


def clear_request_budget() -> None:
    _current_request_id.set("")
    _current_call_decision.set("primary")
    _current_quota_reservation.set(None)


def current_request_budget() -> RequestBudget | None:
    request_id = _current_request_id.get()
    if not request_id:
        return None
    with _states_lock:
        return _states.get(request_id)


def reserve_model_call(
    kind: str,
    *,
    model_name: str = "",
    component: str = "llm",
) -> None:
    if kind not in _VALID_CALL_KINDS:
        raise ValueError(f"非法预算调用类型: {kind}")
    if not model_name:
        from backend.infra.llm.proxy import get_active_model_name

        model_name = get_active_model_name()
    state = current_request_budget()
    if state is not None:
        reservation = state.reserve(
            kind, model_name=model_name, component=component,
        )
        _current_quota_reservation.set(reservation)
    _current_call_decision.set(kind)


def current_call_decision() -> str:
    """读取当前执行上下文最近一次成功预占的调用类型。"""
    return _current_call_decision.get()


def release_model_reservation() -> None:
    """释放本次模型调用失败/重试路径的额度预占。

    仅用于"确定未发给供应商"的失败（熔断开路、鉴权/参数类错误）；
    可能已计费的失败请用 :func:`review_model_reservation`。
    """
    reservation = _current_quota_reservation.get()
    state = current_request_budget()
    if reservation is not None and state is not None and state.quota_store is not None:
        state.quota_store.release(reservation)
    _current_quota_reservation.set(None)


def review_model_reservation(reason: str) -> None:
    """把当前预占转入待对账（needs_review），占额保守保留到周期结束。

    用于"调用可能已对供应商计费但用量未知"的场景：流式缺 usage 尾帧、
    超时/网络类失败（供应商可能已处理）、结算失败。不能按零成本放走，
    也不能让本次预占永久滞留——由 PG 侧 sweep 在周期结束后释放占额并
    留在待对账队列供核查。标记失败只降级为告警，不反噬模型主链路。
    """
    reservation = _current_quota_reservation.get()
    state = current_request_budget()
    if reservation is None or state is None or state.quota_store is None:
        return
    try:
        marked = state.quota_store.mark_needs_review(reservation, reason)
    except Exception as exc:  # noqa: BLE001 — 观测/对账链路不得反噬主链路
        logger.warning(
            "[Budget] 待对账标记失败 reservation=%s reason=%s（预占可能滞留，"
            "由滞留回收兜底）: %s",
            getattr(reservation, "reservation_id", "?"), reason, exc,
        )
    else:
        if marked:
            _record_budget_request(state.mode, "needs_review")
        _current_quota_reservation.set(None)


def record_model_usage(
    *,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
    cost: Decimal | float | str | None = None,
) -> None:
    state = current_request_budget()
    if state is not None:
        state.record_usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost=cost,
        )
        reservation = _current_quota_reservation.get()
        if reservation is not None and state.quota_store is not None:
            try:
                state.quota_store.settle(reservation, Decimal(str(cost or 0)))
            except Exception as exc:
                # 2026-10-01 P0 修复：结算失败不再吞掉——原实现静默吞异常会让
                # 预占永久滞留。转为待对账（占额保留到周期结束）并告警；
                # 仍不向调用方抛错（成本统计失败不能破坏模型主链路）。
                logger.warning(
                    "[Budget] 预算结算失败，转入待对账 reservation=%s: %s",
                    getattr(reservation, "reservation_id", "?"), exc,
                )
                try:
                    state.quota_store.mark_needs_review(reservation, "settle_failed")
                except Exception:
                    logger.warning(
                        "[Budget] 结算失败且待对账标记也失败 reservation=%s"
                        "（由滞留回收兜底）",
                        getattr(reservation, "reservation_id", "?"),
                    )
            finally:
                _current_quota_reservation.set(None)


__all__ = [
    "RequestBudget",
    "RequestBudgetExceeded",
    "RequestBudgetLimits",
    "RequestBudgetSnapshot",
    "bind_request_budget",
    "clear_request_budget",
    "current_call_decision",
    "current_request_budget",
    "record_model_usage",
    "release_model_reservation",
    "reserve_model_call",
    "review_model_reservation",
]
