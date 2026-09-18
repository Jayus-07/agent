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
    return report


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
    return [{**r, "pred": by_id[r["id"]]} for r in rows]


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

    report = {
        "n": len(merged),
        "doc_type": evaluate(merged, "doc_type_gold", list(DOC_TYPES)),
        "business_domain": evaluate(merged, "domain_gold", list(DOMAINS)),
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
