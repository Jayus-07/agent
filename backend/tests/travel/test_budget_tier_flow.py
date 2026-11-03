"""tests/travel/test_budget_tier_flow.py — 平价档（budget）档位贯通单测
（travel-value-showcase B2，2026-11-04）

覆盖：口语触发词 → 档位抽取（「穷游」触发平价档点测）、档位标签与
profile 守卫、展示侧零「穷游」红线。纯函数层，不触网不连库。
"""
from __future__ import annotations

from backend.config import travel as T
from backend.travel.agents.requirement_agent import extract_tier
from backend.travel.models.brief import TIER_KEYWORDS, TIER_LABELS


# ============================================================
# 口语触发 → 档位（B2 验收：「穷游」触发平价档）
# ============================================================
def test_qiongyou_oral_triggers_budget_tier():
    """「穷游」及平价口语（花小钱/越便宜越好/抠门/预算紧张）→ budget。"""
    for message in ("我想穷游", "带爸妈花小钱玩福州", "预算越便宜越好",
                    "抠门一点也行", "预算紧张，2 天", "预算有限", "最低预算能多少"):
        assert extract_tier(message) == "budget", message


def test_shengqian_moved_to_budget_tier():
    """「省钱」按 TASK 词表归平价档（原属 economy，有意变更）。"""
    assert extract_tier("帮我省钱") == "budget"


def test_economy_and_comfortable_keywords_unchanged():
    """economy/comfortable 触发词零回归（红线 4）。"""
    assert extract_tier("经济实惠一点") == "economy"
    assert extract_tier("性价比高些") == "economy"
    assert extract_tier("住得舒适一点") == "comfortable"
    assert extract_tier("正常玩") is None


def test_budget_specific_wins_over_economy_words():
    """平价专属表述与 economy 词同现时优判平价（budget 在词表首位）。"""
    assert extract_tier("穷游式经济出行") == "budget"


# ============================================================
# 标签与 profile 守卫（红线 2：展示侧零「穷游」）
# ============================================================
def test_tier_labels_guard_no_qiongyou_in_display():
    """标签守卫：三档齐全；展示名「平价出行」；全部标签零「穷游」字样。"""
    assert TIER_LABELS == {
        "economy": "经济实用型", "budget": "平价出行", "comfortable": "舒适均衡型",
    }
    assert all("穷游" not in label for label in TIER_LABELS.values())
    assert all("穷游" not in str(profile.get("label")) for profile in T.TIER_PROFILES.values())


def test_tier_profile_budget_guarded():
    """budget profile 守卫：检索意图与 economy 同构、付费体验压到最低。"""
    profile = T.TIER_PROFILES["budget"]
    assert profile["label"] == "平价出行"
    assert profile["meal_price_band"] == (0, 45)
    assert profile["paid_experience_budget_share"] == 0.05
    assert profile["cost_multiplier"] == {"meals": 1.0, "lodging": 1.0}
    assert profile["transport_priority"] == ["transit", "walk", "drive"]
    assert T.BUDGET_POLICY["tier_floor_multiplier"]["budget"] == 1.0


def test_tier_keywords_only_in_trigger_table():
    """「穷游」只允许出现在输入触发词表（brief.TIER_KEYWORDS），不出现在
    任何展示性常量（标签/profile label）里——红线 2 的静态面。"""
    assert "穷游" in TIER_KEYWORDS["budget"]
    assert "穷游" not in TIER_KEYWORDS["economy"]


# ============================================================
# 排程适配（B3）：budget 档免费 POI 权重加成
# ============================================================
def _skeleton_order(brief_tier: str) -> list[str]:
    from backend.travel.models.brief import TravelBrief
    from backend.travel.models.poi import Poi
    from backend.travel.services.poi_service import build_skeleton

    candidates = [
        Poi(poi_id="paid", name="收费点", city="福州", lat=26.0, lng=119.3,
            rating=5.0, suggested_minutes=90, ticket_cny=50.0, tags=[]),
        Poi(poi_id="free", name="免费点", city="福州", lat=26.05, lng=119.3,
            rating=4.5, suggested_minutes=90, ticket_cny=0.0, tags=[]),
    ]
    brief = TravelBrief(destination="福州", days=1, party_size=2, tier=brief_tier)
    skeleton = build_skeleton(brief, candidates)
    return [p.poi_id for p in skeleton.days[0]]


def test_budget_tier_prefers_free_pois():
    """budget 档：免门票候选在同优先级组内排前（热度略低也先落地）。"""
    assert _skeleton_order("budget")[0] == "free"


def test_other_tiers_keep_rating_order_no_regression():
    """economy/comfortable：排序键不变（红线 4），收费高分点仍在前。"""
    assert _skeleton_order("economy")[0] == "paid"
    assert _skeleton_order("comfortable")[0] == "paid"
