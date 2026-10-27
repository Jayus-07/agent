"""窗口4 后端回归（C6-2 删除保护 / C6-3 语料快照 / C9-2 strict / C9-4 mtime 守护）。"""
from __future__ import annotations

import json

import pytest


# ── C6-2 / DATA-06：数据集版本删除保护 ───────────────────────


def test_retention_blocks_referenced_version(monkeypatch):
    from backend.evaluation.dataset import retention

    monkeypatch.setattr(retention, "count_run_references", lambda m, v: 3)
    with pytest.raises(ValueError, match="禁止物理删除"):
        retention.ensure_dataset_version_deletable("rag", "5.0.0-unified")


def test_retention_fails_closed_on_db_error(monkeypatch):
    from backend.evaluation.dataset import retention

    monkeypatch.setattr(retention, "count_run_references", lambda m, v: -1)
    with pytest.raises(ValueError, match="fail-closed"):
        retention.ensure_dataset_version_deletable("rag", "5.0.0-unified")


def test_retention_allows_unreferenced_version(monkeypatch):
    from backend.evaluation.dataset import retention

    monkeypatch.setattr(retention, "count_run_references", lambda m, v: 0)
    retention.ensure_dataset_version_deletable("rag", "9.9.9-orphan")  # 不抛即过


def test_retention_reference_query_shapes(monkeypatch):
    """两种 dataset_version JSONB 形态都能命中（旧 {module: v} + 新 provenance）。"""
    from backend.evaluation.dataset import retention

    captured = {}

    class _FakeCursor:
        def execute(self, sql, params):
            captured["params"] = params

        def fetchone(self):
            return (7,)

    class _FakeConn:
        def __init__(self):
            self.cursor_obj = _FakeCursor()

        def cursor(self):
            return self.cursor_obj

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _FakeEngine:
        def raw_connection(self):
            return _FakeConn()

    import backend.config.database as db_mod
    import backend.infra.db as infra_db

    monkeypatch.setattr(db_mod, "OBS_DB_PG_CONFIG", {}, raising=False)
    monkeypatch.setattr(infra_db, "engine_for", lambda cfg: _FakeEngine())

    refs = retention.count_run_references("rag", "5.0.0-unified")
    assert refs == 7
    module_key, version, provenance_version = captured["params"]
    assert (module_key, version, provenance_version) == (
        "rag", "5.0.0-unified", "5.0.0-unified",
    )


# ── C6-3 / REPRO-06：语料快照全 fixture 覆盖 ─────────────────


def test_all_fixtures_have_snapshot_files():
    from backend.evaluation.datasets.rag.snapshots import load_rag_snapshot

    for fixture in ("baseline", "expanded_100", "scale_20k"):
        snapshot = load_rag_snapshot(fixture)
        assert snapshot["version_id"], f"{fixture} 缺 version_id"
        assert snapshot["kb_id"] == "rag_eval_kb"


def test_nonbaseline_scope_records_version_without_filtering():
    """expanded_100/scale_20k：version_id 进 scope（哈希输入）但不过滤检索。"""
    from backend.evaluation.runners.rag import build_eval_scope

    scope = build_eval_scope(kb_id="rag_eval_kb", fixture_set="expanded_100", multiquery=False)
    assert scope.version_id == "rag_eval_kb-expanded_100-2026-10-04"
    assert scope.enforce_filter is False
    assert scope.filter_version_id is None  # 过滤不受影响
    assert scope.as_dict()["version_id"] == "rag_eval_kb-expanded_100-2026-10-04"


def test_baseline_scope_still_enforces_filter():
    from backend.evaluation.runners.rag import build_eval_scope

    scope = build_eval_scope(kb_id="rag_eval_kb", fixture_set="baseline", multiquery=False)
    assert scope.enforce_filter is True
    assert scope.filter_version_id == scope.version_id


# ── C9-2 / P0-03：严格字段校验 ───────────────────────────────


def _case(case_id, question, expected):
    from backend.evaluation.models import TestCase

    return TestCase(id=case_id, question=question, module="rag", expected=expected)


def test_strict_check_blocks_missing_fields():
    from backend.evaluation.service import StrictFieldValidationError, _strict_fields_check

    cases = [
        _case("ok-1", "问题", {"expected_answer": "答", "ground_truth": "真"}),
        _case("bad-1", "", {"expected_answer": "答", "ground_truth": "真"}),
        _case("bad-2", "问题", {"expected_answer": "", "ground_truth": "真"}),
    ]
    with pytest.raises(StrictFieldValidationError) as exc:
        _strict_fields_check(cases)
    message = str(exc.value)
    assert "bad-1" in message and "question" in message
    assert "bad-2" in message and "expected_answer" in message
    assert "ok-1" not in message


