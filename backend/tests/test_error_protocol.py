"""WP1：统一错误协议契约测试。"""

import json

import pytest
from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from starlette.requests import Request

from backend.app.exceptions import (
    global_exception_handler,
    http_exception_handler,
    request_validation_exception_handler,
)
from backend.app.api.routes.chat import _sse_error_event
from backend.skills.base import execute_with_retry
from backend.shared.error_protocol import (
    ERROR_CODES,
    ErrorCode,
    celery_error_result,
    error_envelope_from_exception,
    serialize_tool_error,
    skill_error_result,
    sse_error_event,
)


def test_error_codes_are_exactly_the_nine_planned_values():
    assert set(ERROR_CODES) == {
        "INVALID_PARAM",
        "PERMISSION_DENIED",
        "NOT_FOUND",
        "TIMEOUT",
        "UPSTREAM_UNAVAILABLE",
        "RATE_LIMITED",
        "IDEMPOTENCY_CONFLICT",
        "BUDGET_EXCEEDED",
        "INTERNAL_ERROR",
    }
    assert {code.value for code in ErrorCode} == set(ERROR_CODES)


def test_unknown_exception_is_safe_and_traceable():
    envelope = error_envelope_from_exception(
        RuntimeError("D:/secret/project/backend.py:99 internal detail"),
        trace_id="trace-123",
        source="tool",
    )

    payload = envelope.to_dict()

    assert payload == {
        "code": "INTERNAL_ERROR",
        "retryable": True,
        "handoff_available": False,
        "message": "服务器内部错误，请稍后重试。",
        "trace_id": "trace-123",
        "source": "tool",
    }
    assert "RuntimeError" not in json.dumps(payload, ensure_ascii=False)
    assert "D:/secret" not in json.dumps(payload, ensure_ascii=False)


def test_http_exception_maps_to_planned_code_without_class_name():
    envelope = error_envelope_from_exception(
        HTTPException(status_code=409, detail="duplicate request"),
        trace_id="trace-409",
        source="http",
    )

    assert envelope.to_dict() == {
        "code": "IDEMPOTENCY_CONFLICT",
        "retryable": False,
        "handoff_available": False,
        "message": "请求已处理或正在处理中。",
        "trace_id": "trace-409",
        "source": "http",
    }


def test_transport_adapters_share_the_same_required_error_fields():
    exc = TimeoutError("upstream timeout at C:/private/service.py")

    tool_payload = json.loads(serialize_tool_error(exc, trace_id="trace-t"))
    skill_payload = skill_error_result("step-1", exc, trace_id="trace-t")
    celery_payload = celery_error_result(exc, trace_id="trace-t")
    sse_payload = sse_error_event(exc, trace_id="trace-t")

    required = {"code", "retryable", "handoff_available", "message", "trace_id"}
    assert required <= tool_payload.keys()
    assert required <= skill_payload["error_protocol"].keys()
    assert required <= celery_payload["error"].keys()
    assert required <= sse_payload["data"].keys()
    assert tool_payload["code"] == skill_payload["error_protocol"]["code"] == celery_payload["error"]["code"] == sse_payload["data"]["code"] == "TIMEOUT"
    assert tool_payload["message"] == skill_payload["error_protocol"]["message"] == celery_payload["error"]["message"] == sse_payload["data"]["message"]
    assert {tool_payload["source"], skill_payload["error_protocol"]["source"], celery_payload["error"]["source"], sse_payload["data"]["source"]} == {"tool", "skill", "celery", "sse"}
    assert "TimeoutError" not in json.dumps(tool_payload, ensure_ascii=False)
    assert "C:/private" not in json.dumps(tool_payload, ensure_ascii=False)


def test_celery_adapter_can_preserve_legacy_status_and_reason():
    payload = celery_error_result(
        TimeoutError("worker stack path"),
        trace_id="trace-celery",
        source="celery.agent",
        status="error",
        reason="timeout",
    )

    assert payload["status"] == "error"
    assert payload["reason"] == "timeout"
    assert payload["error"]["code"] == "TIMEOUT"
    assert payload["error"]["source"] == "celery.agent"
    assert "worker stack path" not in json.dumps(payload, ensure_ascii=False)


