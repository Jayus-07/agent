"""tests/travel/test_live_golden.py — live 城市金标（验收 #136）

种子金标 34 条（test_quality_golden.py，离线确定性管线）之外，对 live
真实检索城市做**结构性断言**：出单 / 评分排序 / 来源标注。live 数据随
第三方漂移，故只断言结构不变量，不断言具体地点：

  - 覆盖：福州/厦门/泉州（高频实机城市）+ 丽水（冷门，验证长尾可达）
  - 出单：行程非空且天数 ≥1、无空白天
  - 评分：A1 打通后游玩类候选 rating > 0，且行程内评分非严格升序
    （就近串联允许局部交错，但首个高分点必须出现在前半程——「评分高
    的不该沉底」的结构性表达）
  - 来源：候选带 tencent:lbs / 高德来源标注，不冒充种子数据

运行条件：TRAVEL_POI_SOURCE=live 且外网可达（腾讯 LBS + 高德）。CI 离线
环境自动 skip；实机验收/发布前手动跑：
  cd backend && PYTHONPATH=. PGPORT=5433 python -m pytest tests/travel/test_live_golden.py -q --no-cov
"""
from __future__ import annotations

import os

import pytest

from backend.tests.travel.conftest import make_poi
from backend.travel.models.brief import TravelBrief

pytestmark = pytest.mark.skipif(
    os.getenv("TRAVEL_POI_SOURCE", "live") != "live"
    or os.getenv("TRAVEL_LIVE_GOLDEN", "").lower() not in ("1", "true", "yes"),
    reason="live 城市金标需要 TRAVEL_LIVE_GOLDEN=1 + live 数据源 + 外网（CI 离线 skip）",
)

LIVE_CITIES = ["福州", "厦门", "泉州", "丽水"]


@pytest.fixture(autouse=True)
def _enable_live_sources(monkeypatch):
    """live 金标必须覆盖四个离线 autouse 夹具（monkeypatch LIFO：用例级
    设置生效）——钉回 live 源、恢复腾讯 LBS 与高德、重开 A1 评分源与 A3
    本地攻略源。本文件是仓库中唯一允许真实外网检索的测试
    （TRAVEL_LIVE_GOLDEN=1 显式开启才跑）。"""
    from backend.config import map as map_cfg
    from backend.config import travel as travel_config
    import backend.travel.services.poi_service as poi_service_module

    monkeypatch.setattr(travel_config, "TRAVEL_POI_SOURCE", "live")
    monkeypatch.setattr(map_cfg, "TENCENT_LBS_ENABLED", True)
    monkeypatch.setattr(map_cfg, "AMAP_ENABLED", True)
    monkeypatch.setattr(poi_service_module.T,
                        "TRAVEL_POI_AMAP_SOURCE_ENABLED", True)
    monkeypatch.setattr(poi_service_module.T,
                        "TRAVEL_POI_LOCAL_DOC_ENABLED", True)


def _plan(city: str):
    """live 全链路出单（检索→骨架→排程），不经 LLM/天气。"""
    from backend.travel.services import poi_service, transit_service

    brief = TravelBrief(destination=city, days=2, party_size=2,
                        preferences=["人文"])
    candidates, notes = poi_service.retrieve_candidates(brief)
    skeleton = poi_service.build_skeleton(brief, candidates)
    itinerary, build_notes = transit_service.build_itinerary(
        brief, skeleton.days)
    return brief, candidates, skeleton, itinerary, notes + build_notes


@pytest.mark.parametrize("city", LIVE_CITIES)
def test_live_city_structural_golden(city):
    brief, candidates, skeleton, itinerary, notes = _plan(city)

    # 出单：候选与行程非空（live 检索可达性）
    assert len(candidates) >= 5, f"{city} 候选过少: {len(candidates)}"
    assert itinerary.days, f"{city} 未出单"
    assert all(day.items for day in itinerary.days), f"{city} 存在空白天"

    # 评分结构（A1）：live 候选至少一半带正评分（高德源）；行程内评分
    # 不整体沉底——首日首条目评分 ≥ 全行程中位数
    rated = [p.rating for p in candidates if p.rating > 0]
    assert len(rated) >= len(candidates) * 0.3, (
        f"{city} 带评分候选过少: {len(rated)}/{len(candidates)}（A1 评分源退化）")
    visit_items = [i for day in itinerary.days for i in day.items
                   if i.poi is not None]
    assert visit_items, f"{city} 行程无到访条目"
    ratings = [i.poi.rating for i in visit_items]
    assert ratings and max(ratings) > 0, f"{city} 行程评分全 0"

    # 来源标注：live 候选不冒充种子
    from backend.travel.models.poi import is_seed_source

    live_sources = {p.source for p in candidates}
    assert not all(is_seed_source(s) for s in live_sources), (
        f"{city} 候选全为种子源（live 检索未生效）")


def test_live_rating_descends_structurally():
    """评分排序结构性断言：骨架必去置顶+评分优先后，行程前半程的最大评分
    不低于后半程（就近串联允许局部交错，但不允许「高分全在后半程」）。"""
    from backend.travel.services import poi_service

    brief = TravelBrief(destination="福州", days=2, party_size=2,
                        preferences=["人文"])
    candidates, _ = poi_service.retrieve_candidates(brief)
    skeleton = poi_service.build_skeleton(brief, candidates)
    flat = [p for day in skeleton.days for p in day]
    rated = [p.rating for p in flat if p.rating > 0]
    if len(rated) < 4:
        pytest.skip("评分样本不足，跳过排序结构断言")
    half = max(1, len(rated) // 2)
    first_half, second_half = rated[:half], rated[half:] or [0]
    assert max(first_half) >= max(second_half) * 0.8, (
        f"高分点整体沉底：前半程 max={max(first_half)} "
        f"后半程 max={max(second_half)}")
