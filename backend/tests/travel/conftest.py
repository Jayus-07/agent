"""tests/travel/conftest.py — 旅游域测试公共构造器

用工厂函数而非 fixture 字典：测试里要临时改某一个字段（如把开放时间
改成 18:00 来触发 TIME_CLOSED）时，工厂的默认参数比复制一坨字典清晰得多。
"""
from __future__ import annotations

from datetime import date

import pytest

from backend.travel.models.brief import TravelBrief
from backend.travel.services import poi_service as poi_service_module


@pytest.fixture(autouse=True)
def _pin_seed_poi_source(monkeypatch: pytest.MonkeyPatch):
    """travel 域测试默认钉回种子候选源（确定性夹具）。

    生产默认 TRAVEL_POI_SOURCE=live（腾讯 LBS 实时检索，结果非确定且
    依赖外网）；本目录测试统一钉回 seed 保证断言可复现。需要测 live
    行为的用例（test_live_poi_source.py）在用例内自行 monkeypatch 覆盖
    ——monkeypatch 的 LIFO 顺序保证用例级设置生效。
    """
    from backend.config import travel as travel_config

    monkeypatch.setattr(travel_config, "TRAVEL_POI_SOURCE", "seed")


@pytest.fixture(autouse=True)
def _disable_zhihu_mcp(monkeypatch: pytest.MonkeyPatch):
    """测试期切断知乎官方 MCP（与根 conftest 的 _disable_tencent_lbs 同口径）。

    背景：2026-10-03 起攻略检索改为规划主链自动触发（不再依赖用户消息
    含「攻略」触发词），开发机 .env 开着 ZHIHU_MCP_ENABLED 时，本目录
    每个端到端用例都会真打知乎接口（3 站内 + 1 全网），消耗配额且结果
    随网络漂移。攻略是增强信息：开关关闭时单路降级为 error 披露，正是
    测试要验证的路径。需要测知乎行为的用例自行替换 tool/service 层
    （test_live_guides.py 的 fake_tools、test_intent.py 的 service patch）。
    """
    from backend.config import mcp as mcp_config

    monkeypatch.setattr(mcp_config, "ZHIHU_MCP_ENABLED", False)


@pytest.fixture(autouse=True)
def _disable_travel_tool_cache(monkeypatch: pytest.MonkeyPatch):
    """测试期关闭 tool 封套缓存（Redis 是外部边界，与上面两个夹具同口径）。

    背景：cached_envelope 读的是容器共享 Redis（travel_tool_cache），实机
    验证写入的缓存（TTL 24h）会让单测假红——mock 掉 Tool 之后 _invoke
    仍先查缓存命中旧封套，实测 2026-10-04 test_live_guides 4 例 DID NOT
    RAISE / calls[0] IndexError。真实缓存链路由实机验收覆盖，单测一律直调。
    """
    from backend.config import travel as travel_config

    monkeypatch.setattr(travel_config, "TRAVEL_TOOL_CACHE_ENABLED", False)


from backend.travel.models.itinerary import (
    Itinerary,
    ItineraryDay,
    ItineraryItem,
    KIND_MEAL,
    KIND_VISIT,
    TransitLeg,
)
from backend.travel.models.poi import Poi

# 2026-09-14 是周一（weekday()==0）—— 用于闭馆日判定
MONDAY = date(2026, 9, 14)
TUESDAY = date(2026, 9, 15)


def make_poi(
    poi_id: str = "p1",
    name: str = "测试景点",
    open_time: str = "09:00",
    close_time: str = "17:00",
    closed_weekdays: tuple[int, ...] = (),
    suggested_minutes: int = 90,
    ticket_cny: float = 0.0,
    required: bool = False,
    rating: float = 4.0,
    lat: float = 26.0,
    lng: float = 119.0,
    source: str = "seed:local",
    category: str = "景点",
    tags: list[str] | None = None,
) -> Poi:
    return Poi(
        poi_id=poi_id, name=name, city="测试城", category=category,
        lat=lat, lng=lng, open_time=open_time, close_time=close_time,
        closed_weekdays=list(closed_weekdays),
        suggested_minutes=suggested_minutes, ticket_cny=ticket_cny,
        tags=tags or [], rating=rating, required=required, source=source,
    )


