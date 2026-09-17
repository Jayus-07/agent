"""tests/test_map_lookup_skill.py — map.lookup 聚合 Skill 的离线回归

关注三件容易静默失效的事：
  1. 能力 ↔ 图节点 ↔ 路由样例 三处注册必须齐全（漏一处 Planner 就看不到）
  2. action 白名单与必填参数在 Skill 边界就拦住，不让错误穿到底层 API
  3. 未配置 Key 时每个 action 都给明确提示，而不是抛异常或静默空值
"""
import json

import pytest

from backend.orchestration.router.types import ALL_CAPABILITIES
from backend.orchestration.router.vector_router import ROUTE_EXAMPLES
from backend.orchestration.capability_registry import tool_registry
from backend.skills.map.skill import ACTIONS, MapLookupSkill, map_lookup_tool


@pytest.fixture()
def skill():
    return MapLookupSkill()


# ── 注册一致性 ──────────────────────────────────────────────
def test_capability_registered_everywhere():
    assert "map.lookup" in ALL_CAPABILITIES
    assert "map.lookup" in ROUTE_EXAMPLES
    assert "map.lookup" in MapLookupSkill.capabilities
    assert tool_registry.get_node("map.lookup") == "map_lookup_skill"


def test_schema_requires_only_action():
    required = {k for k, v in MapLookupSkill.params_schema.items()
                if isinstance(v, dict) and v.get("required")}
    assert required == {"action"}


# ── 参数校验 ────────────────────────────────────────────────
def test_unknown_action_rejected(skill):
    out = json.loads(map_lookup_tool.invoke({"action": "teleport"}))
    assert "error" in out
    assert "teleport" in out["error"]
    assert "weather" in out["supported"]


def test_missing_required_param_rejected(skill):
    # geocode 需要 address，这里什么都不给
    out = json.loads(map_lookup_tool.invoke({"action": "geocode"}))
    assert "error" in out
    assert "address" in out["error"]


def test_all_actions_have_documented_requirements():
    assert set(ACTIONS) == {
        "weather", "geocode", "reverse_geocode", "place_search", "route",
        "navigation", "static_map", "district", "street_view",
    }


# ── 未配置 Key 时的降级（conftest 已全局切断 LBS）────────────
@pytest.mark.parametrize("action,params", [
    ("weather", {"city": "福州"}),
    ("geocode", {"address": "三坊七巷", "city": "福州"}),
    ("reverse_geocode", {"location": "26.08,119.29"}),
    ("place_search", {"keyword": "咖啡", "city": "福州"}),
    ("route", {"from_location": "26.08,119.29", "to_location": "26.05,119.39"}),
    ("navigation", {"to_location": "26.05,119.39"}),
    ("static_map", {"location": "26.08,119.29"}),
    ("district", {"keyword": "鼓楼区"}),
    ("street_view", {"location": "26.08,119.29"}),
])
def test_every_action_degrades_when_not_configured(action, params):
    raw = map_lookup_tool.invoke({"action": action, **params})
    out = json.loads(raw)
    assert "error" in out, f"{action} 未配置时应返回 error，实际: {raw[:200]}"
    assert "未配置" in out["error"] or "不可用" in out["error"]
