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
