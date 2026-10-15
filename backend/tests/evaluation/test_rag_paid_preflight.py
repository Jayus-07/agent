"""付费评测在出站前校验价格；身份及费用账本必须参与最终门禁。"""
import json
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from backend.infra.llm import budget, pricing


@pytest.mark.parametrize("invalid", ["none", "reviewer", "source", "expired", "duplicate", "model", "missing"])
def test_price_snapshot_preserves_approval_and_rejects_invalid_source(monkeypatch, invalid):
    from backend.scripts.seed_prompt_eval_db import build_price_snapshot

    rows = [dict(
        model_name="embedding-test", component="embedding", dimension="input",
        price_per_unit="1.000000", unit="per_1m_tokens", currency="CNY",
        price_table_version="source-v1", source="source-registry",
        effective_from="2020-01-01T00:00:00+00:00", approval_status="approved",
        reviewer_1="original-a", reviewer_2="original-b",
    )]
    if invalid == "reviewer":
        rows[0]["reviewer_2"] = "original-a"
    elif invalid == "source":
        rows[0]["source"] = ""
    elif invalid == "expired":
        rows[0]["effective_to"] = "2021-01-01T00:00:00+00:00"
    elif invalid == "duplicate":
        rows.append(dict(rows[0]))
    elif invalid == "model":
        rows[0]["model_name"] = "unbound-model"
    elif invalid == "missing":
        rows[0]["dimension"] = "output"
    monkeypatch.setenv("PROMPT_EVAL_APPROVED_PRICES_JSON", json.dumps(rows))
    configs = {"embedding": {"model_name": "embedding-test"}}
    if invalid == "none":
        result = build_price_snapshot(configs)
        assert result[0]["reviewer_1"] == "original-a"
        assert result[0]["reviewer_2"] == "original-b"
        assert result[0]["source"] == "source-registry"
        assert result[0]["price_per_unit"] == Decimal("1")
    else:
        with pytest.raises((ValueError, pricing.MissingModelPrice)):
            build_price_snapshot(configs)


def _approved_price_row(dimension: str, *, unit="per_1m_tokens", currency="CNY"):
    return dict(
        model_name="embedding-test", component="embedding", dimension=dimension,
        price_per_unit="1.000000", unit=unit, currency=currency,
        price_table_version="source-v1", source="source-registry",
        effective_from="2020-01-01T00:00:00+00:00", approval_status="approved",
        reviewer_1="original-a", reviewer_2="original-b",
    )


def test_price_snapshot_rejects_per_call_for_token_dimension(monkeypatch):
    from backend.scripts.seed_prompt_eval_db import build_price_snapshot

    rows = [_approved_price_row("input", unit="per_call")]
    monkeypatch.setenv("PROMPT_EVAL_APPROVED_PRICES_JSON", json.dumps(rows))

    with pytest.raises(ValueError, match="Token 维度"):
        build_price_snapshot({"embedding": {"model_name": "embedding-test"}})


def test_price_table_rejects_per_call_for_token_dimension():
    with pytest.raises(ValueError, match="Token 维度"):
        pricing.PriceTable([
            pricing.PriceLine(
                "embedding-test", "embedding", "input", Decimal("1"),
                unit="per_call", currency="CNY",
            ),
        ])


def test_price_snapshot_rejects_mixed_currencies_for_same_component(monkeypatch):
    from backend.scripts.seed_prompt_eval_db import build_price_snapshot

    rows = [
        _approved_price_row("input", currency="CNY"),
        _approved_price_row("cache_read", currency="USD"),
    ]
    monkeypatch.setenv("PROMPT_EVAL_APPROVED_PRICES_JSON", json.dumps(rows))

    with pytest.raises(ValueError, match="不能混用币种"):
        build_price_snapshot({"embedding": {"model_name": "embedding-test"}})


