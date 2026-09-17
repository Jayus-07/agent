# -*- coding: utf-8 -*-
"""test_router_prefilter_order.py — 预过滤顺序与检测缓存回归（2026-09-15）

背景：CS 检测器向量通道每次请求一次云端 embedding（实测 1.0~3.4s），
而旅游预过滤是纯正则（~1ms）。原顺序无条件先跑完整 CS 检测 → 旅游/普通
请求白烧一次 embedding。优化后顺序：
  1) CS 廉价规则预判 → 命中则完整 CS 检测（保客服优先）
  2) 旅游纯正则预过滤 → 命中短路
  3) 都没命中 → 完整 CS 检测（向量语义兜底）
本测试锁定：旅游请求不再触发 CS 检测；含 CS 规则的请求仍走 CS 优先；
detect_cached 对同 query 只调一次真实检测。
"""
from unittest.mock import MagicMock

import pytest

from backend.customer_service.router import domain_detector as dd
from backend.orchestration.graph import router_node as rn


def _fake_detection(is_cs: bool = False):
    d = MagicMock()
    d.is_cs = is_cs
    d.confidence = 0.9
    d.domain = "AFTER_SALES"
    d.rule_hits = []
    d.rule_score = 0.0
    d.vector_score = 0.0
    d.reason = ""
    return d


@pytest.fixture
def fake_detector(monkeypatch):
    """注入假检测器：记录 detect/向量通道调用次数。"""
    det = MagicMock()
    det.rule_hit_count = 0
    det._rule_channel.return_value = ([], 0.0)
    det.detect.return_value = _fake_detection(is_cs=False)
    monkeypatch.setattr(dd, "get_domain_detector", lambda: det)
    monkeypatch.setattr(dd, "_DETECT_CACHE", {})
    return det


@pytest.fixture
def travel_on(monkeypatch):
    import backend.config.travel as tc
    monkeypatch.setattr(tc, "TRAVEL_ENABLED", True)


@pytest.fixture
def cs_on(monkeypatch):
    import backend.config.customer_service as cc
    monkeypatch.setattr(cc, "CS_ENABLED", True)


class TestPrefilterOrder:
    def test_travel_request_skips_cs_detection(self, fake_detector, travel_on, cs_on):
        """旅游问题：CS 规则未命中 → 旅游正则短路 → 不烧 CS 向量检测。"""
        out = rn.router_node({"question": "帮我规划杭州2天旅游行程", "session_id": "s1"})
        assert out.get("route_mode") == "travel"
        fake_detector.detect.assert_not_called()

    def test_cs_rule_hit_still_wins(self, fake_detector, travel_on, cs_on):
        """含 CS 规则的 query：仍先走 CS 检测（优先级不变）。"""
        fake_detector._rule_channel.return_value = (["AFTER_SALES"], 0.33)
        rn.router_node({"question": "订单里的行程单怎么退款", "session_id": "s2"})
        fake_detector.detect.assert_called()

    def test_generic_query_falls_back_to_cs_vector(self, fake_detector, travel_on, cs_on):
        """普通问题：规则未命中且非旅游 → 仍走完整 CS 检测（语义兜底）。"""
        rn.router_node({"question": "这个季度的经营状况怎么样", "session_id": "s3"})
        fake_detector.detect.assert_called()


class TestDetectCache:
    def test_same_query_detected_once(self, monkeypatch):
        det = MagicMock()
        det.detect.return_value = _fake_detection(is_cs=False)
        monkeypatch.setattr(dd, "get_domain_detector", lambda: det)
        monkeypatch.setattr(dd, "_DETECT_CACHE", {})

        dd.detect_cached("退款怎么处理")
        dd.detect_cached("退款怎么处理")
        assert det.detect.call_count == 1  # 第二次命中缓存

    def test_different_query_not_cached_together(self, monkeypatch):
        det = MagicMock()
        det.detect.return_value = _fake_detection(is_cs=False)
        monkeypatch.setattr(dd, "get_domain_detector", lambda: det)
        monkeypatch.setattr(dd, "_DETECT_CACHE", {})

        dd.detect_cached("退款怎么处理")
        dd.detect_cached("发票怎么开")
        assert det.detect.call_count == 2

    def test_cache_expiry_re_detects(self, monkeypatch):
        det = MagicMock()
        det.detect.return_value = _fake_detection(is_cs=False)
        monkeypatch.setattr(dd, "get_domain_detector", lambda: det)
        monkeypatch.setattr(dd, "_DETECT_CACHE", {})
        monkeypatch.setattr(dd, "_DETECT_CACHE_TTL", 0.0)  # 立即过期

        dd.detect_cached("退款怎么处理")
        dd.detect_cached("退款怎么处理")
        assert det.detect.call_count == 2


