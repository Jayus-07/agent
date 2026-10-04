"""Judge golden 样本集跑分 / 升级对比 / 波动检测（C4-4/C4-5/C4-6）。

口径（JUDGE-08/09/10）：
- golden 集是人工确认的判分基准（datasets/judge/golden.jsonl，20 条），
  每条带期望档位（pass/fail）与期望分区间；
- 换 Judge 模型 / 升级 Judge prompt 前必须先跑 golden（运维 Runbook 约束）；
- 与 baseline 对比：任一条期望档位翻转 或 整体偏差超阈值（默认 0.1）
  → exit 2（CI/CLI 阻断）；
- ``repeat`` 重复运行输出每条 mean/std 与稳定性汇总（JUDGE-07）。

judge 调用统一经 run_guards.run_judge（显式并发池，C5-3）。
"""
from __future__ import annotations

import json
import statistics
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.evaluation.judge import judge_answer
from backend.evaluation.run_guards import run_judge
from backend.shared.logger import logger

# backend/evaluation/judge_golden.py → parent×3 = 仓库根
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
GOLDEN_ROOT = _PROJECT_ROOT / "data" / "eval_runs" / "judge_golden"
DEFAULT_GOLDEN_PATH = (
    _PROJECT_ROOT / "backend" / "evaluation" / "datasets" / "judge" / "golden.jsonl"
)

# 固定评分维度 rubric（与 rag runner judge 接线同构）
_BASE_RUBRIC = {
    "faithfulness": "所有数字/事实必须能追溯到给定证据，不得编造或与证据矛盾",
    "completeness": "回答覆盖问题所问的全部要点，重要信息无遗漏",
    "conciseness": "表述精炼，无冗余、重复或与问题无关的内容",
    "citation_quality": "引用标注准确且与证据一致（有 [n] 标注且指向真实证据）",
}


