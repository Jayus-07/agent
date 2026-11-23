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


class TestStrictFieldsByModule:
    """strict 校验是 RAG 数据契约（expected_answer/ground_truth）；SQL 题集
    标注为 gold_sql/expected_tables，套用会误杀全部样本（生产 rel-ebe174ee
    实证）。SQL 必须关闭 strict，RAG 保持强制。"""

    def _build(self, module: str):
        from backend.evaluation.mode import resolve_evaluation_mode
        from backend.evaluation.prompt_release_runner import _build_eval_config

        release = _Release(dataset_provenance={"version": "sql-canonical-15"})
        release.eval_suite = "sql_canonical"
        release.release_id = "rel-test"
        release.prompt_snapshot = {}
        return _build_eval_config(
            release, module, resolve_evaluation_mode("self+ragas"), {}, None,
        )

    def test_rag_keeps_strict_fields(self):
        assert self._build("rag").strict_fields is True

    def test_sql_disables_strict_fields(self):
        assert self._build("sql").strict_fields is False


# ── 批次 6d：veto_rules 一票否决 ─────────────────────────────


class _CaseResult:
    """逐用例替身：只带 veto 引擎消费的字段。"""

    def __init__(self, case_id: str, metrics: dict | None = None, error_msg: str | None = None):
        self.case_id = case_id
        self.metrics = metrics or {}
        self.error_msg = error_msg


def _report_with_results(results, metadata=None):
    report = _Report(metadata=metadata)
    report.results = results
    return report


def test_veto_blocks_gate_despite_perfect_pass_rate():
    """对抗用例未正确拒绝 → 一票否决，整体通过率 100% 也拦（方案 §13.3 核心）。"""
    report = _report_with_results([
        _CaseResult("SC-001", metrics={"safety_pass": 1.0}),
        _CaseResult("SC-007", metrics={"safety_pass": 0.0}, error_msg="期望拒绝状态 ['no_table']，实际 status=success"),
        _CaseResult("SC-013", metrics={"safety_pass": 0.0}, error_msg="期望拒绝状态 ['no_table']，实际 status=success"),
    ])
    gate = _assemble_release_gate(
        report, _Summary(pass_rate=1.0), _Release(),
        module="sql", current_summary=_Summary(pass_rate=0.5), current_expected=True,
    )
    assert gate["veto_pass"] is False
    assert gate["veto"]["veto_pass"] is False
    assert len(gate["veto"]["violations"]) == 2
    assert {v["case_id"] for v in gate["veto"]["violations"]} == {"SC-007", "SC-013"}
    rules = [r["rule"] for r in gate["blocked_rules"]]
    assert "veto:safety_reject_violation" in rules
    assert _gate_passed(gate, "sql") is False


def test_veto_defaults_enabled_and_clean_report_passes():
    """未声明 veto_rules → 默认启用 safety 规则；无违规时门禁行为不变。"""
    report = _report_with_results([
        _CaseResult("SC-001", metrics={"safety_pass": 1.0}),
        _CaseResult("SC-002", metrics={}),  # 正向用例无 safety 标注 → 不命中
    ])
    gate = _assemble_release_gate(
        report, _Summary(pass_rate=1.0), _Release(),
        module="sql", current_summary=_Summary(pass_rate=0.5), current_expected=True,
    )
    assert gate["veto_pass"] is True
    assert gate["veto"]["violations"] == []
    assert gate["veto"]["enabled"] == ["safety_reject_violation"]
    assert _gate_passed(gate, "sql") is True


def test_veto_explicit_disable_via_suite_declaration():
    """suite JSON veto_rules=[] 显式关闭 → 不扫描不拦截。"""
    report = _report_with_results(
        [_CaseResult("SC-007", metrics={"safety_pass": 0.0})],
        metadata={"suite_governance": {"veto_rules": []}},
    )
    gate = _assemble_release_gate(
        report, _Summary(pass_rate=1.0), _Release(),
        module="sql", current_summary=_Summary(pass_rate=0.5), current_expected=True,
    )
    assert gate["veto_pass"] is True
    assert gate["veto"]["enabled"] == []
    assert _gate_passed(gate, "sql") is True


def test_veto_rag_zero_hit_keeps_legacy_behavior():
    """RAG results 暂无 safety_pass 标注 → 零命中、RAG 门禁行为不变。"""
    report = _report_with_results([
        _CaseResult("RC-001", metrics={"recall_at_5": 0.8}),
    ])
    gate = _assemble_release_gate(report, _Summary(), _Release(), module="rag")
    assert gate["veto_pass"] is True
    assert gate["tier_pass"] is True
    assert gate["sample_pass"] is True
    assert _gate_passed(gate, "rag") is True


def test_veto_tolerates_report_without_results():
    """report 无 results 属性（旧替身/异常报告）→ 按零命中处理不炸门禁。"""
    report = _Report()  # 无 results
    gate = _assemble_release_gate(
        report, _Summary(pass_rate=1.0), _Release(),
        module="sql", current_summary=_Summary(pass_rate=0.5), current_expected=True,
    )
    assert gate["veto_pass"] is True
    assert _gate_passed(gate, "sql") is True


def test_veto_rule_name_constant_frozen():
    """口径常量防漂移：suite 声明与 runner 内置实现用同一规则名。"""
    from backend.evaluation.prompt_release_runner import SAFETY_VETO_RULE
    assert SAFETY_VETO_RULE == "safety_reject_violation"
