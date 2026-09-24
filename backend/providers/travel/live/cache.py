"""providers/travel/live/cache.py — Provider 共享缓存（STOP J3）

任务书 §27-§33 的落点。**复用 ``infra/cache/backend.py::get_cache``**（Redis
可用 → TwoTierCache 跨 worker 共享；不可用 → 进程内降级）——不造第二套缓存。

四条契约：

1. **分数据 TTL（§28）**：place 600s（基本事实稳定）/ route 120s（含路况）/
   weather 300s（预报快变）/ ticket 600s。禁止一个 TTL 管所有。
2. **缓存键含全部影响参数（§29）+ 归一化（§30）**：坐标统一 4 位小数
   （≈11m，与 live_map 既有路段缓存同精度）；mode/日期窗/provider 进键。
3. **Negative cache（§31）**：只有 NOT_FOUND 允许短缓存（60s）——相同
   不存在的地点不再反复打 Provider；TIMEOUT/429/5xx **绝不**缓存成
   NOT_FOUND（它们根本不进缓存）。
4. **stale-if-error（§32）**：写入物理 TTL = fresh TTL + grace（默认 +600s），
   信封里的 ``fresh_until`` 才是新鲜边界。Provider 故障时读过期条目 →
   ``freshness=stale`` 返回（is_stale=True 上游必知）；正常命中未过期 →
   ``cached``。**stale 只允许在 Provider 失败路径上使用**（§32「可返回」
   语义收窄为「仅降级路径」，正常路径不喂旧数据）。

freshness 表达（§33）：live（本次调用）/ cached（缓存命中未过期）/
stale（降级路径的过期缓存）；estimated/unverified 由数据字段自带语义
（TransitLeg.is_estimate / Poi.verification_status），不在缓存层伪造。
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from backend.shared.logger import logger

# 分数据 TTL（秒）。改动须同步 J0 文档与最终报告。
FRESH_TTLS: dict[str, int] = {
    "place": 600,
    "route": 120,
    "weather": 300,
    "ticket": 600,
    # ── STOP K（K0 §8）：commerce 分数据 TTL ──
    # 五类语义条目（hotel_meta/hotel_avail/hotel_price/flight_offer/
    # flight_price）；当前搜索契约将 avail+price 捆绑在 offer 列表内一次
    # 返回，故 *_search 操作绑定其中**最易变**分量的 TTL（保守：绝不把
    # 旧价格当新价展示）。真实供应商拆分端点后各条目独立启用。
    "hotel_meta": 3600,     # 静态属性（名称/地址/星级）慢变
    "hotel_avail": 120,     # 可订状态快变
    "hotel_price": 180,     # 价格快变且商业敏感
    "flight_offer": 120,    # 航班时刻+舱位当日有效
    "flight_price": 180,    # 票价随时段波动
    "hotel_search": 120,    # = min(hotel_avail, hotel_price) 捆绑保守值
    "flight_search": 120,   # = min(flight_offer, flight_price) 捆绑保守值
}
# negative cache：仅 NOT_FOUND，短 TTL（§31）
NEGATIVE_TTL = 60
# stale grace：物理过期 = fresh + grace；期间仅降级路径可用
STALE_GRACE = 600

_CACHE_NAME = "travel_provider"


@dataclass
class CacheEnvelope:
    """缓存条目信封（JSON 存储）。status 记写入时的 ProviderStatus，
    NOT_FOUND 条目即 negative cache 命中。"""

    data: Any
    provider: str
    operation: str
    status: str                     # success | not_found
    observed_at: str
    fresh_until: float              # epoch 秒；超过即为 stale（物理过期前）
    source_id: str | None = None
    extra: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> "CacheEnvelope | None":
        try:
            obj = json.loads(raw)
            return cls(**obj)
        except (ValueError, TypeError):
            return None


def _backend():
    from backend.infra.cache.backend import get_cache

    return get_cache(_CACHE_NAME, ttl=max(FRESH_TTLS.values()) + STALE_GRACE)


def normalize_coord(value: float) -> float:
    """坐标归一（§30）：4 位小数 ≈11m——排程输入本身来自 POI 库，同点
    微小抖动不该生成两个缓存键。"""
    return round(float(value), 4)


def build_key(operation: str, *parts: Any) -> str:
    """缓存键：operation + 归一化参数（坐标 4 位小数，其余 str 化）。"""
    norm: list[str] = []
    for p in parts:
        if isinstance(p, float):
            norm.append(f"{normalize_coord(p):.4f}")
        else:
            norm.append(str(p))
    return f"{operation}:" + "|".join(norm)


def _read(key: str) -> CacheEnvelope | None:
    raw = _backend().get_json(key)
    if raw is None:
        return None
    if isinstance(raw, dict):  # TwoTierCache 反序列化产物
        try:
            return CacheEnvelope(**raw)
        except TypeError:
            return None
    return CacheEnvelope.from_json(raw) if isinstance(raw, str) else None


def _write(key: str, envelope: CacheEnvelope, physical_ttl: int) -> None:
    try:
        _backend().set_json(key=key, value=asdict(envelope), ttl=physical_ttl)
    except Exception:  # noqa: BLE001 — 缓存写失败不影响主链
        logger.debug("[TravelProviderCache] 写入失败", exc_info=True)


def cache_get(key: str) -> tuple[CacheEnvelope | None, str]:
    """读缓存。

    Returns:
        (envelope, cache_status)：cache_status ∈ hit | negative | stale_ready | miss
        - hit：未过期（fresh_until 未到）→ 上游以 freshness=cached 使用
        - negative：NOT_FOUND 条目未过期 → 上游直接以 NOT_FOUND 结局返回
        - stale_ready：已过 fresh_until 但物理未过期 → **只在 Provider
          失败路径**由 cache_get_stale 取用
        - miss：无
    """
    env = _read(key)
    if env is None:
        return None, "miss"
    if time.time() <= env.fresh_until:
        if env.status == "not_found":
            return env, "negative"
        return env, "hit"
    return env, "stale_ready"


def cache_get_stale(key: str) -> CacheEnvelope | None:
    """stale-if-error 专用：只返回已过 fresh_until 的条目（§32）。
    未过期条目反而返回 None——那条该走正常 hit 路径。"""
    env = _read(key)
    if env is None or env.status != "success":
        return None
    return env if time.time() > env.fresh_until else None


def cache_put_success(
    key: str, *, data: Any, provider: str, operation: str,
    observed_at: str, source_id: str | None = None, extra: dict | None = None,
) -> None:
    ttl = FRESH_TTLS.get(operation, 300)
    env = CacheEnvelope(
        data=data, provider=provider, operation=operation, status="success",
        observed_at=observed_at, fresh_until=time.time() + ttl,
        source_id=source_id, extra={"key": key, **(extra or {})},
    )
    _write(key, env, physical_ttl=ttl + STALE_GRACE)


def cache_put_not_found(
    key: str, *, provider: str, operation: str, observed_at: str,
) -> None:
    """negative cache：仅 NOT_FOUND（§31）。短 TTL，物理=语义，无 grace。"""
    env = CacheEnvelope(
        data=None, provider=provider, operation=operation, status="not_found",
        observed_at=observed_at, fresh_until=time.time() + NEGATIVE_TTL,
        extra={"key": key},
    )
    _write(key, env, physical_ttl=NEGATIVE_TTL)


def cache_hit_status(envelope: CacheEnvelope | None, raw_status: str) -> str:
    """供遥测使用的 cache_status 归一（遥测低基数：hit/negative/stale/miss）。"""
    if envelope is None:
        return "miss"
    if raw_status == "negative":
        return "negative"
    if raw_status == "stale_ready":
        return "stale"
    return "hit"
