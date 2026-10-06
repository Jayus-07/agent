from __future__ import annotations

import pytest

from backend.core.tool_governance.budget import (
    RequestToolBudget,
    bind_tool_budget,
    reset_tool_budget,
)
from backend.core.tool_governance.guard import GovernanceRuntime, ToolCallRequest
from backend.core.tool_governance.rate_limiter import (
    RateLimitConfig,
    SlidingWindowRateLimiter,
)
from backend.core.tool_governance.registry import (
    all_tool_specs,
    validate_tool_spec_registry,
)
from backend.core.tool_governance.schema_validator import (
    SchemaValidationError,
    validate_json_schema,
)


@pytest.fixture(autouse=True)
def _reset_runtime_context() -> None:
    reset_tool_budget()
    yield
    reset_tool_budget()


def test_all_capabilities_have_complete_tool_specs() -> None:
    result = validate_tool_spec_registry()
    assert result["tool_registry_pass"] is True
    assert result["capability_spec_count"] == 17
    assert result["tool_spec_count"] >= 56
    assert all(
        spec.params_schema["additionalProperties"] is False
        for spec in all_tool_specs().values()
    )


def test_json_schema_enforces_nested_arrays_unknown_fields_and_bounds() -> None:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["items"],
        "properties": {
            "items": {
                "type": "array",
                "minItems": 1,
                "maxItems": 2,
                "uniqueItems": True,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["sku"],
                    "properties": {
                        "sku": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 8,
                            "pattern": r"^[A-Z0-9_-]+$",
                        }
                    },
                },
            },
            "ratio": {
                "type": "number",
                "exclusiveMinimum": 0,
                "maximum": 1,
            },
        },
    }
    validate_json_schema({"items": [{"sku": "SKU_1"}], "ratio": 0.5}, schema)
    cases = [
        {"items": [{"sku": "bad value"}]},
        {"items": [{"sku": "A"}, {"sku": "A"}]},
        {"items": [{"sku": "A"}], "force": True},
        {"items": [{"sku": "A"}], "ratio": 0},
    ]
    for value in cases:
        with pytest.raises(SchemaValidationError):
            validate_json_schema(value, schema)


def test_selection_and_intent_guards_are_deterministic() -> None:
    runtime = GovernanceRuntime()
    request = ToolCallRequest(
        capability="sql.query",
        arguments={"question": "查订单"},
        candidate_capabilities=("rag.search",),
        domain="data",
        user_id="u1",
        tenant_id="t1",
    )
    assert runtime.prepare(request).code == "tool_not_candidate"
    mismatch = ToolCallRequest(
        capability="sql.query",
        arguments={"question": "查订单"},
        candidate_capabilities=("sql.query",),
        domain="data",
        intent_fit="no_match",
        user_id="u1",
        tenant_id="t1",
    )
    assert runtime.prepare(mismatch).code == "tool_intent_mismatch"
    unknown = ToolCallRequest(
        capability="sql.query",
        arguments={"question": "查订单", "force": True},
        user_id="u1",
        tenant_id="t1",
    )
    assert runtime.prepare(unknown).code == "invalid_param"


def test_auto_arguments_must_be_runtime_injected() -> None:
    runtime = GovernanceRuntime()
    direct = ToolCallRequest(
        capability="business.analyze",
        arguments={"sql_result": {"rows": []}},
        user_id="u1",
        tenant_id="t1",
    )
    assert runtime.prepare(direct).code == "runtime_argument_supplied_by_llm"
    injected = ToolCallRequest(
        capability="business.analyze",
        arguments={},
        runtime_injected={"sql_result": {"rows": []}},
        user_id="u1",
        tenant_id="t1",
    )
    assert runtime.prepare(injected).allowed is True


def test_high_risk_prepare_confirm_and_parameter_change_invalidates_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.security import tool_approval

    monkeypatch.setattr(tool_approval, "TOOL_APPROVAL_MODE", "required")
    runtime = GovernanceRuntime()
    request = ToolCallRequest(
        capability="data.export",
        arguments={"question": "导出订单"},
        user_id="u1",
        tenant_id="t1",
    )
    prepared = runtime.prepare(request)
    assert prepared.code == "confirmation_required"
    assert prepared.preview is not None
    runtime.confirm(prepared.preview.confirmation_id)
    changed = ToolCallRequest(
        capability="data.export",
        arguments={"question": "导出全部订单"},
        user_id="u1",
        tenant_id="t1",
        confirmation_id=prepared.preview.confirmation_id,
    )
    assert runtime.prepare(changed).code == "confirmation_invalid"


