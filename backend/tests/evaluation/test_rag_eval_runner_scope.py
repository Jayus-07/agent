"""RAG 评测运行范围契约测试。"""

import pytest

from backend.evaluation.config import EvalConfig
from backend.evaluation.runners.rag import build_eval_scope
from backend.evaluation.runner import run_all
from backend.evaluation.storage import validate_run_id


def test_runner_rejects_unknown_fixture_set():
    with pytest.raises(ValueError, match="fixture_set"):
        build_eval_scope(kb_id="rag_eval_kb", fixture_set="missing", multiquery=False)


def test_runner_records_scope_and_multiquery():
    scope = build_eval_scope(
        kb_id="rag_eval_kb", fixture_set="expanded_100", multiquery=True,
    )

    assert scope.as_dict() == {
        "kb_id": "rag_eval_kb",
        "fixture_set": "expanded_100",
        "multiquery": True,
    }


def test_eval_config_carries_explicit_scope_fields():
    config = EvalConfig(
        module="rag",
        selection="expanded_100",
        kb_id="rag_eval_kb",
        fixture_set="expanded_100",
        dataset_version="5.0.0-unified",
        run_id="resume-me-001",
    )

    assert config.kb_id == "rag_eval_kb"
    assert config.fixture_set == "expanded_100"
    assert config.dataset_version == "5.0.0-unified"
    assert config.run_id == "resume-me-001"


def test_run_id_rejects_path_traversal():
    with pytest.raises(ValueError, match="非法评测 run_id"):
        validate_run_id("..\\outside")


def test_run_id_accepts_resume_safe_name():
    assert validate_run_id("2026-09-18T10-00-00-a1b2c3") == "2026-09-18T10-00-00-a1b2c3"


def test_persist_report_reuses_report_run_id(tmp_path, monkeypatch):
    from backend.evaluation import storage
    from backend.evaluation.models import EvalReport

    monkeypatch.setattr(storage, "DATA_ROOT", tmp_path)
    report = EvalReport(
        module="rag", mode="offline", summaries=[], results=[],
        metadata={"run_id": "resume-001"},
    )

    run_dir = storage.persist_report(report)

    assert run_dir == tmp_path / "resume-001"
    assert '"run_id": "resume-001"' in (run_dir / "report.json").read_text(encoding="utf-8")


def test_cli_runner_exposes_selection_as_named_suite():
    """统一入口保留 selection 参数，完整评测不依赖 pytest 的 --full。"""
    import inspect

    assert "selection" in inspect.signature(run_all).parameters
