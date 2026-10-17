"""RAG 真实评测在任何 Provider 外呼前验证批准价格。"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import psycopg2
import pytest

from backend.infra.llm import pricing
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
    with pytest.raises(pricing.MissingModelPrice):
        seed.verify_pricing()
