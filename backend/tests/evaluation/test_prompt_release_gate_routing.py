"""发布门禁模块路由回归测试（2026-11-22 SQL 双跑对比口径）。

覆盖：
- prompt category → 评测模块路由（SQL 门禁此前硬编码跑 RAG 题集，评的不对题）
- SQL 门禁：候选 vs 现网双跑对比门 + 防全挂下限 + 样本量门 + RAGAS N/A
- RAG 门禁行为不变（tier 阈值门 + 样本量门 + 回归门 + RAGAS 门）
- gate 结构向后兼容（旧调用签名 / _gate_passed 按模块聚合）
"""
from __future__ import annotations

import pytest

from backend.evaluation.prompt_release_runner import (
    SQL_ABSOLUTE_FLOOR,
    SQL_CANDIDATE_REGRESSION_THRESHOLD,
    _assemble_release_gate,
    _gate_passed,
    _resolve_eval_module,
)
from backend.evaluation.models import ModuleKind  # noqa: F401 — ModuleKind 单源契约引用


# ── 测试替身构造 ──────────────────────────────────────────────


class _Summary:
    def __init__(self, metrics=None, pass_rate=1.0, total=15, errors=0, skipped=0):
        self.metrics = metrics or {}
        self.pass_rate = pass_rate
        self.total = total
        self.errors = errors
        self.skipped = skipped


class _Tier:
    def __init__(
        self,
        tier: str = "smoke",
        passed: bool = True,
        pass_rate: float = 1.0,
        threshold: float = 0.95,
        total: int = 5,
        passed_count: int | None = None,
    ):
        self.tier = tier
        self.passed_threshold = passed
        self.pass_rate = pass_rate
        self.threshold = threshold
        self.total = total
        self.passed = passed_count if passed_count is not None else (
            round(total * pass_rate)
        )
        self.gate_reasons = [] if passed else [f"通过率 {pass_rate:.1%} 低于阈值"]


class _Report:
    def __init__(self, tier_summaries=None, metadata=None):
        self.tier_summaries = tier_summaries if tier_summaries is not None else [_Tier()]
        self.metadata = metadata or {}


class _Release:
    def __init__(self, dataset_provenance=None):
        self.dataset_provenance = dataset_provenance or {}


# ── 模块路由 ──────────────────────────────────────────────────


def test_routing_sql_prompt_routes_to_sql_module():
    assert _resolve_eval_module("sql.generator") == "sql"
    assert _resolve_eval_module("sql.router") == "sql"


def test_routing_rag_prompts_keep_rag_module():
    assert _resolve_eval_module("rag.qa") == "rag"
    assert _resolve_eval_module("rag.document") == "rag"


def test_routing_unknown_category_falls_back_to_rag():
    # 未登记映射的 category（planner/reporter/router 等）回落 rag 维持历史行为
    assert _resolve_eval_module("planner.planning") == "rag"
    assert _resolve_eval_module("nonexistent.prompt") == "rag"


# ── SQL 双跑对比门 ────────────────────────────────────────────


def _sql_gate(candidate_rate, current_rate, *, current_expected=True, current_summary=True):
    summary = _Summary(pass_rate=candidate_rate)
    current = None
    if current_expected and current_summary:
        current = _Summary(pass_rate=current_rate)
    report = _Report(tier_summaries=[
        _Tier("smoke", passed=True, pass_rate=max(candidate_rate, 0.0), threshold=0.95),
        _Tier("core", passed=True, pass_rate=max(candidate_rate, 0.0), threshold=0.85),
        _Tier("hard", passed=True, pass_rate=max(candidate_rate, 0.0), threshold=0.70),
    ])
    return (
        _assemble_release_gate(
            report, summary, _Release(),
            module="sql", current_summary=current, current_expected=current_expected,
        ),
        summary,
    )


def test_sql_gate_passes_when_candidate_not_below_current():
    gate, summary = _sql_gate(candidate_rate=0.60, current_rate=0.40)
    assert gate["candidate_regression_pass"] is True
    assert gate["sample_pass"] is True
    assert gate["absolute_floor_pass"] is True
    assert gate["blocked_rules"] == []
    assert _gate_passed(gate, "sql") is True
    assert gate["tier_gate_mode"] == "display_only"
    # RAGAS 对 SQL 不适用：落痕 + publish 侧跳过标志
    assert gate["ragas_applicable"] is False
    assert gate["ragas"]["not_applicable"] is True


def test_sql_gate_blocks_candidate_below_current():
    # 候选比现网低超过允许降幅 → blocked（rule=candidate_regression）
    current_rate = 0.50
    candidate_rate = current_rate - SQL_CANDIDATE_REGRESSION_THRESHOLD - 0.01
    gate, _ = _sql_gate(candidate_rate=candidate_rate, current_rate=current_rate)
    assert gate["candidate_regression_pass"] is False
    rules = [r["rule"] for r in gate["blocked_rules"]]
    assert "candidate_regression" in rules
    assert _gate_passed(gate, "sql") is False


def test_sql_gate_passes_within_allowed_drop():
    # 降幅恰好等于允许阈值（边界）→ 放行
    current_rate = 0.50
    candidate_rate = current_rate - SQL_CANDIDATE_REGRESSION_THRESHOLD
    gate, _ = _sql_gate(candidate_rate=candidate_rate, current_rate=current_rate)
    assert gate["candidate_regression_pass"] is True
    assert _gate_passed(gate, "sql") is True


