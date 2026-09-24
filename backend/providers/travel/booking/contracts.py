"""providers/travel/booking/contracts.py — Booking Provider 契约（STOP L1/L4）

**本文件承接 providers/travel/booking.py 预留资产**（Phase 7 预留、STOP L
实现——任务书 §七指定包形态）。原 booking.py 的 BookingStatus / 幂等键 /
BookingProvider Protocol 语义原样迁入并由既有测试（test_booking_candidate）
继续钉死；新增 STOP L 的 TravelBookingProvider 契约与归一化 Record。

与 STOP K commerce_contracts 同构（任务书 §七）：业务层依赖 Contract，
Provider 层依赖 SDK/API；Record 是 JSON-safe dataclass；**结局分类
（ProviderEffectOutcome）在适配器内完成**——NOT_SENT/REJECTED/SUCCEEDED/
UNKNOWN 四值是 shared/provider_idempotency.py 冻结决策矩阵的输入，
适配器不得偷偷假设幂等能力（能力一律查集中 registry）。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Protocol, runtime_checkable

from backend.providers.travel.live.result import ProviderResult  # noqa: F401  (兼容旧导出)
from backend.shared.provider_idempotency import ProviderEffectOutcome


# ═══════════════════════════════════════════════════
# 预留资产迁入（与原 booking.py 逐字同语义；测试钉死）
# ═══════════════════════════════════════════════════


class BookingStatus(str, Enum):
    """预订状态机（单向流转，不回跳）——Phase 7 预留语义，保持不变。"""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    EXPIRED = "expired"

    @classmethod
    def terminal(cls) -> set["BookingStatus"]:
        return {cls.CONFIRMED, cls.CANCELLED, cls.FAILED, cls.EXPIRED}


_ALLOWED_TRANSITIONS: dict[BookingStatus, set[BookingStatus]] = {
    BookingStatus.PENDING: {BookingStatus.CONFIRMED, BookingStatus.CANCELLED,
                            BookingStatus.FAILED, BookingStatus.EXPIRED},
    BookingStatus.CONFIRMED: set(),
    BookingStatus.CANCELLED: set(),
    BookingStatus.FAILED: set(),
    BookingStatus.EXPIRED: set(),
}


def can_transition(current: BookingStatus, target: BookingStatus) -> bool:
    return target in _ALLOWED_TRANSITIONS.get(current, set())


def make_idempotency_key(
    provider: str, poi_id: str, visit_date: str,
    party_size: int, contact: str,
) -> str:
    """预留幂等键（Phase 7 语义保持；STOP L 主链用 derive_provider_key）。"""
    blob = "|".join([
        provider.strip().lower(), poi_id.strip(), visit_date.strip(),
        str(int(party_size)), contact.strip(),
    ])
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


@dataclass
class BookingRequest:
    """预留请求形状（Phase 7；STOP L 未使用，保留防破坏既有导入）。"""

    poi_id: str
    visit_date: str
    party_size: int
    contact: str = ""
    idempotency_key: str = ""

    @classmethod
    def create(cls, provider: str, poi_id: str, visit_date: str,
               party_size: int, contact: str = "") -> "BookingRequest":
        """便捷构造：自动派生幂等键（键规则与字段同源，防止两处口径漂移）。"""
        return cls(
            poi_id=poi_id, visit_date=visit_date, party_size=party_size,
            contact=contact,
            idempotency_key=make_idempotency_key(
                provider, poi_id, visit_date, party_size, contact))


@dataclass
class BookingRecord:
    """预留记录形状（Phase 7）。"""

    booking_id: str
    status: BookingStatus = BookingStatus.PENDING
    request: BookingRequest | None = None
    created_at: str = ""
    updated_at: str = ""


class BookingProvider(Protocol):
    """预留 Provider Protocol（Phase 7 语义原样保留，无接线无调用方）。"""

    name: str

    def create_booking(self, request: BookingRequest) -> BookingRecord:
        ...

    def cancel_booking(self, booking_id: str) -> BookingRecord:
        ...

    def get_booking(self, booking_id: str) -> BookingRecord | None:
        ...


# ═══════════════════════════════════════════════════
# STOP L：Booking Create 契约
# ═══════════════════════════════════════════════════


@dataclass
class BookingCreateRequest:
    """一次 booking create 的完整入参（canonical 指纹的载荷）。

    client idempotency key / provider key 由执行层派生后传入——适配器只按
    契约能力决定是否携带（native=作为 Idempotency-Key；其余不携带）。
    """

    merchant_order_id: str
    idempotency_key: str            # derive_provider_key 派生（opaque）
    provider: str

    offer_fingerprint: str
    commerce_type: str              # hotel | flight
    booking_facts: dict             # 日期/occupancy/航段要素（JSON-safe）

    amount: str                     # Decimal 字符串（G4：禁 float）
    currency: str

    # 能力声明的传递载体（Model A 才非空；由 executor 按 registry 填写）
    idempotency_key_transport: str = "none"   # none|body|header|query


@dataclass
class BookingCreateRecord:
    """Provider 侧订单回执（JSON-safe）。"""

    provider: str
    provider_order_id: str
    merchant_order_id: str
    status: str                     # confirmed | pending | rejected ...
    amount: str
    currency: str
    payment_deep_link: str | None = None      # 仅 Provider 返回时非 None
    raw_status: str = ""


@dataclass
class BookingCallResult:
    """单次 provider 调用的结局（决策矩阵输入）。

    outcome 语义（shared/provider_idempotency.py 冻结）：
      SUCCEEDED 确认生效；NOT_SENT 明确未过界（可安全重试）；
      REJECTED 明确拒绝；UNKNOWN 可能已生效但结果不确定。
    """

    outcome: ProviderEffectOutcome
    record: BookingCreateRecord | None = None
    detail: str = ""


@runtime_checkable
class TravelBookingProvider(Protocol):
    """Booking Create Provider 契约。"""

    name: str

    def create_booking(self, request: BookingCreateRequest) -> BookingCallResult:
        """调用一次 booking create。结局四值分类由适配器保证。"""
        ...

    def lookup_booking(self, merchant_order_id: str) -> str:
        """按 client reference 查询订单状态（三值）：
        "succeeded" | "failed" | "not_found"；不支持时抛 LookupUnsupported。"""
        ...


class LookupUnsupported(RuntimeError):
    """provider 不支持按 client reference 查询（能力账 UNSUPPORTED）。"""
