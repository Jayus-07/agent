"""在线流量采样评测器回归测试（eval-closure B2/B3）。

验收锚点：
- 开关关闭 / 无可评样本 → skipped 正常终态（beat 不告警不重试）
- 采样口径：优先级排序（负反馈 > 失败 > 降级 > 拒答 > 普通）+ 比例驱动 + 硬封顶
- judge 失败（total=0.0）不吞：status='error' 计数显式暴露、不进均值
- 落库字段：trigger='online' / mode='online' / run_id=online-YYYYMMDD、样本 judge 口径
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from backend.evaluation import online_sampler as sut


def _trace(tid: str, *, status: str = "success", question: str = "q",
           duration_ms: int = 1000, cost_cny: float = 0.1,
           workflow: str = "agent", degraded_meta: bool = False) -> dict:
    return {
        "id": tid, "status": status, "question": question,
        "answer_preview": f"answer-{tid}", "answer_len": len(f"answer-{tid}"),
        "duration_ms": duration_ms,
        "cost_cny": cost_cny, "workflow_name": workflow,
        "metadata": {"degraded": degraded_meta} if degraded_meta else {},
        "error": {} if status != "error" else {"message": "boom"},
    }


@pytest.fixture(autouse=True)
def _sampler_enabled(monkeypatch):
    """默认打开采样开关（单测不测 disabled 之外路径时不受 env 影响）。"""
    monkeypatch.setenv("EVAL_ONLINE_SAMPLER_ENABLED", "true")


@contextmanager
def _patch_trace_store(traces: list[dict]):
    """patch 采样源并阻断测试对共享 feedback 库的读取。"""
    store = MagicMock()
    store.list_since.return_value = traces
    with patch(
        "backend.observability.trace_store.get_trace_store",
        return_value=store,
    ) as trace_store_mock:
        with patch.object(sut, "_negative_trace_ids", return_value=set()):
            yield trace_store_mock


def _patch_run_date(monkeypatch, value: str) -> None:
    """为预算测试使用独立请求 ID，避免复用进程内同日预算状态。"""
    original_strftime = sut.time.strftime

    def _isolated_strftime(fmt: str, *args):
        if fmt == "%Y%m%d":
            return value
        return original_strftime(fmt, *args)

    monkeypatch.setattr(
        sut.time,
        "strftime",
        _isolated_strftime,
    )


def _patch_judge(total: float = 4.0, confidence: str = "high"):
    """patch judge_answer（LLM 外部依赖；失败契约 = total<=0.0）。"""
    judge_result = MagicMock(scores={"completeness": 4, "faithfulness": 4,
                                     "conciseness": 4, "citation_quality": 4},
                             total=total, reasoning="ok", confidence=confidence)
    return patch("backend.evaluation.judge.judge_answer", return_value=judge_result)


# ── 开关与空数据终态 ─────────────────────────────────────────────────────────


def test_disabled_skips(monkeypatch):
    """默认关：未启用时返回 skipped=disabled，不触 trace 源也不落库。"""
    monkeypatch.setenv("EVAL_ONLINE_SAMPLER_ENABLED", "false")
    with _patch_trace_store([]) as store_patch:
        result = sut.run_online_sampling()
    assert result == {"skipped": "disabled", "run_id": ""}
    store_patch.assert_not_called()


def test_no_eligible_traces_skips():
    """窗口内无可评 trace → skipped，不建空 run（避免每日垃圾台账行）。"""
    with _patch_trace_store([]):
        result = sut.run_online_sampling()
    assert result["skipped"] == "no_eligible_traces"


def test_unjudgeable_trace_filtered():
    """无 question 的 trace（纯路由失败等）不进采样池。"""
    with _patch_trace_store([_trace("t1", question="")]), _patch_judge():
        result = sut.run_online_sampling()
    assert result["skipped"] == "no_eligible_traces"


def test_trace_source_unavailable_skips():
    """trace 源不可达 → skipped 终态而非异常（不炸 beat）。"""
    with patch("backend.observability.trace_store.get_trace_store",
               side_effect=RuntimeError("pg down")):
        result = sut.run_online_sampling()
    assert result["skipped"] == "trace_store_unavailable"


def test_negative_feedback_query_closes_pooled_connection():
    """负反馈查询显式归还 raw_connection，不依赖代理对象上下文管理器。"""
    conn = MagicMock(spec=["cursor", "commit", "rollback", "close"])
    conn.cursor.return_value.fetchall.return_value = [("trace-negative",)]
    engine = MagicMock()
    engine.raw_connection.return_value = conn

    with patch("backend.infra.db.engine_for", return_value=engine):
        trace_ids = sut._negative_trace_ids("2026-11-04 00:00:00")

    assert trace_ids == {"trace-negative"}
    conn.commit.assert_called_once()
    conn.close.assert_called_once()
    conn.rollback.assert_not_called()


def test_pooled_connection_rolls_back_and_closes_after_error():
    """连接块异常时回滚并关闭池化连接。"""
    conn = MagicMock(spec=["cursor", "commit", "rollback", "close"])
    engine = MagicMock()
    engine.raw_connection.return_value = conn

    with patch("backend.infra.db.engine_for", return_value=engine):
        with pytest.raises(RuntimeError, match="query failed"):
            with sut._pooled_connection({"dbname": "agent_memory"}):
                raise RuntimeError("query failed")

    conn.rollback.assert_called_once()
    conn.close.assert_called_once()
    conn.commit.assert_not_called()


# ── 采样口径 ─────────────────────────────────────────────────────────────────


def test_priority_ordering_negative_first():
    """负反馈 > 失败 > 降级 > 拒答 > 普通优先级排序，封顶内优先采样。"""
    traces = [
        _trace("normal", workflow="agent"),
        _trace("rejected", status="rejected"),
        _trace("degraded", status="degraded"),
        _trace("failed", status="error"),
        _trace("neg", workflow="agent"),
    ]
    with _patch_trace_store(traces), _patch_judge(), \
            patch.object(sut, "_negative_trace_ids", return_value={"neg"}), \
            patch.dict("os.environ", {"EVAL_ONLINE_SAMPLE_RATE": "1.0",
                                      "EVAL_ONLINE_SAMPLE_MAX": "3"}):
        result = sut.run_online_sampling()
    assert result["sampled_count"] == 3
    assert result["by_priority"] == {"negative_feedback": 1, "failed": 1,
                                     "degraded": 1}
    # 封顶截断：普通/拒答样本被挤出
    assert "normal" not in result["by_priority"]


def test_business_failed_status_gets_failure_priority():
    """无顶层异常的业务 failed 终态也优先采样。"""
    assert sut._priority(_trace("business-failed", status="failed"), set()) == 1


def test_rate_proportional_with_min_one():
    """比例驱动：可评 10 条 × rate=0.5 → 采 5 条；不足时至少 1 条。"""
    traces = [_trace(f"t{i}", degraded_meta=(i % 2 == 0)) for i in range(10)]
    with _patch_trace_store(traces), _patch_judge(), \
            patch.dict("os.environ", {"EVAL_ONLINE_SAMPLE_RATE": "0.5"}):
        result = sut.run_online_sampling()
    assert result["sampled_count"] == 5


def test_degraded_meta_counts_for_priority():
    """status=success 但 metadata.degraded=true 的 trace 归降级优先级。"""
    traces = [_trace("a"), _trace("b", degraded_meta=True)]
    with _patch_trace_store(traces), _patch_judge(), \
            patch.dict("os.environ", {"EVAL_ONLINE_SAMPLE_RATE": "1.0",
                                      "EVAL_ONLINE_SAMPLE_MAX": "1"}):
        result = sut.run_online_sampling()
    assert result["by_priority"] == {"degraded": 1}
    assert "normal" not in result["by_priority"]


# ── judge 契约 ───────────────────────────────────────────────────────────────


def test_judge_failure_not_swallowed():
    """judge 失败（total=0.0）→ 样本 error、failed_count 显式暴露、不进均值。"""
    with _patch_trace_store([_trace("ok"), _trace("bad")]), \
            patch("backend.evaluation.judge.judge_answer",
                  side_effect=[MagicMock(scores={}, total=4.0, reasoning="ok",
                                         confidence="high"),
                               MagicMock(scores={}, total=0.0,
                                         reasoning="judge failed",
                                         confidence="low")]), \
            patch.dict("os.environ", {"EVAL_ONLINE_SAMPLE_RATE": "1.0"}):
        result = sut.run_online_sampling()
    assert result["judge_failed_count"] == 1
    # 均值只含有效分：单条 4.0 → avg 4.0（0.0 混入会拉成 2.0）
    assert result["judge_avg_total"] == 4.0
    assert result["judge_pass_rate"] == 1.0


def test_judge_below_passline_is_fail_sample():
    """judge 低于及格线（3.0）→ 样本 status=fail，pass_rate 归零。"""
    with _patch_trace_store([_trace("low")]), _patch_judge(total=2.0), \
            patch.dict("os.environ", {"EVAL_ONLINE_SAMPLE_RATE": "1.0"}):
        result = sut.run_online_sampling()
    assert result["judge_pass_rate"] == 0.0
    assert result["judge_avg_total"] == 2.0


def test_truncated_answer_is_skipped_without_judging_preview():
    """只有 answer_preview 且全文更长时记录 skip，不用片段伪装完整回答。"""
    trace = _trace("long-answer")
    trace["answer_preview"] = "x" * 200
    trace["answer_len"] = 800
    with _patch_trace_store([trace]), _patch_judge() as judge_mock, \
            patch.object(sut, "_negative_trace_ids", return_value=set()), \
            patch.object(sut, "_persist", return_value=True) as persist_mock, \
            patch.dict("os.environ", {"EVAL_ONLINE_SAMPLE_RATE": "1.0"}):
        result = sut.run_online_sampling()

    judge_mock.assert_not_called()
    assert result["judge_skipped_count"] == 1
    assert result["judge_failed_count"] == 0
    assert result["judge_avg_total"] is None
    assert result["judge_pass_rate"] is None
    rows = persist_mock.call_args.args[1]
    assert rows[0]["status"] == "skip"
    assert rows[0]["answer_skipped"] is True


def test_online_judge_binds_evaluation_budget_and_settles_usage(monkeypatch):
    """在线 judge 复用评测身份，真实走预算预占与结算接口。"""
    from decimal import Decimal

    from backend.core.request_context import get_tool_tenant_id, get_tool_user_id
    from backend.infra.llm import budget as budget_module
    from backend.infra.llm.proxy import _thread_local_user_id

    class _PriceTable:
        def require(self, model_name, component, *, enforce):
            assert model_name == "eval-test-model"
            assert component == "eval_gen"
            assert enforce is True

    class _QuotaStore:
        def __init__(self):
            self.reservations = []
            self.settlements = []

        def reserve(self, **kwargs):
            reservation = object()
            self.reservations.append((kwargs, reservation))
            return reservation

        def settle(self, reservation, actual_cny):
            self.settlements.append((reservation, actual_cny))

    quota_store = _QuotaStore()
    captured = {}
    monkeypatch.setenv("EVAL_TRIGGERED_BY", "user:eval-budget-actor")
    _patch_run_date(monkeypatch, "20990101")

    def _score(samples):
        state = budget_module.current_request_budget()
        assert state is not None
        captured.setdefault("request_ids", []).append(
            budget_module.current_request_id()
        )
        captured["user_id"] = get_tool_user_id()
        captured["tenant_id"] = get_tool_tenant_id()
        captured["rate_limit_user"] = _thread_local_user_id()

        budget_module.reserve_model_call(
            "primary", model_name="eval-test-model", component="eval_gen",
        )
        captured["settlement"] = budget_module.record_model_usage(
            prompt_tokens=12,
            completion_tokens=8,
            total_tokens=20,
            cost=Decimal("0.0125"),
        )
        return [{
            "case_id": "trace-budgeted",
            "priority": "normal",
            "status": "pass",
            "judge_total": 4.0,
            "judge_failed": False,
            "answer_skipped": False,
            "confidence": "high",
            "reasoning": "ok",
            "scores": {},
            "workflow_name": "agent",
            "trace_duration_ms": 1000,
            "trace_cost_cny": 0.1,
            "trace_degraded": False,
        }]

    with _patch_trace_store([_trace("trace-budgeted")]), \
            patch.object(sut, "_sample_traces", return_value=[
                (4, _trace("trace-budgeted")),
            ]), \
            patch.object(sut, "_judge_all", side_effect=_score), \
            patch.object(sut, "_persist", return_value=True), \
            patch.object(budget_module, "_config_mode", return_value="enforce"), \
            patch.object(budget_module, "_config_limits", return_value=(
                budget_module.RequestBudgetLimits(max_cost=Decimal("0.50"))
            )), \
            patch("backend.infra.llm.quota.PostgresQuotaStore",
                  return_value=quota_store), \
            patch("backend.infra.llm.models.lookup_model_entry", return_value=None), \
            patch("backend.infra.llm.pricing.get_current_price_table",
                  return_value=_PriceTable()):
        result = sut.run_online_sampling()
        rerun_result = sut.run_online_sampling()

    assert result["persisted"] is True
    assert rerun_result["persisted"] is True
    assert rerun_result["run_id"] == result["run_id"]
    assert len(captured["request_ids"]) == 2
    assert captured["request_ids"][0] != captured["request_ids"][1]
    assert all(
        request_id.startswith(f"{result['run_id']}:")
        for request_id in captured["request_ids"]
    )
    assert captured["user_id"] == "eval-budget-actor"
    assert captured["tenant_id"] == "eval"
    assert captured["rate_limit_user"] == "eval-budget-actor"
    assert captured["settlement"] == "settled"
    assert len(quota_store.reservations) == 2
    assert [args["request_id"] for args, _ in quota_store.reservations] == (
        captured["request_ids"]
    )
    assert all(args["user_id"] == "eval-budget-actor"
               for args, _ in quota_store.reservations)
    assert all(args["tenant_id"] == "eval"
               for args, _ in quota_store.reservations)
    assert quota_store.settlements == [
        (reservation, Decimal("0.0125"))
        for _, reservation in quota_store.reservations
    ]


def test_enforced_budget_binding_failure_blocks_online_judge(monkeypatch):
    """enforce 下预算存储不可用时，在线 judge 不得绕过预算出站。"""
    from backend.evaluation.service import BudgetBlockedError
    from backend.infra.llm import budget as budget_module

    monkeypatch.setattr("backend.config.llm.LLM_BUDGET_MODE", "enforce")
    _patch_run_date(monkeypatch, "20990102")
    with patch.object(sut, "_sample_traces", return_value=[
            (4, _trace("trace-budget-blocked")),
    ]), \
            patch.object(sut, "_judge_all") as judge_mock, \
            patch.object(budget_module, "_config_mode", return_value="enforce"), \
            patch("backend.infra.llm.quota.PostgresQuotaStore",
                  side_effect=RuntimeError("quota store unavailable")):
        with pytest.raises(BudgetBlockedError, match="拒绝调用模型"):
            sut.run_online_sampling()

    judge_mock.assert_not_called()


# ── 聚合与落库 ───────────────────────────────────────────────────────────────


def test_aggregate_perf_metrics():
    """run 聚合含表现三项（与 release_gate DEFAULT_THRESHOLDS 同键名）。"""
    traces = [_trace(f"t{i}", duration_ms=1000 * (i + 1),
                     cost_cny=0.2, degraded_meta=(i == 0))
              for i in range(4)]
    with _patch_trace_store(traces), _patch_judge(), \
            patch.dict("os.environ", {"EVAL_ONLINE_SAMPLE_RATE": "1.0"}):
        result = sut.run_online_sampling()
    assert result["p95_latency_ms"] == 4000.0  # <20 样本取最大
    assert result["cost_per_case_cny"] == pytest.approx(0.2)
    assert result["degradation_rate"] == 0.25


def test_persist_record_and_sample_fields():
    """落库：run 行 trigger/mode/module 口径 + 样本行 evaluator/status。"""
    with _patch_trace_store([_trace("t1"), _trace("t2", status="error")]), \
            _patch_judge(total=4.5), \
            patch.dict("os.environ", {"EVAL_ONLINE_SAMPLE_RATE": "1.0"}), \
            patch.object(sut, "_negative_trace_ids", return_value=set()), \
            patch("backend.evaluation.run_records.collect_prompt_snapshot",
                  return_value={"rag.qa": "23"}), \
            patch("backend.evaluation.run_records.collect_model_binding_fingerprint",
                  return_value="fp0"), \
            patch("backend.infra.db.engine_for") as engine_mock:
        conn = MagicMock(spec=["cursor", "commit", "rollback", "close"])
        engine_mock.return_value.raw_connection.return_value = conn

        result = sut.run_online_sampling()

    assert result["persisted"] is True
    assert result["run_id"].startswith("online-")
    assert len(result["run_id"]) == len("online-YYYYMMDD")
    run_sql = conn.cursor().execute.call_args_list[0][0][0]
    assert "ai.eval_run_records" in run_sql
    run_params = conn.cursor().execute.call_args_list[0][0][1]
    # trigger='online'（管理端 online 来源过滤口径）、module='all'、mode='online'
    assert run_params[8] == "online"
    assert run_params[0] == result["run_id"]
    assert run_params[1] == "all"
    assert run_params[2] == "online"
    sample_delete = conn.cursor().execute.call_args_list[1]
    assert "DELETE FROM ai.eval_run_samples" in sample_delete[0][0]
    assert sample_delete[0][1] == (result["run_id"], "judge")
    # 样本批量：executemany 写 ai.eval_run_samples
    assert "ai.eval_run_samples" in conn.cursor().executemany.call_args[0][0]
    conn.commit.assert_called_once()
    conn.close.assert_called_once()
    conn.rollback.assert_not_called()


def test_persist_failure_soft():
    """DB 写入失败 → persisted=False 软失败返回，不抛异常。"""
    with _patch_trace_store([_trace("t1")]), _patch_judge(), \
            patch.dict("os.environ", {"EVAL_ONLINE_SAMPLE_RATE": "1.0"}), \
            patch("backend.infra.db.engine_for",
                  side_effect=RuntimeError("db down")):
        result = sut.run_online_sampling()
    assert result["persisted"] is False
    assert result["run_id"]


def test_celery_beat_registers_online_sampling_at_0130_utc():
    """beat 以 Asia/Shanghai 时区在 09:30 调度，对应 01:30 UTC。"""
    from backend.tasks.celery_app import celery_app

    entry = celery_app.conf.beat_schedule["eval-online-sampling"]
    schedule = entry["schedule"]
    assert celery_app.conf.timezone == "Asia/Shanghai"
    assert schedule.hour == {9}
    assert schedule.minute == {30}
    assert entry["task"] == "evaluation.online_sampling"
    assert entry["options"]["queue"] == "report"
    assert "backend.tasks.eval_sampling_tasks" in celery_app.conf.include
