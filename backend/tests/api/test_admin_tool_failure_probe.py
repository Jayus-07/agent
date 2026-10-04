# -*- coding: utf-8 -*-
"""管理端 Tool 强制失败测试入口。"""

import pytest

from backend.app.api.routes import admin_tools


async def _allow_admin(_request):
    return None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_class,status",
    [
        ("timeout", "timeout"),
        ("network_error", "unavailable"),
        ("permission_denied", "unauthorized"),
        ("validation_error", "invalid_request"),
        ("business_error", "failed"),
        ("contract_error", "degraded"),
        ("provider_error", "rate_limited"),
    ],
)
async def test_failure_probe_uses_shared_status_mapping(
    monkeypatch, error_class, status,
):
    monkeypatch.setattr(admin_tools, "require_admin_user", _allow_admin)
    monkeypatch.setattr(admin_tools, "ENVIRONMENT", "development")

    result = await admin_tools.tool_failure_probe(
        None,
        admin_tools.ToolFailureProbeRequest(
            tool="travel_train_search_tool", error_class=error_class,
        ),
    )

    assert result["tool"] == "travel_train_search_tool"
    assert result["error_class"] == error_class
    assert result["status"] == status
    assert result["simulated"] is True
    assert result["trace_id"]


@pytest.mark.asyncio
async def test_failure_probe_rejects_unknown_tool(monkeypatch):
    monkeypatch.setattr(admin_tools, "require_admin_user", _allow_admin)
    monkeypatch.setattr(admin_tools, "ENVIRONMENT", "development")

    with pytest.raises(admin_tools._HTTP) as exc_info:
        await admin_tools.tool_failure_probe(
            None,
            admin_tools.ToolFailureProbeRequest(
                tool="not_in_contract_lock", error_class="timeout",
            ),
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_failure_probe_rejects_production(monkeypatch):
    monkeypatch.setattr(admin_tools, "require_admin_user", _allow_admin)
    monkeypatch.setattr(admin_tools, "ENVIRONMENT", "production")

    with pytest.raises(admin_tools._HTTP) as exc_info:
        await admin_tools.tool_failure_probe(
            None,
            admin_tools.ToolFailureProbeRequest(
                tool="travel_train_search_tool", error_class="timeout",
            ),
        )

    assert exc_info.value.status_code == 403
