"""fixture_set 在索引元数据各落点的契约测试。"""

from backend.rag.indexing.indexer import _apply_fixture_metadata


def test_fixture_set_is_test_only_optional_metadata():
    metadata = {"kb_id": "rag_eval_kb", "doc_type": "policy"}

    _apply_fixture_metadata(metadata, "expanded_100")

    assert metadata["kb_id"] == "rag_eval_kb"
    assert metadata["fixture_set"] == "expanded_100"


def test_production_metadata_without_fixture_set_is_unchanged():
    metadata = {"kb_id": "policy_general", "doc_type": "policy"}

    _apply_fixture_metadata(metadata, None)

    assert metadata == {"kb_id": "policy_general", "doc_type": "policy"}
