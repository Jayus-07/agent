from backend.evaluation.dataset.synthetic import build_v2_candidates


def test_synthetic_result_remains_unverified_until_human_review():
    generated = {
        "manifest": {"dataset_key": "support_golden", "version": "1.0.0"},
        "cases": [{
            "id": "SYN-001",
            "question": "如何申请年假？",
            "expected": {
                "behavior": "answer",
                "reference_answer": "提前三个工作日提交。",
                "required_facts": ["提前申请"],
                "evidence": [{"doc_id": "doc-1", "quote": "提前三个工作日提交申请。"}],
            },
        }],
    }

    [candidate] = build_v2_candidates(
        generated,
        snapshot_id="a" * 24,
        task_id="task-123",
        kb_id="kb_policy",
        active_versions={"doc-1": "generation-7"},
    )

    assert "expected" not in candidate
    assert candidate["observed_answer"] == ""
    assert candidate["proposed_facts"][0]["reference_answer"] == "提前三个工作日提交。"
    assert candidate["proposed_evidence"] == [{
        "kb_id": "kb_policy",
        "doc_id": "doc-1",
        "doc_version": "generation-7",
        "quote": "提前三个工作日提交申请。",
    }]
