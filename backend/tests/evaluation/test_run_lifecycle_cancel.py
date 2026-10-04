"""运行生命周期扩展回归测试（2026-10-04 验收收敛二期）。

覆盖验收项：RUN-03/04（协作式取消+幂等）、RUN-02（终态拒绝隐式重跑）、
RUN-05（attempt 计数）、RUN-06（RAGAS 断点补跑采集）、RUN-08（心跳 stale）、
REL-09（取消审计落库）。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from backend.evaluation import storage as storage_mod
from backend.evaluation.config import EvalConfig
from backend.evaluation.models import EvalResult
from backend.evaluation.runners.rag import _collect_ragas_backfill
from backend.evaluation.service import (
    EvaluationService,
    RunAlreadyFinalizedError,
)


@pytest.fixture
def eval_root(tmp_path, monkeypatch):
    root = tmp_path / "eval_runs"
    root.mkdir()
    monkeypatch.setattr(storage_mod, "DATA_ROOT", root)
    return root


# ── RUN-03/04：协作式取消 + 幂等 ─────────────────────────────


def test_request_cancel_writes_and_is_idempotent(eval_root):
    first = storage_mod.request_cancel("run-x", requested_by="admin-a")
    assert first["requested"] is True
    assert first["requested_by"] == "admin-a"
    assert storage_mod.is_cancel_requested("run-x") is True

    # RUN-04：重复取消不报错、不重复副作用（请求内容不变）
    second = storage_mod.request_cancel("run-x", requested_by="admin-b")
    assert second == first
    assert storage_mod.is_cancel_requested("run-x") is True


def test_cancel_not_requested_by_default(eval_root):
    storage_mod.mark_run_status("run-y", "running")
    assert storage_mod.is_cancel_requested("run-y") is False


def test_persist_marks_cancelled_when_requested(eval_root, monkeypatch):
    """取消命中 → persist_report 终态 cancelled 而非 completed（RUN-03）。"""
    from backend.evaluation.models import EvalReport, ModuleSummary

    storage_mod.mark_run_status("run-z", "running")
    storage_mod.request_cancel("run-z")
    summary = ModuleSummary(
        module="rag", total=1, passed=1, failed=0, errors=0, skipped=0,
        pass_rate=1.0, metrics={},
    )
    report = EvalReport(
        module="rag", mode="offline", summaries=[summary], results=[],
        metadata={"run_id": "run-z"},
    )
    storage_mod.persist_report(report, "run-z")
    assert storage_mod.read_run_status("run-z")["status"] == "cancelled"


def test_persist_keeps_failed_over_completed(eval_root, monkeypatch):
    """运行期守卫已标 failed（deadline/token 熔断）→ persist 不降级 completed。"""
    from backend.evaluation.models import EvalReport, ModuleSummary

    storage_mod.mark_run_status("run-f", "running")
    storage_mod.mark_run_status("run-f", "failed", error="token_circuit_break")
    summary = ModuleSummary(
        module="rag", total=1, passed=0, failed=0, errors=0, skipped=0,
        pass_rate=0.0, metrics={},
    )
    report = EvalReport(
        module="rag", mode="offline", summaries=[summary], results=[],
        metadata={"run_id": "run-f"},
    )
    storage_mod.persist_report(report, "run-f")
    final = storage_mod.read_run_status("run-f")
    assert final["status"] == "failed"
    assert final["error"] == "token_circuit_break"


# ── RUN-02：终态拒绝隐式重跑 ─────────────────────────────────


def test_finalize_rejects_rerun_on_completed_run(eval_root, monkeypatch):
    monkeypatch.setattr(
        EvaluationService, "_evaluate_cases",
        lambda self, config, run_id, ts: (_ for _ in ()).throw(AssertionError("不应执行")),
    )
    storage_mod.mark_run_status("done-run", "running")
    storage_mod.mark_run_status("done-run", "completed")

    service = EvaluationService()
    with pytest.raises(RunAlreadyFinalizedError):
        service.evaluate(EvalConfig(module="rag", run_id="done-run"))


def test_finalize_allows_explicit_resume_on_completed_run(eval_root, monkeypatch):
    """显式 resume（断点续跑语义）放行；不带 resume 才拒绝（C2-2）。"""
    captured = {}

    def _fake_cases(self, config, run_id, ts):
        captured["resume"] = config.resume
        from backend.evaluation.models import EvalReport, ModuleSummary

        summary = ModuleSummary(
            module="rag", total=0, passed=0, failed=0, errors=0, skipped=0,
            pass_rate=0.0, metrics={},
        )
        return EvalReport(module="rag", mode="offline", summaries=[summary],
                          results=[], metadata={})

    monkeypatch.setattr(EvaluationService, "_evaluate_cases", _fake_cases)
    storage_mod.mark_run_status("done-run2", "running")
    storage_mod.mark_run_status("done-run2", "completed")

    service = EvaluationService()
    config = EvalConfig(module="rag", run_id="done-run2")
    config = config.model_copy(update={"resume": True})
    # model_fields_set 需要显式构造才含 resume
    config = EvalConfig(module="rag", run_id="done-run2", resume=True)
    service.evaluate(config)
    assert captured["resume"] is True


def test_finalize_force_rerun_allowed_and_audited(eval_root, monkeypatch):
    audit_rows: list[tuple] = []
    import backend.evaluation.audit as audit_mod

    monkeypatch.setattr(
        audit_mod, "record_operation",
        lambda op, tid, **kw: audit_rows.append((op, tid)) or True,
    )
    monkeypatch.setattr(
        EvaluationService, "_evaluate_cases",
        lambda self, config, run_id, ts: (_ for _ in ()).throw(AssertionError("不应执行")),
    )
    storage_mod.mark_run_status("done-run3", "running")
    storage_mod.mark_run_status("done-run3", "completed")

    service = EvaluationService()
    with pytest.raises(AssertionError):
        service.evaluate(EvalConfig(module="rag", run_id="done-run3", force_rerun=True))
    assert audit_rows and audit_rows[0][0] == "eval_run.force_rerun"


# ── RUN-05：attempt 计数 ─────────────────────────────────────


def test_attempt_no_increments_across_reruns(eval_root):
    storage_mod.mark_run_status("run-att", "running")
    first = storage_mod.read_run_status("run-att")
    assert first["attempt_no"] == 1
    storage_mod.mark_run_status("run-att", "failed", error="boom")
    # 重试（同 run_id 再次 running）→ attempt +1，终态保留累计值
    storage_mod.mark_run_status("run-att", "running")
    assert storage_mod.read_run_status("run-att")["attempt_no"] == 2
    storage_mod.mark_run_status("run-att", "completed")
    final = storage_mod.read_run_status("run-att")
    assert final["attempt_no"] == 2
    assert final["status"] == "completed"


def test_attempt_no_exposed_in_report_metadata(eval_root, monkeypatch):
    def _fake_cases(self, config, run_id, ts):
        from backend.evaluation.models import EvalReport, ModuleSummary

        summary = ModuleSummary(
            module="rag", total=0, passed=0, failed=0, errors=0, skipped=0,
            pass_rate=0.0, metrics={},
        )
        return EvalReport(module="rag", mode="offline", summaries=[summary],
                          results=[], metadata={})

    monkeypatch.setattr(EvaluationService, "_evaluate_cases", _fake_cases)
    storage_mod.mark_run_status("run-att2", "running")
    service = EvaluationService()
    report = service.evaluate(EvalConfig(module="rag", run_id="run-att2"))
    # 首跑 attempt=1，service 续跑自增 → 2（「二次续跑后 attempt_no=2」）
    assert report.metadata["attempt_no"] == 2


# ── RUN-08：心跳 stale 判定 ──────────────────────────────────


def test_heartbeat_fresh_run_not_stale(eval_root):
    storage_mod.mark_run_status("run-hb", "running")
    storage_mod.touch_run_heartbeat("run-hb")
    data = storage_mod.read_run_status("run-hb")
    assert data["stale"] is False
    assert data["stale_basis"] == "heartbeat"


def test_heartbeat_missing_beyond_threshold_marks_stale(eval_root, monkeypatch):
    monkeypatch.setattr(storage_mod, "HEARTBEAT_STALE_SECONDS", 600)
    storage_mod.mark_run_status("run-hb2", "running")
    storage_mod.touch_run_heartbeat("run-hb2")
    path = storage_mod.DATA_ROOT / "run-hb2" / "status.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["heartbeat_at"] = (
        datetime.now() - timedelta(seconds=700)
    ).isoformat()
    path.write_text(json.dumps(data), encoding="utf-8")
    stale = storage_mod.read_run_status("run-hb2")
    assert stale["stale"] is True
    assert stale["stale_basis"] == "heartbeat"


def test_no_heartbeat_falls_back_to_started_at(eval_root, monkeypatch):
    """存量 run 无 heartbeat_at → 原有 6h 口径兜底，不误判 stale。"""
    monkeypatch.setattr(storage_mod, "STALE_RUN_AFTER_SECONDS", 21600)
    storage_mod.mark_run_status("run-hb3", "running")
    fresh = storage_mod.read_run_status("run-hb3")
    assert fresh["stale"] is False
    assert fresh["stale_basis"] == "started_at_fallback"


def test_heartbeat_never_resurrects_terminal_state(eval_root):
    storage_mod.mark_run_status("run-hb4", "running")
    storage_mod.mark_run_status("run-hb4", "completed")
    assert storage_mod.touch_run_heartbeat("run-hb4") is False
    assert storage_mod.read_run_status("run-hb4")["status"] == "completed"


# ── RUN-06 / C2-4：RAGAS 断点补跑采集 ────────────────────────


def test_collect_ragas_backfill_targets_missing_only(tmp_path):
    done = {
        "has-ragas": EvalResult(
            case_id="has-ragas", module="rag", status="pass", expected={},
            actual={}, metrics={"ragas_faithfulness": 0.9},
        ),
        "no-ragas": EvalResult(
            case_id="no-ragas", module="rag", status="pass", expected={},
            actual={}, metrics={},
        ),
        "no-answer": EvalResult(
            case_id="no-answer", module="rag", status="pass", expected={},
            actual={}, metrics={},
        ),
    }
    answers = tmp_path / "answers.jsonl"
    rows = [
        {"case_id": "has-ragas", "question": "q1", "answer": "a1", "contexts": ["c1"]},
        {"case_id": "no-ragas", "question": "q2", "answer": "a2", "contexts": ["c2"]},
        {"case_id": "no-answer", "question": "q3", "answer": "", "contexts": []},
    ]
    answers.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8",
    )
    backfill = _collect_ragas_backfill(tmp_path, done)
    # 只有「缺分值且 answers 里有完整输入」的样本进补跑清单
    assert set(backfill) == {"no-ragas"}
    assert backfill["no-ragas"]["question"] == "q2"
