"""middleware/body_limit.py — 请求体上限（2026-11-08 安全审计 T6）。

背景：仓库与 APISIX 配置此前均无 body size 上限（审计
``bff-arraybuffer-body-unbounded`` / ``automations.webhook.body-buffer-
before-auth-no-size-limit`` / ``selection-funnel-unbounded-import-body``）。
边缘层由 APISIX ``client_max_body_size`` 兜底；本中间件是源内第二道：
在 endpoint 读取 body 之前按 Content-Length 拒绝，避免先整包读入内存。

分路径上限（字节）：
  - /webhooks/automations  1 MiB —— 外部回调负载只有事件 JSON；
  - /selection-funnel/import 32 MiB —— 选品表格导入；
  - /rag/upload            RAG_MAX_FILE_SIZE + multipart 开销余量（默认 50 MiB
                           + 16 KiB）——余量与路由层预检同源（F10），贴上限的
                           合法文件不被中间件误拒；文件体精确上限仍由路由的
                           流式字节计数强制执行；
  - 其余                   8 MiB —— 普通 JSON API。

已知边界：chunked 请求没有 Content-Length，源内无法预检；此时依赖
APISIX 边缘上限（nginx 按实际收包字节计数，chunked 同样生效）。
"""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from backend.config.rag import RAG_MAX_FILE_SIZE, RAG_UPLOAD_MULTIPART_OVERHEAD

DEFAULT_BODY_LIMIT = 8 * 1024 * 1024

_BODY_LIMIT_RULES: tuple[tuple[str, int], ...] = (
    ("/webhooks/automations", 1 * 1024 * 1024),
    ("/selection-funnel/import", 32 * 1024 * 1024),
    ("/rag/upload", RAG_MAX_FILE_SIZE * 1024 * 1024 + RAG_UPLOAD_MULTIPART_OVERHEAD),
)


def request_body_limit(path: str) -> int:
    """返回该路径允许的最大请求体字节数（未命中规则用默认值）。"""
    for prefix, limit in _BODY_LIMIT_RULES:
        if path.startswith(prefix):
            return limit
    return DEFAULT_BODY_LIMIT


def _payload_too_large(limit: int, declared: int) -> JSONResponse:
    return JSONResponse(
        status_code=413,
        content={
            "error": "PayloadTooLarge",
            "detail": (
                f"请求体超过上限：{declared} > {limit} bytes"
                "（如需更大上传请改对应路径上限，而不是绕过检查）"
            ),
        },
    )


async def body_size_limit_middleware(request: Request, call_next):
    """读取前按 Content-Length 拒绝超限请求（GET/HEAD 不检查）。"""
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        declared = request.headers.get("content-length")
        if declared and declared.isdigit():
            limit = request_body_limit(request.url.path)
            if int(declared) > limit:
                return _payload_too_large(limit, int(declared))
    return await call_next(request)
