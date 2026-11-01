"""providers/travel/poi.py — POI 数据 Provider（Phase 1 §3）

把「POI 事实从哪来」收敛为两个可替换的 Provider：

- ``SeedPOIProvider``      — 本地种子数据（poi_seed），营业时间/票价经人工整理，
  ``verification_status=verified``；离线可用，是城市覆盖的基座。
- ``TencentPOIProvider``   — 腾讯位置服务点名解析（live_map.resolve_place），
  **坐标与名称是权威事实，但营业时间/票价是契约默认占位值** —— 打
  ``verification_status=unverified`` 标记并记录 ``observed_at``，
  让「未核实」沿状态链路走到行程单，而不是在中途被当成事实消费。

纪律：
- Provider 只搬运与标注事实，**不做任何推断**（禁止 LLM 补营业时间/票价）。
- 域模型 Poi 的字段全部有向后兼容默认值，Provider 层是纯增量接线。
"""
from __future__ import annotations

from typing import Protocol

from backend.shared.logger import logger
from backend.tools.travel import poi_seed
from backend.travel.models.poi import Poi

from backend.providers.travel.facts import VERIFIED


class POIProvider(Protocol):
    """POI 数据源契约。实现方不得修改传入对象之外的任何状态。"""

    name: str

    def resolve(self, name: str, city: str, *, required: bool = False) -> Poi | None:
        """把一个地点名解析为 Poi；解析不到返回 None（由调用方降级）。"""
        ...

    def search(
        self,
        city: str,
        preferences: list[str] | None = None,
        avoid: list[str] | None = None,
        must_go: list[str] | None = None,
        limit: int = 20,
    ) -> list[Poi]:
        """城市级候选检索；数据源无该能力时返回 []。"""
        ...


class SeedPOIProvider:
    """本地种子数据源（verified 事实，离线可用）。"""

    name = "seed:local"

    def resolve(self, name: str, city: str, *, required: bool = False) -> Poi | None:
        from backend.tools.travel.poi import _matches_name, resolve_city

        city_key = resolve_city(city)
        if city_key is None:
            return None
        for poi in poi_seed.load_city(city_key):
            if _matches_name(poi, [name]):
                poi.required = poi.required or required
                return poi
        return None

    def search(
        self,
        city: str,
        preferences: list[str] | None = None,
        avoid: list[str] | None = None,
        must_go: list[str] | None = None,
        limit: int = 20,
    ) -> list[Poi]:
        from backend.tools.travel.poi import search_poi

        return search_poi(
            city=city, preferences=preferences, avoid=avoid,
            must_go=must_go, limit=limit,
        )


class TencentPOIProvider:
    """腾讯位置服务点名解析（坐标权威；营业时间/票价为占位 → unverified）。"""

    name = "tencent:lbs"

    def __init__(self) -> None:
        # 延迟导入：live_map 依赖 config.map 与 lbs 基础设施，避免模块加载即拉起
        from backend.tools.travel import live_map

        self._live_map = live_map

    def is_enabled(self) -> bool:
        return self._live_map.is_enabled()

    def resolve(self, name: str, city: str, *, required: bool = False) -> Poi | None:
        # unverified + observed_at 标注由数据源唯一出口 resolve_place 统一打上
        #（live_map.py 内），此处不做二次标注 —— 时效标注只有一个真相源。
        poi = self._live_map.resolve_place(name, city, required=required)
        if poi is None:
            return None
        return poi

    def search(
        self,
        city: str,
        preferences: list[str] | None = None,
        avoid: list[str] | None = None,
        must_go: list[str] | None = None,
        limit: int = 20,
    ) -> list[Poi]:
        # 城市级候选仍由种子池承担（腾讯逐城拉取全量 POI 成本高且无营业时间，
        # 打满 unverified 的候选池会让校验轴全部失真）。此处显式不支持而非静默降级。
        logger.debug("[TravelPOIProvider] tencent 源不支持城市级检索，返回空")
        return []


