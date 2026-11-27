"""RAG 文档上传与索引调度 API 路由门面。

本模块遵照单一职责原则（SRP）解耦，物理实现拆分至 `rag_upload_subroutes` 子领域模块：
- `base.py`: 异常定义、全局常量配置与动态门面透明代理
- `lock.py`: 文件并发锁与心跳租约管理
- `validation.py`: 文件安全校验、格式探测与OCR预算
- `cleanup.py`: 临时文件清理与垃圾回收
- `progress.py`: 进度持久化、Redis事件流与所有者鉴权
- `indexing.py`: 核心索引调度、Celery分发与同步流水线执行引擎
- `failures.py`: 质检与失败信息摘要明细
- `routes.py`: 外部 HTTP API 路由端点集合

本文件作为统一路由聚合入口与向后兼容门面，保留历史导出符号与 Monkeypatch 穿透能力。
"""
from __future__ import annotations

from backend.app.api.routes.rag_upload_subroutes import (
    base,
    cleanup,
    failures,
    indexing,
    lock,
    progress,
    routes,
    validation,
)

# 重新导出所有契约、依赖、函数与路由符号（保持 100% 外部导入及 monkeypatch 兼容）
FileLockedByOtherError = base.FileLockedByOtherError
UploadIdempotencyConflict = base.UploadIdempotencyConflict
LOCK_STALE_SECONDS = base.LOCK_STALE_SECONDS
LOCK_HEARTBEAT_SECONDS = base.LOCK_HEARTBEAT_SECONDS
_INDEX_CONCURRENCY_LIMIT = base._INDEX_CONCURRENCY_LIMIT
PARSABLE_EXTS = base.PARSABLE_EXTS
MANAGEMENT_UPLOAD_ALLOWED_EXTS = base.MANAGEMENT_UPLOAD_ALLOWED_EXTS
MANAGEMENT_UPLOAD_FORMAT_LABEL = base.MANAGEMENT_UPLOAD_FORMAT_LABEL
_MIME_BY_EXT = base._MIME_BY_EXT
ALLOWED_MIME_TYPES = base.ALLOWED_MIME_TYPES
SUPPORTED_EXTS = base.SUPPORTED_EXTS
PROGRESS_QUEUE_TTL_SECONDS = base.PROGRESS_QUEUE_TTL_SECONDS
SSE_KEEPALIVE_TIMEOUT_SECONDS = base.SSE_KEEPALIVE_TIMEOUT_SECONDS
_BATCH_ID_MAX_LENGTH = base._BATCH_ID_MAX_LENGTH
_BATCH_ID_PATTERN = base._BATCH_ID_PATTERN
_MAGIC_PREFIX_BYTES = base._MAGIC_PREFIX_BYTES
_SSE_REDIS_POLL_SECONDS = base._SSE_REDIS_POLL_SECONDS
_SSE_REDIS_POLL_MAX_SECONDS = base._SSE_REDIS_POLL_MAX_SECONDS
_SSE_REDIS_EMPTY_GRACE_SECONDS = base._SSE_REDIS_EMPTY_GRACE_SECONDS
_SSE_REDIS_ERROR_GRACE_SECONDS = base._SSE_REDIS_ERROR_GRACE_SECONDS
_SSE_REDIS_KEEPALIVE_SECONDS = base._SSE_REDIS_KEEPALIVE_SECONDS
_SSE_TERMINAL_STAGES = base._SSE_TERMINAL_STAGES
_lock_age_seconds = lock._lock_age_seconds
_try_create_lock = lock._try_create_lock
acquire_index_lock = lock.acquire_index_lock
release_index_lock = lock.release_index_lock
_start_lock_heartbeat = lock._start_lock_heartbeat
_validate_ocr_page_budget = validation._validate_ocr_page_budget
_pdf_has_text_layer = validation._pdf_has_text_layer
sha256_of_file = validation.sha256_of_file
_validate_mime = validation._validate_mime
_magic_matches = validation._magic_matches
_cleanup_candidate_artifacts = cleanup._cleanup_candidate_artifacts
cleanup_stale_upload_artifacts = cleanup.cleanup_stale_upload_artifacts
cleanup_expired_progress_queues = cleanup.cleanup_expired_progress_queues
progress_queue_gc_loop = cleanup.progress_queue_gc_loop
_finalize_upload_queue = cleanup._finalize_upload_queue
_cleanup_failed_upload_sync = cleanup._cleanup_failed_upload_sync
_cleanup_failed_upload = cleanup._cleanup_failed_upload
_remove_bak = cleanup._remove_bak
_write_progress_redis = progress._write_progress_redis
_read_progress_redis = progress._read_progress_redis
_run_still_active = progress._run_still_active
_redis_poll_events = progress._redis_poll_events
_redis_poll_stream_response = progress._redis_poll_stream_response
_progress_owner_of = progress._progress_owner_of
_ensure_progress_owner = progress._ensure_progress_owner
_mark_registry_failed = indexing._mark_registry_failed
_get_index_semaphore = indexing._get_index_semaphore
_latest_upload_trace_id = indexing._latest_upload_trace_id
_settle_index_result = indexing._settle_index_result
_dispatch_index_to_celery = indexing._dispatch_index_to_celery
_dispatch_index_with_idempotency = indexing._dispatch_index_with_idempotency
_resolve_replayed_dispatch = indexing._resolve_replayed_dispatch
_run_index_background = indexing._run_index_background
_reverify_publish_authorization = indexing._reverify_publish_authorization
_latest_processing_trace_for_doc = indexing._latest_processing_trace_for_doc
_active_document_artifacts_complete = indexing._active_document_artifacts_complete
_active_vector_ids_for_doc = indexing._active_vector_ids_for_doc
_do_index_sync = indexing._do_index_sync
sync_upload_impl = indexing.sync_upload_impl
_quality_failure_summary = failures._quality_failure_summary
_load_quality_failure_details = failures._load_quality_failure_details
upload_document = routes.upload_document
list_upload_failures = routes.list_upload_failures
stream_upload_progress = routes.stream_upload_progress
_get_registry = getattr(base, '_get_registry', None)
_get_op_logger = getattr(base, '_get_op_logger', None)
_safe_log_op = getattr(base, '_safe_log_op', None)
_progress_queues = getattr(base, '_progress_queues', None)
_progress_owners = getattr(base, '_progress_owners', None)
_extract_source = getattr(base, '_extract_source', None)
_sse_encode = getattr(base, '_sse_encode', None)
sanitize_doc_row = getattr(base, 'sanitize_doc_row', None)
get_rag_pipeline = getattr(base, 'get_rag_pipeline', None)
_DOCS_DIRECTORY = getattr(base, '_DOCS_DIRECTORY', None)
_celery_routed = base._celery_routed
_background_index_tasks = base._background_index_tasks
require_rag_ready = base.require_rag_ready
RagAuthorization = base.RagAuthorization
RagAuthorizationError = base.RagAuthorizationError
RAG_SSE_REDIS_POLL_MAX_SECONDS = base.RAG_SSE_REDIS_POLL_MAX_SECONDS
_index_semaphore = None
logger = base.logger

# 导出主路由器
router = routes.router
