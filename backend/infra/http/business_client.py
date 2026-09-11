"""infra.http.business_client — 调用 Java business-service 的统一 HTTP 客户端。

设计（对齐 infra/redis/client.py 的单例降级模式）：
- httpx.AsyncClient 单例，连接池复用（替代每请求裸建 client）
- 统一超时：连接 3s / 读 10s
- 幂等 GET 网络错误时重试 1 次；POST 不重试（非幂等）
- 自动注入 X-Internal-Token + X-Trace-Id（跨服务 trace 串联）
- 异常统一映射为 BusinessServiceError，调用方无需感知 httpx
"""
from __future__ import annotations

import asyncio
import threading
import time

import httpx

from backend.shared.logger import logger

_CONNECT_TIMEOUT = 3.0
_READ_TIMEOUT = 10.0
_GET_RETRIES = 1

_client: httpx.AsyncClient | None = None
_lock = threading.Lock()


class BusinessServiceError(Exception):
    """business-service 调用失败（网络错误或非 2xx 响应）。

    status_code 为 0 表示网络层失败（连接拒绝/超时等），
    否则为下游 HTTP 状态码。
    """

    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


def _get_client() -> httpx.AsyncClient:
    """获取 AsyncClient 单例（FastAPI 事件循环内复用连接池）。"""
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                _client = httpx.AsyncClient(
                    timeout=httpx.Timeout(
                        connect=_CONNECT_TIMEOUT,
                        read=_READ_TIMEOUT,
                        write=_READ_TIMEOUT,
                        pool=_CONNECT_TIMEOUT,
                    ),
                    limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
                )
    return _client


def _build_headers() -> dict[str, str]:
    """构造内部调用头：服务间令牌 + trace 透传。"""
    from backend.config.messaging import INTERNAL_API_TOKEN

    headers: dict[str, str] = {}
    if INTERNAL_API_TOKEN:
        headers["X-Internal-Token"] = INTERNAL_API_TOKEN

    try:
        from backend.observability.tracer import current_trace_context

        trace_id, _ = current_trace_context()
        if trace_id:
            headers["X-Trace-Id"] = trace_id
    except Exception:
        pass

    return headers


async def request_json(method: str, path: str, *, params=None, json_body=None) -> dict:
    """请求 business-service 并返回 JSON 响应。

    Args:
        method: "GET" 或 "POST"
        path: 以 / 开头的路径（如 /cs/conversations）
        params: query 参数
        json_body: POST 请求体

    Raises:
        BusinessServiceError: 网络失败或非 2xx 响应。
    """
    from backend.config.messaging import BUSINESS_SERVICE_URL

    url = f"{BUSINESS_SERVICE_URL}{path}"
    client = _get_client()
    attempts = _GET_RETRIES + 1 if method.upper() == "GET" else 1

    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            resp = await client.request(
                method.upper(), url, params=params, json=json_body,
                headers=_build_headers(),
            )
            if 200 <= resp.status_code < 300:
                return resp.json()
            last_error = BusinessServiceError(
                f"business-service {method} {path} -> {resp.status_code}",
                status_code=resp.status_code,
            )
            logger.warning(f"[BusinessClient] {last_error}")
        except httpx.HTTPError as e:
            last_error = BusinessServiceError(
                f"business-service {method} {path} network error: {e}"
            )
            logger.warning(f"[BusinessClient] {last_error} (attempt {attempt}/{attempts})")

        if attempt < attempts:
            await asyncio.sleep(0.3)

    raise last_error  # type: ignore[misc]


async def get_json(path: str, *, params=None) -> dict:
    """GET 请求（幂等，网络错误自动重试 1 次）。"""
    return await request_json("GET", path, params=params)


async def post_json(path: str, json_body: dict) -> dict:
    """POST 请求（非幂等，不重试）。"""
    return await request_json("POST", path, json_body=json_body)


# ── 同步变体 ─────────────────────────────────────────────
# 供 sync facade（如 StateTransitionService.apply）使用。
# 不能复用 AsyncClient 单例：调用方可能运行在专用 db_loop 线程，
# 共享 AsyncClient 会跨事件循环复用 keep-alive 连接导致
# "attached to a different loop"。httpx.Client 线程安全且无循环绑定。

_sync_client: httpx.Client | None = None
_sync_lock = threading.Lock()


def _get_sync_client() -> httpx.Client:
    global _sync_client
    if _sync_client is None:
        with _sync_lock:
            if _sync_client is None:
                _sync_client = httpx.Client(
                    timeout=httpx.Timeout(
                        connect=_CONNECT_TIMEOUT,
                        read=_READ_TIMEOUT,
                        write=_READ_TIMEOUT,
                        pool=_CONNECT_TIMEOUT,
                    ),
                )
    return _sync_client


def request_json_sync(method: str, path: str, *, params=None, json_body=None) -> dict:
    """request_json 的同步版本（语义一致：GET 重试 1 次，异常映射相同）。"""
    from backend.config.messaging import BUSINESS_SERVICE_URL

    url = f"{BUSINESS_SERVICE_URL}{path}"
    client = _get_sync_client()
    attempts = _GET_RETRIES + 1 if method.upper() == "GET" else 1

    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            resp = client.request(
                method.upper(), url, params=params, json=json_body,
                headers=_build_headers(),
            )
            if 200 <= resp.status_code < 300:
                return resp.json()
            last_error = BusinessServiceError(
                f"business-service {method} {path} -> {resp.status_code}",
                status_code=resp.status_code,
            )
            logger.warning(f"[BusinessClient] {last_error}")
        except httpx.HTTPError as e:
            last_error = BusinessServiceError(
                f"business-service {method} {path} network error: {e}"
            )
            logger.warning(f"[BusinessClient] {last_error} (attempt {attempt}/{attempts})")

        if attempt < attempts:
            time.sleep(0.3)

    raise last_error  # type: ignore[misc]


def get_json_sync(path: str, *, params=None) -> dict:
    """GET 同步请求。"""
    return request_json_sync("GET", path, params=params)


def post_json_sync(path: str, json_body: dict) -> dict:
    """POST 同步请求（非幂等，不重试）。"""
    return request_json_sync("POST", path, json_body=json_body)
