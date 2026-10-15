"""RAG 真实评测在任何 Provider 外呼前验证批准价格。"""
import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

from backend.infra.llm import pricing


def _approved_price_row(dimension: str, *, unit="per_1m_tokens", currency="CNY"):
    return dict(
        model_name="embedding-test",
        component="embedding",
        dimension=dimension,
        price_per_unit="1.000000",
        unit=unit,
        currency=currency,
        price_table_version="source-v1",
        source="source-registry",
        effective_from="2020-01-01T00:00:00+00:00",
        approval_status="approved",
        reviewer_1="original-a",
        reviewer_2="original-b",
    )


@pytest.mark.parametrize(
    "invalid",
    ["reviewer", "source", "expired", "duplicate", "model", "missing"],
)
def test_price_snapshot_rejects_unapproved_or_incomplete_rows(monkeypatch, invalid):
    from backend.scripts.seed_prompt_eval_db import build_price_snapshot

    rows = [_approved_price_row("input")]
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

    with pytest.raises((ValueError, pricing.MissingModelPrice)):
        build_price_snapshot(configs)


def test_price_snapshot_preserves_approval_source_and_version(monkeypatch):
    from backend.scripts.seed_prompt_eval_db import build_price_snapshot

    row = _approved_price_row("input")
    monkeypatch.setenv("PROMPT_EVAL_APPROVED_PRICES_JSON", json.dumps([row]))
    result = build_price_snapshot({"embedding": {"model_name": "embedding-test"}})

    assert result[0]["reviewer_1"] == "original-a"
    assert result[0]["reviewer_2"] == "original-b"
    assert result[0]["source"] == "source-registry"
    assert result[0]["price_table_version"] == "source-v1"
    assert result[0]["price_per_unit"] == Decimal("1")


def test_price_snapshot_rejects_per_call_token_price(monkeypatch):
    from backend.scripts.seed_prompt_eval_db import build_price_snapshot

    row = _approved_price_row("input", unit="per_call")
    monkeypatch.setenv("PROMPT_EVAL_APPROVED_PRICES_JSON", json.dumps([row]))

    with pytest.raises(ValueError, match="Token 维度"):
        build_price_snapshot({"embedding": {"model_name": "embedding-test"}})


def test_price_snapshot_rejects_mixed_currency_within_model_component(monkeypatch):
    from backend.scripts.seed_prompt_eval_db import build_price_snapshot

    rows = [
        _approved_price_row("input", currency="CNY"),
        _approved_price_row("cache_read", currency="USD"),
    ]
    monkeypatch.setenv("PROMPT_EVAL_APPROVED_PRICES_JSON", json.dumps(rows))

    with pytest.raises(ValueError, match="不能混用币种"):
        build_price_snapshot({"embedding": {"model_name": "embedding-test"}})


def test_price_verification_fails_before_usage_pricing_when_snapshot_is_missing(monkeypatch):
    from backend.scripts import seed_prompt_eval_db as seed

    monkeypatch.setattr(
        seed, "_require_test_database", lambda: ("localhost", "agent_memory_test")
    )
    monkeypatch.setattr(
        seed,
        "build_seed_configs",
        lambda: {"eval_gen": {"model_name": "chat-test"}},
    )
    monkeypatch.setattr(
        pricing,
        "get_current_price_table",
        lambda *_args: pricing.PriceTable([]),
    )
    usage_pricing_called = False

    def price_usage(*_args, **_kwargs):
        nonlocal usage_pricing_called
        usage_pricing_called = True
        return SimpleNamespace(
            pricing_source="approved_price_table",
            cost_status=pricing.COST_STATUS_EXACT,
        )

    monkeypatch.setattr(pricing, "price_usage", price_usage)
    with pytest.raises(pricing.MissingModelPrice):
        seed.verify_pricing()

    assert usage_pricing_called is False
