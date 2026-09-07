"""评测集 V3 迁移脚本 — 去 doc_id 化，用原文片段替代。

用法:
    python backend/scripts/migrate_eval_to_v3.py --dry-run   # 默认报告模式
    python backend/scripts/migrate_eval_to_v3.py --write     # 实际写入文件

迁移逻辑:
1. 通过 ChunkStore.get_by_doc_id() 获取原文 chunks
2. 对每条正样本：选 chunks 使其覆盖所有 relevant_snippets
3. 输出 ground_truth_context: [{text, source_doc, section}]
4. 拒答用例：ground_truth_context: []
5. 来源推断：adversarial shard → adversarial; tier=regression → regression; 其余 → curated
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

DATASET_DIR = Path(__file__).resolve().parent.parent / "evaluation" / "datasets"
MAX_GT_CHARS = 1500
MAX_GT_ENTRIES = 4


def _normalize_snippet_text(text: str) -> str:
    """去除空白和标点，用于模糊匹配。"""
    return re.sub(r"\s+", "", text).lower()


def _find_covering_chunks(
    chunks: list[dict], snippets: list[str]
) -> list[dict]:
    """从 chunks 中选出能覆盖所有 snippets 的最小集合。"""
    snippet_norms = [_normalize_snippet_text(s) for s in snippets]
    selected: list[dict] = []
    covered = [False] * len(snippets)

    for chunk in chunks:
        content_norm = _normalize_snippet_text(chunk.get("content", ""))
        for i, sn in enumerate(snippet_norms):
            if sn and sn in content_norm:
                covered[i] = True
        if any(covered) and chunk not in selected:
            selected.append(chunk)
        if all(covered):
            break

    return selected[:MAX_GT_ENTRIES]


def _extract_ground_truth_context(
    doc_id: str,
    snippets: list[str],
    chunk_store=None,
) -> tuple[list[dict], str]:
    """提取 ground_truth_context。返回 (entries, status)。

    status: "auto" | "partial" | "manual" | "no_source"
    """
    if not doc_id or not snippets:
        return [], "no_source"

    if chunk_store is None:
        return _fallback_from_snippets(snippets), "manual"

    chunks = chunk_store.get_by_doc_id(doc_id)
    if not chunks:
        return _fallback_from_snippets(snippets), "manual"

    source_doc = ""
    if chunks:
        source_doc = chunks[0].get("kb_id", "")

    selected = _find_covering_chunks(chunks, snippets)
    if not selected:
        return _fallback_from_snippets(snippets), "partial"

    entries = []
    for chunk in selected:
        text = chunk.get("content", "")[:MAX_GT_CHARS]
        section = chunk.get("section_title", "")
        entries.append({
            "text": text,
            "source_doc": source_doc,
            "section": section,
        })

    snippet_norms = [_normalize_snippet_text(s) for s in snippets]
    all_text = " ".join(
        _normalize_snippet_text(e["text"]) for e in entries
    )
    uncovered = [
        s for sn, s in zip(snippet_norms, snippets)
        if sn and sn not in all_text
    ]
    if uncovered:
        for s in uncovered[:2]:
            entries.append({
                "text": s,
                "source_doc": source_doc,
                "section": "",
            })
        return entries[:MAX_GT_ENTRIES], "partial"

    return entries, "auto"


def _fallback_from_snippets(snippets: list[str]) -> list[dict]:
    return [
        {"text": s, "source_doc": "", "section": ""}
        for s in snippets[:MAX_GT_ENTRIES]
    ]


def _infer_source(case: dict, filename: str) -> str:
    if "adversarial" in filename:
        return "adversarial"
    metadata = case.get("metadata", {})
    tier = metadata.get("tier", "")
    if tier in ("regression", "fix_note"):
        return "regression"
    if metadata.get("source") == "trace":
        return "production_log"
    return "curated"


def _migrate_case(
    case: dict,
    filename: str,
    chunk_store=None,
) -> tuple[dict, dict[str, int]]:
    """迁移单条用例到 V3 schema。返回 (new_case, stats)。"""
    expected = case.get("expected", {})
    metadata = case.get("metadata", {})
    stats = {"auto": 0, "partial": 0, "manual": 0, "no_source": 0, "reject": 0}

    should_reject = metadata.get("should_reject", False)
    relevant_docs = expected.get("relevant_docs", [])
    relevant_snippets = expected.get("relevant_snippets", [])

    if should_reject or not relevant_docs:
        gt_context = []
        stats["reject"] += 1
    else:
        doc_id = relevant_docs[0] if relevant_docs else ""
        gt_context, status = _extract_ground_truth_context(
            doc_id, relevant_snippets, chunk_store
        )
        stats[status] += 1

    new_expected = dict(expected)
    new_expected["ground_truth_context"] = gt_context

    source = _infer_source(case, filename)
    new_metadata = dict(metadata)
    new_metadata["source"] = source
    new_metadata["schema_version"] = "3.0"

    generation_eval = new_metadata.get("generation_eval", False)
    if generation_eval and not new_metadata.get("expected_answer"):
        new_metadata["expected_answer"] = new_metadata.get("expected_answer", "")

    new_case = {
        "id": case.get("id", ""),
        "question": case.get("question", ""),
        "module": case.get("module", "rag"),
        "expected": new_expected,
        "metadata": new_metadata,
    }
    if case.get("kb_id"):
        new_case["kb_id"] = case["kb_id"]

    return new_case, stats


def _migrate_file(
    filepath: Path,
    chunk_store=None,
    dry_run: bool = True,
) -> dict:
    """迁移单个文件。返回统计报告。"""
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    old_version = data.get("version", "1.0")
    cases = data.get("test_cases", [])
    new_cases = []
    total_stats = {"auto": 0, "partial": 0, "manual": 0, "no_source": 0, "reject": 0}

    for case in cases:
        new_case, stats = _migrate_case(case, filepath.name, chunk_store)
        new_cases.append(new_case)
        for k, v in stats.items():
            total_stats[k] += v

    new_data = dict(data)
    new_data["version"] = "3.0"
    new_data["test_cases"] = new_cases

    if not dry_run:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(new_data, f, ensure_ascii=False, indent=2)
            f.write("\n")

    return {
        "file": filepath.name,
        "old_version": old_version,
        "cases": len(cases),
        "stats": total_stats,
        "dry_run": dry_run,
    }


def _try_load_chunk_store():
    try:
        from backend.rag.indexing.chunk_store import get_chunk_store
        cs = get_chunk_store()
        print("[OK] ChunkStore 已加载，可使用原文片段提取")
        return cs
    except Exception as e:
        print(f"[WARN] ChunkStore 不可用 ({e})，将使用 snippet 降级")
        return None


def main():
    parser = argparse.ArgumentParser(description="评测集 V3 迁移")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="仅报告，不写入（默认）")
    parser.add_argument("--write", action="store_true",
                        help="实际写入文件")
    parser.add_argument("--files", nargs="*", default=None,
                        help="指定要迁移的文件（默认全部）")
    args = parser.parse_args()

    dry_run = not args.write
    chunk_store = _try_load_chunk_store()

    target_files: list[Path] = []
    if args.files:
        for f in args.files:
            p = Path(f)
            if p.exists():
                target_files.append(p)
    else:
        kb_file = DATASET_DIR / "rag_test_kb.json"
        if kb_file.exists():
            target_files.append(kb_file)
        rag_dir = DATASET_DIR / "rag"
        if rag_dir.is_dir():
            target_files.extend(sorted(rag_dir.glob("*.json")))

    print(f"\n迁移模式: {'DRY RUN' if dry_run else 'WRITE'}")
    print(f"目标文件: {len(target_files)} 个\n")

    all_reports = []
    grand_stats = {"auto": 0, "partial": 0, "manual": 0, "no_source": 0, "reject": 0}

    for filepath in target_files:
        report = _migrate_file(filepath, chunk_store, dry_run)
        all_reports.append(report)
        for k, v in report["stats"].items():
            grand_stats[k] += v
        print(f"  {report['file']}: {report['cases']} 条 "
              f"(v{report['old_version']} → v3.0)")
        s = report["stats"]
        print(f"    auto={s['auto']} partial={s['partial']} "
              f"manual={s['manual']} reject={s['reject']}")

    print(f"\n{'=' * 50}")
    print(f"总计: {sum(r['cases'] for r in all_reports)} 条用例")
    print(f"  auto:     {grand_stats['auto']}")
    print(f"  partial:  {grand_stats['partial']}")
    print(f"  manual:   {grand_stats['manual']}")
    print(f"  reject:   {grand_stats['reject']}")
    print(f"  no_source:{grand_stats['no_source']}")

    if dry_run:
        print("\n[DRY RUN] 未写入任何文件。加 --write 执行实际迁移。")
    else:
        print("\n[DONE] 所有文件已更新为 V3 schema。")


if __name__ == "__main__":
    main()