def load_golden(path: Path | None = None) -> list[dict[str, Any]]:
    """加载 golden 样本（每行一条；坏行跳过并告警）。"""
    target = Path(path) if path else DEFAULT_GOLDEN_PATH
    cases: list[dict[str, Any]] = []
    for lineno, line in enumerate(target.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("[judge-golden] %s 第 %d 行非法 JSON，跳过", target.name, lineno)
            continue
        required = ("id", "question", "context", "answer", "expected_verdict")
        if any(item.get(k) in (None, "") for k in required):
            logger.warning("[judge-golden] 第 %d 行缺必填字段，跳过", lineno)
            continue
        cases.append(item)
    if not cases:
        raise ValueError(f"golden 样本集为空: {target}")
    return cases


def run_golden(
    *,
    golden_path: Path | None = None,
    repeat: int = 1,
) -> dict[str, Any]:
    """跑一遍 golden 集；repeat>1 时输出逐条 mean/std 与稳定性汇总。"""
    repeat = max(1, int(repeat))
    cases = load_golden(golden_path)
    started = time.time()

    per_case: dict[str, dict[str, Any]] = {}
    for case in cases:
        rubric = dict(_BASE_RUBRIC)
        scores: list[float] = []
        verdicts: list[str] = []
        for _ in range(repeat):
            result = run_judge(
                judge_answer,
                case["question"],
                rubric,
                f"证据：{case['context']}\n\n回答：{case['answer']}",
            )
            # judge 失败 total=0.0 是「评估失败」语义——计入波动序列但
            # 单独标记，不冒充真实 0 分
            failed = result.total <= 0
            scores.append(round(result.total, 3))
            verdicts.append("judge_error" if failed else _verdict_of(case, result.total))
        per_case[case["id"]] = {
            "expected_verdict": case["expected_verdict"],
            "expected_range": [case.get("expected_score_min"), case.get("expected_score_max")],
            "scores": scores,
            "mean": round(statistics.mean(scores), 4) if scores else None,
            "std": round(statistics.stdev(scores), 4) if len(scores) > 1 else 0.0,
            "verdicts": verdicts,
            "in_expected_range": _in_range(case, statistics.mean(scores)),
            "note": case.get("note", ""),
        }

    tier_flips = [
        cid for cid, c in per_case.items() if any(v != c["expected_verdict"] for v in c["verdicts"])
    ]
    report = {
        "kind": "judge_golden",
        "golden_path": str(golden_path or DEFAULT_GOLDEN_PATH),
        "repeat": repeat,
        "total": len(cases),
        "judge_error_count": sum(
            1 for c in per_case.values() if "judge_error" in c["verdicts"]
        ),
        "tier_flip_count": len(tier_flips),
        "tier_flip_cases": tier_flips,
        "cases": per_case,
        "duration_ms": int((time.time() - started) * 1000),
        "ran_at": datetime.now().isoformat(),
    }
    report["stability"] = {
        "mean_of_std": round(
            statistics.mean(c["std"] for c in per_case.values()), 4,
        ) if repeat > 1 else None,
        "max_std": max((c["std"] for c in per_case.values()), default=0.0),
    }
    return report


def _verdict_of(case: dict[str, Any], score: float) -> str:
    """按期望分区间给出实际档位（在期望档位区间内=expected_verdict）。"""
    if _in_range(case, score):
        return str(case["expected_verdict"])
    # 区间外：明显高分=pass 倾向、明显低分=fail 倾向（用于漂移归因）
    lo = case.get("expected_score_min")
    hi = case.get("expected_score_max")
    if lo is not None and score < float(lo) and case["expected_verdict"] == "fail":
        return "pass"
    if hi is not None and score > float(hi) and case["expected_verdict"] == "pass":
        return "fail"
    return "boundary"


def _in_range(case: dict[str, Any], score: float | None) -> bool:
    if score is None:
        return False
    lo = case.get("expected_score_min")
    hi = case.get("expected_score_max")
    if lo is not None and score < float(lo):
        return False
    if hi is not None and score > float(hi):
        return False
    return True


def compare_golden_reports(
    current: dict[str, Any],
    baseline: dict[str, Any],
    *,
    deviation_threshold: float = 0.1,
) -> dict[str, Any]:
    """对比两次 golden 结果（JUDGE-09/10）。

    阻断条件（任一命中）：期望档位翻转数增加 / 同一条平均分漂移超过
    ``deviation_threshold``。返回对比明细；调用方据 ``passed`` 决定 exit code。
    """
    base_cases = baseline.get("cases") or {}
    drifts: list[dict[str, Any]] = []
    new_flips: list[str] = []
    for cid, cur in (current.get("cases") or {}).items():
        base = base_cases.get(cid)
        if not base:
            continue
        delta = (cur.get("mean") or 0.0) - (base.get("mean") or 0.0)
        if abs(delta) > deviation_threshold:
            drifts.append({
                "case_id": cid,
                "baseline_mean": base.get("mean"),
                "current_mean": cur.get("mean"),
                "delta": round(delta, 4),
            })
        cur_flips = set(cur.get("verdicts") or []) - {cur.get("expected_verdict")}
        base_flips = set(base.get("verdicts") or []) - {base.get("expected_verdict")}
        if cur_flips and cur_flips != base_flips:
            new_flips.append(cid)
    passed = not drifts and not new_flips
    return {
        "passed": passed,
        "deviation_threshold": deviation_threshold,
        "score_drifts": drifts,
        "new_tier_flips": new_flips,
        "summary": (
            "golden 通过：档位与分值均在容差内"
            if passed
            else f"golden 漂移：{len(drifts)} 条分值漂移 / {len(new_flips)} 条档位翻转"
        ),
    }


def persist_golden_report(report: dict[str, Any]) -> Path:
    """golden 报告落盘（data/eval_runs/judge_golden/，供 --baseline 对比）。"""
    GOLDEN_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = GOLDEN_ROOT / f"golden-{stamp}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_golden_report(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


__all__ = [
    "run_golden",
    "compare_golden_reports",
    "persist_golden_report",
    "load_golden_report",
    "load_golden",
    "DEFAULT_GOLDEN_PATH",
]
