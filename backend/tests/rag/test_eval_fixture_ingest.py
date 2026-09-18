"""统一评测 fixture 入库入口的契约测试。"""

from pathlib import Path

from backend.evaluation.dataset.fixture_catalog import FixtureDocument
from backend.scripts.ingest_eval_fixtures import ingest_fixture


class FakeRegistry:
    def __init__(self):
        self.rows = {}

    def get_by_path(self, file_path):
        return self.rows.get(str(Path(file_path).resolve()))

    def register_in_progress(self, file_path, doc_id, file_hash, kb_id, department=""):
        self.rows[str(Path(file_path).resolve())] = {
            "file_path": str(Path(file_path).resolve()),
            "doc_id": doc_id,
            "file_hash": file_hash,
            "kb_id": kb_id,
            "department": department,
            "status": "parsing",
        }

    def update_fields(self, file_path, fields):
        self.rows[str(Path(file_path).resolve())].update(fields)

    def register(self, file_path, doc_id, file_hash, kb_id, chunk_ids, doc_db_id, metadata):
        row = self.rows[str(Path(file_path).resolve())]
        row.update(metadata)
        row.update({
            "doc_id": doc_id,
            "file_hash": file_hash,
            "kb_id": kb_id,
            "status": "active",
        })

    def count_by_fixture_doc_id(self, fixture_doc_id):
        return sum(row.get("doc_id") == fixture_doc_id for row in self.rows.values())


class FakeIndexer:
    def __init__(self, registry):
        self.registry = registry

    def _index_file(self, file_path, file_hash=None):
        row = self.registry.get_by_path(file_path)
        self.registry.register(
            file_path=file_path,
            doc_id=row["doc_id"],
            file_hash=file_hash or row["file_hash"],
            kb_id=row["kb_id"],
            chunk_ids=[f"{row['doc_id']}_0"],
            doc_db_id="doc-1",
            metadata={"fixture_set": row["fixture_set"]},
        )
        return {"chunk_count": 1, "doc_id": row["doc_id"]}


def _document() -> FixtureDocument:
    return FixtureDocument(
        fixture_doc_id="policy_attendance_rnd",
        source_file=(
            "backend/evaluation/fixtures/rag_100_docs/files/"
            "md/policy_考勤管理制度_研发中心.md"
        ),
        fixture_set="expanded_100",
        format="md",
        kb_id="rag_eval_kb",
        metadata={},
    )


def test_ingest_propagates_canonical_kb_and_fixture_set():
    registry = FakeRegistry()
    result = ingest_fixture(_document(), registry=registry, indexer=FakeIndexer(registry))

    assert result.metadata["kb_id"] == "rag_eval_kb"
    assert result.metadata["fixture_set"] == "expanded_100"
    assert registry.rows[result.file_path]["fixture_set"] == "expanded_100"


def test_reingest_same_fixture_is_idempotent():
    registry = FakeRegistry()
    indexer = FakeIndexer(registry)
    document = _document()

    first = ingest_fixture(document, registry=registry, indexer=indexer)
    second = ingest_fixture(document, registry=registry, indexer=indexer)

    assert first.skipped is False
    assert second.skipped is True
    assert registry.count_by_fixture_doc_id("policy_attendance_rnd") == 1
