"""providers/travel/live/router.py — ProviderRouter（v4 §8，Phase 4）

**组装期链账装配**（Phase 4 决策点 D2 拍板）：``PROVIDER_CHAINS`` 是
「capability → 有序 provider 链」的**单一声明事实源**（G2）；工厂按链账
装配，装配产物与原硬编码**逐字节同构**（parity 门 = qweather_backup 15 用例
+ 工厂装配两用例）。

三条治理纪律（v4 §8 冻结）：
1. Router 只存在于 Provider 层；Agent/Node 永远拿到统一 ``ProviderResult``，
   不知道也不允许知道背后是哪个 adapter——Agent 声明需要什么 capability，
   Router 决定谁供给；
2. 链的**执行语义**（fresh → stale-if-error → 七态失败）已内嵌在各 adapter
   内部的 STOP J 冻结共享基建里——Router 不重写 retry/缓存/降级，只负责：
   ①链声明 ②provider 装配 ③``ttl_for``/``timeout_for`` 薄读口；
3. map/poi/price 链**先声明后接管**：map 已由 ``routing.set_route_provider``
   注入槽承担（register 启动接线）、poi=seed 唯一源（城市级 live 源 BLOCKED
   台账）、price=fake_commerce（冻结交易域自治）——此处仅声明，不重复接线。

归位裁决（Phase 4 D1）：providers 原位不动，Router 内聚本层；
physical relocation deferred to Phase 8 re-evaluation（G4 登记）。
"""
from __future__ import annotations

# capability → 有序 provider 链（v4 §8 冻结选择链；命名对齐 capabilities.py
# 能力账，poi/price 两条无账面条目的链用域内 capability 口径声明）。
PROVIDER_CHAINS: dict[str, tuple[str, ...]] = {
    "weather.forecast": ("tencent", "qweather"),
    "maps.route": ("tencent_live", "local_estimate"),
    "poi.search": ("seed",),
    "price.quotes": ("fake_commerce",),
}

# 无凭据语义的链成员（声明占位/冻结域自治，装配期不实例化）
_DECLARATION_ONLY = frozenset({"seed", "local_estimate", "fake_commerce", "tencent_live"})


def _is_configured(name: str) -> bool:
    """链成员是否配置可用（qweather/tencent 查真实配置；占位成员恒 True）。"""
    if name == "qweather":
        from backend.config.map import is_qweather_configured

        return is_qweather_configured()
    if name == "tencent":
        from backend.config.map import is_configured

        return is_configured()
    if name in _DECLARATION_ONLY:
        return True
    raise KeyError(f"ProviderRouter: 链账中的未知 provider: {name}")


def _provider_instance(name: str):
    """链成员名 → 可调用 provider 实例（仅 fallback 成员需要；惰性 import
    防 live/__init__ ↔ router 循环）。"""
    if name == "qweather":
        from backend.providers.travel.live import get_qweather_provider

        return get_qweather_provider()
    raise KeyError(f"ProviderRouter: provider 无装配工厂: {name}")


def assemble_weather_provider():
    """weather 链装配（与原 get_weather_provider 硬编码逐字节同构）：

    - 链首恒为主源 TencentWeatherProvider（主源缺席由 DISABLED 语义表达，
      不换位——fallback 不跨语义纪律）；
    - 其余链成员**配置即参与降级**：QWEATHER_API_KEY 配置 →
      FallbackWeatherProvider(腾讯, 和风)；未配置 → 裸腾讯（行为与旧版一致）。
    """
    from backend.providers.travel.live.tencent import TencentWeatherProvider

    chain = PROVIDER_CHAINS["weather.forecast"]
    primary = TencentWeatherProvider()
    configured = [n for n in chain[1:] if _is_configured(n)]
    if not configured:
        return primary

    from backend.providers.travel.live import FallbackWeatherProvider

    return FallbackWeatherProvider(primary, _provider_instance(configured[0]))


def ttl_for(operation: str) -> int:
    """数据 TTL 薄读口（Evidence.expire_at 组装用，v4 §4「现有 TTL 即
    expire_at」）。键空间与 cache.FRESH_TTLS 一致（place/route/weather/
    ticket/commerce 各条），缺省回落 300——与 cache_put_success 同款。"""
    from backend.providers.travel.live import cache as pcache

    return pcache.FRESH_TTLS.get(operation, 300)


def timeout_for(operation: str) -> float:
    """超时预算薄读口（单一事实源 = resilience.resolve_budget，weather 走
    config TRAVEL_WEATHER_TIMEOUT_S 接线）。"""
    from backend.providers.travel.live.resilience import resolve_budget

    return resolve_budget(operation)
