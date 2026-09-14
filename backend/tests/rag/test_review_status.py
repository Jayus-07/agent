"""4.1 near_dup → pending_review — 近似重复文档进入审核态。

契约：
- register() 无 near_dup_id → status='active'（行为不变）
- register() 带 near_dup_id → status='pending_review'（此前静默 active）
- pending_review 文档数据完整入库（可人工比对），检索层软过滤另做
"""
import pytest

from backend.rag.indexing.doc_registry import DocumentRegistry


@pytest.fixture
def registry(tmp_path):
    return DocumentRegistry(str(tmp_path / "registry_test.db"))


def _register(reg, near_dup_id: str = ""):
    reg.register(
        file_path="/tmp/docs/kb/dept/doc_a.md",
        doc_id="doc_a_1",
        file_hash="hash_a",
        kb_id="kb",
        chunk_ids=["c1", "c2"],
        doc_db_id="db_1",
        metadata={
            "doc_type": "policy",
            "minhash_sig": "[1, 2, 3]",
            "near_dup_id": near_dup_id,
        },
    )
    return reg.get_by_path("/tmp/docs/kb/dept/doc_a.md")


class TestRegisterStatus:

    def test_normal_doc_active(self, registry):
        row = _register(registry)
        assert row["status"] == "active"
        assert row["near_dup_id"] == ""

    def test_near_dup_pending_review(self, registry):
        row = _register(registry, near_dup_id="doc_original_1")
        assert row["status"] == "pending_review"
        assert row["near_dup_id"] == "doc_original_1"

    def test_pending_review_data_intact(self, registry):
        """审核态文档数据完整（chunk_ids/near_dup 保留，可人工比对）。"""
        row = _register(registry, near_dup_id="doc_original_1")
        import json
        assert json.loads(row["chunk_ids"]) == ["c1", "c2"]
        assert row["doc_type"] == "policy"
