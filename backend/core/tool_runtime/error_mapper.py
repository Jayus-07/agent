"""tool_runtime/error_mapper.py — 底层异常 → 标准 ToolStatus（归一化唯一出口）

LangGraph / Domain 不应感知 httpx.TimeoutException / ConnectError /
RedisError / SQLAlchemyError / HTTP 429 / 502 / 503 / Python TimeoutError ——
本模块把它们全部映射为 ToolStatus + 错误码 + 是否可重试。

重试规则（§9，在线请求保守原则）：
  Connect error / Connect timeout → 最多快速 retry 1 次（策略层封顶）
  502 / 503                       → 最多 retry 1 次
  429                             → 参考 Retry-After 与剩余预算
  Read timeout                    → 默认不 retry
  400 / 422 / 401 / 403 / 404     → 不 retry
  500（上游代码 bug）/ 业务校验失败  → 不 retry

原始异常始终挂在 ToolResult.original_exception 上供日志/trace 使用，
用户侧只见 user_friendly_message()。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from backend.core.tool_runtime.models import ToolStatus

# 可重试的连接类状态（策略 retries 封顶后再生效）
RETRYABLE_STATUSES = frozenset({
    ToolStatus.UNAVAILABLE,   # connect error / 502 / 503
    ToolStatus.RATE_LIMITED,  # 429（还要看 Retry-After 与剩余预算）
})

# 连接超时（可快速重试一次）；与读超时区分
_CONNECT_TIMEOUT_HINTS = ("connect timeout", "connection timeout", "getaddrinfo", "dns")


@dataclass
class ErrorClassification:
    status: ToolStatus
    error_code: str
    error_message: str
    retryable: bool
    retry_after_ms: float | None = None


def _http_status_of(exc: BaseException) -> int | None:
    """从异常中提取 HTTP 状态码（httpx.HTTPStatusError / 自定义 attrs）。"""
    resp = getattr(exc, "response", None)
    code = getattr(resp, "status_code", None)
    if isinstance(code, int):
        return code
    return None


def _retry_after_ms_of(exc: BaseException) -> float | None:
    resp = getattr(exc, "response", None)
    headers = getattr(resp, "headers", None)
    if not headers:
        return None
    try:
        return float(headers.get("retry-after", "")) * 1000
    except (TypeError, ValueError):
        return None


def map_exception(exc: BaseException) -> ErrorClassification:
    """任意异常 → ErrorClassification。永不抛出（映射失败按 FAILED 兜底）。"""
    try:
        return _map_exception(exc)
    except Exception:  # pragma: no cover — 映射器自身的 bug 不允许外溢
        return ErrorClassification(ToolStatus.FAILED, "mapped_error", str(exc)[:200], False)


def _map_exception(exc: BaseException) -> ErrorClassification:
    msg = str(exc).lower()
    name = type(exc).__name__

    # ── 取消：调用方主动放弃，不是错误（executor 不重试，向上传播由 wait_for 处理）──
    if isinstance(exc, asyncio.CancelledError):
        return ErrorClassification(ToolStatus.FAILED, "cancelled", str(exc), False)

    # ── httpx 家族（导入失败则跳过该分支）──
    try:
        import httpx
    except ImportError:  # pragma: no cover
        httpx = None

    if httpx is not None and isinstance(exc, httpx.HTTPError):
        status_code = _http_status_of(exc)
        if status_code is not None:
            return _classify_http_status(status_code, _retry_after_ms_of(exc), msg)
        if isinstance(exc, httpx.ConnectTimeout):
            return ErrorClassification(ToolStatus.TIMEOUT, "connect_timeout", str(exc)[:200], True)
        if isinstance(exc, httpx.TimeoutException):
            # 读超时：请求可能已在服务端执行，默认不重试
            return ErrorClassification(ToolStatus.TIMEOUT, "read_timeout", str(exc)[:200], False)
        if isinstance(exc, httpx.ConnectError):
            return ErrorClassification(ToolStatus.UNAVAILABLE, "connect_error", str(exc)[:200], True)
        return ErrorClassification(ToolStatus.UNAVAILABLE, "http_transport_error", str(exc)[:200], True)

    # ── HTTPStatusError 兜底（httpx 已在上方处理；其他库的同名异常）──
    status_code = _http_status_of(exc)
    if status_code is not None:
        return _classify_http_status(status_code, _retry_after_ms_of(exc), msg)

    # ── 超时 ──
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        # asyncio.wait_for 触发的整体超时无法区分连接/读阶段，保守不重试
        return ErrorClassification(ToolStatus.TIMEOUT, "timeout", str(exc)[:200], False)

    # ── 常见基础设施异常（按异常名/消息匹配，避免重量级 import）──
    infra_markers = (
        ("connection refused", "connect_error", True),
        ("connection reset", "connect_error", True),
        ("broken pipe", "connect_error", True),
        ("connection aborted", "connect_error", True),
        ("connection timed out", "connect_timeout", True),
        ("name or service not known", "dns_error", True),
        ("temporarily unavailable", "service_unavailable", True),
        ("service unavailable", "service_unavailable", True),
    )
    for hint, code, retryable in infra_markers:
        if hint in msg:
            return ErrorClassification(ToolStatus.UNAVAILABLE, code, str(exc)[:200], retryable)

    if "sqlalchemy" in name.lower() or "rediserror" in name.lower() or "redis" in name.lower():
        return ErrorClassification(ToolStatus.UNAVAILABLE, "infra_error", str(exc)[:200], True)

    if "permission" in msg or "unauthorized" in msg or "forbidden" in msg or "权限" in str(exc):
        return ErrorClassification(ToolStatus.UNAUTHORIZED, "unauthorized", str(exc)[:200], False)

    if "not found" in msg or "不存在" in str(exc) or "no such" in msg:
        return ErrorClassification(ToolStatus.FAILED, "not_found", str(exc)[:200], False)

    if "invalid" in msg or "参数" in str(exc) or "validation" in msg:
        return ErrorClassification(ToolStatus.INVALID_REQUEST, "invalid_request", str(exc)[:200], False)

    # 默认：上游业务失败，重试无意义
    return ErrorClassification(ToolStatus.FAILED, "internal_error", str(exc)[:200], False)


def _classify_http_status(status_code: int, retry_after_ms: float | None, msg: str) -> ErrorClassification:
    if status_code == 429:
        return ErrorClassification(ToolStatus.RATE_LIMITED, "rate_limited", msg[:200], True, retry_after_ms)
    if status_code in (502, 503):
        return ErrorClassification(ToolStatus.UNAVAILABLE, f"http_{status_code}", msg[:200], True)
    if status_code in (400, 422):
        return ErrorClassification(ToolStatus.INVALID_REQUEST, f"http_{status_code}", msg[:200], False)
    if status_code in (401, 403):
        return ErrorClassification(ToolStatus.UNAUTHORIZED, f"http_{status_code}", msg[:200], False)
    if status_code == 404:
        return ErrorClassification(ToolStatus.FAILED, "http_404", msg[:200], False)
    if 500 <= status_code < 600:
        return ErrorClassification(ToolStatus.FAILED, f"http_{status_code}", msg[:200], False)
    return ErrorClassification(ToolStatus.FAILED, f"http_{status_code}", msg[:200], False)
