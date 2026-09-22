# -*- coding: utf-8 -*-
"""test_score_calibration.py — 工具路由分数校准单测（2026-09-22 D6 修复）

覆盖：特征命中计算 / 向量分数校准（加分、零命中降权、非 opt-in 零变化）/
VectorRouter 校准接入（桩 store，离线）/ hierarchical 唯一强信号直通（含
MEDIUM 风险不直通门槛）/ rule_router data.collect 组强/弱信号。
全部离线（不碰 pgvector / embedding / LLM）。
"""
import pytest

from backend.orchestration.router.hierarchical import HierarchicalRouter, resolve_domain_tools
from backend.orchestration.router.rule_router import RuleRouter
from backend.orchestration.router.score_calibration import (
    BONUS_PER_HIT,
    STRONG_SIGNAL_HITS,
    ZERO_HIT_DEMOTION,
    calibrate_scores,
    compute_signal,
)
from backend.orchestration.router.types import ExecutionMode


# ── 特征命中计算 ──────────────────────────────────────────────

class TestComputeSignal:
    def test_sql_statistical_features(self):
        hits = compute_signal("统计本月订单金额", ["sql.query", "data.collect"])
        assert hits["sql.query"] >= 2
        assert hits.get("data.collect", 0) == 0

    def test_collect_features(self):
        hits = compute_signal("导入供应商商品信息", ["sql.query", "data.collect"])
        assert hits["data.collect"] >= 1
        assert hits.get("sql.query", 0) == 0

    def test_non_opted_capability_absent(self):
        """rag.search 未 opt-in 校准 → 即使命中其 rule_keywords 也不出现。"""
        hits = compute_signal("员工报销制度是什么", ["rag.search", "sql.query"])
        assert "rag.search" not in hits


# ── 分数校准 ──────────────────────────────────────────────────

class TestCalibrateScores:
    def test_d6_drift_flipped(self):
        """D6 漂移场景：data.collect 0.697 > sql.query 0.55 → 校准后 sql 反超。"""
        raw = {"data.collect": 0.697, "sql.query": 0.55}
        calibrated, info = calibrate_scores("统计本月订单金额", raw)
        assert calibrated["sql.query"] > calibrated["data.collect"]
        assert info["hits"]["sql.query"] >= 2
        assert info["adjusted"]["sql.query"] > 0
        assert info["adjusted"]["data.collect"] < 0  # 零命中降权

    def test_bonus_cap_and_clamp(self):
        raw = {"sql.query": 0.9}
        calibrated, info = calibrate_scores("统计本月订单金额总和数量", {"sql.query": 0.9})
        assert calibrated["sql.query"] == pytest.approx(
            min(0.9 + BONUS_PER_HIT * 3, 1.0), abs=1e-3)
        assert "data.collect" not in info["hits"]

    def test_zero_demotion_requires_strong_competitor(self):
        """仅 1 命中时零命中候选不降权（无强信号竞争者）。"""
        raw = {"data.collect": 0.7, "sql.query": 0.5}
        calibrated, _ = calibrate_scores("帮我采集竞品价格", raw)
        assert calibrated["data.collect"] == pytest.approx(0.7 + BONUS_PER_HIT, abs=1e-3)
        assert calibrated["sql.query"] == pytest.approx(0.5, abs=1e-3)

    def test_all_zero_hits_noop(self):
        raw = {"data.collect": 0.7, "sql.query": 0.5}
        calibrated, info = calibrate_scores("今天天气怎么样", raw)
        assert calibrated == raw
        assert info == {}

    def test_non_opted_scores_untouched(self):
        raw = {"rag.search": 0.8, "web.search": 0.6}
        calibrated, info = calibrate_scores("员工报销制度是什么", raw)
        assert calibrated == raw
        assert info == {}


# ── VectorRouter 校准接入（桩 store，离线）─────────────────────

class _FakeDoc:
    def __init__(self, text: str, capability: str):
        self.page_content = text
        self.metadata = {"capability": capability}


class _FakeStore:
    def __init__(self, pairs: list[tuple[str, str, float]]):
        # pairs: (example_text, capability, distance)
        self._pairs = pairs

    def count(self) -> int:
        return len(self._pairs)

    def similarity_search_with_score(self, query: str, k: int = 3):
        return [(_FakeDoc(t, c), d) for t, c, d in self._pairs[:k]]