def test_strict_check_passes_complete_cases():
    from backend.evaluation.service import _strict_fields_check

    cases = [
        _case("ok-1", "问题", {"expected_answer": "答", "ground_truth": "真"}),
        _case("ok-2", "问题2", {"expected_answer": "答2", "ground_truth": "真2"}),
    ]
    _strict_fields_check(cases)  # 不抛即过


def test_strict_check_accepts_v4_structured_ground_truth_context():
    """2026-10-28 回归：V4 canonical 的 ground_truth_context（结构化片段）
    语义上就是 ground truth，strict 门禁必须识别，否则发布管线对所有
    canonical 样本死锁（实测 9 个既有 suite 全部被拦）。"""
    from backend.evaluation.service import _strict_fields_check

    cases = [
        _case("rc-v4", "现货商品的发货时间是多久？", {
            "expected_answer": "现货商品24小时内发出",
            "ground_truth_context": [
                {"text": "现货商品 24 小时内发出", "source_doc": "faq_常见问题FAQ.md"},
            ],
        }),
    ]
    _strict_fields_check(cases)  # 不抛即过


def test_strict_check_still_blocks_empty_context_and_answer():
    from backend.evaluation.service import StrictFieldValidationError, _strict_fields_check

    cases = [
        # context 为空列表 / 全空文本：不算有依据，仍然拦截
        _case("empty-ctx", "问题", {
            "expected_answer": "答", "ground_truth_context": [],
        }),
        _case("blank-ctx", "问题", {
            "expected_answer": "答",
            "ground_truth_context": [{"text": "  ", "source_doc": "x.md"}],
        }),
        _case("no-answer", "问题", {
            "ground_truth_context": [{"text": "依据", "source_doc": "x.md"}],
        }),
    ]
    with pytest.raises(StrictFieldValidationError) as exc:
        _strict_fields_check(cases)
    message = str(exc.value)
    assert "empty-ctx" in message and "ground_truth" in message
    assert "blank-ctx" in message and "ground_truth" in message
    assert "no-answer" in message and "expected_answer" in message


def test_strict_check_accepts_rejection_case_without_answer_fields():
    """拒答类样本（should_reject=true）：无答案字段是标注本身（RC-061 形态）。"""
    from backend.evaluation.service import _strict_fields_check

    cases = [
        _case("rc-061", "头疼应该吃什么药？", {
            "ground_truth_context": [],
            "should_reject": True,
            "required_facts": [],
            "expected_answer": "",
        }),
    ]
    _strict_fields_check(cases)  # 不抛即过


def test_strict_check_reject_case_still_needs_question():
    from backend.evaluation.service import StrictFieldValidationError, _strict_fields_check

    cases = [_case("bad-reject", "", {"should_reject": True})]
    with pytest.raises(StrictFieldValidationError) as exc:
        _strict_fields_check(cases)
    assert "bad-reject" in str(exc.value) and "question" in str(exc.value)


# ── C9-4 / CON-05：运行期 suite mtime 守护 ───────────────────


def test_suite_mtime_change_detected(tmp_path, monkeypatch):
    from backend.evaluation import service
    from backend.evaluation.dataset import loader

    suite_dir = tmp_path / "rag" / "suites"
    suite_dir.mkdir(parents=True)
    suite_file = suite_dir / "pr_smoke.json"
    suite_file.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(loader, "DATASET_DIR", tmp_path)
    baseline = service._snapshot_suite_mtimes(
        type("C", (), {"selection": "pr_smoke"})(),
    )
    assert suite_file.name in baseline

    # 模拟运行期修改
    import os as _os
    import time as _time

    old = _time.time() - 10
    _os.utime(suite_file, (old, old))
    baseline[suite_file.name] = old
    _os.utime(suite_file)  # 触到当前时间
    note = service._check_suite_mtime(baseline)
    assert "pr_smoke.json" in note and "内存态" in note


def test_suite_mtime_untouched_no_warning(tmp_path, monkeypatch):
    from backend.evaluation import service
    from backend.evaluation.dataset import loader

    suite_dir = tmp_path / "rag" / "suites"
    suite_dir.mkdir(parents=True)
    (suite_dir / "pr_smoke.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(loader, "DATASET_DIR", tmp_path)
    baseline = service._snapshot_suite_mtimes(
        type("C", (), {"selection": "pr_smoke"})(),
    )
    assert service._check_suite_mtime(baseline) == ""
