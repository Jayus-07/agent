"""RAG Upload 共享基础、常量、异常与动态门面透明代理。"""
from __future__ import annotations

import logging
import math
import os
import re
import sys
from typing import Any

from backend.app.api.deps import get_rag_pipeline, require_rag_ready
from backend.rag.authz import RagAuthorization, RagAuthorizationError
from backend.rag.preprocessing.parser import PARSABLE_EXTS
from backend.app.api.routes._rag_shared import (
    _extract_source,
    _get_op_logger,
    _get_registry,
    _progress_owners,
    _progress_queues,
    _safe_log_op,
    _sse_encode,
    sanitize_doc_row,
)
from backend.config.rag import (
    RAG_MAX_CONCURRENT_INDEX,
    RAG_MAX_FILE_SIZE,
    RAG_OCR_MAX_PAGES_UPLOAD_HARD_LIMIT,
    RAG_OCR_MIN_TEXT_CHARS,
    RAG_PDF_MAX_PAGES,
    RAG_SSE_REDIS_EMPTY_GRACE_SECONDS,
    RAG_SSE_REDIS_ERROR_GRACE_SECONDS,
    RAG_SSE_REDIS_KEEPALIVE_SECONDS,
    RAG_SSE_REDIS_POLL_MAX_SECONDS,
    RAG_SSE_REDIS_POLL_SECONDS,
    RAG_TMP_DIR,
    RAG_UPLOAD_CHUNK_SIZE,
    RAG_UPLOAD_EMIT_BYTES,
    RAG_UPLOAD_EMIT_MS,
    RAG_UPLOAD_LOCK_HEARTBEAT_SECONDS,
    RAG_UPLOAD_LOCK_STALE_SECONDS,
    RAG_UPLOAD_PATH_GUARD,
    RAG_UPLOAD_PROGRESS_GC_INTERVAL_SECONDS,
)
from backend.shared.logger import logger

class FacadeProxy:
    """透明门面代理：
    优先从门面模块 (backend.app.api.routes.rag_upload) 解析被 monkeypatch 替换的属性，
    如果未被 patch 或门面未就绪，则回退到本地默认实现。
    """
    def __init__(self, facade_module: str) -> None:
        self._facade_module = facade_module
        self._defaults: dict[str, Any] = {}

    def register(self, name: str, default_val: Any) -> None:
        self._defaults[name] = default_val

    def __getattr__(self, name: str) -> Any:
        mod = sys.modules.get(self._facade_module)
        default_val = self._defaults.get(name)
        if mod is not None and hasattr(mod, name):
            val = getattr(mod, name)
            if default_val is None or val is not default_val:
                return val
        if default_val is not None:
            return default_val
        if mod is not None and hasattr(mod, name):
            return getattr(mod, name)
        raise AttributeError(f"'{self._facade_module}' has no attribute '{name}'")


facade = FacadeProxy("backend.app.api.routes.rag_upload")

_celery_routed: set = set()
_background_index_tasks: set[asyncio.Task] = set()

# 默认注册共享引用
facade.register("_celery_routed", _celery_routed)
facade.register("_background_index_tasks", _background_index_tasks)
facade.register("require_rag_ready", require_rag_ready)
facade.register("RagAuthorization", RagAuthorization)
facade.register("RagAuthorizationError", RagAuthorizationError)
facade.register("logger", logger)
facade.register("_get_registry", _get_registry)
facade.register("_get_op_logger", _get_op_logger)
facade.register("_safe_log_op", _safe_log_op)
facade.register("_progress_queues", _progress_queues)
facade.register("_progress_owners", _progress_owners)
facade.register("_extract_source", _extract_source)
facade.register("_sse_encode", _sse_encode)
facade.register("sanitize_doc_row", sanitize_doc_row)
facade.register("get_rag_pipeline", get_rag_pipeline)
facade.register("_DOCS_DIRECTORY", None)

class FileLockedByOtherError(Exception):
    """同文件正在被另一个请求处理,当前请求拒绝（避免双写向量库）。"""


class UploadIdempotencyConflict(Exception):
    """同幂等键不同内容的上传（或与在途任务冲突）——HTTP 层映射 409。"""

    def __init__(self, message: str, *, upload_id: str = ""):
        super().__init__(message)
        self.upload_id = upload_id


LOCK_STALE_SECONDS = RAG_UPLOAD_LOCK_STALE_SECONDS


