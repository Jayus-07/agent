"""tests/travel/test_quality_golden.py — STOP I5 质量门禁（任务书 §51）

跑全量旅游金标（34 条，G 组为 STOP I 新增的 12 条质量场景），聚合 Q1-Q10
确定性指标并断言门禁。这是 STOP I 的回归门：任何让行程质量退化的改动
（数据源、排程、校验、修复、抽取）在这里 FAIL。

阈值口径（不拍脑袋，据 I0 审计 + 本数据集真实能力冻结）：
  - 任务书硬门：valid_poi_rate=1.0 / must_go_coverage=1.0 /
    avoid_violation_rate=0 / duplicate_rate=0 / day_count_accuracy=1.0 /
    hard_constraint_pass_rate>=0.98 / unsupported_fact_rate<=0.1
  - Q6 地理紧凑度：单日在途分钟峰值 ≤ TRAVEL_DAY_MAX_TRANSIT_MINUTES(150)
    —— 与 validator GEO_SCATTER 同一阈值（同一口径两处消费是刻意的：
    金标验证「产出行程不越过校验阈值」，两值若分叉会掩盖真相）
  - Q7 单日负载：活动+在途 ≤ 一天时间窗（TRAVEL_DAY_START→TRAVEL_DAY_END，
    默认 08:30-21:30 = 780 分钟）—— 排程器把条目顺序排在窗内，负载
    超窗即时间轴 bug；上限即窗口物理约束，非拍脑袋数字
  - Q8：预算可计算场景合规或显式标注（budget_silent_over 必须 = 0）

测试环境纪律：离线（天气/RAG/LBS/真实路线全关）——金标验证的是确定性
管线的质量，外部数据源的可用性属于 e2e_travel_runtime 的实测范围。
"""
from __future__ import annotations

import pytest

from backend.config import travel as T
from backend.evaluation.dataset.loader import (
    DATASET_DIR,
    _load_jsonl,
    verify_dataset_integrity,
)
from backend.evaluation.runners.travel import _run_case
from backend.evaluation.travel_quality import aggregate
from backend.tests.travel.test_travel_dataset import (
    _next_monday_text,
    _normalize_e_group_dates,
)

# ============================================================
# 阈值冻结（修改须同步更新最终验收报告 §10）
# ============================================================
GATE = {
    "valid_poi_rate": 1.0,
    "must_go_coverage": 1.0,
    "avoid_violation_rate": 0.0,
    "duplicate_rate": 0.0,
    "day_count_accuracy": 1.0,
    "hard_constraint_pass_rate": 0.98,
    # 预算无法自动调整时会披露结构化行程外的「最低需要约 ¥X」下限；
    # 该金额来自约束求解结果，不冒充行程实际费用，按诚实披露口径允许
    # 当前金标中预算冲突组的 0.1 比例。
    "unsupported_fact_rate": 0.1,
    "budget_silent_over": 0,
}
MAX_INTRADAY_TRANSIT = 150   # = TRAVEL_DAY_MAX_TRANSIT_MINUTES
MAX_DAILY_LOAD = 780         # 一天时间窗（08:30→21:30）


@pytest.fixture(scope="module", autouse=True)
def _offline_travel_env():
    """金标离线纪律：外部数据源全关，只验证确定性管线。

    module 级手动 MonkeyPatch（function 级 monkeypatch fixture 不能进
    module 作用域）；undo 保证不影响其他测试模块。
    """
    import backend.tools.travel.live_map as live_map
    from backend.tools.travel import routing

    mp = pytest.MonkeyPatch()
    mp.setattr(T, "TRAVEL_WEATHER_ENABLED", False)
    mp.setattr(T, "TRAVEL_RAG_ENABLED", False)
    mp.setattr(live_map, "is_enabled", lambda: False)
    mp.setattr(routing, "_route_provider", None)
    yield
    mp.undo()


_GOLDEN_CACHE: tuple | None = None


def _golden_quality_cached():
    """全量金标只跑一遍（module 内两组断言共享同一份结果）。"""
    global _GOLDEN_CACHE
    if _GOLDEN_CACHE is None:
        _GOLDEN_CACHE = _golden_quality()
    return _GOLDEN_CACHE


def _golden_quality():
    """跑全部金标 → (逐例结果, 聚合指标)。供本模块两组断言共用。"""
    # 质量金标按 DATA-01 新版本目录冻结；canonical travel 数据集保留旧版本，
    # 本轮产品文案/排程演进写入 travel_v2，不原地改写既有评测集。
    evolved_dir = DATASET_DIR / "travel_v2"
    verify_dataset_integrity("travel_v2", evolved_dir)
    cases = _load_jsonl(evolved_dir / "cases.jsonl", default_module="travel")
    _normalize_e_group_dates({c.id: c for c in cases})
    # G 组日期锚与 E 组同漂移问题：周一闭馆场景需要真实周一
    for c in cases:
        if c.metadata.get("group") == "G" and "9月28日" in c.question:
            c.question = c.question.replace("9月28日", _next_monday_text())

    results = [_run_case(c) for c in cases]
    qualities: list[dict] = []
    expectations: list[dict] = []
    ids: list[str] = []
    for case, result in zip(cases, results):
        turns = result.actual.get("turns", [])
        exps = [case.expected]
        meta = case.metadata or {}
        if meta.get("followup"):
            exps.append(meta.get("followup_expected") or {})
        # 逐轮聚合（追问轮 has_itinerary=False 自动被聚合层跳过）
        for turn, exp in zip(turns, exps):
            qualities.append(turn.get("quality") or {"has_itinerary": False})
            expectations.append(exp)
            ids.append(case.id)
    return results, aggregate(qualities, expectations, ids)


def test_golden_contract_all_pass():
    """第一层门：全部用例的契约断言（既有 runner 判定）必须全过。"""
    results, _ = _golden_quality_cached()
    failed = [(r.case_id, r.error_msg) for r in results if r.status != "pass"]
    assert not failed, f"金标契约失败: {failed}"


def test_quality_gate_thresholds():
    """第二层门：Q1-Q10 聚合指标达到冻结阈值（任务书 §51）。"""
    _, agg = _golden_quality_cached()
    assert agg["cases_total"] >= 30, f"金标不足 30 条: {agg['cases_total']}"
    assert agg["cases_produced"] >= 20, \
        f"产出行程的用例过少: {agg['cases_produced']}"

    for key, threshold in GATE.items():
        got = agg.get(key)
        if key == "unsupported_fact_rate":
            assert got <= threshold, (
                f"{key}={got}，门禁 ≤{threshold}（聚合: {agg}）"
            )
        elif isinstance(threshold, float) and threshold in (0.0, 1.0):
            assert got == threshold, f"{key}={got}，门禁 {threshold}（聚合: {agg}）"
        elif key == "hard_constraint_pass_rate":
            assert got >= threshold, f"{key}={got}，门禁 ≥{threshold}（聚合: {agg}）"
        else:
            assert got == threshold, f"{key}={got}，门禁 {threshold}（聚合: {agg}）"

    assert agg["max_intraday_transit"] <= MAX_INTRADAY_TRANSIT
    assert agg["max_daily_load"] <= MAX_DAILY_LOAD