def _vector_router_with(pairs):
    from backend.orchestration.router.vector_router import VectorRouter

    vr = object.__new__(VectorRouter)  # 跳过 __init__（不碰 pgvector）
    vr.collection_name = "router_index"
    vr._store = _FakeStore(pairs)
    return vr


class TestVectorRouterCalibration:
    def test_d6_query_top1_calibrated_to_sql(self):
        """向量原始分 data.collect 领先，校准后 top1 必须是 sql.query。"""
        vr = _vector_router_with([
            ("采集最近一个月的订单数据", "data.collect", 0.4347),  # score≈0.697
            ("统计本月订单金额", "sql.query", 0.8182),            # score≈0.55
        ])
        decision = vr.route("统计本月订单金额")
        assert decision.candidates[0].name == "sql.query"
        assert decision.candidates[1].name == "data.collect"
        assert "calibrated" in (decision.reason or "")

    def test_collect_query_not_flipped(self):
        """采集类问题：data.collect 命中特征，sql.query 零命中 → 不反转。"""
        vr = _vector_router_with([
            ("导入供应商商品信息", "data.collect", 0.3),   # score≈0.769
            ("统计本月订单金额", "sql.query", 0.7),        # score≈0.588
        ])
        decision = vr.route("帮我导入供应商商品信息")
        assert decision.candidates[0].name == "data.collect"


# ── hierarchical：唯一强信号直通 ──────────────────────────────

class TestHierarchicalStrongSignal:
    def test_d6_direct_to_sql_query(self):
        """细路由向量分漂移（data.collect 领先）时，唯一强信号兜住 sql.query。"""
        router = HierarchicalRouter()
        router._fine_scores = lambda q, cands: {
            c.name: (0.697 if c.name == "data.collect" else 0.55) for c in cands}
        sel = router.select_tool(
            "统计本月订单金额", "data", resolve_domain_tools("data"))
        assert sel.fine_top1 == "sql.query"
        assert sel.route_mode == "fast_path"
        assert sel.calibration["basis"] == "rule_strong_signal"
        assert sel.calibration["strong"] == ["sql.query"]

    def test_strong_signal_medium_risk_not_fast_path(self):
        """强信号候选是 MEDIUM / fast_path_enabled=false（data.collect）→ 仍走灰区。"""
        router = HierarchicalRouter()
        from backend.orchestration.router.hierarchical import ToolCandidate

        cands = [ToolCandidate(name="data.collect", risk_level="MEDIUM",
                               fast_path_enabled=False)]
        router._fine_scores = lambda q, c: {"data.collect": 0.7}
        sel = router.select_tool("导入并同步外部供应商数据", "data", cands)
        assert sel.fine_top1 == "data.collect"
        assert sel.route_mode == "llm_selection"
        assert sel.calibration["basis"] == "grey_zone"

    def test_ambiguous_strong_signals_no_override(self, monkeypatch):
        """两个候选都强信号 → 不强行置顶任何一方，交灰区判定。"""
        import backend.orchestration.router.score_calibration as sc

        monkeypatch.setattr(
            sc, "compute_signal",
            lambda q, names: {"sql.query": 2, "data.collect": 2})
        router = HierarchicalRouter()
        router._fine_scores = lambda q, cands: {
            c.name: (0.7 if c.name == "data.collect" else 0.62) for c in cands}
        sel = router.select_tool(
            "采集订单数据并导入系统", "data", resolve_domain_tools("data"))
        assert sel.route_mode == "llm_selection"
        assert sel.calibration["basis"] == "grey_zone"
        assert len(sel.calibration["strong"]) == 2


# ── rule_router：yaml 声明组（data.collect）────────────────────

class TestRuleRouterCollectGroup:
    def test_collect_strong_signal(self):
        d = RuleRouter().route("导入并同步供应商库存数据")
        assert d is not None
        assert d.candidates[0].name == "data.collect"
        assert d.confidence >= 0.85

    def test_collect_weak_signal_hint(self):
        d = RuleRouter().route("导入供应商商品信息")
        assert d is not None
        assert d.candidates[0].name == "data.collect"
        assert d.confidence < 0.8  # 弱信号 hint，交向量/LLM

    def test_sql_query_still_wins_for_stats(self):
        """统计类问题不被采集组拦截。"""
        d = RuleRouter().route("统计本月订单金额")
        assert d is not None
        assert d.candidates[0].name == "sql.query"
        assert d.confidence >= 0.9

    def test_no_collect_keywords_untouched(self):
        d = RuleRouter().route("退款审核时间是多少？")
        assert d is not None
        assert d.candidates[0].name == "rag.search"