class AmapPOIProvider:
    """高德景点候选源（2026-11-01 收编重构 U1：链账 poi.search 链首）。

    高德「搜索POI 2.0」景点类目（AMAP_ATTRACTION_TYPES：风景名胜+科教文化）
    返回评分/营业时间/坐标——**票价与游玩时长高德不提供**，占位值与腾讯源
    同纪律（unverified + 默认时长），不推断不编造。检索失败/未配置 Key 返回
    空（调用方降级到链上下一位 = seed），绝不抛穿。
    """

    name = "amap:live"

    def is_enabled(self) -> bool:
        from backend.config.map import is_amap_configured

        return is_amap_configured()

    def resolve(self, name: str, city: str, *, required: bool = False) -> Poi | None:
        from backend.config.map import AMAP_ATTRACTION_TYPES
        from backend.infra.lbs import amap as amap_lbs

        for keywords in (name, f"{city} {name}"):
            records = amap_lbs.place_text(
                keywords, region=city, types=AMAP_ATTRACTION_TYPES,
                page_size=5,
            )
            for rec in records or []:
                poi = self._to_poi(rec, city)
                if poi is not None and _name_hit(poi.name, name):
                    poi.required = poi.required or required
                    return poi
        return None

    def search(
        self,
        city: str,
        preferences: list[str] | None = None,
        avoid: list[str] | None = None,
        must_go: list[str] | None = None,
        limit: int = 20,
    ) -> list[Poi]:
        from backend.config.map import AMAP_ATTRACTION_TYPES
        from backend.infra.lbs import amap as amap_lbs

        if not self.is_enabled():
            logger.debug("[TravelPOIProvider] amap 未配置 Key，返回空")
            return []
        records = amap_lbs.place_text(
            "景点", region=city, types=AMAP_ATTRACTION_TYPES,
            page_size=min(max(limit, 1), 25),
        )
        if not records:
            return []
        pois: list[Poi] = []
        for rec in records:
            poi = self._to_poi(rec, city)
            if poi is None:
                continue
            if avoid and any(token in poi.name for token in avoid):
                continue
            if must_go and any(_name_hit(poi.name, m) for m in must_go):
                poi.required = True
            pois.append(poi)
            if len(pois) >= limit:
                break
        return pois

    def _to_poi(self, rec: dict, city: str) -> Poi | None:
        from datetime import datetime, timezone

        from backend.tools.travel.live_map import map_category

        name = str(rec.get("name") or "").strip()
        lat, lng = rec.get("lat"), rec.get("lng")
        if not name or lat is None or lng is None:
            return None
        observed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        open_hours = str(rec.get("open_time_today") or "").strip()
        poi = Poi(
            poi_id=f"amap:{rec.get('id') or f'{name}|{lat}|{lng}'}",
            name=name,
            city=str(rec.get("city") or city),
            category=map_category(str(rec.get("category") or "")),
            lat=float(lat),
            lng=float(lng),
            suggested_minutes=120,   # 占位默认（高德无游玩时长事实）
            ticket_cny=0.0,          # 占位默认（高德无票价事实；unverified 标注）
            tags=[],
            rating=float(rec.get("rating") or 0.0),
            source="amap:live",
            observed_at=observed_at,
            verification_status="unverified",
            reason="高德景点类目检索",
            location_status="verified",   # 坐标来自权威 LBS（字段级拆分语义）
        )
        if open_hours:
            poi.tags = list(poi.tags) + [f"营业时间:{open_hours}（未核实）"]
        return poi


def _name_hit(candidate: str, target: str) -> bool:
    """名称命中：去空白包含判定（「灵隐寺」⊂「灵隐寺·飞来峰」）。"""
    c = (candidate or "").strip()
    t = (target or "").strip()
    return bool(c and t) and (t in c or c in t)


class FallbackPOIProvider:
    """链式 POI Provider：主源成功/有结果即用，否则降级下一源（语义如实留痕）。"""

    name = "fallback"

    def __init__(self, primary: POIProvider, fallback: POIProvider) -> None:
        self._primary = primary
        self._fallback = fallback

    @property
    def chain(self) -> tuple[str, str]:
        return self._primary.name, self._fallback.name

    def resolve(self, name: str, city: str, *, required: bool = False) -> Poi | None:
        try:
            poi = self._primary.resolve(name, city, required=required)
        except Exception as e:  # noqa: BLE001 — 主源异常按不可用降级
            logger.warning("[TravelPOIProvider] 主源 %s resolve 失败: %s",
                           self._primary.name, e)
            poi = None
        return poi if poi is not None else self._fallback.resolve(
            name, city, required=required)

    def search(
        self,
        city: str,
        preferences: list[str] | None = None,
        avoid: list[str] | None = None,
        must_go: list[str] | None = None,
        limit: int = 20,
    ) -> list[Poi]:
        try:
            pois = self._primary.search(
                city, preferences=preferences, avoid=avoid,
                must_go=must_go, limit=limit)
        except Exception as e:  # noqa: BLE001
            logger.warning("[TravelPOIProvider] 主源 %s search 失败: %s",
                           self._primary.name, e)
            pois = []
        return pois if pois else self._fallback.search(
            city, preferences=preferences, avoid=avoid,
            must_go=must_go, limit=limit)


def verified_poi(poi: Poi) -> Poi:
    """显式把一条 POI 标为已核实（种子数据构建时使用，语义自文档化）。"""
    poi.verification_status = VERIFIED
    return poi
