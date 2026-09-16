"""一次性修复：重建主语料 registry 的 minhash_sig（被全量重建路径的
_sync_registry_after_full_rebuild 清空），恢复 near-dup 检测能力，
并把 4 份近似重复副本恢复 pending_review（R2 基线语料状态 24+4）。

签名来源：chunk_store 按 doc_id 拼接 chunk 正文（分块/重叠不改变
字符级 n-gram 集合，签名与索引期全文本签名自洽等价）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from backend.config.database import DOC_REGISTRY_PATH  # noqa: E402
from backend.rag.indexing.chunk_store import get_chunk_store  # noqa: E402
from backend.rag.indexing.doc_registry import DocumentRegistry  # noqa: E402
from backend.rag.preprocessing.metadata import (  # noqa: E402
    compute_minhash,
    minhash_similarity,
)

# 被翻成 active 的近似重复副本（R1 迁移对账 + run-1 MinHash 告警证实）
DUP_DOC_IDS = {"4b8b61b688", "8f11837e71", "91fb11c3a2", "f1805bdacd"}


def _row_meta(row: dict, sig: list[int]) -> dict:
    return {
        "doc_type": row.get("doc_type") or "general",
        "confidence": row.get("confidence") or 0,
        "llm_used": row.get("llm_used"),
        "quality_score": row.get("quality_score") or 0,
        "quality_issues": row.get("quality_issues") or "",
        "summary": row.get("summary") or "",
        "keywords": row.get("keywords") or "",
        "time_refs": row.get("time_refs") or "",
        "business_domain": row.get("business_domain") or "",
        "complexity": row.get("complexity") or "",
        "metadata_fingerprint": row.get("metadata_fingerprint") or "",
        "doc_version": row.get("doc_version") or 1,
        "kb_version": row.get("kb_version") or "v1",
        "department": row.get("department") or "",
        "minhash_sig": json.dumps(sig),
        "near_dup_id": row.get("near_dup_id") or "",
        "embedding_model": row.get("embedding_model") or "",
    }


def main() -> int:
    registry = DocumentRegistry(DOC_REGISTRY_PATH)
    store = get_chunk_store()
    rows = registry.list_all()
    main_rows = {p: r for p, r in rows.items() if r.get("kb_id") != "rag_100_docs"}

    # ① 重算签名 + register 保留原值回写
    sigs: dict[str, list[int]] = {}
    for p, r in main_rows.items():
        doc_id = r.get("doc_id", "")
        text = "\n".join(ch.get("content", "") for ch in store.get_by_doc_id(doc_id))
        sig = compute_minhash(text)
        sigs[doc_id] = sig
        try:
            chunk_ids = json.loads(r.get("chunk_ids") or "[]")
        except (ValueError, TypeError):
            chunk_ids = []
        registry.register(
            p, doc_id=doc_id, file_hash=r.get("file_hash") or "",
            kb_id=r.get("kb_id") or "default",
            chunk_ids=chunk_ids, doc_db_id=r.get("doc_db_id") or "",
            metadata=_row_meta(r, sig),
        )
    print(f"签名重算回写: {len(sigs)} 行")

    # ② 副本恢复 pending_review
    restored = 0
    for p, r in main_rows.items():
        if r.get("doc_id") in DUP_DOC_IDS and r.get("status") == "active":
            registry.update_status(p, "pending_review")
            restored += 1
            print(f"恢复 pending_review: {Path(p).name} ({r.get('doc_id')})")
    print(f"副本恢复: {restored} 行")

    # ③ 自检：副本 vs 原件相似度应 > 阈值
    from backend.config.indexing_rules import get_rules
    th = get_rules().near_dup_similarity_threshold
    by_id = {r.get("doc_id"): r for r in main_rows.values()}
    for dup in sorted(DUP_DOC_IDS):
        best, best_id = 0.0, ""
        for did, sig in sigs.items():
            if did == dup:
                continue
            s = minhash_similarity(sigs[dup], sig)
            if s > best:
                best, best_id = s, did
        flag = "OK" if best > th else "!!"
        print(f"  [{flag}] {dup} vs {best_id}: sim={best:.2f} (阈值 {th})")
    active = sum(1 for r in main_rows.values() if r.get("status") == "active")
    print(f"主语料 active: {active}（应为 24）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
