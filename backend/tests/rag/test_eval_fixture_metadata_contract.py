"""评测 fixture 写入向量前的身份元数据契约。"""

from __future__ import annotations

from backend.rag.indexing.indexer import build_fixture_chunk_metadata


def test_fixture_metadata_contains_full_evaluation_identity():
    metadata = build_fixture_chunk_metadata(
        {
            "doc_id": "refund_policy",
            "kb_id": "rag_eval_kb",
            "fixture_set": "baseline",
            "version_id": "rag_eval_kb-baseline-2026-09-18",
        }
    )

    assert metadata == {
        "doc_id": "refund_policy",
        "kb_id": "rag_eval_kb",
        "fixture_set": "baseline",
        "dataset": "rag_eval",
        "version_id": "rag_eval_kb-baseline-2026-09-18",
    }


def test_production_metadata_does_not_receive_evaluation_dataset_marker():
    assert build_fixture_chunk_metadata({"kb_id": "default"}) == {}
