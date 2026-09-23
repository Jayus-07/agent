"""travel_quality.py — 旅游行程质量度量（STOP I5，任务书 Q1-Q10）

定位：Golden 评测的**确定性**度量层。全部指标从图 final state（dict 形态，
与 evaluation.runners.travel._snapshot_actual 同源）计算，零 LLM judge——
must_go / avoid / duplicate / budget / POI existence / day count 这些硬口径
不允许模型评分（任务书 §49）。LLM judge 只可在其上做「自然与否」的辅助
主观评价，本项目暂未接入（留待后续，不属于质量门禁）。

消费方：
  - evaluation.runners.travel：逐例嵌入 quality 快照（CLI 报告可见）
  - tests/travel/test_quality_golden.py：质量门禁（聚合阈值断言）

原则（任务书 §71）：
  unknown != false；estimated != factual；best effort != constraint satisfied。
  Q10（unsupported fact）把「行程单里的 ¥ 数字必须能从结构化行程推导」
  作为可判定口径 —— reporter 是模板渲染，任何超出 itinerary/candidates
  数值集的金额都是事实创造。
"""
from __future__ import annotations

import re

from backend.tools.travel import poi_seed
from backend.travel.planning import (
    names_match,
    resolve_must_go,
    scheduled_must_go,
)

# final answer 中出现的金额（¥ 前缀）
_MONEY_RE = re.compile(r"¥\s*(\d+(?:\.\d+)?)")


def canonical_poi_ids(extra_candidates: list[dict] | None = None) -> set[str]:
    """canonical poi_id 全集 = 种子池 ∪ 本轮候选池（含 LBS 补全条目）。

    候选池是行程的合法输入投影：行程里出现的 poi_id 不在这里，即
    「凭空造出的地点」（Q1=0 的唯一情形，任务书 G3）。
    """
    ids: set[str] = set()
    for city in poi_seed.all_cities():
        ids.update(p.poi_id for p in poi_seed.load_city(city))
    for c in extra_candidates or []:
        if c.get("poi_id"):
            ids.add(c["poi_id"])
    return ids


