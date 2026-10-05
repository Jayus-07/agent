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


# ======================= S10（2026-10-06）：reindex 审计 actor 透传 =======================

class TestReindexAuditActor:
    """S10 五动作矩阵实机发现 reindex 审计行 user_id=anonymous（upload/delete
    均=440）——log_reindex_* 必须把 actor 透传进 doc_operation_log。"""

    def test_log_reindex_success_carries_user_id(self, monkeypatch):
        captured: dict = {}

        class FakeLogger:
            def __init__(self, *a, **k):
                pass

            def log(self, **kw):
                captured.update(kw)

        import backend.rag.indexing.operation_log_pg as olp
        monkeypatch.setattr(olp, "PostgresDocumentOperationLogger", FakeLogger)

        from backend.rag.indexing.reindex_service import log_reindex_success
        log_reindex_success("d1", "n.md", "probe", trace_id=None, batch_id=None,
                            result={"doc": {}, "chunk_count": 1},
                            duration_ms=1, user_id="440")
        assert captured.get("user_id") == "440"

    def test_log_reindex_defaults_anonymous_when_no_actor(self, monkeypatch):
        captured: dict = {}

        class FakeLogger:
            def __init__(self, *a, **k):
                pass

            def log(self, **kw):
                captured.update(kw)

        import backend.rag.indexing.operation_log_pg as olp
        monkeypatch.setattr(olp, "PostgresDocumentOperationLogger", FakeLogger)

        from backend.rag.indexing.reindex_service import log_reindex_success
        log_reindex_success("d1", "n.md", "probe", trace_id=None, batch_id=None,
                            result={"doc": {}, "chunk_count": 1}, duration_ms=1)
        assert captured.get("user_id") == "anonymous"

    def test_run_reindex_signature_accepts_user_id(self):
        """run_reindex 形参面：user_id 可选透传（worker/路由调用方接线基础）。"""
        import inspect
        from backend.rag.indexing.reindex_service import run_reindex
        assert "user_id" in inspect.signature(run_reindex).parameters
