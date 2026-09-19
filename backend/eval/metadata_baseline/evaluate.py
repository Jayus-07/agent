"""evaluate.py — 黄金集基线评估器（纯 stdlib，零第三方依赖）。

指标口径（规划 §1.2 成功标准）：
  - doc_type / domain：per-label P/R/F1 + macro-F1 + 混淆矩阵
  - 风险字段：召回率 = 命中风险正例 / 全部风险正例（gold.risk_gold 非空为正例）
  - 延迟：预测文件携带 latency_ms 时输出 P50/P95

evaluate() 等核心函数为纯函数，供 tests/eval/test_metadata_baseline_eval.py 回归。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

GOLDEN_REQUIRED = {"id", "text", "doc_type_gold"}


def load_jsonl(path: str | Path) -> list[dict]:
    rows: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{lineno} 不是合法 JSON: {e}") from e
    return rows


def validate_golden(rows: list[dict]) -> None:
    for i, r in enumerate(rows):
        missing = GOLDEN_REQUIRED - set(r)
        if missing:
            raise ValueError(f"golden 第 {i + 1} 行缺少必填字段: {sorted(missing)}")
        if r["doc_type_gold"] not in _valid_doc_types():
            raise ValueError(
                f"golden 第 {i + 1} 行 doc_type_gold={r['doc_type_gold']!r} 不在 taxonomy；"
                "新增类型必须走 schema 演进流程（规划 §2.2 N6）")


def _valid_doc_types() -> set:
    from backend.rag.preprocessing.metadata_schema import DOC_TYPES
    return set(DOC_TYPES)


def per_label_prf(preds: list[str], golds: list[str], labels: list[str]) -> dict:
    """per-label P/R/F1 + macro-F1（unsupported label 记 0 并标注 support=0）。"""
    out: dict = {}
    f1s: list[float] = []
    for lab in labels:
        tp = sum(1 for p, g in zip(preds, golds) if p == lab and g == lab)
        fp = sum(1 for p, g in zip(preds, golds) if p == lab and g != lab)
        fn = sum(1 for p, g in zip(preds, golds) if p != lab and g == lab)
        support = sum(1 for g in golds if g == lab)
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        out[lab] = {"precision": round(precision, 4), "recall": round(recall, 4),
                    "f1": round(f1, 4), "support": support}
        if support:
            f1s.append(f1)
    macro = sum(f1s) / len(f1s) if f1s else 0.0
    return {"per_label": out, "macro_f1": round(macro, 4)}


def confusion(preds: list[str], golds: list[str], labels: list[str],
              top_n: int = 8) -> dict:
    """混淆矩阵（gold → pred 计数，只输出非对角 top_n 误判对）。"""
    pairs = Counter((g, p) for g, p in zip(golds, preds) if g != p)
    return {"misclassifications": [
        {"gold": g, "pred": p, "count": c}
        for (g, p), c in pairs.most_common(top_n)]}


def risk_recall(rows: list[dict]) -> dict | None:
    """风险字段召回（规划成功标准 3）：gold 有 risk_gold（列表非空/True）为正例，
    pred.risk.level != none 判命中。无正例返回 None。"""
    positives = [r for r in rows if _risk_gold_positive(r)]
    if not positives:
        return None
    hit = sum(1 for r in positives
              if (r.get("pred") or {}).get("risk", {}).get("level", "none") != "none")
    return {"positives": len(positives), "hit": hit,
            "recall": round(hit / len(positives), 4)}


def _risk_gold_positive(r: dict) -> bool:
    rg = r.get("risk_gold")
    if isinstance(rg, list):
        return bool(rg)
    return rg is True


def latency_percentiles(rows: list[dict]) -> dict | None:
    vals = sorted(float(r["pred"]["latency_ms"]) for r in rows
                  if isinstance(r.get("pred"), dict) and "latency_ms" in r["pred"])
    if not vals:
        return None

    def pct(p: float) -> float:
        idx = min(len(vals) - 1, max(0, round(p / 100 * (len(vals) - 1))))
        return round(vals[idx], 1)

    return {"p50": pct(50), "p95": pct(95), "n": len(vals)}


def _prediction(row: dict) -> dict:
    value = row.get("pred")
    return value if isinstance(value, dict) else {}


def _is_abstain(row: dict) -> bool:
    pred = _prediction(row)
    decision = str(pred.get("decision") or "").lower()
    return decision in {"abstain", "review"} or bool(pred.get("abstain_reason"))


def coverage(rows: list[dict]) -> float:
    """可交付覆盖率；abstain/review 是覆盖损失，不能静默改成 general。"""
    if not rows:
        return 0.0
    return round(sum(1 for row in rows if not _is_abstain(row)) / len(rows), 4)


def abstain_rate(rows: list[dict]) -> float:
    """显式 abstain/review 占比。"""
    if not rows:
        return 0.0
    return round(sum(1 for row in rows if _is_abstain(row)) / len(rows), 4)


def expected_calibration_error(rows: list[dict], bins: int = 10) -> float:
    """按 confidence 分箱计算 ECE；缺少 confidence 的行不伪造校准数据。"""
    if bins <= 0:
        raise ValueError("bins must be positive")
    buckets: list[list[tuple[float, bool]]] = [[] for _ in range(bins)]
    for row in rows:
        pred = _prediction(row)
        try:
            confidence = float(pred["confidence"])
        except (KeyError, TypeError, ValueError):
            continue
        confidence = max(0.0, min(1.0, confidence))
        predicted = str(pred.get("doc_type") or "general")
        gold = str(row.get("doc_type_gold") or "general")
        index = min(bins - 1, int(confidence * bins))
        buckets[index].append((confidence, predicted == gold))
    total = sum(len(bucket) for bucket in buckets)
    if not total:
        return 0.0
    ece = 0.0
    for bucket in buckets:
        if not bucket:
            continue
        avg_conf = sum(item[0] for item in bucket) / len(bucket)
        accuracy = sum(1 for _, correct in bucket if correct) / len(bucket)
        ece += len(bucket) / total * abs(avg_conf - accuracy)
    return round(ece, 4)


# gold 字段 → 预测字段映射（domain 在统一 schema 里叫 business_domain）
_GOLD_TO_PRED_FIELD = {"doc_type_gold": "doc_type", "domain_gold": "business_domain"}


def evaluate(gold_rows: list[dict], label_field: str, label_pool: list[str]) -> dict:
    """对单一标签字段（doc_type_gold / domain_gold）产出评估报告。"""
    pred_field = _GOLD_TO_PRED_FIELD[label_field]
    preds = [(r.get("pred") or {}).get(pred_field) or "general" for r in gold_rows]
    golds = [r.get(label_field) or "general" for r in gold_rows]
    labels = sorted({*label_pool, *golds, *preds})
    report = per_label_prf(preds, golds, labels)
    report.update(confusion(preds, golds, labels))
    report["accuracy"] = round(
        sum(1 for p, g in zip(preds, golds) if p == g) / max(len(golds), 1), 4)
    report["coverage"] = coverage(gold_rows)
    report["abstain_rate"] = abstain_rate(gold_rows)
    report["calibration"] = {"ece": expected_calibration_error(gold_rows)}
    return report


def _route_source(pred: dict) -> str:
    source = str(pred.get("route_source") or pred.get("source") or "").lower()
    if source in {"l0", "r0"}:
        return "r0"
    if source in {"l1", "r1"}:
        return "r1"
    if source in {"l2", "r2"}:
        return "r2"
    if source in {"l3", "llm"}:
        return "llm"
    return source


def _threshold_gate(actual, threshold: float, *, strict: bool = False) -> dict:
    if actual is None:
        return {"actual": None, "threshold": threshold, "passed": False, "reason": "metric_missing"}
    passed = actual > threshold if strict else actual >= threshold
    return {
        "actual": actual,
        "threshold": threshold,
        "passed": passed,
        "reason": "" if passed else "below_threshold",
    }


def _r0_precision(rows: list[dict]) -> float | None:
    selected = [row for row in rows if _route_source(_prediction(row)) == "r0"]
    if not selected:
        return None
    correct = sum(
        1 for row in selected
        if str(_prediction(row).get("doc_type") or "general")
        == str(row.get("doc_type_gold") or "general")
    )
    return round(correct / len(selected), 4)


def _r1_precision(rows: list[dict]) -> dict | None:
    selected = [row for row in rows if _route_source(_prediction(row)) == "r1"]
    if not selected:
        return None
    labels = sorted({
        str(row.get("doc_type_gold") or "general") for row in selected
    })
    preds = [str(_prediction(row).get("doc_type") or "general") for row in selected]
    golds = [str(row.get("doc_type_gold") or "general") for row in selected]
    return per_label_prf(preds, golds, labels)["per_label"]


def _r2_schema_pass_rate(rows: list[dict]) -> float | None:
    selected = [row for row in rows if _route_source(_prediction(row)) in {"r2", "llm"}]
    if not selected:
        return None
    valid = 0
    valid_labels = _valid_doc_types()
    for row in selected:
        pred = _prediction(row)
        explicit = pred.get("schema_valid")
        if explicit is None:
            explicit = (
                str(pred.get("doc_type") or "") in valid_labels
                and str(pred.get("decision") or "accepted") == "accepted"
            )
        valid += int(bool(explicit))
    return round(valid / len(selected), 4)


def build_release_report(
    gold_rows: list[dict], prediction_rows: list[dict]
) -> dict:
    """生成发布门禁报告；诊断字段与阻断门禁显式分离。"""
    gold_by_id = {str(row["id"]): row for row in gold_rows}
    pred_by_id = {str(row["id"]): row for row in prediction_rows}
    missing = [key for key in gold_by_id if key not in pred_by_id]
    if missing:
        raise ValueError(f"预测文件缺少 {len(missing)} 条 gold id，首条: {missing[0]}")
    rows = [{**gold_by_id[key], **pred_by_id[key], "pred": pred_by_id[key].get("pred", {})}
            for key in gold_by_id]

    doc_type_report = evaluate(rows, "doc_type_gold", sorted(_valid_doc_types()))
    accuracy = doc_type_report["accuracy"]
    incumbent_rows = [row for row in rows if row.get("incumbent_pred") is not None]
    if incumbent_rows:
        agreement = round(sum(
            str(row.get("incumbent_pred"))
            == str(_prediction(row).get("doc_type") or "general")
            for row in incumbent_rows
        ) / len(incumbent_rows), 4)
    else:
        agreement = None

    r1_precision = _r1_precision(rows)
    r2_schema = _r2_schema_pass_rate(rows)
    fallback_rows = [
        row for row in rows if _route_source(_prediction(row)) == "fallback"
        or str(_prediction(row).get("source") or "") == "fallback"
    ]
    fallback_llm_calls = sum(
        int(_prediction(row).get("llm_call_count", 0) or 0)
        for row in fallback_rows
    )
    risk = risk_recall(rows)
    primary_latency = latency_percentiles(rows)
    shadow_values = [
        float(_prediction(row)["shadow_latency_ms"])
        for row in rows
        if "shadow_latency_ms" in _prediction(row)
    ]
    shadow_p95 = None
    if shadow_values:
        shadow_values.sort()
        shadow_p95 = round(shadow_values[min(len(shadow_values) - 1,
                                             round(0.95 * (len(shadow_values) - 1)))], 1)

    gates = {
        "accuracy": _threshold_gate(accuracy, 0.95),
        # 0.995 的边界采用严格大于，避免 199/200 恰好四舍五入为 0.995
        # 时被错误当作“无误差”放行；正式数据应留出统计余量。
        "r0_precision": _threshold_gate(_r0_precision(rows), 0.995, strict=True),
        "r1_per_class_precision": {
            "actual": r1_precision,
            "threshold": 0.98,
            "passed": bool(r1_precision) and all(
                value.get("precision", 0.0) >= 0.98
                for value in r1_precision.values()
            ),
            "reason": "metric_missing" if r1_precision is None else "",
        },
        "r2_schema_pass_rate": _threshold_gate(r2_schema, 0.995),
        "fallback_llm_calls": {
            "actual": fallback_llm_calls,
            "threshold": 0,
            "passed": fallback_llm_calls == 0,
            "reason": "" if fallback_llm_calls == 0 else "fallback_llm_call_detected",
        },
        "high_risk_recall": _threshold_gate(
            risk["recall"] if risk else None, 0.95
        ),
        "shadow_primary_p95": {
            "actual": {"primary": primary_latency, "shadow": shadow_p95},
            "threshold": "shadow_not_higher",
            "passed": (
                primary_latency is not None and shadow_p95 is not None
                and shadow_p95 <= primary_latency["p95"]
            ),
            "reason": "metric_missing" if primary_latency is None or shadow_p95 is None else "",
        },
    }
    return {
        "n": len(rows),
        "accuracy": accuracy,
        "coverage": coverage(rows),
        "abstain_rate": abstain_rate(rows),
        "calibration": {"ece": expected_calibration_error(rows)},
        "doc_type": doc_type_report,
        "risk": risk,
        "latency_ms": primary_latency,
        "incumbent_agreement": agreement,
        "r0_precision": _r0_precision(rows),
        "r1_per_class_precision": r1_precision,
        "r2_schema_pass_rate": r2_schema,
        "fallback_llm_calls": fallback_llm_calls,
        "shadow_p95_ms": shadow_p95,
        "gates": gates,
    }


def run_rule_predictions(rows: list[dict]) -> list[dict]:
    """规则链预测（现场推理）。胶着样本会触发既有 LLM 仲裁——那是规则链
    真实成本的一部分；纯离线评估请改用 predict 脚本预生成。"""
    from backend.rag.preprocessing.metadata import (
        classify_with_confidence, detect_business_domain,
    )
    import time

    out = []
    for r in rows:
        t0 = time.monotonic()
        doc_type, conf = classify_with_confidence(
            r["text"], filename=r.get("filename", ""), file_path=r.get("file_path", ""))
        domain = detect_business_domain(r["text"])[0]
        out.append({**r, "pred": {
            "doc_type": doc_type, "business_domain": domain,
            "confidence": round(float(conf), 2),
            "latency_ms": round((time.monotonic() - t0) * 1000, 1)}})
    return out


def run_file_predictions(rows: list[dict], pred_path: str | Path) -> list[dict]:
    """回放预生成预测（统一抽取 / 级联路由 / 影子采集均可）。"""
    preds = load_jsonl(pred_path)
    by_id = {p["id"]: p.get("pred") for p in preds}
    missing = [r["id"] for r in rows if r["id"] not in by_id]
    if missing:
        raise ValueError(f"预测文件缺少 {len(missing)} 条 gold id，首条: {missing[0]}")
    by_row = {p["id"]: p for p in preds}
    return [
        {**r, **{k: v for k, v in by_row[r["id"]].items() if k != "id"},
         "pred": by_row[r["id"]].get("pred", {})}
        for r in rows
    ]


def _field_coverage(rows: list[dict], field: str) -> float:
    """gold 字段非空占比（种子集不预标 domain/risk 时对应报告跳过）。"""
    if not rows:
        return 0.0
    filled = sum(1 for r in rows if r.get(field))
    return filled / len(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="元数据基线评估（规划阶段 1.2/1.3）")
    ap.add_argument("--golden", required=True, help="黄金集 JSONL 路径")
    ap.add_argument("--pred", required=True,
                    help="'rule'（现场跑规则链）或预测 JSONL 文件路径")
    ap.add_argument("--json", dest="json_out", default="", help="报告 JSON 输出路径")
    args = ap.parse_args(argv)

    rows = load_jsonl(args.golden)
    validate_golden(rows)
    merged = (run_rule_predictions(rows) if args.pred == "rule"
              else run_file_predictions(rows, args.pred))

    from backend.rag.preprocessing.metadata_schema import DOMAINS, DOC_TYPES

    # 维度覆盖门控：种子集（fixture_seed）不预标 domain/risk → 对应报告置 None，
    # 避免全 general 的误导性指标混进正式对比
    domain_report = (evaluate(merged, "domain_gold", list(DOMAINS))
                     if _field_coverage(rows, "domain_gold") >= 0.5 else None)
    report = {
        "n": len(merged),
        "doc_type": evaluate(merged, "doc_type_gold", list(DOC_TYPES)),
        "business_domain": domain_report,
        "risk": risk_recall(merged),
        "latency_ms": latency_percentiles(merged),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