def case_quality(final: dict) -> dict:
    """单例 Q1-Q10 度量（final state → 指标 dict；无行程时返回适用子集）。

    返回字段：
      has_itinerary            是否产出行程（追问轮 False，多数指标不适用）
      q1_valid_poi_rate        行程内 poi_id ∈ canonical 比例（目标 1.0）
      q2_must_go_coverage      可解析必去的排入比例（目标 1.0）
      q3_avoid_violations      行程命中 avoid 的条目数（目标 0）
      q4_duplicate_count       全行程重复安排条目数（目标 0）
      q5_day_count_accuracy    1.0/0.0（请求天数 == 交付天数；请求未知时 None）
      q6_max_intraday_transit  单日在途分钟峰值（地理紧凑度代理，目标 ≤150）
      q7_max_daily_load        单日活动+在途分钟峰值（体力负载）
      q8_budget                {"budget":…, "total":…, "compliant":bool,
                                "flagged":bool} —— flagged=系统如实标注超支
                                （BUDGET_OVER 在最终违反里），无预算 None
      q9_error_count           最终 error 级违反条数（0 = 硬约束全过）
      q10_unsupported_amounts  行程单中无法从结构化数据推导的金额列表（目标 []）
    """
    it = final.get("itinerary") or {}
    brief = final.get("brief") or {}
    validation = final.get("validation") or {}
    violations = validation.get("violations") or []
    candidates = final.get("candidates") or []
    answer = final.get("final_answer") or ""

    out: dict = {"has_itinerary": bool(it)}

    if not it:
        # 追问/澄清轮：多数指标 not_evaluable（None），聚合层跳过
        for key in ("q1_valid_poi_rate", "q2_must_go_coverage",
                    "q3_avoid_violations", "q4_duplicate_count",
                    "q5_day_count_accuracy", "q6_max_intraday_transit",
                    "q7_max_daily_load", "q8_budget"):
            out[key] = None
        out["q9_error_count"] = None
        out["q10_unsupported_amounts"] = None
        return out

    days = it.get("days") or []
    scheduled: list[dict] = [
        i["poi"] for d in days for i in (d.get("items") or [])
        if i.get("poi")
    ]
    scheduled_ids = [p.get("poi_id", "") for p in scheduled]
    scheduled_names = [p.get("name", "") for p in scheduled]

    # ── Q1 valid POI rate ──
    valid_ids = canonical_poi_ids(candidates)
    out["q1_valid_poi_rate"] = (
        round(sum(1 for pid in scheduled_ids if pid in valid_ids)
              / len(scheduled_ids), 4) if scheduled_ids else 1.0)

    # ── Q2 must-go coverage（可解析口径：数据里没有的不计入分母）──
    brief_model = _brief_of(brief)
    candidate_models = _poi_models(candidates)
    resolvable = resolve_must_go(brief_model, candidate_models).resolved
    if resolvable:
        got = scheduled_must_go(brief_model, _itinerary_stub(scheduled_names)).resolved
        out["q2_must_go_coverage"] = round(len(got) / len(resolvable), 4)
    else:
        out["q2_must_go_coverage"] = 1.0  # 无必去 = 空集覆盖（vacuous truth）

    # ── Q3 avoid violations ──
    out["q3_avoid_violations"] = sum(
        1 for name in scheduled_names
        if any(names_match(name, a) for a in brief.get("avoid") or []))

    # ── Q4 duplicates（全行程，poi_id 口径）──
    out["q4_duplicate_count"] = len(scheduled_ids) - len(set(scheduled_ids))

    # ── Q5 day count accuracy ──
    requested = brief.get("days")
    out["q5_day_count_accuracy"] = (
        1.0 if requested and len(days) == requested
        else (None if not requested else 0.0))

    # ── Q6 地理紧凑度：单日在途分钟峰值 ──
    transit_peaks = [sum(l.get("minutes", 0) for l in (d.get("legs") or []))
                     for d in days]
    out["q6_max_intraday_transit"] = max(transit_peaks) if transit_peaks else 0

    # ── Q7 单日负载：活动+在途分钟峰值 ──
    loads = [d.get("active_minutes", 0) + d.get("transit_minutes", 0)
             for d in days]
    out["q7_max_daily_load"] = max(loads) if loads else 0

    # ── Q8 budget compliance（可计算场景才判定）──
    budget = brief.get("budget_cny")
    if budget:
        total = (it.get("cost") or {}).get("total", 0.0)
        out["q8_budget"] = {
            "budget": budget,
            "total": total,
            "compliant": total <= budget,
            "flagged": any(v.get("code") == "BUDGET_OVER" for v in violations),
        }
    else:
        out["q8_budget"] = None

    # ── Q9 硬约束：最终 error 数（期望违反的用例由聚合层结合 expected 判定）──
    out["q9_error_count"] = sum(1 for v in violations if v.get("level") == "error")

    # ── Q10 unsupported facts：行程单金额 ⊥ 结构化数值集 ──
    out["q10_unsupported_amounts"] = _unsupported_amounts(answer, it, brief,
                                                          candidates)
    return out


def _unsupported_amounts(answer: str, it: dict, brief: dict,
                         candidates: list[dict]) -> list[float]:
    """答案中出现的、无法从 itinerary/brief/candidates 推导的金额。

    合法金额集 = 费用拆分与合计 + 预算 + 每日花费 + 各通勤段费用 +
    候选池门票单价（repair 说明会引用被移除项的票价）——全部按
    reporter 的 :.0f 格式化口径比对。
    """
    valid: set[float] = {0.0}

    def _add(*values) -> None:
        for v in values:
            if v is None:
                continue
            valid.add(round(float(v)))

    cost = it.get("cost") or {}
    _add(cost.get("tickets"), cost.get("meals"), cost.get("lodging"),
         cost.get("transit"), cost.get("total"))
    budget = brief.get("budget_cny")
    _add(budget)
    total = cost.get("total")
    if budget and total:
        # BUDGET_OVER 文案里的「超 ¥X」= total - budget（事实算术，可推导）
        _add(float(total) - float(budget))
    for d in it.get("days") or []:
        _add(d.get("cost_cny"))
        for leg in d.get("legs") or []:
            _add(leg.get("cost_cny"))
    for c in candidates or []:
        _add(c.get("ticket_cny"))

    found = [float(m.group(1)) for m in _MONEY_RE.finditer(answer)]
    return [x for x in found if not any(abs(x - v) < 0.5 for v in valid)]


