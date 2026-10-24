import hashlib

import pytest

from backend.evaluation_v2.runner import _snapshot_evidence_quote


def test_evidence_quote_resolves_from_frozen_chunk_and_checks_hash():
    quote = "费用发生后须在 20 个工作日内提交报销单。"
    anchor = {
        "doc_id": "doc-a",
        "anchor_id": "chunk-a",
        "anchor_text_hash": hashlib.sha256(quote.encode("utf-8")).hexdigest(),
    }
    snapshot = {
        "fulltext": [
            {"doc_id": "doc-b", "chunk_id": "chunk-a", "content": "另一个文档"},
            {"doc_id": "doc-a", "chunk_id": "chunk-a", "content": quote},
        ]
    }

    assert _snapshot_evidence_quote(anchor, snapshot) == quote


@pytest.mark.parametrize(
    ("snapshot", "expected_error"),
    [
        ({"fulltext": []}, "缺失或不唯一"),
        (
            {"fulltext": [{"doc_id": "doc-a", "chunk_id": "chunk-a", "content": "已变化"}]},
            "哈希不匹配",
        ),
        (
            {
                "fulltext": [
                    {"doc_id": "doc-a", "chunk_id": "chunk-a", "content": "锚点一"},
                    {"doc_id": "doc-a", "chunk_id": "chunk-a", "content": "锚点二"},
                ]
            },
            "缺失或不唯一",
        ),
    ],
)
def test_evidence_quote_fails_closed(snapshot, expected_error):
    quote = "已审核原文"
    anchor = {
        "doc_id": "doc-a",
        "anchor_id": "chunk-a",
        "anchor_text_hash": hashlib.sha256(quote.encode("utf-8")).hexdigest(),
    }

    with pytest.raises(ValueError, match=expected_error):
        _snapshot_evidence_quote(anchor, snapshot)
