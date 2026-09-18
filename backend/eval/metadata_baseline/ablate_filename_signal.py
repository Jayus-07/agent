"""ablate_filename_signal.py — 规则链"文件名信号"消融（基线可信度审计）。

背景（2026-09-19 用户指正"规则链识别不准"）：种子集 doc_type_gold 来源
= fixture 文件名前缀，而规则链分类器重度依赖文件名/路径提示
（classify_filename_weight=100）——两者共享信号源，种子基线
macro-F1=0.9206 存在循环论证虚高。本脚本量化虚高幅度：

  形态 1（完整信号）：现状，含文件名 +100 / 路径 +40 / 标题 +20；
  形态 2（纯正文）：filename/file_path 传空，只剩正文正则 + 标题 hints + 动态词库。

两形态之差 = 文件名循环贡献。结论记录于
docs/2026-09-19-影子一致率模拟报告与标注排期.md §7。

用法（仓库根）：
  python -m backend.eval.metadata_baseline.ablate_filename_signal \
      --golden backend/eval/metadata_baseline/golden_seed.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys

from backend.eval.metadata_baseline.evaluate import load_jsonl


def _accuracy(preds: list[str], golds: list[str]) -> float:
    return sum(1 for p, g in zip(preds, golds) if p == g) / max(len(golds), 1)


def _macro_f1(preds: list[str], golds: list[str]) -> float:
    labels = sorted(set(golds) | set(preds))
    f1s: list[float] = []
    for lab in labels:
        sup = sum(1 for g in golds if g == lab)
        if not sup:
            continue
        tp = sum(1 for p, g in zip(preds, golds) if p == lab and g == lab)
        fp = sum(1 for p, g in zip(preds, golds) if p == lab and g != lab)
        fn = sum(1 for p, g in zip(preds, golds) if p != lab and g == lab)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1s.append(2 * prec * rec / (prec + rec) if prec + rec else 0.0)
    return sum(f1s) / len(f1s) if f1s else 0.0


def run(rows: list[dict]) -> dict:
    from backend.rag.preprocessing.metadata import classify_with_confidence

    golds = [r["doc_type_gold"] for r in rows]
    full, pure_text, flips = [], [], []
    for r in rows:
        t = r["text"]
        p_full, _ = classify_with_confidence(
            t, filename=r.get("filename", ""), file_path=r.get("file_path", ""))
        p_pure, _ = classify_with_confidence(t, filename="", file_path="")
        full.append(p_full)
        pure_text.append(p_pure)
        if p_full != p_pure:
            flips.append({
                "id": r["id"], "gold": r["doc_type_gold"],
                "with_file": p_full, "without": p_pure,
                "saved": p_full == r["doc_type_gold"] and p_pure != r["doc_type_gold"],
            })
    return {
        "n": len(rows),
        "full_signal": {"accuracy": round(_accuracy(full, golds), 4),
                        "macro_f1": round(_macro_f1(full, golds), 4)},
        "pure_text_signal": {"accuracy": round(_accuracy(pure_text, golds), 4),
                             "macro_f1": round(_macro_f1(pure_text, golds), 4)},
        "flipped_by_filename": len(flips),
        "flip_detail": flips,
        "caveat": "种子 gold 来自文件名前缀 → 形态 1 与 gold 共享信号源（循环）；"
                  "形态 2 才接近线上无命名线索文档的真实水平。切流门禁的规则链"
                  "基线应取形态 2。",
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="规则链文件名信号消融")
    ap.add_argument("--golden", required=True)
    args = ap.parse_args(argv)
    report = run(load_jsonl(args.golden))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