def aggregate(qualities: list[dict], expectations: list[dict] | None = None,
              case_ids: list[str] | None = None) -> dict:
    """聚合 Q1-Q10（只统计产出行程的用例；Q9 可结合期望违反判定）。

    expectations/case_ids 与 qualities **按下标一一对应**（含被跳过的
    追问轮）——先配对再过滤，避免过滤后错位（实测：追问轮被剔除后
    expected 会串到别的用例头上，Q9 误判）。
    """
    exps = expectations or [{} for _ in qualities]
    paired = [
        (q, exps[i] if i < len(exps) else {})
        for i, q in enumerate(qualities) if q.get("has_itinerary")
    ]
    n = len(paired)
    if not n:
        return {"cases_total": len(qualities), "cases_produced": 0}
    produced = [q for q, _ in paired]

    def _rate(sum_values: float) -> float:
        return round(sum_values / n, 4)

    # Q9：期望违反/修复/决策的用例，出现预期内的 error 不算硬约束失败
    q9_ok = 0
    for q, expected in paired:
        declared = bool(expected.get("violation_codes")
                        or expected.get("repair_triggered")
                        or expected.get("decision_required"))
        if q.get("q9_error_count") == 0 or declared:
            q9_ok += 1

    budget_cases = [q["q8_budget"] for q in produced if q.get("q8_budget")]
    silent_over = sum(1 for b in budget_cases
                      if b and not b["compliant"] and not b["flagged"])

    return {
        "cases_total": len(qualities),
        "cases_produced": n,
        # Q1-Q5（目标 1.0 / 0）
        "valid_poi_rate": _rate(sum(q["q1_valid_poi_rate"] for q in produced)),
        "must_go_coverage": _rate(sum(q["q2_must_go_coverage"] for q in produced)),
        "avoid_violation_rate": _rate(sum(1 for q in produced if q["q3_avoid_violations"])),
        "duplicate_rate": _rate(sum(1 for q in produced if q["q4_duplicate_count"])),
        "day_count_accuracy": _rate(
            sum(1 for q in produced if q["q5_day_count_accuracy"] == 1.0)),
        # Q6/Q7 峰值（阈值据数据能力冻结，见 test_quality_golden）
        "max_intraday_transit": max(q["q6_max_intraday_transit"] for q in produced),
        "max_daily_load": max(q["q7_max_daily_load"] for q in produced),
        # Q8：预算合规（可计算场景）+ 静默超支数（必须 0）
        "budget_evaluable": len(budget_cases),
        "budget_compliance": _rate(sum(1 for b in budget_cases if b["compliant"])
                                   ) if budget_cases else None,
        "budget_silent_over": silent_over,
        # Q9/Q10
        "hard_constraint_pass_rate": round(q9_ok / n, 4),
        "unsupported_fact_rate": _rate(
            sum(1 for q in produced if q["q10_unsupported_amounts"])),
    }


# ============================================================
# 轻量替身（复用 planning 的判定，不引入 Itinerary 构造）
# ============================================================
class _ItineraryStub:
    """只暴露 all_pois() 的最小行程替身（scheduled_must_go 只用它）。"""

    def __init__(self, names: list[str]):
        self._names = names

    def all_pois(self):  # noqa: D102 — Poi 只被取 name
        from backend.travel.models.poi import Poi

        return [Poi(poi_id=f"stub_{i}", name=n, city="", lat=0, lng=0)
                for i, n in enumerate(self._names)]


def _itinerary_stub(names: list[str]) -> _ItineraryStub:
    return _ItineraryStub(names)


def _brief_of(brief: dict):
    from backend.travel.models.brief import TravelBrief

    return TravelBrief.model_validate(brief) if brief else TravelBrief()


def _poi_models(candidates: list[dict]) -> list:
    from backend.travel.models.poi import Poi

    models: list = []
    for c in candidates or []:
        try:
            models.append(Poi.model_validate(c))
        except Exception:
            continue
    return models
