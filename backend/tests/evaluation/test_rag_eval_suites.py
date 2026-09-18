"""统一 RAG 评测套件的范围与 canonical 案例契约测试。"""

import json

import pytest

from backend.evaluation.dataset import load_dataset


def test_pr_suite_has_canonical_cases_and_baseline_scope():
    """PR 套件只能跑统一评测 KB 的 baseline 语料。"""
    cases = load_dataset("rag", selection="pr_baseline")

    assert cases
    assert {case.metadata["kb_id"] for case in cases} == {"rag_eval_kb"}
    assert {case.metadata["fixture_set"] for case in cases} == {"baseline"}
    assert {case.metadata["source_fixture_set"] for case in cases} == {"baseline"}


def test_expanded_suite_is_separate_from_pr_suite():
    """100 文档专项案例不能与 PR baseline 复用同一批 ID。"""
    baseline = {case.id for case in load_dataset("rag", selection="pr_baseline")}
    expanded = {case.id for case in load_dataset("rag", selection="expanded_100")}

    assert baseline
    assert expanded
    assert baseline.isdisjoint(expanded)
    assert all(case.metadata["fixture_set"] == "expanded_100" for case in load_dataset("rag", selection="expanded_100"))


def test_scale_suite_reuses_expanded_questions_but_changes_runtime_scope():
    """20k 套件复用 RD 问题，但运行时必须要求 staging 的 scale_20k 语料。"""
    expanded = load_dataset("rag", selection="expanded_100")
    scale = load_dataset("rag", selection="scale_20k")

    assert [case.id for case in scale] == [case.id for case in expanded]
    assert {case.metadata["fixture_set"] for case in scale} == {"scale_20k"}
    assert {case.metadata["source_fixture_set"] for case in scale} == {"expanded_100"}


def test_suite_metadata_is_not_mutated_across_loads():
    """同一 canonical 案例被不同 suite 引用时，不能污染后续加载结果。"""
    scale = load_dataset("rag", selection="scale_20k")
    expanded = load_dataset("rag", selection="expanded_100")

    assert scale[0].metadata["fixture_set"] == "scale_20k"
    assert expanded[0].metadata["fixture_set"] == "expanded_100"


def test_suites_declare_required_scope_fields():
    """套件文件必须显式声明范围，禁止再次依赖默认路径猜测。"""
    from backend.evaluation.dataset.loader import DATASET_DIR

    for suite_name in ("pr_baseline", "expanded_100", "scale_20k"):
        data = json.loads(
            (DATASET_DIR / "rag" / "suites" / f"{suite_name}.json").read_text(encoding="utf-8")
        )
        assert data["kb_id"] == "rag_eval_kb"
        assert data["fixture_set"] in {"baseline", "expanded_100", "scale_20k"}
        assert data["dataset_version"]
        assert data["case_ids"]


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