def test_sql_gate_blocks_absolute_floor():
    # smoke 层低于防全挂下限 → blocked（rule=absolute_floor）
    summary = _Summary(pass_rate=0.10)
    report = _Report(tier_summaries=[
        _Tier("smoke", passed=True, pass_rate=0.10, threshold=0.95),
    ])
    gate = _assemble_release_gate(
        report, summary, _Release(),
        module="sql", current_summary=_Summary(pass_rate=0.10),
        current_expected=True,
    )
    assert gate["absolute_floor_pass"] is False
    rules = [r["rule"] for r in gate["blocked_rules"]]
    assert "absolute_floor" in rules
    assert _gate_passed(gate, "sql") is False
    assert SQL_ABSOLUTE_FLOOR == 0.20  # 口径常量防漂移


def test_sql_gate_degrades_when_current_release_unavailable():
    # 现网从未发布（无 active 版本）：对比门显式降级放行并留痕
    gate, _ = _sql_gate(
        candidate_rate=0.50, current_rate=0.0,
        current_expected=False, current_summary=False,
    )
    assert gate["candidate_regression_pass"] is True
    assert gate["candidate_regression"]["regression_pass"] is None
    assert "current_release_unavailable" in gate["candidate_regression"]["note"]
    assert _gate_passed(gate, "sql") is True


def test_sql_gate_fails_closed_when_current_eval_missing():
    # 现网双跑已执行但未产出摘要：数据缺失不能假装对比成功
    gate, _ = _sql_gate(
        candidate_rate=0.50, current_rate=0.0,
        current_expected=True, current_summary=False,
    )
    assert gate["candidate_regression_pass"] is False
    rules = [r["rule"] for r in gate["blocked_rules"]]
    assert "candidate_regression" in rules
    assert _gate_passed(gate, "sql") is False


def test_sql_gate_blocks_insufficient_samples():
    # 样本量门与 RAG 同口径（GATE-12）：valid < EVAL_MIN_SAMPLES → blocked
    summary = _Summary(pass_rate=1.0, total=5)
    report = _Report(tier_summaries=[_Tier("smoke", pass_rate=1.0, total=5)])
    gate = _assemble_release_gate(
        report, summary, _Release(),
        module="sql", current_summary=None, current_expected=False,
    )
    assert gate["sample_pass"] is False
    rules = [r["rule"] for r in gate["blocked_rules"]]
    assert "min_samples" in rules
    assert _gate_passed(gate, "sql") is False


def test_sql_gate_tier_display_only_never_blocks():
    # tier 层通过率再低也不阻塞 SQL 发布（display_only）：分层结果仅展示
    summary = _Summary(pass_rate=0.40)
    report = _Report(tier_summaries=[
        _Tier("smoke", passed=False, pass_rate=0.30, threshold=0.95),
        _Tier("core", passed=False, pass_rate=0.50, threshold=0.85),
        _Tier("hard", passed=False, pass_rate=0.30, threshold=0.70),
    ])
    gate = _assemble_release_gate(
        report, summary, _Release(),
        module="sql", current_summary=_Summary(pass_rate=0.30),
        current_expected=True,
    )
    rules = [r["rule"] for r in gate["blocked_rules"]]
    assert "tier_threshold" not in rules
    assert {t["tier"] for t in gate["tiers"]} == {"smoke", "core", "hard"}


# ── RAG 门禁行为不变（回归）──────────────────────────────────


def test_rag_gate_still_enforces_tier_thresholds():
    summary = _Summary(pass_rate=0.9, total=20)
    report = _Report(tier_summaries=[
        _Tier("smoke", passed=True, pass_rate=1.0),
        _Tier("core", passed=False, pass_rate=0.80, threshold=0.85),
    ])
    gate = _assemble_release_gate(report, summary, _Release())
    assert gate["tier_pass"] is False
    rules = [r["rule"] for r in gate["blocked_rules"]]
    assert "tier_threshold" in rules
    assert "candidate_regression" not in rules
    assert "tier_gate_mode" not in gate
    assert _gate_passed(gate, "rag") is False


def test_rag_gate_pass_keeps_legacy_shape():
    summary = _Summary(pass_rate=1.0, total=20)
    report = _Report(tier_summaries=[_Tier()])
    gate = _assemble_release_gate(report, summary, _Release())
    assert gate["tier_pass"] is True
    assert gate["sample_pass"] is True
    assert _gate_passed(gate, "rag") is True


# ── 门禁分派 ──────────────────────────────────────────────────


def test_gate_dispatch_by_module():
    summary = _Summary(pass_rate=0.5)
    report = _Report(tier_summaries=[_Tier("smoke", pass_rate=0.5)])
    rag_gate = _assemble_release_gate(report, summary, _Release(), module="rag")
    sql_gate = _assemble_release_gate(
        report, summary, _Release(),
        module="sql", current_summary=_Summary(pass_rate=0.5), current_expected=True,
    )
    assert "candidate_regression" in sql_gate
    assert "candidate_regression" not in rag_gate
    assert rag_gate.get("ragas_applicable", True) is True
    assert sql_gate["ragas_applicable"] is False
