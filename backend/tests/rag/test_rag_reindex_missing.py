"""缺失索引批量重建的目标选择规则。"""


def test_select_targets_only_failed_real_docs_and_preserves_path_identity():
    from backend.scripts.rag_reindex_missing import select_targets

    rows = [
        {"file_path": "/app/data/docs/z.md", "doc_id": "z", "status": "failed",
         "updated_at": "2026-10-03 05:13:46"},
        {"file_path": "/app/data/docs/a.md", "doc_id": "a", "status": "failed",
         "updated_at": "2026-10-03 05:13:46"},
        {"file_path": "/tmp/pytest/a.md", "doc_id": "a", "status": "failed",
         "updated_at": "2026-10-03 05:13:46"},
        {"file_path": "/app/data/docs/review.md", "doc_id": "r",
         "status": "pending_review", "updated_at": "2026-10-03 05:13:46"},
    ]
    assert select_targets(rows, since="2026-10-03 05:13:46") == [rows[1], rows[0]]


def test_select_targets_supports_active_missing_vector_rows():
    from backend.scripts.rag_reindex_missing import select_targets

    rows = [
        {"status": "active", "doc_id": "a", "file_path": "/app/data/docs/a.md",
         "updated_at": "2026-10-03 00:00:00"},
        {"status": "active", "doc_id": "b", "file_path": "/app/data/docs/b.md",
         "updated_at": "2026-10-03 00:00:00"},
    ]

    selected = select_targets(
        rows, since="1970-01-01", status="active", missing_doc_ids={"b"}
    )

    assert [row["doc_id"] for row in selected] == ["b"]