LOCK_HEARTBEAT_SECONDS = RAG_UPLOAD_LOCK_HEARTBEAT_SECONDS


_INDEX_CONCURRENCY_LIMIT = RAG_MAX_CONCURRENT_INDEX


MANAGEMENT_UPLOAD_ALLOWED_EXTS: frozenset[str] = frozenset({
    ".pdf", ".docx", ".xlsx", ".txt", ".md", ".markdown",
})

MANAGEMENT_UPLOAD_FORMAT_LABEL = (
    "PDF、Word（.docx）、Excel（.xlsx）、TXT（.txt）和 Markdown（.md/.markdown）"
)

_MIME_BY_EXT: dict[str, set[str]] = {
    "pdf":      {"application/pdf"},
    "md":       {"text/markdown", "text/plain"},
    "markdown": {"text/markdown", "text/plain"},
    "txt":      {"text/plain"},
    "docx":     {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    "xlsx":     {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
}

ALLOWED_MIME_TYPES: dict[str, set[str]] = {
    ext: mimes for ext, mimes in _MIME_BY_EXT.items()
    if f".{ext}" in PARSABLE_EXTS
    and f".{ext}" in MANAGEMENT_UPLOAD_ALLOWED_EXTS
}

SUPPORTED_EXTS: frozenset[str] = frozenset(ALLOWED_MIME_TYPES.keys())

for _k, _v in [
    ("PARSABLE_EXTS", PARSABLE_EXTS),
    ("MANAGEMENT_UPLOAD_ALLOWED_EXTS", MANAGEMENT_UPLOAD_ALLOWED_EXTS),
    ("MANAGEMENT_UPLOAD_FORMAT_LABEL", MANAGEMENT_UPLOAD_FORMAT_LABEL),
    ("_MIME_BY_EXT", _MIME_BY_EXT),
    ("ALLOWED_MIME_TYPES", ALLOWED_MIME_TYPES),
    ("SUPPORTED_EXTS", SUPPORTED_EXTS),
]:
    facade.register(_k, _v)


PROGRESS_QUEUE_TTL_SECONDS = math.ceil(RAG_SSE_REDIS_POLL_MAX_SECONDS)


SSE_KEEPALIVE_TIMEOUT_SECONDS = RAG_SSE_REDIS_KEEPALIVE_SECONDS


_BATCH_ID_MAX_LENGTH = 64


_BATCH_ID_PATTERN = re.compile(rf"[A-Za-z0-9_.:-]{{1,{_BATCH_ID_MAX_LENGTH}}}")


_MAGIC_PREFIX_BYTES = max(
    len(b"%PDF-"), len(b"PK\x03\x04"), len(b"\xff\xfe"), len(b"\xfe\xff"),
)


_SSE_REDIS_POLL_SECONDS = RAG_SSE_REDIS_POLL_SECONDS


_SSE_REDIS_POLL_MAX_SECONDS = RAG_SSE_REDIS_POLL_MAX_SECONDS


_SSE_REDIS_EMPTY_GRACE_SECONDS = RAG_SSE_REDIS_EMPTY_GRACE_SECONDS


_SSE_REDIS_ERROR_GRACE_SECONDS = RAG_SSE_REDIS_ERROR_GRACE_SECONDS


_SSE_REDIS_KEEPALIVE_SECONDS = RAG_SSE_REDIS_KEEPALIVE_SECONDS


_SSE_TERMINAL_STAGES = ("done", "error", "duplicate")

for _k, _v in [
    ("PARSABLE_EXTS", PARSABLE_EXTS),
    ("MANAGEMENT_UPLOAD_ALLOWED_EXTS", MANAGEMENT_UPLOAD_ALLOWED_EXTS),
    ("MANAGEMENT_UPLOAD_FORMAT_LABEL", MANAGEMENT_UPLOAD_FORMAT_LABEL),
    ("_MIME_BY_EXT", _MIME_BY_EXT),
    ("ALLOWED_MIME_TYPES", ALLOWED_MIME_TYPES),
    ("SUPPORTED_EXTS", SUPPORTED_EXTS),
    ("LOCK_STALE_SECONDS", LOCK_STALE_SECONDS),
    ("LOCK_HEARTBEAT_SECONDS", LOCK_HEARTBEAT_SECONDS),
    ("_INDEX_CONCURRENCY_LIMIT", _INDEX_CONCURRENCY_LIMIT),
    ("PROGRESS_QUEUE_TTL_SECONDS", PROGRESS_QUEUE_TTL_SECONDS),
    ("SSE_KEEPALIVE_TIMEOUT_SECONDS", SSE_KEEPALIVE_TIMEOUT_SECONDS),
    ("_BATCH_ID_MAX_LENGTH", _BATCH_ID_MAX_LENGTH),
    ("_BATCH_ID_PATTERN", _BATCH_ID_PATTERN),
    ("_MAGIC_PREFIX_BYTES", _MAGIC_PREFIX_BYTES),
    ("_SSE_REDIS_POLL_SECONDS", _SSE_REDIS_POLL_SECONDS),
    ("_SSE_REDIS_POLL_MAX_SECONDS", _SSE_REDIS_POLL_MAX_SECONDS),
    ("_SSE_REDIS_EMPTY_GRACE_SECONDS", _SSE_REDIS_EMPTY_GRACE_SECONDS),
    ("_SSE_REDIS_ERROR_GRACE_SECONDS", _SSE_REDIS_ERROR_GRACE_SECONDS),
    ("_SSE_REDIS_KEEPALIVE_SECONDS", _SSE_REDIS_KEEPALIVE_SECONDS),
    ("_SSE_TERMINAL_STAGES", _SSE_TERMINAL_STAGES),
]:
    facade.register(_k, _v)

__all__ = [
    "facade",
    "FacadeProxy",
    "FileLockedByOtherError",
    "UploadIdempotencyConflict",
    "LOCK_STALE_SECONDS",
    "LOCK_HEARTBEAT_SECONDS",
    "_INDEX_CONCURRENCY_LIMIT",
    "MANAGEMENT_UPLOAD_ALLOWED_EXTS",
    "MANAGEMENT_UPLOAD_FORMAT_LABEL",
    "_MIME_BY_EXT",
    "ALLOWED_MIME_TYPES",
    "SUPPORTED_EXTS",
    "PROGRESS_QUEUE_TTL_SECONDS",
    "SSE_KEEPALIVE_TIMEOUT_SECONDS",
    "_BATCH_ID_MAX_LENGTH",
    "_BATCH_ID_PATTERN",
    "_MAGIC_PREFIX_BYTES",
    "_SSE_REDIS_POLL_SECONDS",
    "_SSE_REDIS_POLL_MAX_SECONDS",
    "_SSE_REDIS_EMPTY_GRACE_SECONDS",
    "_SSE_REDIS_ERROR_GRACE_SECONDS",
    "_SSE_REDIS_KEEPALIVE_SECONDS",
    "_SSE_TERMINAL_STAGES",
    "_celery_routed",
    "_background_index_tasks",
    "require_rag_ready",
    "RagAuthorization",
    "RagAuthorizationError",
    "logger",
    "_get_registry",
    "_get_op_logger",
    "_safe_log_op",
    "_progress_queues",
    "_progress_owners",
    "_extract_source",
    "_sse_encode",
    "sanitize_doc_row",
    "get_rag_pipeline",
    "PARSABLE_EXTS",
    "RAG_MAX_CONCURRENT_INDEX",
    "RAG_MAX_FILE_SIZE",
    "RAG_OCR_MAX_PAGES_UPLOAD_HARD_LIMIT",
    "RAG_OCR_MIN_TEXT_CHARS",
    "RAG_PDF_MAX_PAGES",
    "RAG_SSE_REDIS_EMPTY_GRACE_SECONDS",
    "RAG_SSE_REDIS_ERROR_GRACE_SECONDS",
    "RAG_SSE_REDIS_KEEPALIVE_SECONDS",
    "RAG_SSE_REDIS_POLL_MAX_SECONDS",
    "RAG_SSE_REDIS_POLL_SECONDS",
    "RAG_TMP_DIR",
    "RAG_UPLOAD_CHUNK_SIZE",
    "RAG_UPLOAD_EMIT_BYTES",
    "RAG_UPLOAD_EMIT_MS",
    "RAG_UPLOAD_LOCK_HEARTBEAT_SECONDS",
    "RAG_UPLOAD_LOCK_STALE_SECONDS",
    "RAG_UPLOAD_PATH_GUARD",
    "RAG_UPLOAD_PROGRESS_GC_INTERVAL_SECONDS",
]

