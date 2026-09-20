"""统一失败协议。

该模块只负责错误语义、脱敏和传输边界封装，不负责记录日志或决定业务重试。
成功结果保持各业务现有契约；失败结果统一包含九个协议字段，业务细码只留在
服务端日志或 details 中，不能直接透传异常对象。
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    """计划冻结的九个通用错误码。"""

    INVALID_PARAM = "INVALID_PARAM"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    NOT_FOUND = "NOT_FOUND"
    TIMEOUT = "TIMEOUT"
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


ERROR_CODES = tuple(code.value for code in ErrorCode)

_SAFE_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.INVALID_PARAM: "请求参数有误，请检查后重试。",
    ErrorCode.PERMISSION_DENIED: "无权执行此操作。",
    ErrorCode.NOT_FOUND: "请求的资源不存在。",
    ErrorCode.TIMEOUT: "操作超时，请稍后重试。",
    ErrorCode.UPSTREAM_UNAVAILABLE: "依赖服务暂时不可用，请稍后重试。",
    ErrorCode.RATE_LIMITED: "请求过于频繁，请稍后重试。",
    ErrorCode.IDEMPOTENCY_CONFLICT: "请求已处理或正在处理中。",
    ErrorCode.BUDGET_EXCEEDED: "已达到本次请求预算上限。",
    ErrorCode.INTERNAL_ERROR: "服务器内部错误，请稍后重试。",
}

_RETRYABLE: dict[ErrorCode, bool] = {
    ErrorCode.INVALID_PARAM: False,
    ErrorCode.PERMISSION_DENIED: False,
    ErrorCode.NOT_FOUND: False,
    ErrorCode.TIMEOUT: True,
    ErrorCode.UPSTREAM_UNAVAILABLE: True,
    ErrorCode.RATE_LIMITED: True,
    ErrorCode.IDEMPOTENCY_CONFLICT: False,
    ErrorCode.BUDGET_EXCEEDED: False,
    ErrorCode.INTERNAL_ERROR: True,
}


@dataclass(frozen=True)
class ErrorEnvelope:
    """可安全跨边界传输的错误封套。"""

    code: ErrorCode
    retryable: bool
    handoff_available: bool
    message: str
    trace_id: str = ""
    source: str = ""
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """转换成 JSON-safe 字典；默认不带内部异常信息。"""
        payload: dict[str, Any] = {
            "code": self.code.value,
            "retryable": self.retryable,
            "handoff_available": self.handoff_available,
            "message": self.message,
            "trace_id": self.trace_id,
            "source": self.source,
        }
        if self.details:
            payload["details"] = self.details
        return payload


class ProtocolError(Exception):
    """业务代码可主动抛出的统一协议异常。"""

    def __init__(
        self,
        code: ErrorCode,
        message: str | None = None,
        *,
        trace_id: str = "",
        source: str = "",
        handoff_available: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.envelope = ErrorEnvelope(
            code=code,
            retryable=_RETRYABLE[code],
            handoff_available=handoff_available,
            message=message or _SAFE_MESSAGES[code],
            trace_id=trace_id,
            source=source,
            details=details,
        )
        super().__init__(self.envelope.message)


def _http_status_code(exc: BaseException) -> int | None:
    status_code = getattr(exc, "status_code", None)
    return status_code if isinstance(status_code, int) else None


def _http_error_code(exc: BaseException) -> ErrorCode | None:
    status_code = _http_status_code(exc)
    if status_code is None:
        return None
    detail = str(getattr(exc, "detail", "")).lower()
    if status_code in (400, 422):
        return ErrorCode.INVALID_PARAM
    if status_code in (401, 403):
        return ErrorCode.PERMISSION_DENIED
    if status_code == 404:
        return ErrorCode.NOT_FOUND
    if status_code == 409:
        return ErrorCode.IDEMPOTENCY_CONFLICT
    if status_code == 429:
        return ErrorCode.BUDGET_EXCEEDED if "budget" in detail or "预算" in detail else ErrorCode.RATE_LIMITED
    if status_code == 504:
        return ErrorCode.TIMEOUT
    if status_code in (502, 503):
        return ErrorCode.UPSTREAM_UNAVAILABLE
    if status_code >= 500:
        return ErrorCode.INTERNAL_ERROR
    return ErrorCode.INTERNAL_ERROR


def _exception_code(exc: BaseException) -> ErrorCode:
    http_code = _http_error_code(exc)
    if http_code is not None:
        return http_code
    if isinstance(exc, ProtocolError):
        return exc.envelope.code
    if getattr(exc, "code", "") == ErrorCode.BUDGET_EXCEEDED.value:
        return ErrorCode.BUDGET_EXCEEDED
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return ErrorCode.TIMEOUT
    if isinstance(exc, PermissionError):
        return ErrorCode.PERMISSION_DENIED
    if isinstance(exc, ValueError) and "IDEMPOTENCY_CONFLICT" in str(exc):
        return ErrorCode.IDEMPOTENCY_CONFLICT
    try:
        from backend.shared.idempotency import IdempotencyUnavailable

        if isinstance(exc, IdempotencyUnavailable):
            return ErrorCode.UPSTREAM_UNAVAILABLE
    except ImportError:
        pass
    try:
        from backend.infra.llm.budget import RequestBudgetExceeded

        if isinstance(exc, RequestBudgetExceeded):
            return ErrorCode.BUDGET_EXCEEDED
    except ImportError:
        pass
    if isinstance(exc, LookupError):
        return ErrorCode.NOT_FOUND
    if isinstance(exc, ValueError):
        return ErrorCode.INVALID_PARAM
    if isinstance(exc, ConnectionError):
        return ErrorCode.UPSTREAM_UNAVAILABLE
    return ErrorCode.INTERNAL_ERROR


def _current_trace_id() -> str:
    """尽量读取当前 Trace；失败时返回空，不让错误处理再次失败。"""
    try:
        from backend.observability.tracer import current_trace_context

        trace_id, _session_id = current_trace_context()
        return trace_id or ""
    except Exception:
        return ""


def error_envelope_from_exception(
    exc: BaseException,
    *,
    trace_id: str = "",
    source: str = "",
    handoff_available: bool | None = None,
) -> ErrorEnvelope:
    """把任意异常映射成安全的统一错误封套。"""
    if isinstance(exc, ProtocolError):
        envelope = exc.envelope
        return ErrorEnvelope(
            code=envelope.code,
            retryable=envelope.retryable,
            handoff_available=(
                envelope.handoff_available
                if handoff_available is None
                else handoff_available
            ),
            message=envelope.message,
            trace_id=trace_id or envelope.trace_id or _current_trace_id(),
            source=source or envelope.source,
            details=envelope.details,
        )

    code = _exception_code(exc)
    inherited_trace_id = str(getattr(exc, "trace_id", "") or "")
    inherited_handoff = bool(getattr(exc, "handoff_available", False))
    if handoff_available is None:
        handoff_available = inherited_handoff

    # 只有已经经过协议模型的安全 message 可以复用；普通异常的 detail 永不透传。
    safe_message = _SAFE_MESSAGES[code]
    if isinstance(getattr(exc, "user_message", None), str):
        safe_message = str(exc.user_message)

    return ErrorEnvelope(
        code=code,
        retryable=_RETRYABLE[code] if not hasattr(exc, "retryable") else bool(exc.retryable),
        handoff_available=bool(handoff_available),
        message=safe_message,
        trace_id=trace_id or inherited_trace_id or _current_trace_id(),
        source=source,
    )


def serialize_tool_error(
    exc: BaseException,
    *,
    trace_id: str = "",
    source: str = "tool",
) -> str:
    """Tool 边界返回 JSON 字符串，避免把异常类名/堆栈传给调用方。"""
    return json.dumps(
        error_envelope_from_exception(exc, trace_id=trace_id, source=source).to_dict(),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def skill_error_result(
    step_id: str,
    exc: BaseException,
    *,
    trace_id: str = "",
    source: str = "skill",
) -> dict[str, Any]:
    """Skill 失败结果：保留旧 error 字符串，新增结构化协议封套。"""
    envelope = error_envelope_from_exception(exc, trace_id=trace_id, source=source)
    return {
        "step_id": step_id,
        "status": "failed",
        "output": None,
        "error": envelope.message,
        "error_type": envelope.code.value.lower(),
        "error_protocol": envelope.to_dict(),
    }


def celery_error_result(
    exc: BaseException,
    *,
    trace_id: str = "",
    source: str = "celery",
    status: str = "FAILED",
    reason: str = "",
) -> dict[str, Any]:
    """Celery 失败结果：保留 status，错误封套放在 error 字段。"""
    payload: dict[str, Any] = {
        "status": status,
        "error": error_envelope_from_exception(
            exc, trace_id=trace_id, source=source
        ).to_dict(),
    }
    if reason:
        payload["reason"] = reason
    return payload


def sse_error_event(
    exc: BaseException,
    *,
    trace_id: str = "",
    source: str = "sse",
) -> dict[str, Any]:
    """SSE error 帧适配器；保留 event:error 兼容现有客户端。"""
    return {
        "event": "error",
        "data": error_envelope_from_exception(
            exc, trace_id=trace_id, source=source
        ).to_dict(),
    }


__all__ = [
    "ERROR_CODES",
    "ErrorCode",
    "ErrorEnvelope",
    "ProtocolError",
    "celery_error_result",
    "error_envelope_from_exception",
    "serialize_tool_error",
    "skill_error_result",
    "sse_error_event",
]
