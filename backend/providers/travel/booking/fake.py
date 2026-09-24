"""providers/travel/booking/fake.py — Booking Fake Provider（STOP L4）

三种能力 profile（与 shared/provider_idempotency.py 集中 registry 一一对应，
能力声明以 registry 为唯一事实源，适配器只实现行为）：

  fake_booking_native     Model A：native Idempotency-Key（同 key 同 payload
                          返回同一订单）+ 按 merchant ref 查询 → SAFE_RETRY
  fake_booking_clientref  Model B：无原生键，但 merchant_order_id 落库可查询
                          → RECONCILE_FIRST
  fake_booking_bare       Model C：两者皆无 → IN_DOUBT

**FAKE = 显式测试数据源**：provider_order_id 带 `fbk-` 前缀，渲染层披露
「模拟交易」。行为确定性（无随机）；场景注入仅影响 outcome 分类：
  success          正常创建
  timeout_unknown  请求已发出但结果未知（UNKNOWN——IN_DOUBT 语义的来源）
  reject_before    provider 明确拒收、无副作用（REJECTED）
  fail_before_send 明确未越过边界（NOT_SENT——可安全重试）
"""
from __future__ import annotations

import threading

from backend.providers.travel.booking.contracts import (
    BookingCallResult,
    BookingCreateRecord,
    BookingCreateRequest,
    LookupUnsupported,
)
from backend.shared.provider_idempotency import ProviderEffectOutcome

PROVIDER_NAMES = ("fake_booking_native", "fake_booking_clientref", "fake_booking_bare")


class FakeTravelBookingProvider:
    """三类能力 profile 的 Booking Fake 适配器（确定性、可注入场景）。"""

    def __init__(self, profile: str = "native", scenario: str = "success"):
        if profile not in ("native", "clientref", "bare"):
            raise ValueError(f"未知 profile: {profile}")
        self.profile = profile
        self.scenario = scenario
        self.name = f"fake_booking_{profile}"
        self._orders: dict[str, BookingCreateRecord] = {}   # idem_key → record
        self._by_ref: dict[str, BookingCreateRecord] = {}   # merchant_ref → record
        self._lock = threading.Lock()
        self.create_calls = 0                                # 测试断言外部调用次数

    # ── create ──────────────────────────────────────────
    def create_booking(self, request: BookingCreateRequest) -> BookingCallResult:
        with self._lock:
            self.create_calls += 1
            if self.scenario == "fail_before_send":
                return BookingCallResult(
                    outcome=ProviderEffectOutcome.NOT_SENT,
                    detail="fake: 连接失败，明确未到达 provider")
            if self.scenario == "reject_before":
                return BookingCallResult(
                    outcome=ProviderEffectOutcome.REJECTED,
                    detail="fake: provider 明确拒收（无副作用）")
            if self.scenario == "timeout_unknown":
                return BookingCallResult(
                    outcome=ProviderEffectOutcome.UNKNOWN,
                    detail="fake: 请求已发出但响应超时，结果未知")

            # success：native profile 按 idempotency key 去重（同 key 同请求
            # 返回同一订单——Model A 语义）；clientref/bare 每次调用都创建
            existing = self._orders.get(request.idempotency_key) \
                if self.profile == "native" else None
            if existing is not None:
                return BookingCallResult(
                    outcome=ProviderEffectOutcome.SUCCEEDED,
                    record=existing,
                    detail="fake: native idempotent replay")
            record = BookingCreateRecord(
                provider=self.name,
                provider_order_id=f"fbk-{self.profile}-{len(self._orders) + 1:04d}",
                merchant_order_id=request.merchant_order_id,
                status="confirmed",
                amount=request.amount,
                currency=request.currency,
                payment_deep_link=None,
                raw_status="confirmed",
            )
            self._orders[request.idempotency_key] = record
            self._by_ref[request.merchant_order_id] = record
            return BookingCallResult(
                outcome=ProviderEffectOutcome.SUCCEEDED, record=record)

    # ── lookup（§二十五：查询事实；Model B 的 reconcile 通道）─────────
    def lookup_booking(self, merchant_order_id: str) -> str:
        if self.profile == "bare":
            raise LookupUnsupported("fake_booking_bare 不支持状态查询")
        with self._lock:
            record = self._by_ref.get(merchant_order_id)
        if record is None:
            return "not_found"
        if record.status == "confirmed":
            return "succeeded"
        return "failed"

    # ── 测试辅助 ────────────────────────────────────────
    def logical_order_count(self) -> int:
        """已创建的逻辑订单数（去重后）——并发/重试断言口径。"""
        with self._lock:
            return len(self._orders)


def build_provider(profile: str, scenario: str = "success") -> FakeTravelBookingProvider:
    """按 profile 构造（executor 工厂的测试注入点；生产工厂按配置选择）。"""
    return FakeTravelBookingProvider(profile=profile, scenario=scenario)
