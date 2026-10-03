"""customer_service/vocab_gate.py — 词表/规则层变更评测门（C7/C8/C10）。

三份回放集（backend/evaluation/datasets/cs/gate/，生成器 generate_golden.py）：
  - intent_triage_golden.json   C7 意图分诊（304 条，规则层域判定）
  - handoff_timing_golden.json  C8 转人工时机（50 条，漏转=0 / 误转<10%）
  - confirm_cancel_golden.json  C10 确认/取消词表门（45 条，热更 fail-closed）

转人工命中口径（规则面）：HUMAN 域粗路由命中（转人工意图）∪ P0 升级标记
（12315/曝光/报警）∪ ANGRY 强情绪标记——三者为既有词表已定义的规则信号；
纯上下文推理（连错 N 次/VIP）不属规则面，见 manifest 口径注。

热更门语义（fail-closed）：
  - `_write_override` 写盘前调 `evaluate_vocab_change`：候选词表在回放集上
    出现「基线对→候选错」回归或准确率跌破下限 → 拒绝写入（VocabGateRejected）；
  - 门禁基础设施自身故障（数据集缺失等）fail-open 放行 + error 留痕
    （可用性优先，与 hybrid ReviewFilter 同哲学），但门禁裁决本身 fail-closed。

CLI 基线：``python -m backend.customer_service.vocab_gate``（exit 1 = 门槛被破）。
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_DATASET_DIR = _HERE.parent / "evaluation" / "datasets" / "cs" / "gate"

# 词表热更允许的键（其余一律拒绝，防未知词表绕过门禁）
HOT_VOCAB_KEYS = frozenset({"confirm_keywords", "cancel_keywords"})

TRIAGE_ACCURACY_MIN = 0.90
HANDOFF_MISSED_MAX = 0
HANDOFF_FALSE_RATE_MAX = 0.10
CONFIRM_CANCEL_ACCURACY_MIN = 0.95


class VocabGateRejected(Exception):
    """词表变更未过评测门（fail-closed，变更未落盘）。"""


def _load(name: str) -> list[dict]:
    data = json.loads((_DATASET_DIR / name).read_text(encoding="utf-8"))
    return data["cases"]


# ── 回放器 ────────────────────────────────────────────────────────────

def _route_domain(text: str) -> str:
    from backend.customer_service.router.coarse_router import CSCoarseRouter
    domain, _conf, _reason = CSCoarseRouter().classify(text)
    return domain.value


def evaluate_triage(cases: list[dict] | None = None) -> dict:
    """C7：规则层域判定准确率（expected_domain 含 UNKNOWN=出域期望）。"""
    cases = cases if cases is not None else _load("intent_triage_golden.json")
    per_domain: dict[str, dict[str, int]] = {}
    fails: list[dict] = []
    for c in cases:
        got = _route_domain(c["text"])
        ok = got == c["expected_domain"]
        stat = per_domain.setdefault(c["expected_domain"], {"total": 0, "correct": 0})
        stat["total"] += 1
        if ok:
            stat["correct"] += 1
        else:
            fails.append({"id": c["id"], "text": c["text"],
                          "expected": c["expected_domain"], "got": got})
    total = len(cases)
    correct = sum(s["correct"] for s in per_domain.values())
    return {
        "suite": "intent_triage", "total": total, "correct": correct,
        "accuracy": correct / total if total else 0.0,
        "threshold": TRIAGE_ACCURACY_MIN, "pass": total > 0 and correct / total >= TRIAGE_ACCURACY_MIN,
        "per_domain": per_domain, "fails": fails,
    }


def _handoff_hit(text: str) -> bool:
    """规则面转人工/强升级命中。

    四信号（均为既有词表已定义的规则面信号）：
      - HUMAN 域粗路由命中（转人工意图）；
      - 主管角色词在场（经理/主管/领导/组长/负责人）——点名找人即接管信号，
        即使句中混有投诉/业务词被路由进其他域；
      - P0 升级标记（12315/曝光/报警）；
      - ANGRY 强情绪标记。
    纯上下文推理（连错 N 次/VIP）不属规则面，见 manifest 口径注。
    """
    from backend.customer_service.vocab import ANGRY_MARKERS, P0_ESCALATION_MARKERS
    if _route_domain(text) == "HUMAN":
        return True
    supervisor_roles = ("经理", "主管", "领导", "组长", "负责人", "店长")
    if any(r in text for r in supervisor_roles):
        return True
    return any(m in text for m in P0_ESCALATION_MARKERS) or any(m in text for m in ANGRY_MARKERS)


def evaluate_handoff(cases: list[dict] | None = None) -> dict:
    """C8：应转全转（漏转=0）+ 误转 <10%。"""
    cases = cases if cases is not None else _load("handoff_timing_golden.json")
    missed: list[dict] = []
    false_positives: list[dict] = []
    for c in cases:
        hit = _handoff_hit(c["text"])
        if c["should_handoff"] and not hit:
            missed.append({"id": c["id"], "text": c["text"], "trigger": c["trigger"]})
        elif not c["should_handoff"] and hit:
            false_positives.append({"id": c["id"], "text": c["text"], "trigger": c["trigger"]})
    n_false = sum(1 for c in cases if not c["should_handoff"])
    false_rate = len(false_positives) / n_false if n_false else 0.0
    return {
        "suite": "handoff_timing", "total": len(cases),
        "missed": len(missed), "missed_detail": missed,
        "false_positives_n": len(false_positives), "false_rate": false_rate,
        "false_detail": false_positives,
        "thresholds": {"missed_max": HANDOFF_MISSED_MAX, "false_rate_max": HANDOFF_FALSE_RATE_MAX},
        "pass": len(missed) <= HANDOFF_MISSED_MAX and false_rate < HANDOFF_FALSE_RATE_MAX,
    }


def classify_with(text: str, confirm_kws: frozenset[str], cancel_kws: frozenset[str]) -> str:
    """detect_confirmation_intent 的纯函数镜像（词表集合由入参给定）。

    与 confirmation.detect_confirmation_intent 的规则顺序保持逐句一致：
    疑问句守卫（词表标记 + 句尾「么」锚定）→ 先 CANCEL 后 CONFIRM
    （词表为子串匹配）。一致性由测试 parity 用例守护。
    """
    from backend.customer_service.vocab import QUESTION_MARKERS
    text_lower = text.strip().lower()
    if not text_lower:
        return "NONE"
    if any(marker in text_lower for marker in QUESTION_MARKERS):
        return "NONE"
    if text_lower.rstrip("。！!？?~～，,  、；;…").endswith("么"):
        return "NONE"
    for kw in cancel_kws:
        if kw in text_lower:
            return "CANCEL"
    for kw in confirm_kws:
        if kw in text_lower:
            return "CONFIRM"
    return "NONE"


def evaluate_confirm_cancel(candidate_override: dict | None = None) -> dict:
    """C10：确认/取消词表回放；candidate_override=None 时评当前生效词表。"""
    from backend.customer_service.vocab import get_cancel_keywords, get_confirm_keywords
    cases = _load("confirm_cancel_golden.json")
    confirm_kws = get_confirm_keywords()
    cancel_kws = get_cancel_keywords()
    if candidate_override:
        items = candidate_override.get("confirm_keywords")
        if isinstance(items, list):
            confirm_kws = confirm_kws | {str(i) for i in items}
        items = candidate_override.get("cancel_keywords")
        if isinstance(items, list):
            cancel_kws = cancel_kws | {str(i) for i in items}
    fails: list[dict] = []
    for c in cases:
        got = classify_with(c["text"], confirm_kws, cancel_kws)
        if got != c["expected"]:
            fails.append({"id": c["id"], "text": c["text"],
                          "expected": c["expected"], "got": got})
    total = len(cases)
    correct = total - len(fails)
    return {
        "suite": "confirm_cancel", "total": total, "correct": correct,
        "accuracy": correct / total if total else 0.0,
        "threshold": CONFIRM_CANCEL_ACCURACY_MIN, "fails": fails,
        "pass": total > 0 and correct / total >= CONFIRM_CANCEL_ACCURACY_MIN,
    }


# ── 热更门（fail-closed）─────────────────────────────────────────────

@dataclass
class GateReport:
    ok: bool
    reason: str = ""
    baseline: dict = field(default_factory=dict)
    candidate: dict = field(default_factory=dict)


def evaluate_vocab_change(update: dict) -> GateReport:
    """裁决一次词表热更（update 键 → 追加词列表）是否可落盘。

    规则：
      1) 未知键拒绝（不在 HOT_VOCAB_KEYS）；
      2) 非法值类型拒绝（list[str] 之外）；
      3) 候选词表在确认/取消回放集上出现基线回归或跌破准确率下限 → 拒绝。
    """
    unknown = set(update) - HOT_VOCAB_KEYS
    if unknown:
        return GateReport(False, f"未知热更词表键 {sorted(unknown)}，允许: {sorted(HOT_VOCAB_KEYS)}")
    for key, items in update.items():
        if not isinstance(items, list) or not all(isinstance(i, str) for i in items):
            return GateReport(False, f"{key} 必须为字符串列表")
    baseline = evaluate_confirm_cancel()
    candidate = evaluate_confirm_cancel(update)
    if not candidate["pass"]:
        return GateReport(
            False,
            f"候选词表准确率 {candidate['accuracy']:.3f} < {CONFIRM_CANCEL_ACCURACY_MIN}",
            baseline=baseline, candidate=candidate,
        )
    base_ok = {f["id"] for f in baseline["fails"]}
    regressions = [f for f in candidate["fails"] if f["id"] not in base_ok]
    if regressions:
        sample = [
            (f["id"], f["text"], "期望" + f["expected"])
            for f in regressions[:5]
        ]
        return GateReport(
            False,
            f"候选词表引入 {len(regressions)} 条回归: {sample}",
            baseline=baseline, candidate=candidate,
        )
    return GateReport(True, "门禁通过", baseline=baseline, candidate=candidate)


def run_all() -> dict:
    """全量基线（CLI / CI 门禁消费）。"""
    return {
        "intent_triage": evaluate_triage(),
        "handoff_timing": evaluate_handoff(),
        "confirm_cancel": evaluate_confirm_cancel(),
    }


def _summarize(report: dict) -> dict:
    """CLI 摘要：剥离明细只留统计与 fail 摘要。"""
    out = dict(report)
    for key in ("intent_triage", "handoff_timing", "confirm_cancel"):
        r = dict(out[key])
        r.pop("fails", None)
        r.pop("missed_detail", None)
        r.pop("per_domain", None)
        out[key] = r
    return out


def main() -> int:
    try:
        reports = run_all()
    except Exception as e:  # noqa: BLE001 —— CLI 顶层显式失败
        print(json.dumps({"error": f"门禁运行失败: {e}"}, ensure_ascii=False))
        return 2
    ok = all(r["pass"] for r in reports.values())
    print(json.dumps({"ok": ok, **_summarize(reports)}, ensure_ascii=False, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