class TestCheapRuleCount:
    def test_rule_count_does_not_touch_vector(self, fake_detector):
        """cs_rule_hit_count 必须只走规则通道（不触发 embedding）。"""
        fake_detector._rule_channel.return_value = (["KNOWLEDGE"], 0.33)
        assert dd.cs_rule_hit_count("怎么退货") == 1
        fake_detector.detect.assert_not_called()

    def test_empty_query(self, fake_detector):
        assert dd.cs_rule_hit_count("") == 0
        fake_detector._rule_channel.assert_not_called()


class TestDomainHintLock:
    """客服窗口锁域（2026-09-18）：domain_hint=customer_service 强制 CS 入口。

    背景：CSDrawer 与主问答共用 /chat/stream，此前抽屉内每条消息重新判域，
    "下周去大阪怎么玩"会被旅游 prefilter 抢走（域漏判进旅游域图硬答）。
    """

    @pytest.fixture(autouse=True)
    def _no_cs_router_cache(self):
        """关掉 cs_router 模块级缓存（同 test_cs_router.py 手法），防跨测试污染。"""
        from backend.customer_service.router import cs_router as cs_router_mod
        original_get = cs_router_mod._cs_cache.get_json
        original_set = cs_router_mod._cs_cache.set_json
        cs_router_mod._cs_cache.get_json = lambda key: None
        cs_router_mod._cs_cache.set_json = lambda key, value: None
        yield
        cs_router_mod._cs_cache.get_json = original_get
        cs_router_mod._cs_cache.set_json = original_set

    def test_domain_hint_forces_cs_past_travel_and_failed_detection(
        self, fake_detector, travel_on, cs_on, monkeypatch,
    ):
        """非客服问法 + 域检测漏判 + 旅游 prefilter 可路由 → 仍强制进 CS。"""
        import backend.config.customer_service as cc
        monkeypatch.setattr(cc, "CS_ROLLOUT_PERCENT", 0)
        monkeypatch.setattr(cc, "CS_ROLLOUT_WHITELIST", set())

        out = rn.router_node({
            "question": "帮我规划杭州2天旅游行程",
            "session_id": "s-lock",
            "domain_hint": "customer_service",
        })
        assert out.get("route_mode") == "customer_service"

    def test_domain_hint_skips_travel_even_without_rule_hit(
        self, fake_detector, travel_on, cs_on,
    ):
        """域锁下旅游 prefilter 不参与（灰度默认配置，不额外 patch）。"""
        out = rn.router_node({
            "question": "帮我规划杭州2天旅游行程",
            "session_id": "s-lock2",
            "domain_hint": "customer_service",
        })
        assert out.get("route_mode") == "customer_service"
        fake_detector.detect.assert_called()  # 域检测仍执行，作 coarse hint

    def test_domain_hint_degrades_to_main_router_when_cs_disabled(
        self, fake_detector, travel_on, monkeypatch,
    ):
        """CS 总闸关闭：域锁降级回主路由（旅游 prefilter 也被跳过）。"""
        import backend.config.customer_service as cc
        monkeypatch.setattr(cc, "CS_ENABLED", False)

        def _boom():
            raise RuntimeError("router off in test")
        monkeypatch.setattr(rn, "get_router", _boom)

        out = rn.router_node({
            "question": "退款怎么处理",
            "session_id": "s-lock-off",
            "domain_hint": "customer_service",
        })
        assert out.get("route_mode") == "plan"  # 主 router 异常兜底路径

    def test_no_domain_hint_keeps_legacy_order(
        self, fake_detector, travel_on, cs_on,
    ):
        """不带 domain_hint 的全局入口：行为与旧顺序完全一致。"""
        out = rn.router_node({
            "question": "帮我规划杭州2天旅游行程", "session_id": "s-legacy",
        })
        assert out.get("route_mode") == "travel"
