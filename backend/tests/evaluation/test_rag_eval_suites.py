"""统一 RAG 评测套件的范围与 canonical 案例契约测试。"""

import json

import pytest

from backend.evaluation.dataset import load_dataset


def test_desktop_upload_suites_cover_the_new_canonical_cases():
    """上传专项 smoke/full 组必须只引用新 canonical 题库。"""
    full = load_dataset("rag", selection="desktop_upload_full")
    smoke = load_dataset("rag", selection="desktop_upload_smoke")

    assert [case.id for case in full] == [f"UFQ-{number:03d}" for number in range(1, 25)]
    assert [case.id for case in smoke] == [
        "UFQ-001", "UFQ-002", "UFQ-007", "UFQ-008",
        "UFQ-011", "UFQ-016", "UFQ-022", "UFQ-023",
    ]
    assert {case.id for case in smoke} <= {case.id for case in full}
    for cases in (full, smoke):
        assert {case.metadata["kb_id"] for case in cases} == {"rag_eval_kb"}
        assert {case.metadata["fixture_set"] for case in cases} == {"baseline"}
        assert {case.metadata["source_fixture_set"] for case in cases} == {"baseline"}


def test_canonical_loader_returns_the_24_case_candidate_dataset():
    """未指定 suite 时加载 24 条 canonical 桌面上传题目。"""
    cases = load_dataset("rag")

    assert len(cases) == 24
    assert {case.metadata["dataset_version"] for case in cases} == {
        "6.0.0-desktop-candidate.1"
    }


def test_suites_declare_required_scope_fields():
    """两组必须声明版本、统一 KB 和 baseline 范围。"""
    from backend.evaluation.dataset.loader import DATASET_DIR

    for suite_name, case_count in (("desktop_upload_smoke", 8), ("desktop_upload_full", 24)):
        data = json.loads(
            (DATASET_DIR / "rag" / "suites" / f"{suite_name}.json").read_text(encoding="utf-8")
        )
        assert data["kb_id"] == "rag_eval_kb"
        assert data["fixture_set"] == "baseline"
        assert data["dataset_version"] == "6.0.0-desktop-candidate.1"
        assert len(data["case_ids"]) == case_count
        assert data["min_samples"] == case_count


def test_suite_rejects_conflicting_case_kb(tmp_path, monkeypatch):
    """suite 声明与案例 KB 冲突时必须报错，而不是静默回退。"""
    from backend.evaluation.dataset import loader

    rag_dir = tmp_path / "rag"
    suites_dir = rag_dir / "suites"
    suites_dir.mkdir(parents=True)
    (rag_dir / "cases.jsonl").write_text(
        json.dumps(
            {
                "id": "RC-CONFLICT",
                "question": "测试问题",
                "module": "rag",
                "kb_id": "other_kb",
                "source_fixture_set": "baseline",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (rag_dir / "manifest.json").write_text(json.dumps({"version": "test"}), encoding="utf-8")
    (suites_dir / "conflict.json").write_text(
        json.dumps(
            {
                "kb_id": "rag_eval_kb",
                "fixture_set": "baseline",
                "dataset_version": "test",
                "case_ids": ["RC-CONFLICT"],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(loader, "DATASET_DIR", tmp_path)

    with pytest.raises(ValueError, match="kb_id"):
        load_dataset("rag", selection="conflict")
