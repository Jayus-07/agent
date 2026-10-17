"""baseline fixture 的稳定模块入口。"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace

import pytest


def test_import_fixture_delegates_to_canonical_fixture_set(monkeypatch):
    from backend.evaluation import import_fixture
    from backend.scripts import ingest_eval_fixtures

    seen: list[list[str]] = []

    def fake_main():
        seen.append(sys.argv[:])
        return 0

    monkeypatch.setattr(ingest_eval_fixtures, "main", fake_main)
    bootstrapped: list[bool] = []
    monkeypatch.setattr(
        import_fixture,
        "_bootstrap_llm_registry",
        lambda: bootstrapped.append(True),
    )

    assert import_fixture.main(["baseline"]) == 0
    assert bootstrapped == [True]
    assert "--fixture-set" in seen[0]
    assert "baseline" in seen[0]
    assert sys.argv[0] != "--fixture-set"


def test_ci_fixture_import_uses_eval_identity_and_shared_budget(monkeypatch):
    from backend.core import request_context
    from backend.evaluation import import_fixture, service
    from backend.evaluation.run_guards import EVAL_TENANT_ID
    from backend.infra.llm import budget, proxy
    from backend.scripts import ingest_eval_fixtures

    monkeypatch.setenv("EVAL_RUN_ID", "rag-smoke-42")
    monkeypatch.setenv("EVAL_TRIGGERED_BY", "github-actions-rag-smoke")
    monkeypatch.setattr(import_fixture, "_bootstrap_llm_registry", lambda: None)
    monkeypatch.setattr(ingest_eval_fixtures, "main", lambda: 0)

    seen = {"budget": [], "user": [], "identity": []}

    @contextmanager
    def budget_scope(request_id, **kwargs):
        seen["budget"].append((request_id, kwargs))
        yield None

    @contextmanager
    def user_scope(user_id):
        seen["user"].append(user_id)
        yield

    @contextmanager
    def identity_scope(*, user_id, tenant_id):
        seen["identity"].append((user_id, tenant_id))
        yield

    monkeypatch.setattr(budget, "scoped_request_budget", budget_scope)
    monkeypatch.setattr(proxy, "scoped_current_user_id", user_scope)
    monkeypatch.setattr(request_context, "scoped_tool_identity", identity_scope)
    monkeypatch.setattr(service, "_handle_budget_binding_error", lambda exc: None)
    monkeypatch.setattr(budget, "current_request_budget", lambda: SimpleNamespace(
        limits=SimpleNamespace(max_calls=16, max_total_tokens=8000, max_cost=Decimal("0.10")),
        snapshot=lambda: SimpleNamespace(calls=1, total_tokens=8, cost=Decimal("0.11")),
    ))

    with pytest.raises(RuntimeError, match="fixture 索引阶段超出请求预算: cost"):
        import_fixture.main(["baseline"])
    assert seen["budget"] == [(
        "eval:rag-smoke-42",
        {
            "user_id": "github-actions-rag-smoke",
            "tenant_id": EVAL_TENANT_ID,
            "on_error": service._handle_budget_binding_error,
        },
    )]
    assert seen["user"] == ["github-actions-rag-smoke"]
    assert seen["identity"] == [("github-actions-rag-smoke", EVAL_TENANT_ID)]
