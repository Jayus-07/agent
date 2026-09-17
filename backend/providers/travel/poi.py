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


def verified_poi(poi: Poi) -> Poi:
    """显式把一条 POI 标为已核实（种子数据构建时使用，语义自文档化）。"""
    poi.verification_status = VERIFIED
    return poi
