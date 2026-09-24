"""travel/booking/provider_capabilities.py — Booking Provider 装配（STOP L4）

配置（TRAVEL_BOOKING_PROVIDER）→ (provider 实例, registry 契约)。
**能力一律查 shared/provider_idempotency.py 集中 registry**（唯一事实源），
适配器/执行器不得各自假设；未登记/UNKNOWN → fail-closed。

Fake provider 为进程内单例（native profile 的同 key 去重记忆必须跨调用）。
"""
from __future__ import annotations

from backend.shared.provider_idempotency import (
    ProviderContractMissing,
    get_provider_contract,
)

_provider_instances: dict[str, object] = {}


def resolve_provider(configured: str) -> tuple[object, object] | None:
    """配置名 → (provider, contract)；off → None；未知名 → fail-closed。

    Raises:
        ProviderContractMissing: 配置的 provider 未在集中 registry 登记
        （真实供应商未接入时的默认结局——绝不静默降级到 fake，§四十八）。
    """
    if not configured or configured == "off":
        return None
    contract = get_provider_contract(configured)  # 未登记 → ProviderContractMissing
    provider = _provider_instances.get(configured)
    if provider is None:
        from backend.providers.travel.booking.fake import (
            FakeTravelBookingProvider,
        )

        profile = configured.removeprefix("fake_booking_")
        if profile not in ("native", "clientref", "bare"):
            # 真实供应商名：registry 登记了契约但无适配器实现（同 STOP K
            # 「live 未实现」口径）——fail-closed，绝不以 fake 冒充
            raise ProviderContractMissing(
                f"booking provider {configured!r} 无适配器实现（真实供应商"
                "未接入）——BLOCKED_BY_EXTERNAL_BOOKING_PROVIDER")
        provider = FakeTravelBookingProvider(profile=profile)
        _provider_instances[configured] = provider
    return provider, contract
