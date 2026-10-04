"""窗口2（五期 C5-6 + 七期 C7 + 四期部分 C4）回归测试。

覆盖：COST-10 预算阻断（启动拒+运行熔断）、REL-06/EVD-10 展示层脱敏、
REL-08 租户钩子、UI-07 500 收敛、JUDGE-03/RAGAS-11/REPRO-07 配置快照、
JUDGE-09/10 golden 对比、RAGAS-14/COST-08 成本折算。
"""
from __future__ import annotations

import json

import pytest


# ── C5-6 / COST-10：预算阻断 ─────────────────────────────────


def test_budget_check_disabled_by_default(monkeypatch):
    from backend.evaluation import run_guards

    monkeypatch.setattr(run_guards, "BUDGET_BLOCK_ENABLED", False)
    guard = run_guards.RunGuard("run-b1")
    assert guard.check_budget() == ""  # 关闭时不查询不阻塞


def test_budget_check_blocked_reason(monkeypatch):
    from backend.evaluation import run_guards

    monkeypatch.setattr(run_guards, "BUDGET_BLOCK_ENABLED", True)

    class _FakeStore:
        def get_budget_status(self, *, user_id, tenant_id):
            assert tenant_id == "default"
            return {"blocked": True, "windows": [
                {"scope": "tenant", "period": "month", "blocked": True,
                 "used": 100.0, "limit": 80.0},
            ]}

    import backend.infra.llm.quota as quota_mod

    monkeypatch.setattr(quota_mod, "PostgresQuotaStore", _FakeStore)
    guard = run_guards.RunGuard("run-b2", actor="eval-runner")
    assert guard.check_budget() == "budget_exceeded"
    # 已 block 后短路（不再查询）
    assert guard.check_budget() == "budget_exceeded"


def test_budget_check_query_failure_passes(monkeypatch):
    from backend.evaluation import run_guards

    monkeypatch.setattr(run_guards, "BUDGET_BLOCK_ENABLED", True)

    class _BadStore:
        def get_budget_status(self, **kw):
            raise RuntimeError("db down")

    import backend.infra.llm.quota as quota_mod

    monkeypatch.setattr(quota_mod, "PostgresQuotaStore", _BadStore)
    guard = run_guards.RunGuard("run-b3")
    assert guard.check_budget() == ""  # 查询失败放行（预算是保护不是障碍）


def test_budget_block_marks_failed_not_completed(monkeypatch, tmp_path):
    """熔断路径：预算超限 → stop_reason + run 状态钉 failed（COST-10）。"""
    from backend.evaluation import run_guards, storage

    root = tmp_path / "eval_runs"
    root.mkdir()
    monkeypatch.setattr(storage, "DATA_ROOT", root)
    monkeypatch.setattr(run_guards, "BUDGET_BLOCK_ENABLED", True)

    class _FakeStore:
        def get_budget_status(self, **kw):
            return {"blocked": True, "windows": []}

    import backend.infra.llm.quota as quota_mod

    monkeypatch.setattr(quota_mod, "PostgresQuotaStore", _FakeStore)
    storage.mark_run_status("run-budget", "running")
    guard = run_guards.RunGuard("run-budget", actor="t")
    reason = guard.blocking_reason()
    assert reason == "budget_exceeded"
    assert storage.read_run_status("run-budget")["status"] == "failed"


# ── C7-1 / REL-06 / EVD-10：展示层脱敏 ───────────────────────


def _sample_result() -> dict:
    return {
        "case_id": "RC-001",
        "status": "pass",
        "expected": {"expected_answer": "联系 13800138000 退货", "ground_truth": ""},
        "actual": {
            "question": "手机号 13912345678 的订单",
            "generated_answer": "请致电 13800138000",
            "contexts": ["用户 13912345678 于 10 月购买"],
            "details": [{"page_content": "联系邮箱 a@b.com", "rerank_score": 0.9}],
            "rejection": {"query_entities": None, "confidence": "high"},
        },
        "metrics": {"recall@5": 1.0},
        "error_msg": None,
    }


def test_mask_report_masks_pii_fields():
    from backend.evaluation.export_masking import mask_report_for_viewer

    report = {"results": [_sample_result()], "summaries": []}
    masked = mask_report_for_viewer(report)
    sample = masked["results"][0]
    joined = json.dumps(sample, ensure_ascii=False)
    assert "13912345678" not in joined
    assert "13800138000" not in joined
    assert "a@b.com" not in joined
    # 数值指标不受影响
    assert sample["metrics"]["recall@5"] == 1.0
    # 原始入参不被修改（明细权威保留）
    assert "13912345678" in json.dumps(report, ensure_ascii=False)


def test_mask_report_disabled_returns_raw(monkeypatch):
    from backend.evaluation import export_masking

    monkeypatch.setattr(export_masking, "MASKING_ENABLED", False)
    report = {"results": [_sample_result()]}
    masked = export_masking.mask_report_for_viewer(report)
    assert "13912345678" in json.dumps(masked, ensure_ascii=False)


