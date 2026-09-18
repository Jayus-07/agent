"""build_seed_from_fixtures.py — 从既有评测 fixture 生成黄金集 v0 种子集。

规划阶段 1.1 的推进手段：黄金标注集被标注人力阻断（§7.2），本脚本把
rag_100_docs 评测 fixture 中「文件名类型前缀直接落在 taxonomy」的文档转成
golden_seed.jsonl，让评估管线（1.2/1.3）可以先跑通。

种子集性质（务必与正式黄金集区分）：
  - doc_type_gold 来源 = fixture 命名约定（评测集设计意图），单人标注、
    无双人 Kappa → 只可用于管线试跑与阈值粗调，**不可作为上线门禁的
    +5pp 基线测量**（正式基线必须用双人标注黄金集，规划 §1.2）。
  - 语义模糊的前缀（table/report/manual/scan/notes/spec）一律 skip——
    宁缺毋滥，预标争议样本会污染基线（规划 §5 风险：标注一致性低 →
    基线不可信）。
  - domain_gold / risk_gold 不预标（fixture 命名无此信号），留正式标注。

用法（仓库根）：
  python -m backend.eval.metadata_baseline.build_seed_from_fixtures \
      [--fixtures backend/evaluation/fixtures/rag_100_docs/files] \
      [--out backend/eval/metadata_baseline/golden_seed.jsonl]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# fixture 文件名前缀 → taxonomy doc_type（显式映射，可审计）。
# contract_ → legal：fixture 的 contract_* 是实际合同（法律文书语义），
# 不是模板/范本（contract_template 要求"模板"语义），见 README 说明。
PREFIX_TO_DOCTYPE: dict[str, str] = {
    "policy": "policy",
    "legal": "legal",
    "sop": "sop",
    "faq": "faq",
    "contract": "legal",
    "readme": "general",
}

# 前缀语义模糊/类型各异 → 不预标，留正式标注环节
SKIP_PREFIXES = {"table", "report", "manual", "scan", "notes", "spec"}
SKIP_REASON = {
    "table": "台账/统计表，doc_type 需按内容判断（发票台账→financial、客户满意度→customer_data）",
    "report": "报告类横跨 financial/政策/安全等，需逐篇人工",
    "manual": "手册类横跨 sop/training/policy，需逐篇人工",
    "scan": "扫描件（OCR 产出），文本质量影响标注效率，批次 2 处理",
    "notes": "会议纪要，taxonomy 无对应类（趋势上是 general），需确认口径",
    "spec": "规范类（数据仓库/API 网关），偏 policy 或 compliance，需人工",
}


def classify_filename(name: str) -> tuple[str, str] | None:
    """按文件名前缀判定 doc_type；返回 (doc_type, prefix) 或 None（skip）。"""
    prefix = name.split("_", 1)[0].lower()
    if prefix in PREFIX_TO_DOCTYPE:
        return PREFIX_TO_DOCTYPE[prefix], prefix
    return None


def extract_text(file_path: Path, max_chars: int = 6000) -> str:
    """用项目既有 parser 抽全文采样（与线上一致，≤ METADATA_LLM_EXTRACT_MAX_CHARS）。"""
    from backend.rag.preprocessing.parser import parse_file
    ast = parse_file(str(file_path))
    text = (ast.raw_text or "").strip()
    return text[:max_chars]


def build(fixtures_dir: Path) -> tuple[list[dict], list[dict]]:
    """扫描 fixture 目录，产出 (种子行, 跳过记录)。解析失败的文件计入跳过。"""
    from backend.rag.preprocessing.metadata_schema import DOC_TYPES

    rows: list[dict] = []
    skipped: list[dict] = []
    for fp in sorted(fixtures_dir.rglob("*")):
        if not fp.is_file() or fp.name.startswith("."):
            continue
        name = fp.name
        judged = classify_filename(name)
        if judged is None:
            prefix = name.split("_", 1)[0].lower()
            skipped.append({"file": name,
                            "reason": SKIP_REASON.get(prefix, f"未知前缀 {prefix!r}")})
            continue
        doc_type, prefix = judged
        if doc_type not in DOC_TYPES:
            skipped.append({"file": name, "reason": f"映射结果 {doc_type} 不在 taxonomy"})
            continue
        try:
            text = extract_text(fp)
        except Exception as e:
            skipped.append({"file": name, "reason": f"解析失败: {e}"})
            continue
        if len(text) < 50:
            skipped.append({"file": name, "reason": f"文本过短（{len(text)} 字符）"})
            continue
        rows.append({
            "id": f"seed-{prefix}-{len(rows) + 1:03d}",
            "text": text,
            "doc_type_gold": doc_type,
            "filename": name,
            "file_path": str(fp),
            "annotators": ["fixture_seed"],
            "dispute": "v0 种子：来源=fixture 命名约定，待双人复核后并入正式黄金集",
        })
    return rows, skipped


def main(argv: list[str] | None = None) -> int:
    repo_root = Path(__file__).resolve().parents[3]
    default_fixtures = repo_root / "backend" / "evaluation" / "fixtures" / "rag_100_docs" / "files"
    ap = argparse.ArgumentParser(description="从评测 fixture 生成黄金集 v0 种子")
    ap.add_argument("--fixtures", default=str(default_fixtures))
    ap.add_argument("--out", default=str(Path(__file__).parent / "golden_seed.jsonl"))
    args = ap.parse_args(argv)

    rows, skipped = build(Path(args.fixtures))
    out = Path(args.out)
    with open(out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    by_type: dict[str, int] = {}
    for r in rows:
        by_type[r["doc_type_gold"]] = by_type.get(r["doc_type_gold"], 0) + 1
    print(f"种子集: {len(rows)} 条 → {out}")
    print(f"分布: {json.dumps(by_type, ensure_ascii=False)}")
    print(f"跳过: {len(skipped)} 条（逐条原因见 {out.parent / 'golden_seed_skipped.json'}）")
    (out.parent / "golden_seed_skipped.json").write_text(
        json.dumps(skipped, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
