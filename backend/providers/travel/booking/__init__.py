"""providers/travel/booking — Booking Provider 契约与适配器（STOP L）

原 `providers/travel/booking.py` 预留资产（Phase 7）升格为本包：预留语义
（BookingStatus/can_transition/make_idempotency_key/BookingRequest/
BookingRecord）在 contracts.py 逐字保留并由既有测试继续钉死；包根再导出
保持 `from backend.providers.travel.booking import BookingStatus` 等既有
导入路径不变。

STOP L 新增：TravelBookingProvider 契约、BookingCreateRequest/Record/
CallResult（ProviderEffectOutcome 四值分类）、三类能力 profile 的 Fake
适配器（native/clientref/bare，与 shared/provider_idempotency.py 集中
registry 一一对应）。
"""
from backend.providers.travel.booking.contracts import (
    BookingCallResult,
    BookingCreateRecord,
    BookingCreateRequest,
    BookingProvider,
    BookingRequest,
    BookingRecord,
    BookingStatus,
    LookupUnsupported,
    TravelBookingProvider,
    can_transition,
    make_idempotency_key,
)
from backend.providers.travel.booking.fake import (
    FakeTravelBookingProvider,
    PROVIDER_NAMES,
    build_provider,
)

__all__ = [
    "BookingCallResult",
    "BookingCreateRecord",
    "BookingCreateRequest",
    "BookingProvider",
    "BookingRecord",
    "BookingRequest",
    "BookingStatus",
    "FakeTravelBookingProvider",
    "LookupUnsupported",
    "PROVIDER_NAMES",
    "TravelBookingProvider",
    "build_provider",
    "can_transition",
    "make_idempotency_key",
]