def test_mask_failure_degrades_to_skeleton(monkeypatch):
    from backend.evaluation import export_masking

    monkeypatch.setattr(
        export_masking, "mask_result_for_viewer",
        lambda r: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    report = {"results": [_sample_result()], "summaries": [{"module": "rag"}]}
    masked = export_masking.mask_report_for_viewer(report)
    assert "results" not in masked  # 宁可少暴露
    assert masked["summaries"] == [{"module": "rag"}]


# ── C7-2 / REL-08：租户钩子 ──────────────────────────────────


@pytest.mark.asyncio
async def test_tenant_hook_rejects_non_default():
    from backend.app.api.routes.evaluation import _eval_tenant_scope

    with pytest.raises(Exception) as exc:
        await _eval_tenant_scope(x_tenant_id="tenant-b")
    assert "403" in str(getattr(exc.value, "status_code", "")) or "单租户" in str(exc.value)


@pytest.mark.asyncio
async def test_tenant_hook_defaults_to_default():
    from backend.app.api.routes.evaluation import _eval_tenant_scope

    assert await _eval_tenant_scope(x_tenant_id=None) == "default"
    assert await _eval_tenant_scope(x_tenant_id="default") == "default"


# ── C4-1/C4-2/C7-4：配置快照与 seed 口径 ─────────────────────


def test_judge_config_snapshot_shape():
    from backend.evaluation.evaluator_config import collect_judge_config

    config = collect_judge_config()
    assert config["kind"] == "self_judge"
    assert config["seed_support"] is False  # REPRO-07 显式口径
    assert "temperature" in config
    assert config["prompt_key"] == "evaluation.judge.user"


def test_ragas_config_snapshot_shape():
    from backend.evaluation.evaluator_config import collect_ragas_config

    config = collect_ragas_config(level="standard")
    assert config["kind"] == "ragas"
    assert config["level"] == "standard"
    assert config["temperature"] == 0
    assert config["seed_support"] is False
    assert "ragas_version" in config


# ── C4-9 / RAGAS-14：成本折算 ────────────────────────────────


def test_evaluator_cost_unavailable_price_is_none_not_zero(monkeypatch):
    from backend.evaluation import evaluator_cost

    monkeypatch.setattr(
        evaluator_cost, "_model_price", lambda m: None,
    )
    result = evaluator_cost.estimate_evaluator_cost_cny(
        {"evaluator": {"judge": 5000}}, model_name="some-model",
    )
    assert result["cost_cny"] is None
    assert result["basis"] == "unavailable_price"
    assert result["tokens"] == 5000


def test_evaluator_cost_computed_with_price(monkeypatch):
    from backend.evaluation import evaluator_cost

    monkeypatch.setattr(evaluator_cost, "_model_price", lambda m: (2.0, 8.0))
    result = evaluator_cost.estimate_evaluator_cost_cny(
        {"evaluator": {"judge": 1_000_000}}, model_name="m",
    )
    assert result["cost_cny"] == pytest.approx(2.0 * 7.20, abs=0.01)


def test_evaluator_cost_none_without_tokens():
    from backend.evaluation.evaluator_cost import estimate_evaluator_cost_cny

    assert estimate_evaluator_cost_cny({"evaluator": {"judge": 0}}) is None
    assert estimate_evaluator_cost_cny(None) is None


# ── C4-5/C4-6：golden 对比与波动 ─────────────────────────────


def _golden_report(means: dict[str, float], verdicts: dict[str, str]):
    return {
        "cases": {
            cid: {
                "expected_verdict": "pass",
                "verdicts": [verdicts.get(cid, "pass")],
                "scores": [means[cid]],
                "mean": means[cid],
                "std": 0.0,
            }
            for cid in means
        },
    }


def test_golden_compare_passes_within_tolerance():
    from backend.evaluation.judge_golden import compare_golden_reports

    base = _golden_report({"A": 4.0, "B": 3.0}, {})
    cur = _golden_report({"A": 4.05, "B": 2.95}, {})
    result = compare_golden_reports(cur, base, deviation_threshold=0.1)
    assert result["passed"] is True


def test_golden_compare_blocks_on_drift():
    from backend.evaluation.judge_golden import compare_golden_reports

    base = _golden_report({"A": 4.0}, {})
    cur = _golden_report({"A": 3.7}, {})  # 漂移 0.3 > 0.1
    result = compare_golden_reports(cur, base, deviation_threshold=0.1)
    assert result["passed"] is False
    assert result["score_drifts"][0]["case_id"] == "A"


def test_golden_compare_blocks_on_tier_flip():
    from backend.evaluation.judge_golden import compare_golden_reports

    base = _golden_report({"A": 4.0}, {"A": "pass"})
    cur = _golden_report({"A": 4.0}, {"A": "fail"})  # 档位翻转
    result = compare_golden_reports(cur, base)
    assert result["passed"] is False
    assert "A" in result["new_tier_flips"]


def test_golden_dataset_loads_and_validates():
    from backend.evaluation.judge_golden import DEFAULT_GOLDEN_PATH, load_golden

    cases = load_golden()
    assert len(cases) >= 20  # 施工书口径：20~30 条人工确认
    ids = [c["id"] for c in cases]
    assert len(set(ids)) == len(ids)
    for c in cases:
        assert c["expected_verdict"] in {"pass", "fail"}
        assert c["expected_score_min"] <= c["expected_score_max"]


# ── C4-7：RAGAS degraded 判定 ────────────────────────────────


def test_ragas_degraded_ratio():
    from backend.evaluation.service import _ragas_degraded

    assert _ragas_degraded({"valid": 8, "invalid": 2}) is True   # 80% < 90%
    assert _ragas_degraded({"valid": 9, "invalid": 1}) is False  # 90% 达标
    assert _ragas_degraded({"valid": 0, "invalid": 0}) is False  # 未执行≠degraded
