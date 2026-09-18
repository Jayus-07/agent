"""PG chunk store 一致性检查回归测试。"""

from types import SimpleNamespace


def test_consistency_checker_uses_chunk_store_doc_id_interface(monkeypatch):
    import backend.rag.indexing.chunk_store as chunk_store_module
    from backend.rag.indexing.consistency import (
        ConsistencyReport,
        IndexConsistencyChecker,
    )

    fake_store = SimpleNamespace(list_doc_ids=lambda: {"doc-active", "doc-orphan"})
    monkeypatch.setattr(chunk_store_module, "get_chunk_store", lambda: fake_store)

    checker = object.__new__(IndexConsistencyChecker)
    checker.registry = SimpleNamespace(
        list_active=lambda: [{"doc_id": "doc-active"}],
        list_by_statuses=lambda statuses: [],
    )
    report = ConsistencyReport()

    checker._check_chunk_store(report)

    assert [(issue.store, issue.doc_id) for issue in report.issues] == [
        ("chunk_store", "doc-orphan")
    ]
