from contextlib import contextmanager

import pytest

from backend.evaluation_v2.review_quality import (
    RAGAS_QUALITY_CHECKS,
    resolve_fact_evidence_bindings,
    validate_ragas_candidate_review,
)


def _review_inputs():
    return {
        "payload": {
            "metadata": {
                "generation_method": "ragas_mixed",
                "query_type": "multi_hop_specific",
            },
        },
        "expected": {
            "behavior": "answer",
            "reference_answer": "两个规则组合后的答案",
            "required_facts": ["规则一", "规则二"],
        },
        "evidence": [
            {"anchor_id": "anchor-one", "quote": "规则一原文"},
            {"anchor_id": "anchor-two", "quote": "规则二原文"},
        ],
        "fact_evidence_bindings": [
            {"fact_index": 0, "evidence_indices": [0]},
            {"fact_index": 1, "evidence_indices": [1]},
        ],
        "quality_checks": {key: True for key in RAGAS_QUALITY_CHECKS},
        "rationale": "已逐条核对原文、标准答案、必需事实和多跳证据，问题清晰且可判分。",
    }


def test_ragas_candidate_review_persists_all_quality_attestations():
    inputs = _review_inputs()

    assert validate_ragas_candidate_review(**inputs) == {
        key: True for key in RAGAS_QUALITY_CHECKS
    }


def test_ragas_candidate_review_rejects_missing_facts_and_checklist():
    inputs = _review_inputs()
    inputs["expected"]["required_facts"] = []
    with pytest.raises(ValueError, match="必需事实"):
        validate_ragas_candidate_review(**inputs)

    inputs = _review_inputs()
    inputs["quality_checks"].pop(RAGAS_QUALITY_CHECKS[0])
    with pytest.raises(ValueError, match="五项质量核验"):
        validate_ragas_candidate_review(**inputs)


def test_ragas_multihop_candidate_requires_two_verified_anchors():
    inputs = _review_inputs()
    inputs["evidence"][1]["anchor_id"] = "anchor-one"

    with pytest.raises(ValueError, match="至少两条原文证据锚点"):
        validate_ragas_candidate_review(**inputs)


def test_ragas_review_requires_each_fact_to_map_to_a_verified_anchor():
    inputs = _review_inputs()
    inputs["fact_evidence_bindings"] = []
    with pytest.raises(ValueError, match="每条必需事实都必须关联"):
        validate_ragas_candidate_review(**inputs)

    inputs = _review_inputs()
    inputs["fact_evidence_bindings"][1]["evidence_indices"] = [2]
    with pytest.raises(ValueError, match="引用序号越界"):
        validate_ragas_candidate_review(**inputs)


def test_ragas_multihop_review_requires_two_distinct_required_facts():
    inputs = _review_inputs()
    inputs["expected"]["required_facts"] = ["同一事实", " 同一 事实 "]
    with pytest.raises(ValueError, match="至少两条不同的必需事实"):
        validate_ragas_candidate_review(**inputs)


def test_fact_evidence_audit_resolves_only_verified_anchor_ids():
    bindings = resolve_fact_evidence_bindings(
        required_facts=["规则一", "规则二"],
        evidence=[
            {"anchor_id": "anchor-one"},
            {"anchor_id": "anchor-two"},
        ],
        bindings=[
            {"fact_index": 0, "evidence_indices": [0]},
            {"fact_index": 1, "evidence_indices": [1]},
        ],
    )

    assert bindings == [
        {"fact_index": 0, "fact": "规则一", "anchor_ids": ["anchor-one"]},
        {"fact_index": 1, "fact": "规则二", "anchor_ids": ["anchor-two"]},
    ]

    with pytest.raises(ValueError, match="服务端核验过"):
        resolve_fact_evidence_bindings(
            required_facts=["规则一"],
            evidence=[{"quote": "还未核验"}],
            bindings=[{"fact_index": 0, "evidence_indices": [0]}],
        )


def test_non_ragas_candidate_keeps_existing_review_flow():
    inputs = _review_inputs()
    inputs["payload"]["metadata"]["generation_method"] = "manual"
    inputs["quality_checks"] = None

    assert validate_ragas_candidate_review(**inputs) is None


def test_candidate_annotations_query_is_scoped_to_tenant_and_candidate(monkeypatch):
    from backend.evaluation_v2 import repository

    class Cursor:
        query = ""
        params = None

        def execute(self, query, params):
            self.query = query
            self.params = params

        def fetchall(self):
            return [{
                "reviewer_id": "user:reviewer",
                "verdict": "approved",
                "evidence_json": [],
                "candidate_evidence_json": None,
            }]

    cursor = Cursor()

    @contextmanager
    def transaction(tenant_id):
        assert tenant_id == "tenant-a"
        yield None, cursor

    monkeypatch.setattr(repository, "_transaction", transaction)
    records = repository.list_candidate_annotations(
        "tenant-a", "42000000-0000-4000-8000-000000000002",
    )

    assert records == [{
        "reviewer_id": "user:reviewer",
        "verdict": "approved",
        "evidence_json": [],
    }]
    assert "WHERE a.tenant_id = %s AND a.candidate_id = %s" in cursor.query
    assert cursor.params == (
        "tenant-a", "42000000-0000-4000-8000-000000000002",
    )


def test_rejected_review_history_falls_back_to_original_candidate_evidence(monkeypatch):
    from backend.evaluation_v2 import repository

    class Cursor:
        query = ""
        params = None

        def execute(self, query, params):
            self.query = query
            self.params = params

        def fetchall(self):
            return [{
                "verdict": "rejected",
                "evidence_json": [],
                "candidate_evidence_json": [{
                    "kb_id": "kb-test",
                    "doc_id": "doc-test",
                    "doc_version": "v1",
                    "quote": "原始候选证据。",
                }],
            }]

    cursor = Cursor()

    @contextmanager
    def transaction(tenant_id):
        assert tenant_id == "tenant-a"
        yield None, cursor

    monkeypatch.setattr(repository, "_transaction", transaction)
    records = repository.list_candidate_annotations(
        "tenant-a", "42000000-0000-4000-8000-000000000004",
    )

    assert records[0]["evidence_json"] == [{
        "kb_id": "kb-test",
        "doc_id": "doc-test",
        "doc_version": "v1",
        "quote": "原始候选证据。",
    }]
    assert "JOIN evaluation_v2.eval_candidates" in cursor.query
    assert "c.tenant_id = a.tenant_id AND c.candidate_id = a.candidate_id" in cursor.query
    assert cursor.params == (
        "tenant-a", "42000000-0000-4000-8000-000000000004",
    )


def test_candidate_list_query_filters_dataset_key(monkeypatch):
    from backend.evaluation_v2 import repository

    class Cursor:
        query = ""
        params = None

        def execute(self, query, params):
            self.query = query
            self.params = params

        def fetchall(self):
            return []

    cursor = Cursor()

    @contextmanager
    def transaction(tenant_id):
        assert tenant_id == "tenant-a"
        yield None, cursor

    monkeypatch.setattr(repository, "_transaction", transaction)
    repository.list_candidates(
        "tenant-a", review_status="approved", dataset_key="rag_core", limit=50, offset=0,
    )

    assert "NULLIF(payload_json #>> '{metadata,dataset_key}', '')" in cursor.query
    assert "payload_json #>> '{source_ref,dataset_key}'" in cursor.query
    assert cursor.params == (
        "tenant-a", "approved", "approved", "rag_core", "rag_core", 50, 0,
    )