def test_dynamic_operation_risk_requires_confirmation_only_for_write_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.security import tool_approval

    monkeypatch.setattr(tool_approval, "TOOL_APPROVAL_MODE", "required")
    runtime = GovernanceRuntime()

    read = runtime.prepare(
        ToolCallRequest(
            capability="competitor_watchlist_tool",
            arguments={"action": "list"},
        )
    )
    write = runtime.prepare(
        ToolCallRequest(
            capability="competitor_watchlist_tool",
            arguments={"action": "add", "url": "https://example.com/item"},
        )
    )

    assert read.allowed is True
    assert write.code == "confirmation_required"


def test_budget_and_dedupe_keep_provider_call_count_controlled() -> None:
    reset_tool_budget()
    bind_tool_budget(RequestToolBudget(total_limit=2, per_capability_limit=2))
    runtime = GovernanceRuntime()
    calls = {"count": 0}

    def provider() -> dict:
        calls["count"] += 1
        return {"items": [{"id": 1}], "total": 1}

    request = ToolCallRequest(
        capability="sql.query",
        arguments={"question": "查订单"},
        user_id="u1",
        tenant_id="t1",
    )
    first = runtime.execute_sync(request, provider)
    second = runtime.execute_sync(request, provider)
    third = runtime.execute_sync(request, provider)
    assert first.ok and second.ok
    assert second.fallback_used == "dedupe_cache"
    assert calls["count"] == 1
    assert third.error_code == "tool_call_budget_exhausted"
    reset_tool_budget()


def test_empty_and_invalid_output_are_not_normal_success() -> None:
    runtime = GovernanceRuntime()
    request = ToolCallRequest(
        capability="sql.query",
        arguments={"question": "查订单"},
        user_id="u1",
        tenant_id="t1",
    )
    empty = runtime.execute_sync(request, lambda: [])
    assert empty.ok
    assert empty.data["status"] == "empty"
    invalid = runtime.execute_sync(
        ToolCallRequest(
            capability="sql.query",
            arguments={"question": "查订单"},
            user_id="u2",
            tenant_id="t1",
        ),
        lambda: "<html>502 Bad Gateway</html>",
    )
    assert invalid.error_code == "output_invalid"


def test_sliding_window_rate_limit_blocks_before_provider() -> None:
    limiter = SlidingWindowRateLimiter()
    config = RateLimitConfig(enabled=True, requests=1, window_seconds=60, scope="tenant")
    assert limiter.allow("tool:t1", config, now=1.0)
    assert not limiter.allow("tool:t1", config, now=2.0)
    assert limiter.allow("tool:t1", config, now=62.0)


def test_execute_sync_allows_sync_tool_with_inner_event_loop() -> None:
    """STOP A 回归（2026-10-07 12306 全挂事故）：同步适配器 loop 内的同步
    Tool 内部再起 asyncio.run（MCP 同步桥形态）必须成功。

    修复前：execute_sync 用 asyncio.run 起 loop，provider 在 loop 线程
    内联执行，内部 asyncio.run 撞「cannot be called from a running
    event loop」——12306/知乎所有真实调用（缓存未命中）全挂。
    修复后：SafeToolExecutor 把同步 call 挪到工作线程，嵌套 loop 不复现。
    """
    import asyncio
    import threading

    runtime = GovernanceRuntime()
    seen_threads: list[int] = []

    def provider() -> dict:
        seen_threads.append(threading.get_ident())
        asyncio.run(asyncio.sleep(0))  # 修复前此处抛 RuntimeError
        return {"items": [{"id": 1}], "total": 1}

    result = runtime.execute_sync(
        ToolCallRequest(
            capability="sql.query",
            arguments={"question": "查订单"},
            user_id="u-stop-a",
            tenant_id="t1",
        ),
        provider,
    )
    assert result.ok, f"预期成功封套，实际 {result.status}: {result.error_message}"
    assert seen_threads, "同步 Tool 未被执行"
