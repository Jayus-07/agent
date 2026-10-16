import pytest

from backend.app.api.routes.price_dto import coverage_report, validate_price_rows
from backend.app.api.routes.model_prices import router as model_price_router


def test_price_rows_require_usd_valid_dimensions_and_unique_composite_key() -> None:
    result = validate_price_rows([
        {
            "model_name": "m",
            "component": "llm",
            "dimension": "input",
            "price_per_unit": "0.125000",
            "unit": "per_1m_tokens",
            "currency": "USD",
        },
    ])
    assert result["valid"] is True
    assert result["errors"] == []

    invalid = validate_price_rows([
        {
            "model_name": "m",
            "component": "llm",
            "dimension": "input",
            "price_per_unit": "-1",
            "unit": "per_call",
            "currency": "CNY",
        },
        {
            "model_name": "m",
            "component": "llm",
            "dimension": "input",
            "price_per_unit": "1",
            "unit": "per_1m_tokens",
            "currency": "USD",
        },
    ])
    assert invalid["valid"] is False
    assert len(invalid["errors"]) >= 3


def test_price_coverage_reports_missing_dimensions_and_zero_calculation_errors() -> None:
    coverage = coverage_report([
        {
            "model_name": "m",
            "component": "llm",
            "dimension": "input",
        },
    ])
    assert coverage["coverage_ratio"] == 0.5
    assert coverage["missing_price_count"] == 1
    assert coverage["price_calculation_error_count"] == 0


def test_price_governance_routes_use_plural_import_and_review_contract() -> None:
    paths = {route.path for route in model_price_router.routes}

    assert "/admin/model-prices/imports/validate" in paths
    assert "/admin/model-prices/imports" in paths
    assert "/admin/model-prices/versions/{version}/reviews" in paths
    assert "/admin/model-prices/monitor/runs" in paths


def test_price_monitor_history_is_read_only_and_requires_platform_role(
    monkeypatch,
) -> None:
    import asyncio

    from fastapi import HTTPException

    from backend.app.api.deps import OperatorIdentity
    from backend.app.api.routes import model_prices

    class _MonitorRepository:
        def list_runs(self, limit: int):
            assert limit == 30
            return [{"run_id": "run-1", "status": "partial"}]

    monkeypatch.setattr(model_prices, "_monitor_repo", lambda: _MonitorRepository())
    result = asyncio.run(model_prices.list_model_price_monitor_runs(
        30, OperatorIdentity(role="viewer", actor="user:15")
    ))
    assert result["items"] == [{"run_id": "run-1", "status": "partial"}]
    assert result["schedule"] == {
        "timezone": "Asia/Shanghai", "hour": 8, "minute": 0,
    }

    with pytest.raises(HTTPException) as error:
        asyncio.run(model_prices.list_model_price_monitor_runs(
            30, OperatorIdentity(role="user", actor="user:15")
        ))
    assert error.value.status_code == 403


def test_price_import_validation_is_server_side_and_does_not_persist() -> None:
    from backend.app.api.deps import OperatorIdentity
    from backend.app.api.routes.model_prices import (
        PriceImportRequest,
        validate_model_price_import,
    )

    result = __import__("asyncio").run(validate_model_price_import(
        PriceImportRequest(
            version="v-test",
            source="unit-test",
            rows=[{
                "model_name": "m",
                "component": "llm",
                "dimension": "input",
                "price_per_unit": "-1",
                "unit": "per_1m_tokens",
                "currency": "CNY",
            }],
        ),
        OperatorIdentity(role="admin", actor="user:admin"),
    ))

    assert result["valid"] is False
    assert result["errors"]
    assert result["rows"] == []
