"""evidence_gate/lab.py — 阈值实验台（C9 / 施工书 T5.3，2026-10-04）。

纯函数网格回放：给定定标集录制的分数向量，对 (vec_min_score, min_top1,
min_avg) 阈值网格逐点调用**生产 gate 函数**（evidence_gate_retrieval /
evidence_gate_rerank，不经任何镜像实现），输出每个阈值组合的
假拒率（应答被拒）/漏拒率（应拒放行）二维表与推荐工作点。

铁律：本模块**只读不改**——不写任何生产阈值（决策门 T5.3：向用户呈报
后再改，冻结期不动）；推荐值以报告形式输出。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 生产默认（与 operations.py 签名默认 / config 注入值对齐，作基线行）
CURRENT_THRESHOLDS = {
    "vec_min_score": 0.2,
    "min_top1": 0.35,
    "min_avg": 0.25,
    "min_gap": 0.05,
}
# 验收目标（演进分析 C9 / K7）
FALSE_REJECT_MAX = 0.10
MISS_REJECT_MAX = 0.05


class ScoredDoc:
    """gate 函数的最小文档形态（只带 metadata，_safe_top_score 读 rerank_score）。"""

    __slots__ = ("metadata",)

    def __init__(self, score: float):
        self.metadata = {"rerank_score": float(score), "score": float(score)}


def score_vector_from_records(docs: list[dict]) -> list[float]:
    """录制 payload（retrieve_docs 的 docs）→ 降序分数向量。"""
    scores = []
    for d in docs or []:
        meta = d.get("metadata", d) if isinstance(d, dict) else {}
        s = meta.get("rerank_score") or meta.get("similarity") or meta.get("score") or 0.0
        scores.append(float(s))
    return sorted(scores, reverse=True)


def gate_pass(
    scores: list[float],
    *,
    vec_min_score: float,
    min_top1: float,
    min_avg: float,
    min_gap: float,
    high_risk_min_top1: float = 0.55,
) -> bool:
    """对一段分数向量执行生产检索门+重排门，返回是否放行。"""
    from backend.rag.evidence_gate.operations import (
        evidence_gate_retrieval,
        evidence_gate_rerank,
    )

    docs = [ScoredDoc(s) for s in scores]
    retrieval = evidence_gate_retrieval(docs, vec_min_score=vec_min_score)
    if not retrieval.passed:
        return False
    rerank = evidence_gate_rerank(
        docs,
        intent="summary_query",
        risk_level="low",
        min_top1=min_top1,
        min_avg=min_avg,
        min_gap=min_gap,
        high_risk_min_top1=high_risk_min_top1,
    )
    return rerank.passed


@dataclass
class ComboResult:
    thresholds: dict
    false_reject_n: int = 0          # 应答被拒（假拒）
    false_reject_total: int = 0
    miss_reject_n: int = 0           # 应拒放行（漏拒）
    miss_reject_total: int = 0
    fail_ids: list = field(default_factory=list)

    @property
    def false_reject_rate(self) -> float:
        return self.false_reject_n / self.false_reject_total if self.false_reject_total else 0.0

    @property
    def miss_reject_rate(self) -> float:
        return self.miss_reject_n / self.miss_reject_total if self.miss_reject_total else 0.0

    @property
    def pass_targets(self) -> bool:
        return (
            self.false_reject_rate < FALSE_REJECT_MAX
            and self.miss_reject_rate < MISS_REJECT_MAX
        )

    def to_dict(self) -> dict:
        return {
            "thresholds": self.thresholds,
            "false_reject": {"n": self.false_reject_n, "total": self.false_reject_total,
                             "rate": round(self.false_reject_rate, 4)},
            "miss_reject": {"n": self.miss_reject_n, "total": self.miss_reject_total,
                            "rate": round(self.miss_reject_rate, 4)},
            "pass_targets": self.pass_targets,
        }


def run_grid(cases: list[dict], grid: dict[str, list[float]]) -> dict:
    """网格回放。cases: [{id, klass: answerable|unanswerable, scores: [..]}]。"""
    combos: list[ComboResult] = []
    for vec_min in grid["vec_min_score"]:
        for min_top1 in grid["min_top1"]:
            for min_avg in grid["min_avg"]:
                cr = ComboResult(thresholds={
                    "vec_min_score": vec_min, "min_top1": min_top1,
                    "min_avg": min_avg, "min_gap": CURRENT_THRESHOLDS["min_gap"],
                })
                for case in cases:
                    passed = gate_pass(
                        case["scores"],
                        vec_min_score=vec_min,
                        min_top1=min_top1,
                        min_avg=min_avg,
                        min_gap=CURRENT_THRESHOLDS["min_gap"],
                    )
                    if case["klass"] == "answerable":
                        cr.false_reject_total += 1
                        if not passed:
                            cr.false_reject_n += 1
                            cr.fail_ids.append({"id": case["id"], "top1": round(case["scores"][0], 4) if case["scores"] else 0.0})
                    else:
                        cr.miss_reject_total += 1
                        if passed:
                            cr.miss_reject_n += 1
                            cr.fail_ids.append({"id": case["id"], "top1": round(case["scores"][0], 4) if case["scores"] else 0.0})
                combos.append(cr)
    return {
        "cases": len(cases),
        "grid_size": len(combos),
        "combos": combos,
        "targets": {"false_reject_max": FALSE_REJECT_MAX, "miss_reject_max": MISS_REJECT_MAX},
    }


def current_baseline(cases: list[dict]) -> ComboResult:
    """生产默认阈值下的基线行。"""
    t = CURRENT_THRESHOLDS
    cr = ComboResult(thresholds=dict(t))
    for case in cases:
        passed = gate_pass(
            case["scores"],
            vec_min_score=t["vec_min_score"],
            min_top1=t["min_top1"],
            min_avg=t["min_avg"],
            min_gap=t["min_gap"],
        )
        if case["klass"] == "answerable":
            cr.false_reject_total += 1
            if not passed:
                cr.false_reject_n += 1
        else:
            cr.miss_reject_total += 1
            if passed:
                cr.miss_reject_n += 1
    return cr


def recommend(report: dict) -> dict:
    """推荐工作点：先取达标组合（假拒<10% 且 漏拒<5%），其中按假拒率、
    再按漏拒率升序取最优；无达标组合时如实返回空并给帕累托近优。"""
    combos: list[ComboResult] = report["combos"]
    feasible = [c for c in combos if c.pass_targets]
    if feasible:
        feasible.sort(key=lambda c: (c.false_reject_rate, c.miss_reject_rate))
        return {
            "feasible_n": len(feasible),
            "best": feasible[0].to_dict(),
            "note": "达标组合存在；改生产阈值须走决策门（T5.3），本实验台只出报告",
        }
    pareto = sorted(combos, key=lambda c: (c.false_reject_rate + c.miss_reject_rate))
    return {
        "feasible_n": 0,
        "near_optimal": pareto[0].to_dict(),
        "note": "当前定标集与网格下无同时达标组合：要么阈值面不覆盖（扩网格），要么存在定标集噪声（核对 suspicious 案例）",
    }
