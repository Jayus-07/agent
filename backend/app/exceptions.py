"""全局异常处理与统一失败协议适配。"""
import traceback

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.shared.logger import logger
from backend.memory.database import MemoryDatabaseUnavailable
from backend.shared.error_protocol import (
    ErrorCode,
    ErrorEnvelope,
    error_envelope_from_exception,
)


def _http_payload(envelope: ErrorEnvelope) -> dict:
    """返回新协议字段，同时保留旧 error/detail 字段兼容现有客户端。"""
    payload = envelope.to_dict()
    payload["error"] = envelope.code.value
    payload["detail"] = envelope.message
    return payload


async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """HTTP 失败保持状态码，并统一为安全错误封套。"""
    envelope = error_envelope_from_exception(exc, source="http")
    return JSONResponse(
        status_code=exc.status_code,
        content=_http_payload(envelope),
    )


async def request_validation_exception_handler(
    request: Request, exc: RequestValidationError
):
    """请求体/查询参数校验失败：保持 422，响应改用统一安全协议。"""
    envelope = ErrorEnvelope(
        code=ErrorCode.INVALID_PARAM,
        retryable=False,
        handoff_available=False,
        message="请求参数有误，请检查后重试。",
        source="http",
    )
    return JSONResponse(
        status_code=422,
        content=_http_payload(envelope),
    )


async def memory_db_unavailable_handler(request: Request, exc: MemoryDatabaseUnavailable):
    """记忆库配置缺失/不可用 → 503（而非 500 兜底）。

    完整信息（host/dbname/user）只写日志，不进 HTTP 响应体，避免泄露基础设施细节；
    响应里给出可操作指引，让调用方一眼看出是配置问题而不是"没有数据"。
    """
    logger.error(f"[MemoryDB] {request.method} {request.url.path} → {exc}")
    envelope = ErrorEnvelope(
        code=ErrorCode.UPSTREAM_UNAVAILABLE,
        retryable=True,
        handoff_available=False,
        message="记忆库暂时不可用，请稍后重试。",
        source="http",
    )
    return JSONResponse(
        status_code=503,
        content=_http_payload(envelope),
    )


async def global_exception_handler(request: Request, exc: Exception):
    """非业务异常的兜底：记录堆栈 → 返回 500（避免泄露内部信息到 detail）"""
    logger.error(
        f"[Unhandled] {request.method} {request.url.path} → "
        f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
    )
    envelope = error_envelope_from_exception(exc, source="http")
    return JSONResponse(
        status_code=500,
        content=_http_payload(envelope),
    )
