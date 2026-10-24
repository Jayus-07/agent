import copy

import pytest

from backend.evaluation import corpus_snapshots
from backend.evaluation.dataset import imports as assets


def _snapshot_record() -> dict:
    return {
        "snapshot_id": "snapshot-one",
        "kb_id": "kb-a",
        "actor": "operator-one",
        "created_at": "2026-01-01T00:00:00+00:00",
        "collections": ["eval-one-0", "eval-one-1"],
        "documents": [{"doc_id": "doc-a", "file_hash": "a" * 64, "active_generation": "gen-a"}],
        "visible_ids": ["doc-a"],
        "capture_warnings": [],
        "registry_vector_mapping_complete": True,
        "fulltext": [
            {
                "id": 1,
                "created_at": "2026-01-01T00:00:00+00:00",
                "doc_id": "doc-a",
                "chunk_id": "chunk-a",
                "content": "稳定语料内容",
            }
        ],
        "vector_manifests": ["vector-hash-a", "vector-hash-b"],
    }


def test_corpus_content_hash_ignores_capture_identity_and_surrogate_ids():
    first = _snapshot_record()
    second = copy.deepcopy(first)
    second.update(
        snapshot_id="snapshot-two",
        actor="operator-two",
        created_at="2026-01-02T00:00:00+00:00",
        collections=["eval-two-0", "eval-two-1"],
    )
    second["fulltext"][0].update(id=99, created_at="2026-01-02T00:00:00+00:00")

    assert corpus_snapshots.corpus_content_hash(first) == corpus_snapshots.corpus_content_hash(second)


def test_vector_manifest_ignores_random_snapshot_collection_prefix():
    class Cursor:
        def __init__(self, collection: str):
            self.collection = collection

        def execute(self, _query, _params):
            pass

        def fetchall(self):
            return [
                {"id": f"{self.collection}:chroma:doc-a:0", "hash": "row-hash-a"},
                {"id": f"{self.collection}:chroma:doc-a:1", "hash": "row-hash-b"},
            ]

    first = corpus_snapshots.vector_manifest(Cursor("eval-one-0"), "vectors", "eval-one-0")
    second = corpus_snapshots.vector_manifest(Cursor("eval-two-0"), "vectors", "eval-two-0")

    assert first == second


def test_load_validates_new_integrity_hash_and_legacy_snapshot(monkeypatch):
    record = _snapshot_record()
    record["content_hash"] = corpus_snapshots.corpus_content_hash(record)
    record["integrity_hash"] = assets.digest(record)
    monkeypatch.setattr(assets, "read", lambda *_args: copy.deepcopy(record))

    assert corpus_snapshots.load("snapshot-one")["content_hash"] == record["content_hash"]

    tampered = copy.deepcopy(record)
    tampered["actor"] = "tampered"
    monkeypatch.setattr(assets, "read", lambda *_args: copy.deepcopy(tampered))
    with pytest.raises(ValueError, match="校验失败"):
        corpus_snapshots.load("snapshot-one")

    legacy = _snapshot_record()
    legacy["content_hash"] = assets.digest(legacy)
    monkeypatch.setattr(assets, "read", lambda *_args: copy.deepcopy(legacy))
    assert corpus_snapshots.load("snapshot-one")["snapshot_id"] == "snapshot-one"
