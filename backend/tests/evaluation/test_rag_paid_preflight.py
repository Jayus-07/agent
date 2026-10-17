"""RAG 真实评测在任何 Provider 外呼前验证批准价格。"""
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import psycopg2
import pytest
import yaml

from backend.infra.llm import budget, pricing
from backend.scripts import seed_prompt_eval_db as seed


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
        effective_from=datetime.now(timezone.utc) - timedelta(days=1),
        effective_to=None,
        approval_status="approved",
        reviewer_1="reviewer-a",
        reviewer_2="reviewer-b",
        version_status="active",
        version_source="source-registry",
        version_imported_by="price-importer",
        version_effective_from=datetime.now(timezone.utc) - timedelta(days=2),
        version_reviewer_1="reviewer-a",
        version_reviewer_2="reviewer-b",
        canary_started_at=datetime.now(timezone.utc) - timedelta(days=2),
        canary_completed_at=datetime.now(timezone.utc) - timedelta(days=1),
        coverage_ratio=Decimal("1.00000"),
        missing_price_count=0,
        price_calculation_error_count=0,
    )


def _embedding_configs():
    return {"embedding": {"model_name": "embedding-test"}}


@pytest.mark.parametrize(
    "invalid",
    ["reviewer", "source", "inactive", "expired", "duplicate", "model", "missing"],
)
def test_price_snapshot_rejects_unapproved_or_incomplete_rows(invalid):
    rows = [_approved_price_row("input")]
    if invalid == "reviewer":
        rows[0]["reviewer_2"] = "reviewer-a"
    elif invalid == "source":
        rows[0]["source"] = ""
    elif invalid == "inactive":
        rows[0]["version_status"] = "expired"
    elif invalid == "expired":
        rows[0]["effective_to"] = datetime.now(timezone.utc) - timedelta(days=1)
    elif invalid == "duplicate":
        rows.append(dict(rows[0]))
    elif invalid == "model":
        rows[0]["model_name"] = "unbound-model"
    elif invalid == "missing":
        rows[0]["dimension"] = "output"

    with pytest.raises((ValueError, pricing.MissingModelPrice)):
        seed.build_price_snapshot(_embedding_configs(), rows)


def test_price_snapshot_preserves_registry_approval_and_version():
    result = seed.build_price_snapshot(
        _embedding_configs(), [_approved_price_row("input")]
    )

    assert result[0]["reviewer_1"] == "reviewer-a"
    assert result[0]["reviewer_2"] == "reviewer-b"
    assert result[0]["source"] == "source-registry"
    assert result[0]["price_table_version"] == "source-v1"
    assert seed._active_version_rows(result)[0]["status"] == "active"
    assert result[0]["price_per_unit"] == Decimal("1")


@pytest.mark.parametrize(
    ("changed", "value", "message"),
    [
        ("price_table_version", "source-v2", "版本或来源"),
        ("source", "different-registry", "版本或来源"),
        ("currency", "USD", "不能混用币种"),
    ],
)
def test_price_snapshot_rejects_mixed_registry_dimensions(changed, value, message):
    rows = [
        _approved_price_row("input"),
        _approved_price_row("cache_read"),
    ]
    rows[1][changed] = value

    with pytest.raises(ValueError, match=message):
        seed.build_price_snapshot(_embedding_configs(), rows)


def test_price_snapshot_rejects_per_call_token_price():
    row = _approved_price_row("input", unit="per_call")

    with pytest.raises(ValueError, match="Token 维度"):
        seed.build_price_snapshot(_embedding_configs(), [row])


def test_registry_read_requires_configured_dsn(monkeypatch):
    monkeypatch.delenv("PROMPT_EVAL_PRICE_REGISTRY_DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="只读价格 Registry"):
        seed.load_registry_price_rows(_embedding_configs())


def test_registry_read_uses_readonly_replica_and_bound_models(monkeypatch):
    calls = []
    rows = [_approved_price_row("input")]

    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, query, params=None):
            calls.append((query, params))

        def fetchone(self):
            return {"is_read_replica": True}

        def fetchall(self):
            return rows

    class _Connection:
        def cursor(self, **_kwargs):
            return _Cursor()

        def set_session(self, **kwargs):
            calls.append(("session", kwargs))

        def rollback(self):
            calls.append(("rollback", None))

        def close(self):
            calls.append(("close", None))

    monkeypatch.setenv(
        "PROMPT_EVAL_PRICE_REGISTRY_DATABASE_URL", "postgresql://readonly/registry"
    )
    monkeypatch.setattr(
        seed.psycopg2,
        "connect",
        lambda dsn, **kwargs: calls.append((dsn, kwargs)) or _Connection(),
    )

    result = seed.load_registry_price_rows(_embedding_configs())

    assert result == rows
    assert ("session", {"readonly": True, "autocommit": False}) in calls
    price_query = next(call for call in calls if isinstance(call[1], list))
    assert price_query[1] == ["embedding-test", "embedding"]
    assert "model_price_versions" in str(price_query[0])
    assert "status = 'active'" in str(price_query[0])
    assert calls[-2:] == [("rollback", None), ("close", None)]


