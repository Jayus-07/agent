"""test_m16_domain_switch_dynamic.py — 域开关迁 sys_config 动态读（M16/D16）

锁：三个 prefilter 总闸与 CS 灰度判定读 sys_config（DB 覆盖免重启生效），
而非 config 模块导入期常量。DB 覆盖 > env 默认；总闸关闭恒 None（降级主路由）。

旅游/选品 prefilter 命中链是纯规则（零 IO），可端到端翻转断言；CS 灰度
用 _in_rollout 直测（百分比动态读），CS 总闸用「env 开 + DB 关 → 恒 None」
验证覆盖方向（None 仅可能来自总闸——同问句在 DB 未覆盖时进入灰度链路）。
"""
from __future__ import annotations

import pytest

from backend.services import sys_config


@pytest.fixture(autouse=True)
def _reset_switch_cache():
    """_values 是模块级缓存：前后各清一次，防污染其他测试。"""
    sys_config.reset_cache_for_tests()
    yield
    sys_config.reset_cache_for_tests()


def _db_override(key: str, value: str) -> None:
    """模拟 refresh_once 已拉到 DB 覆盖值（set_value 的缓存写入路径）。"""
    sys_config._values[key] = value


TRAVEL_QUERY = "帮我规划厦门两天的行程"
SELECTION_QUERY = "给宠物零食做一次智能选品"
CS_QUERY = "帮我查一下订单1001的物流进度"


class TestTravelPrefilterDynamic:
    def test_db_off_blocks_even_if_env_true(self, monkeypatch):
        monkeypatch.setenv("TRAVEL_ENABLED", "true")
        _db_override("TRAVEL_ENABLED", "false")
        from backend.orchestration.graph.travel_prefilter import try_travel_prefilter

        assert try_travel_prefilter(TRAVEL_QUERY, {}) is None

    def test_db_on_opens_without_restart(self, monkeypatch):
        """env 未设置（false）+ DB 覆盖 true → prefilter 立即放行（免重启）。"""
        monkeypatch.delenv("TRAVEL_ENABLED", raising=False)
        _db_override("TRAVEL_ENABLED", "true")
        from backend.orchestration.graph.travel_prefilter import try_travel_prefilter

        result = try_travel_prefilter(TRAVEL_QUERY, {})
        assert result is not None
        assert result.get("route_mode") == "travel"


class TestSelectionPrefilterDynamic:
    def test_default_closed(self, monkeypatch):
        # 根 .env 可能开着该开关：测试基线显式 delenv（env 未设置=default false）
        monkeypatch.delenv("SELECTION_FUNNEL_ENABLED", raising=False)
        from backend.orchestration.graph.selection_funnel_prefilter import (
            try_selection_funnel_prefilter,
        )

        assert try_selection_funnel_prefilter(SELECTION_QUERY, {}) is None

    def test_db_on_opens_without_restart(self):
        _db_override("SELECTION_FUNNEL_ENABLED", "true")
        from backend.orchestration.graph.selection_funnel_prefilter import (
            try_selection_funnel_prefilter,
        )

        result = try_selection_funnel_prefilter(SELECTION_QUERY, {})
        assert result is not None
        assert result.get("route_mode") == "selection_funnel"


class TestCsGateAndRolloutDynamic:
    def test_db_off_blocks_even_if_env_true(self, monkeypatch):
        monkeypatch.setenv("CS_ENABLED", "true")
        _db_override("CS_ENABLED", "false")
        from backend.orchestration.graph.cs_prefilter import try_cs_prefilter

        # 总闸关恒 None（降级主路由），锁域入口也不例外（AGENTS：锁域仍受总闸）
        assert try_cs_prefilter(CS_QUERY, {}) is None
        assert try_cs_prefilter(CS_QUERY, {}, forced=True) is None
        # 对照：清 DB 覆盖回 env true → 锁域入口立即放行（总闸是唯一变量）
        sys_config.reset_cache_for_tests()
        assert try_cs_prefilter(CS_QUERY, {}, forced=True) is not None

    def test_rollout_zero_blocks_non_whitelist(self, monkeypatch):
        monkeypatch.setenv("CS_ROLLOUT_PERCENT", "100")
        _db_override("CS_ROLLOUT_PERCENT", "0")
        from backend.config.customer_service import CS_ROLLOUT_WHITELIST
        from backend.orchestration.graph.cs_prefilter import _in_rollout

        # 测试环境白名单为空集（env 未设置）→ 任何会话都走百分比路径
        assert CS_ROLLOUT_WHITELIST == set()
        assert _in_rollout("some-random-session") is False

    def test_rollout_hundred_via_db(self):
        _db_override("CS_ROLLOUT_PERCENT", "100")
        from backend.orchestration.graph.cs_prefilter import _in_rollout

        assert _in_rollout("any-session") is True

    def test_rollout_env_fallback_when_no_db(self, monkeypatch):
        """无 DB 覆盖时 env 兜底（_env_default 自动读同名 env）。"""
        monkeypatch.setenv("CS_ROLLOUT_PERCENT", "0")
        from backend.orchestration.graph.cs_prefilter import _in_rollout

        assert _in_rollout("non-whitelisted-session") is False
