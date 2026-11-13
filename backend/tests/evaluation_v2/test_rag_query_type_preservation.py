import hashlib

from backend.evaluation.runners.rag import _multi_hop_success
from backend.evaluation_v2 import runner


def test_v2_ragas_query_type_survives_legacy_runner_adapter(monkeypatch):
    from backend.evaluation.dataset import imports as assets

    captured = {}

    def create_import(validation, _actor):
        assert validation["valid"]
        captured["case"] = validation["cases"][0]
        return {"import_id": "import-id", "content_hash": "a" * 64}

    def load_binding(_binding_id):
        return (
            {
                "kb_id": "kb-test",
                "split": "",
                "mode": "answer",
                "subject_user_id": None,
            },
            {
                "cases": [captured["case"]],
                "manifest": {"version": "1.0.0"},
                "content_hash": "c" * 64,
            },
        )

    monkeypatch.setattr(assets, "create_import", create_import)
    monkeypatch.setattr(assets, "write_once", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(assets, "load_binding", load_binding)
    monkeypatch.setattr(assets, "check_evidence", lambda *_args: None)

    file_hash = "b" * 64
    quote_a = "订单需要先完成资格核验。"
    quote_b = "确认后还要提交付款凭证。"
    run = {
        "run_id": "a52d012e-59b6-467e-aad2-5a40b8337d17",
        "kb_id": "kb-test",
        "created_by": "user:reviewer",
        "snapshot_json": {},
    }
    definition = {
        "dataset_version": "1.0.0",
        "minimum_valid_samples": 1,
        "metric_policy": {"min_pass_rate": 0.95},
        "cases": [{
            "case_id": "ragas-case-1",
            "input_json": {"query": "资格核验和付款凭证都完成后怎么处理？"},
            "expected_json": {
                "behavior": "answer",
                "reference_answer": "先核验资格，再确认付款凭证。",
                "required_facts": ["先核验资格", "确认付款凭证"],
            },
            "evidence_json": [
                {
                    "kb_id": "kb-test", "doc_id": "doc-1", "doc_version": "v1",
                    "anchor_id": "chunk-1",
                    "anchor_text_hash": hashlib.sha256(quote_a.encode()).hexdigest(),
                    "source_content_hash": file_hash,
                    "quote": quote_a,
                },
                {
                    "kb_id": "kb-test", "doc_id": "doc-1", "doc_version": "v1",
                    "anchor_id": "chunk-2",
                    "anchor_text_hash": hashlib.sha256(quote_b.encode()).hexdigest(),
                    "source_content_hash": file_hash,
                    "quote": quote_b,
                },
            ],
            "metadata_json": {"query_type": "multi_hop_specific", "tier": "core"},
        }],
    }
    snapshot = {
        "snapshot_id": "snapshot-1",
        "documents": [{
            "doc_id": "doc-1", "version_id": "v1", "file_hash": file_hash,
            "file_name": "rules.docx",
        }],
        "fulltext": [
            {"doc_id": "doc-1", "chunk_id": "chunk-1", "content": quote_a},
            {"doc_id": "doc-1", "chunk_id": "chunk-2", "content": quote_b},
        ],
    }

    binding_id = runner._legacy_dataset(run, definition, snapshot)
    normalized_case = assets.normalized_cases(binding_id)[0]

    assert normalized_case.metadata["query_type"] == "multi_hop_specific"
    assert normalized_case.expected["relevant_snippets"] == [quote_a, quote_b]


def test_multi_hop_success_scores_each_verified_evidence_snippet():
    query_type = "multi_hop_specific"
    expected = ["订单需要先完成资格核验。", "确认后还要提交付款凭证。"]
    one_anchor = [{"page_content": expected[0]}]
    both_anchors = [{"page_content": item} for item in expected]

    partial = _multi_hop_success(
        query_type,
        details=one_anchor,
        expected_docs=["doc-1"],
        expected_chunks=set(),
        expected_snippets=expected,
        actual_docs=["doc-1"],
    )
    complete = _multi_hop_success(
        query_type,
        details=both_anchors,
        expected_docs=["doc-1"],
        expected_chunks=set(),
        expected_snippets=expected,
        actual_docs=["doc-1"],
    )

    assert partial == 0.5
    assert complete == 1.0

    legacy_document_score = _multi_hop_success(
        "multi_doc",
        details=[],
        expected_docs=["doc-1", "doc-2"],
        expected_chunks=set(),
        expected_snippets=[],
        actual_docs=["doc-1"],
    )
    legacy_chunk_score = _multi_hop_success(
        "reasoning",
        details=[{"chunk_id": "chunk-1"}],
        expected_docs=[],
        expected_chunks={"chunk-1", "chunk-2"},
        expected_snippets=[],
        actual_docs=[],
    )
    single_hop_score = _multi_hop_success(
        "single_hop_specific",
        details=both_anchors,
        expected_docs=["doc-1"],
        expected_chunks=set(),
        expected_snippets=expected,
        actual_docs=["doc-1"],
    )

    assert legacy_document_score == 0.5
    assert legacy_chunk_score == 0.5
    assert single_hop_score is None
