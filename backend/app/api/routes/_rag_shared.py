"""RAG 路由共享工具 — PR-2.x 从 rag.py 抽出。"""
import asyncio
import os, uuid, json, time
from asyncio import Queue
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import StreamingResponse
from backend.app.api.schemas import RAGAskRequest, ErrorResponse
from backend.app.api.deps import get_rag_pipeline, require_rag_ready, get_rag_status
from backend.rag.indexing.doc_registry import DocumentRegistry
# F7: 移除未使用的重依赖顶层导入（IncrementalIndexer/ProgressListener 导入链
# 含 langchain/tracer，拖慢所有 RAG 路由模块冷启动；各调用方按需自行导入）
from backend.rag.indexing.operation_log import DocumentOperationLogger
from backend.config import DOC_REGISTRY_PATH, DOC_OPERATION_LOG_PATH
from backend.config.rag import METADATA_SCHEMA_FINGERPRINT
from backend.shared.logger import logger

# ── Upload 进度队列（按 upload_id 索引） ───────────────────────
# 内存 dict，30 分钟未活动自动清理
_progress_queues: dict[str, Queue] = {}

def _sse_encode(event: str, data: dict) -> str:
    """SSE 编码：data 中已含 stage 字段，前端用单一 onmessage 解析。

    故意省略 'event: <name>' 字段 — 让所有消息走 EventSource 默认 message event
    触发 onmessage listener，避免 addEventListener 注册时机 race condition。

    故意用 ensure_ascii=True — StreamingResponse 默认 Content-Type 没 charset，
    非 ASCII 字节可能被客户端按 latin-1 解码导致乱码。ensure_ascii=True 让 SSE 流
    纯 ASCII（中文 → \\uXXXX），客户端 JSON.parse 自动还原为 unicode 字符串。
    """
    return f"data: {json.dumps(data, ensure_ascii=True)}\n\n"

router = APIRouter(prefix="/rag", tags=["知识库"])

_registry: DocumentRegistry | None = None

def _get_registry() -> DocumentRegistry:
    global _registry
    if _registry is None:
        _registry = DocumentRegistry(DOC_REGISTRY_PATH)
    return _registry


_op_logger: DocumentOperationLogger | None = None


def _get_op_logger() -> DocumentOperationLogger:
    global _op_logger
    if _op_logger is None:
        _op_logger = DocumentOperationLogger(DOC_OPERATION_LOG_PATH)
    return _op_logger


def _extract_source(request: Request) -> str:
    """提取操作来源：IP | User-Agent（auth 接入后可加 user_id）。"""
    client_host = request.client.host if request.client else "unknown"
    ua = request.headers.get("user-agent", "")
    return f"{client_host} | {ua}"


def _safe_log_op(
    doc_id: str, doc_name: str, operation: str, source: str,
    trace_id: str | None = None, batch_id: str | None = None,
    result: str = "success", detail: dict | None = None,
    duration_ms: int = 0,
) -> None:
    """记录操作日志，失败不影响主流程（审计日志写挂不能阻断业务）。"""
    try:
        _get_op_logger().log(
            doc_id=doc_id, doc_name=doc_name, operation=operation, source=source,
            trace_id=trace_id, batch_id=batch_id, result=result, detail=detail,
            duration_ms=duration_ms,
        )
    except Exception as e:
        logger.warning(f"[RAG] 记录操作日志失败 ({operation}): {e}")


from pydantic import BaseModel


class SearchRequest(BaseModel):
    query: str = ""


# 本模块的公开工具集合。注意：调用方必须用显式 from ... import xxx，
# 禁止 from ._rag_shared import * —— __all__ 会让星号导入只带走这几个名字，
# 静默丢掉 os/time/logger 等，且 except 里的 logger 也一并丢失，
# 导致异常兜底本身抛 NameError（历史故障：/rag/documents 500）
__all__ = [
    "_progress_queues", "_sse_encode",
    "_get_registry", "_get_op_logger", "_extract_source", "_safe_log_op",
    "SearchRequest",
]


