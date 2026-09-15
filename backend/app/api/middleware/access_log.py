"""middleware/access_log.py — 请求级访问审计日志（网关审计链路的后端侧）

每请求一行 [Access] 日志，回答"谁、从哪个 IP、访问了什么端点、结果如何"：

    [Access] ip=1.2.3.4 user=1001 auth=jwt GET /api/chat?session_id=s1 -> 200 35.2ms trace=...

信任边界（与 docker-compose P3 网络边界一致：app:8000 仅绑 127.0.0.1，
外部流量必经 APISIX）：

  - IP：X-Forwarded-For 由 APISIX 以 $proxy_add_x_forwarded_for 追加，真实客户端
    IP 恒在最右一跳；客户端自带的前缀可伪造，因此**只取最后一跳**。
    直连 8000（本机调试）无 XFF，回退 request.client.host（=127.0.0.1）。
  - user_id / auth_type：读网关验签后注入的身份头（enforce 下不可伪造）。
    注意 /api/auth/*、/api/sys/* 白名单路由不挂 gateway-auth，头来自客户端
    原样透传——日志如实记录该值，审计解读时对这两段路由不可当真。
  - trace_id：网关生成/透传的 X-Trace-Id，用于关联 ai.trace_records 的
    agent 执行链路。

不打点的噪音流量：OPTIONS 预检、/health、/metrics（探测与抓取）。
日志级别 info，走全局 logger（console + LOG_FILE）。
"""
import time

from fastapi import Request

from backend.config.auth import AUTH_TYPE_HEADER, USER_ID_HEADER
from backend.shared.logger import logger

# 探测/抓取类端点：高频且无审计价值，打点只会稀释日志
_SKIP_PATHS = frozenset({"/health", "/metrics", "/ops"})

# 身份/链路字段缺失时的占位符（避免逐字段判空）
_DASH = "-"


def client_ip(request: Request) -> str:
    """客户端 IP：XFF 最后一跳（网关追加的真实来源），直连时回退 peer。

    为什么取最后一跳而不是第一跳：XFF 语义是"追加"，客户端可自带任意前缀，
    唯一不可伪造的是 APISIX 附加的最右一个（$remote_addr）。
    """
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[-1].strip()
    return request.client.host if request.client else _DASH


async def access_log_middleware(request: Request, call_next):
    """HTTP 中间件：请求完成后记录一行访问日志（含被 401/429/503 拒绝的请求）。

    注意注册顺序必须在 api_key_middleware **外层**（server.py 注册序在其后），
    否则认证中间件短路时本层不在 call_next 链上，被拒请求会漏记。
    流式响应（SSE）记的是首包耗时，连接总时长由网关 access log 的
    duration 字段覆盖。
    """
    if request.method == "OPTIONS" or request.url.path in _SKIP_PATHS:
        return await call_next(request)

    start = time.perf_counter()
    status = 500  # call_next 抛异常（含 handler 再失败）时的兜底状态码
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000
        path = request.url.path
        query = request.url.query
        logger.info(
            "[Access] ip=%s user=%s auth=%s %s %s%s -> %s %.1fms trace=%s",
            client_ip(request),
            request.headers.get(USER_ID_HEADER, "") or _DASH,
            request.headers.get(AUTH_TYPE_HEADER, "") or _DASH,
            request.method,
            path,
            f"?{query}" if query else "",
            status,
            elapsed_ms,
            request.headers.get("X-Trace-Id", "") or _DASH,
        )
