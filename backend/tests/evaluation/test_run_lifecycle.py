"""评测 run 生命周期状态文件 + 分桶/评估器模式标记的回归测试。

覆盖验收项：RUN-01（running→completed/failed 状态文件）、RUN-07/08（stale
判定地基）、SELF-06（domain/difficulty/query_type 分桶）、RAGAS-01/02
（evaluator_mode 标记）、RAGAS-10（valid/invalid 样本口径）。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from backend.evaluation import storage as storage_mod
from backend.evaluation.config import EvalConfig
from backend.evaluation.models import EvalResult, TestCase
from backend.evaluation.service import (
    EvaluationService,
    _build_buckets,
    _evaluator_mode,
    _ragas_sample_stats,
)


@pytest.fixture
def eval_root(tmp_path, monkeypatch):
    """把 DATA_ROOT 指到临时目录，避免污染真实 data/eval_runs。"""
    root = tmp_path / "eval_runs"
    root.mkdir()
    monkeypatch.setattr(storage_mod, "DATA_ROOT", root)
    return root


def _case(case_id: str, metadata: dict) -> TestCase:
    return TestCase(
        id=case_id, question=f"q-{case_id}", module="rag",
        expected={}, metadata=metadata,
    )


def _result(case_id: str, status: str, metrics: dict | None = None) -> EvalResult:
    return EvalResult(
        case_id=case_id, module="rag", status=status,
        expected={}, actual={}, metrics=metrics or {},
    )


# ── 状态文件（RUN-01/07/08）──────────────────────────────────


def test_status_lifecycle_running_to_completed(eval_root):
    storage_mod.mark_run_status("run-a", "running")
    assert storage_mod.read_run_status("run-a")["status"] == "running"
    assert storage_mod.read_run_status("run-a")["stale"] is False

    storage_mod.mark_run_status("run-a", "completed")
    final = storage_mod.read_run_status("run-a")
    assert final["status"] == "completed"
    # 终态保留 started_at，补 finished_at，且不再参与 stale 判定
    assert final["started_at"]
    assert final["finished_at"]
    assert "stale" not in final


def test_status_failed_carries_error(eval_root):
    storage_mod.mark_run_status("run-b", "running")
    storage_mod.mark_run_status("run-b", "failed", error="boom")
    data = storage_mod.read_run_status("run-b")
    assert data["status"] == "failed"
    assert data["error"] == "boom"


def test_stale_running_detected_by_age(eval_root, monkeypatch):
    storage_mod.mark_run_status("run-c", "running")
    # 手工把 started_at 拨老（模拟 worker 被杀后再无人收口）
    path = storage_mod.DATA_ROOT / "run-c" / "status.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["started_at"] = (
        datetime.now() - timedelta(hours=7)
    ).isoformat()
    path.write_text(json.dumps(data), encoding="utf-8")

    monkeypatch.setattr(storage_mod, "STALE_RUN_AFTER_SECONDS", 21600)
    stale = storage_mod.read_run_status("run-c")
    assert stale["status"] == "running"
    assert stale["stale"] is True
    assert stale["age_seconds"] > 21600


def test_read_status_missing_file_returns_none(eval_root):
    assert storage_mod.read_run_status("never-started") is None


def test_illegal_status_rejected(eval_root):
    with pytest.raises(ValueError):
        storage_mod.mark_run_status("run-d", "cancelled-unknown")


# ── _evaluate 异常路径把 running 收口为 failed（RUN-01）────────


def test_evaluate_marks_failed_on_runner_crash(eval_root, monkeypatch):
    service = EvaluationService()
    monkeypatch.setattr(
        EvaluationService, "_evaluate_cases",
        lambda self, config, run_id, ts: (_ for _ in ()).throw(RuntimeError("kb 炸了")),
    )
    with pytest.raises(RuntimeError):
        service.evaluate(EvalConfig(module="rag", live=False))

    # run_id 是运行时生成的，扫描目录找唯一 run
    runs = list(storage_mod.DATA_ROOT.iterdir())
    assert len(runs) == 1
    data = storage_mod.read_run_status(runs[0].name)
    assert data["status"] == "failed"
    assert "kb 炸了" in data["error"]


# ── evaluator_mode（RAGAS-01/02）─────────────────────────────


def test_evaluator_mode_flags():
    assert _evaluator_mode(EvalConfig(module="rag")) == "self+ragas"
    assert _evaluator_mode(EvalConfig(module="rag", ragas=True)) == "self+ragas"
    assert _evaluator_mode(EvalConfig(module="rag", no_ragas=True)) == "self"


# ── 分桶统计（SELF-06）───────────────────────────────────────


def test_build_buckets_group_and_rate():
    cases = [
        _case("1", {"domain": "order", "difficulty": "easy", "query_type": "fact"}),
        _case("2", {"domain": "order", "difficulty": "hard", "query_type": "fact"}),
        _case("3", {"domain": "policy", "difficulty": "easy", "query_type": "reject"}),
        _case("4", {}),  # 元数据缺失 → unknown 桶
    ]
    results = [
        _result("1", "pass"),
        _result("2", "fail"),
        _result("3", "pass"),
        _result("4", "error"),
    ]
    buckets = _build_buckets(cases, results)

    assert buckets["by_domain"]["order"] == {
        "total": 2, "passed": 1, "failed": 1, "errors": 0, "skipped": 0, "pass_rate": 0.5,
    }
    assert buckets["by_domain"]["policy"]["pass_rate"] == 1.0
    assert buckets["by_domain"]["unknown"]["errors"] == 1
    assert buckets["by_difficulty"]["easy"]["total"] == 2
    assert buckets["by_difficulty"]["hard"]["failed"] == 1
    assert buckets["by_query_type"]["fact"]["total"] == 2
    assert buckets["by_query_type"]["reject"]["pass_rate"] == 1.0


def test_build_buckets_empty_inputs_no_fake_data():
    buckets = _build_buckets([], [])
    assert buckets == {"by_domain": {}, "by_difficulty": {}, "by_query_type": {}}


# ── RAGAS 样本口径（RAGAS-10）───────────────────────────────


def test_ragas_sample_stats_valid_vs_invalid():
    results = [
        _result("1", "pass", {"ragas_faithfulness": 0.9}),
        _result("2", "pass", {"ragas_faithfulness": None, "ragas_reason": 0.0}),
        _result("3", "fail", {}),  # 未进 RAGAS 批量
    ]
    stats = _ragas_sample_stats(results)
    assert stats == {"valid": 1, "invalid": 1}