def test_price_table_rejects_mixed_currencies_at_runtime_boundary():
    with pytest.raises(ValueError, match="不能混用币种"):
        pricing.PriceTable([
            pricing.PriceLine(
                "embedding-test", "embedding", "input", Decimal("1"),
                currency="CNY",
            ),
            pricing.PriceLine(
                "embedding-test", "embedding", "cache_read", Decimal("1"),
                currency="USD",
            ),
        ])


@pytest.mark.parametrize("mode,blocked", [("enforce", True), ("observe", False)])
def test_budget_binding_failure_obeys_enforcement(monkeypatch, mode, blocked):
    from backend.config import llm
    from backend.evaluation.service import BudgetBlockedError, _handle_budget_binding_error

    monkeypatch.setattr(llm, "LLM_BUDGET_MODE", mode)

    def fail(*_args, **_kwargs):
        raise RuntimeError("预算存储不可用")

    monkeypatch.setattr(budget, "bind_request_budget", fail)
    outer_token = budget._current_request_id.set("outer-request")
    called = []
    try:
        if blocked:
            with pytest.raises(BudgetBlockedError):
                with budget.scoped_request_budget("eval", on_error=_handle_budget_binding_error):
                    called.append(True)
            assert called == []
        else:
            with budget.scoped_request_budget("eval", on_error=_handle_budget_binding_error):
                called.append(True)
            assert called == [True]
        assert budget._current_request_id.get() == "outer-request"
    finally:
        budget._current_request_id.reset(outer_token)


@pytest.mark.parametrize("missing", [True, False])
def test_price_preflight_requires_complete_approved_prices(monkeypatch, missing):
    from backend.scripts import seed_prompt_eval_db as seed

    monkeypatch.setenv("MEMORY_PGDATABASE", "original_test")
    monkeypatch.setattr(seed, "_require_test_database", lambda: ("localhost", "isolated_test"))
    monkeypatch.setattr(seed, "build_seed_configs", lambda: {
        "eval_gen": {"model_name": "test-chat"},
        "embedding": {"model_name": "test-embedding"},
        "rerank": {"model_name": "test-rerank"},
    })

    def table(model, component):
        dimensions = ["input", "output"] if component == "llm" else ["input"]
        return pricing.PriceTable([
            pricing.PriceLine(model, component, dim, Decimal("1"), currency="CNY")
            for dim in dimensions
            if not (missing and component == "rerank")
        ])

    monkeypatch.setattr(pricing, "get_current_price_table", table)
    if missing:
        with pytest.raises(pricing.MissingModelPrice):
            seed.verify_pricing()
    else:
        seed.verify_pricing()


@pytest.mark.parametrize("cost,ledger,envelope,passes", [
    ("VERIFIED", "VERIFIED", "WITHIN_LIMITS", True),
    ("ESTIMATED", "VERIFIED", "WITHIN_LIMITS", True),
    ("UNVERIFIED", "VERIFIED", "WITHIN_LIMITS", False),
    ("VERIFIED", "UNVERIFIED", "WITHIN_LIMITS", False),
    ("VERIFIED", "VERIFIED", "OVER_LIMIT", False),
])
def test_real_workflow_rejects_unverified_cost_or_ledger_or_budget(
    monkeypatch, tmp_path, cost, ledger, envelope, passes,
):
    root = Path(__file__).resolve().parents[3]
    definition = yaml.safe_load((root / ".github/workflows/rag_smoke.yml").read_text(encoding="utf-8"))
    steps = definition["jobs"]["rag-smoke"]["steps"]
    step = next(row for row in steps if row.get("name") == "Require reconciled cost ledger")
    script = step["run"].split("python - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "data/eval_runs/llm_usage_summary.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({
        "cost_verification": cost,
        "budget_ledger_verification": ledger,
        "budget_envelope_verification": envelope,
    }))
    if passes:
        exec(compile(script, "cost-ledger-gate", "exec"), {})
    else:
        with pytest.raises(SystemExit):
            exec(compile(script, "cost-ledger-gate", "exec"), {})