def _request(path: str = "/test") -> Request:
    return Request({
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": [],
        "query_string": b"",
        "server": ("testserver", 80),
        "client": ("testclient", 1234),
        "scheme": "http",
    })


@pytest.mark.asyncio
async def test_http_handler_keeps_status_and_adds_compatible_protocol_fields():
    response = await http_exception_handler(
        _request("/conflict"),
        HTTPException(status_code=409, detail="duplicate request"),
    )

    payload = json.loads(response.body)

    assert response.status_code == 409
    assert payload["code"] == "IDEMPOTENCY_CONFLICT"
    assert payload["retryable"] is False
    assert payload["handoff_available"] is False
    assert payload["trace_id"] == ""
    assert payload["error"] == "IDEMPOTENCY_CONFLICT"
    assert payload["detail"] == payload["message"]
    assert "HTTPException" not in json.dumps(payload, ensure_ascii=False)


@pytest.mark.asyncio
async def test_global_handler_does_not_leak_unknown_exception_details():
    response = await global_exception_handler(
        _request("/internal"),
        RuntimeError("C:/private/backend.py:12 secret"),
    )

    payload = json.loads(response.body)

    assert response.status_code == 500
    assert payload["code"] == "INTERNAL_ERROR"
    assert payload["retryable"] is True
    assert "RuntimeError" not in json.dumps(payload, ensure_ascii=False)
    assert "C:/private" not in json.dumps(payload, ensure_ascii=False)


@pytest.mark.asyncio
async def test_request_validation_handler_uses_invalid_param_protocol():
    exc = RequestValidationError([
        {
            "type": "missing",
            "loc": ("body", "query"),
            "msg": "Field required",
            "input": None,
        }
    ])

    response = await request_validation_exception_handler(_request("/chat"), exc)
    payload = json.loads(response.body)

    assert response.status_code == 422
    assert payload["code"] == "INVALID_PARAM"
    assert payload["error"] == "INVALID_PARAM"
    assert payload["detail"] == payload["message"]
    assert "Field required" not in json.dumps(payload, ensure_ascii=False)


def test_chat_sse_error_adapter_does_not_expose_exception_text():
    event = _sse_error_event(
        RuntimeError("C:/private/backend.py:88 secret"),
        trace_id="trace-sse",
    )

    assert event["event"] == "error"
    assert event["data"]["code"] == "INTERNAL_ERROR"
    assert event["data"]["trace_id"] == "trace-sse"
    assert "secret" not in json.dumps(event, ensure_ascii=False)


def test_task_sse_error_adapter_does_not_expose_exception_text():
    payload = sse_error_event(
        RuntimeError("C:/private/tasks.py:22 secret"), source="sse"
    )["data"]

    assert payload["code"] == "INTERNAL_ERROR"
    assert payload["source"] == "sse"
    assert "secret" not in json.dumps(payload, ensure_ascii=False)


@pytest.mark.asyncio
async def test_skill_failure_keeps_legacy_error_and_adds_protocol_envelope():
    class FailingTool:
        def invoke(self, _params):
            raise TimeoutError("C:/private/tool.py:88 secret")

    result = await execute_with_retry(
        {
            "current_step_id": "step-1",
            "plan": {"nodes": {"step-1": {"capability": "demo.run", "params": {}}}},
        },
        FailingTool(),
        max_retries=0,
        timeout=0.1,
    )

    failure = result["step_results"]["step-1"]
    assert failure["status"] == "failed"
    assert failure["error_protocol"]["code"] == "TIMEOUT"
    assert failure["error_protocol"]["retryable"] is True
    assert "C:/private" not in json.dumps(failure, ensure_ascii=False)


def test_idempotency_exceptions_map_to_protocol_codes():
    from backend.shared.error_protocol import error_envelope_from_exception
    from backend.shared.idempotency import (
        IdempotencyContextMissing,
        IdempotencyUnavailable,
    )

    assert error_envelope_from_exception(
        IdempotencyContextMissing("tenant missing")
    ).code.value == "PERMISSION_DENIED"
    assert error_envelope_from_exception(
        IdempotencyUnavailable("redis down")
    ).code.value == "UPSTREAM_UNAVAILABLE"
    assert error_envelope_from_exception(
        ValueError("IDEMPOTENCY_CONFLICT")
    ).code.value == "IDEMPOTENCY_CONFLICT"