def make_item(
    title: str = "测试景点",
    start: str = "09:00",
    end: str = "10:30",
    kind: str = KIND_VISIT,
    poi: Poi | None = None,
    wait_minutes: int = 0,
    note: str = "",
) -> ItineraryItem:
    return ItineraryItem(
        title=title, kind=kind, start=start, end=end,
        minutes=_diff(start, end), wait_minutes=wait_minutes,
        poi=poi, note=note,
    )


def make_meal(start: str = "12:00", end: str = "13:00") -> ItineraryItem:
    return make_item(title="午餐", start=start, end=end, kind=KIND_MEAL, poi=None)


def _diff(start: str, end: str) -> int:
    def _m(hhmm: str) -> int:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)
    return max(0, _m(end) - _m(start))


def make_leg(
    from_title: str = "A", to_title: str = "B",
    minutes: int = 20, distance_km: float = 5.0,
    mode: str = "drive", cost_cny: float = 25.0,
) -> TransitLeg:
    return TransitLeg(
        from_title=from_title, to_title=to_title, minutes=minutes,
        distance_km=distance_km, mode=mode, cost_cny=cost_cny,
    )


def make_day(
    day_index: int = 1,
    items: list[ItineraryItem] | None = None,
    legs: list[TransitLeg] | None = None,
    day_date: date | None = None,
) -> ItineraryDay:
    day = ItineraryDay(
        day_index=day_index, day_date=day_date,
        items=items or [], legs=legs or [],
    )
    # 与 schedule_day 保持同一口径：活动时长只计到访项
    day.active_minutes = sum(i.minutes for i in day.items if i.kind == KIND_VISIT)
    day.transit_minutes = sum(leg.minutes for leg in day.legs)
    return day


def make_itinerary(
    brief: TravelBrief | None = None,
    days: list[ItineraryDay] | None = None,
) -> Itinerary:
    return Itinerary(brief=brief or TravelBrief(destination="测试城", days=1),
                     days=days or [])


@pytest.fixture
def simple_brief() -> TravelBrief:
    return TravelBrief(destination="测试城", days=1, party_size=1, pace="moderate")


@pytest.fixture(autouse=True)
def _memory_context_repo(monkeypatch):
    """STOP G 测试隔离：ConversationContext repository 换进程内 Memory。

    生产默认 backend=redis（宿主机 .env REDIS_ENABLED=true 会连真 Redis）；
    单元测试只 mock 外部边界（Redis）。多 worker / 真 Redis 行为见
    tests/test_stop_g4_matrix.py 显式实测（不走本 fixture）。
    """
    import backend.orchestration.context.context_repository as repo_mod
    from backend.orchestration.context.context_repository import (
        MemoryConversationContextRepository,
    )

    repo = MemoryConversationContextRepository(ttl_seconds=1800, max_entries=100)
    monkeypatch.setattr(repo_mod, "_repo", repo)
    yield repo
    monkeypatch.setattr(repo_mod, "_repo", None)


@pytest.fixture(autouse=True)
def _isolated_provider_cache(monkeypatch):
    """STOP J 测试隔离：Provider 共享缓存换进程内空实例。

    生产 backend 是 Redis（TwoTierCache），单测若直连会把测试数据写进
    共享栈、且用例间互相污染（实测：先跑的成功用例缓存了路线，后面的
    失败用例命中缓存而跳过失败路径）。多 worker 共享缓存行为见 STOP J
    专项实测（不走本 fixture）。
    """
    from backend.infra.cache.backend import InMemoryCache
    from backend.providers.travel.live import cache as pcache

    store = InMemoryCache(default_ttl=600)  # 单实例：写读同源
    monkeypatch.setattr(pcache, "_backend", lambda: store)
    yield


@pytest.fixture(autouse=True)
def _disable_amap_attraction_source(monkeypatch: pytest.MonkeyPatch):
    """travel 域测试默认关掉高德景点评分源（A1）与本地攻略源（A3）。

    生产默认两源均开；开启时候选检索会真打高德商户 API / 本地攻略
    doc_registry+腾讯补坐标（结果非确定且依赖外网），本目录测试统一关闭
    保证断言可复现——需要测合并行为的用例在用例内自行 monkeypatch 覆盖。
    """
    monkeypatch.setattr(
        poi_service_module.T, "TRAVEL_POI_AMAP_SOURCE_ENABLED", False)
    monkeypatch.setattr(
        poi_service_module.T, "TRAVEL_POI_LOCAL_DOC_ENABLED", False)


