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
    ProtocolError,
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
    """请求体/查询参数校验失败：保持 422，响应改用统一安全协议。

    2026-09-22 排障修正：此前 errors() 详情被吞，客户端只见「请求参数有误」
    无法定位字段 —— 现将字段级错误落日志（对外响应不变，不泄露内部结构，
    仅记录 path 与 pydantic 错误要点）。

    2026-09-22 Chat/RAG 收口：聊天输入超长（question > CHAT_INPUT_MAX_CHARS）
    给出明确业务语义（details.reason=CHAT_INPUT_TOO_LARGE + limit_chars），
    不再让用户看到裸 422「请求参数有误」。
    """
    errors = exc.errors()
    logger.error(
        "[RequestValidation] %s %s → %s",
        request.method, request.url.path,
        [(e.get("loc"), e.get("msg"), e.get("type")) for e in errors],
    )
    envelope = ErrorEnvelope(
        code=ErrorCode.INVALID_PARAM,
        retryable=False,
        handoff_available=False,
        message="请求参数有误，请检查后重试。",
        source="http",
    )
    # 聊天输入超长：明确的业务错误语义（引导走知识库上传），实现细节不外泄
    if any(
        "question" in (e.get("loc") or ())
        and e.get("type") in ("string_too_long", "length_error", "value_error")
        for e in errors
    ):
        from backend.config.chat_input import CHAT_INPUT_MAX_CHARS
        envelope = ErrorEnvelope(
            code=ErrorCode.INVALID_PARAM,
            retryable=False,
            handoff_available=False,
            message="输入内容过长，请缩短内容或通过知识库文件上传处理。",
            source="http",
            details={"reason": "CHAT_INPUT_TOO_LARGE",
                     "limit_chars": CHAT_INPUT_MAX_CHARS},
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


# B1-F：业务错误码 → HTTP 状态（全局兜底不再一律 500——请求超时、上游
# 不可用、限流等与普通内部错误的 HTTP 语义必须可区分）。未列出的码保持 500。
_ERROR_CODE_HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.INVALID_PARAM: 400,
    ErrorCode.PERMISSION_DENIED: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.TIMEOUT: 504,
    ErrorCode.UPSTREAM_UNAVAILABLE: 503,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.IDEMPOTENCY_CONFLICT: 409,
    ErrorCode.BUDGET_EXCEEDED: 429,
    # 499 仅用于 HTTP 响应尚未建立时的服务端取消映射；SSE 已建立连接后
    # 无法再改变 HTTP 状态，取消语义由 REQUEST_CANCELLED 终帧表达。
    ErrorCode.REQUEST_CANCELLED: 499,
}


async def global_exception_handler(request: Request, exc: Exception):
    """非业务异常的兜底：记录堆栈 → 按错误封套映射状态码（避免泄露内部信息到 detail）"""
    logger.error(
        f"[Unhandled] {request.method} {request.url.path} → "
        f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
    )
    envelope = error_envelope_from_exception(exc, source="http")
    # 通用 ValueError/KeyError 常表示服务端状态或代码错误，不可据此降为
    # 400/404；非 500 状态只由显式协议/HTTP 或其他明确业务异常触发。
    if not isinstance(exc, ProtocolError) and envelope.code in (
        ErrorCode.INVALID_PARAM,
        ErrorCode.NOT_FOUND,
    ):
        envelope = error_envelope_from_exception(
            ProtocolError(ErrorCode.INTERNAL_ERROR, source="http"),
            source="http",
        )
    status_code = _ERROR_CODE_HTTP_STATUS.get(envelope.code, 500)
    return JSONResponse(
        status_code=status_code,
        content=_http_payload(envelope),
    )