def test_registry_read_rejects_primary_database(monkeypatch):
    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, *_args):
            return None

        def fetchone(self):
            return {"is_read_replica": False}

    class _Connection:
        def cursor(self, **_kwargs):
            return _Cursor()

        def set_session(self, **_kwargs):
            return None

        def rollback(self):
            return None

        def close(self):
            return None

    monkeypatch.setenv(
        "PROMPT_EVAL_PRICE_REGISTRY_DATABASE_URL", "postgresql://readonly/registry"
    )
    monkeypatch.setattr(seed.psycopg2, "connect", lambda *_args, **_kwargs: _Connection())

    with pytest.raises(RuntimeError, match="只读副本"):
        seed.load_registry_price_rows(_embedding_configs())


def test_registry_prefix_is_validated_before_connect(monkeypatch):
    monkeypatch.setenv(
        "PROMPT_EVAL_PRICE_REGISTRY_DATABASE_URL", "postgresql://readonly/registry"
    )
    monkeypatch.setenv("PROMPT_EVAL_PRICE_REGISTRY_TABLE_PREFIX", "bad-prefix;")
    connect_called = False

    def _connect(*_args, **_kwargs):
        nonlocal connect_called
        connect_called = True
        raise AssertionError("不应连接 Registry")

    monkeypatch.setattr(seed.psycopg2, "connect", _connect)
    with pytest.raises(ValueError, match="表前缀"):
        seed.load_registry_price_rows(_embedding_configs())
    assert connect_called is False


def test_registry_database_errors_are_sanitized(monkeypatch, capsys):
    monkeypatch.setenv(
        "PROMPT_EVAL_PRICE_REGISTRY_DATABASE_URL", "postgresql://secret@registry/db"
    )
    monkeypatch.setattr(
        seed.psycopg2,
        "connect",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            psycopg2.OperationalError("credential-sentinel")
        ),
    )

    with pytest.raises(RuntimeError, match="保持阻断"):
        seed.load_registry_price_rows(_embedding_configs())
    assert "credential-sentinel" not in capsys.readouterr().out


def test_price_verification_fails_before_usage_pricing_when_snapshot_is_missing(
    monkeypatch,
):
    monkeypatch.setattr(
        seed,
        "_require_test_database",
        lambda: {
            "host": "localhost", "port": 5432,
            "database": "agent_memory_test", "username": "postgres",
            "password": "test-password",
        },
    )
    monkeypatch.setattr(
        seed,
        "build_seed_configs",
        lambda: {"eval_gen": {"model_name": "chat-test"}},
    )
    class Repository:
        def __init__(self, connection_factory):
            assert callable(connection_factory)

        def get_current(self, *_args):
            return pricing.PriceTable([])

    monkeypatch.setattr(pricing, "PostgresPriceRepository", Repository)

    # #52：价格核验必须先于任何 usage 计价，失败时不得悄悄回落到估价。
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


def test_price_table_rejects_per_call_for_token_dimension():
    with pytest.raises(ValueError, match="Token 维度"):
        pricing.PriceTable(
            [
                pricing.PriceLine(
                    "embedding-test",
                    "embedding",
                    "input",
                    Decimal("1"),
                    unit="per_call",
                    currency="CNY",
                )
            ]
        )


def test_price_table_rejects_mixed_currencies_at_runtime_boundary():
    with pytest.raises(ValueError, match="不能混用价格币种"):
        pricing.PriceTable(
            [
                pricing.PriceLine(
                    "embedding-test", "embedding", "input", Decimal("1"), currency="CNY"
                ),
                pricing.PriceLine(
                    "embedding-test", "embedding", "cache_read", Decimal("1"), currency="USD"
                ),
            ]
        )


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


@pytest.mark.parametrize(
    "cost,ledger,envelope,passes",
    [
        ("VERIFIED", "VERIFIED", "WITHIN_LIMITS", True),
        ("ESTIMATED", "VERIFIED", "WITHIN_LIMITS", True),
        ("UNVERIFIED", "VERIFIED", "WITHIN_LIMITS", False),
        ("VERIFIED", "UNVERIFIED", "WITHIN_LIMITS", False),
        ("VERIFIED", "VERIFIED", "OVER_LIMIT", False),
    ],
)
def test_real_workflow_rejects_unverified_cost_or_ledger_or_budget(
    monkeypatch, tmp_path, cost, ledger, envelope, passes
):
    root = Path(__file__).resolve().parents[3]
    definition = yaml.safe_load(
        (root / ".github/workflows/rag_smoke.yml").read_text(encoding="utf-8")
    )
    steps = definition["jobs"]["rag-smoke"]["steps"]
    step = next(row for row in steps if row.get("name") == "Require reconciled cost ledger")
    script = step["run"].split("python - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "data/eval_runs/llm_usage_summary.json"
    target.parent.mkdir(parents=True)
    target.write_text(
        json.dumps(
            {
                "cost_verification": cost,
                "budget_ledger_verification": ledger,
                "budget_envelope_verification": envelope,
            }
        ),
        encoding="utf-8",
    )
    if passes:
        exec(compile(script, "cost-ledger-gate", "exec"), {})
    else:
        with pytest.raises(SystemExit):
            exec(compile(script, "cost-ledger-gate", "exec"), {})
