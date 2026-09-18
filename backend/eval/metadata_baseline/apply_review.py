"""apply_review.py — 复核结果回写，生成正式黄金集 v1（规划阶段 1.1 收口）。

输入：review_67.html 导出的 review_result_<姓名>.json（放本目录或 --result 指定）。
校验：覆盖条数、doc_type 枚举合法性；同名 id 多份结果 = 第二标注员 → 算
Cohen's Kappa（规划 §1.1 门禁 ≥0.80）。

产出：
  golden_v1.jsonl   — 正式黄金集 v1（doc_type_gold = 人工定标值）
  控制台统计        — 人工 vs 机器一致率 / 修改分布 / Kappa（双人时）

用法（仓库根）：
  python -m backend.eval.metadata_baseline.apply_review \
      --result backend/eval/metadata_baseline/review_result_张三.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

BASE = Path(__file__).parent


def _cohen_kappa(a: list[str], b: list[str]) -> float:
    labels = sorted(set(a) | set(b))
    n = len(a)
    if not n:
        return 0.0
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca.get(l, 0) * cb.get(l, 0) for l in labels) / (n * n)
    return round((po - pe) / (1 - pe), 4) if pe < 1 else 1.0


def main(argv: list[str] | None = None) -> int:
    from backend.rag.preprocessing.metadata_schema import DOC_TYPES

    ap = argparse.ArgumentParser(description="复核结果回写 → 黄金集 v1")
    ap.add_argument("--result", nargs="+", required=True,
                    help="一或两份 review_result_*.json（两份 = 双人，算 Kappa）")
    ap.add_argument("--out", default=str(BASE / "golden_v1.jsonl"))
    args = ap.parse_args(argv)

    # 多份结果按 id 对齐
    by_id: dict[str, dict] = {}
    reviewers: list[str] = []
    for rp in args.result:
        data = json.loads(Path(rp).read_text(encoding="utf-8"))
        reviewers.append(data.get("reviewer") or Path(rp).stem)
        for d in data.get("decisions", []):
            t = d.get("final_doc_type")
            if t not in DOC_TYPES:
                raise ValueError(f"{d['id']}: final_doc_type={t!r} 不在 taxonomy")
            by_id.setdefault(d["id"], {})[data["reviewer"]] = d
    if not by_id:
        raise ValueError("结果文件为空")

    reviewer_a = reviewers[0]
    decisions_a = {i: v[reviewer_a] for i, v in by_id.items()}
    kappa = None
    if len(reviewers) > 1:
        reviewer_b = reviewers[1]
        common = [i for i in by_id if reviewer_b in by_id[i]]
        kappa = _cohen_kappa(
            [decisions_a[i]["final_doc_type"] for i in common],
            [by_id[i][reviewer_b]["final_doc_type"] for i in common])

    # 黄金集 v1：正文取种子集/fixture（workpackage 的 item 正文在 HTML 生成时
    # 已截断，这里从原始来源重取全文采样）
    from backend.eval.metadata_baseline.build_seed_from_fixtures import extract_text
    pkg = json.loads((BASE / "annotation_workpackage.json").read_text(encoding="utf-8"))
    skip_by_file = {p["file"]: p for p in pkg["part_b_detail"]}
    seed_by_id: dict[str, dict] = {}
    for line in (BASE / "golden_seed.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            seed_by_id[r["id"]] = r

    out_rows, agreed_n, dispute_n = [], 0, 0
    for rid, dec in decisions_a.items():
        final = dec["final_doc_type"]
        machine = dec.get("machine_suggested")
        if machine == final:
            agreed_n += 1
        else:
            dispute_n += 1
        if rid in seed_by_id:
            row = seed_by_id[rid]
            out_rows.append({**row,
                             "doc_type_gold": final,
                             "annotators": [reviewer_a, "fixture_seed"],
                             "dispute": f"机器建议 {machine}，人工定 {final}"
                             if machine != final else row.get("dispute", "")})
        else:
            fname = dec.get("filename", "")
            meta = skip_by_file.get(fname) or {}
            fp = meta.get("file_path")
            if not fp or not Path(fp).exists():
                raise ValueError(f"{rid}: 找不到 fixture 文件 {fname}")
            text = extract_text(Path(fp))[:6000]
            out_rows.append({
                "id": rid, "text": text, "doc_type_gold": final,
                "filename": fname, "file_path": fp,
                "annotators": [reviewer_a, "llm_prelabel"],
                "dispute": f"LLM 预标 {machine}（conf {meta.get('prelabel', {}).get('confidence')}），"
                           f"人工定 {final}" if machine != final else ""})

    Path(args.out).write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in out_rows) + "\n",
        encoding="utf-8")

    print(json.dumps({
        "golden_v1": args.out,
        "条数": len(out_rows),
        "人工与机器一致": agreed_n,
        "人工推翻机器": dispute_n,
        "推翻率": f"{dispute_n / max(len(out_rows), 1):.0%}",
        "双人Kappa": kappa,
        "达标门禁(Kappa>=0.80)": None if kappa is None else kappa >= 0.80,
        "下一步": "python -m backend.eval.metadata_baseline.evaluate "
                  f"--golden {args.out} --pred rule --json baseline_report.json",
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
