"""PG Trace Mirror — 异步写入 ai.trace_records。

trace_writer worker 运行在 daemon 线程，通过 run_sync() 桥接到专属 event loop
执行 async PG 操作。仅在 TRACE_PG_MIRROR_ENABLED=true 时激活。
"""
from __future__ import annotations

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from backend.config.observability import TRACE_PG_MIRROR_ENABLED
from backend.shared.logger import logger

_loop: asyncio.AbstractEventLoop | None = None
_thread: threading.Thread | None = None


def _ensure_loop() -> asyncio.AbstractEventLoop:
    """惰性启动后台 event loop 线程（首次写入时初始化）。"""
    global _loop, _thread
    if _loop is not None and _loop.is_running():
        return _loop
    _loop = asyncio.new_event_loop()
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pg-sink")
    _loop.set_default_executor(executor)
    _thread = threading.Thread(
        target=lambda: asyncio.set_event_loop(_loop) or _loop.run_forever(),
        daemon=True,
        name="pg-sink-loop",
    )
    _thread.start()
    return _loop


def run_sync(coro):
    """从 sync 上下文提交协程到后台 loop 并阻塞等待结果。"""
    loop = _ensure_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=15)


async def _write_one(data: dict) -> None:
    """将单条 trace dict 写入 ai.trace_records（UPSERT）。"""
    from backend.memory.database import AsyncSessionLocal
    from sqlalchemy import text

    trace_id = data.get("id", "")
    if not trace_id:
        return

    session_id = data.get("session_id")
    conversation_id = data.get("conversation_id")
    workflow_name = data.get("workflow_name")
    status = data.get("status", "running")
    duration_ms = int(data.get("duration_ms", 0) or 0)

    async with AsyncSessionLocal() as db:
        await db.execute(
            text("""
                INSERT INTO ai.trace_records
                    (trace_id, session_id, conversation_id, workflow_name,
                     status, duration_ms, data)
                VALUES (:trace_id, :session_id, :conversation_id, :workflow_name,
                        :status, :duration_ms, :data::jsonb)
                ON CONFLICT (trace_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    duration_ms = EXCLUDED.duration_ms,
                    data = EXCLUDED.data
            """),
            {
                "trace_id": trace_id,
                "session_id": session_id,
                "conversation_id": conversation_id,
                "workflow_name": workflow_name,
                "status": status,
                "duration_ms": duration_ms,
                "data": json.dumps(data, ensure_ascii=False, default=str),
            },
        )
        await db.commit()


def write_trace(data: dict) -> None:
    """同步入口 — 从 trace_writer worker 线程调用。"""
    if not TRACE_PG_MIRROR_ENABLED:
        return
    try:
        run_sync(_write_one(data))
    except Exception:
        logger.warning("[PGSink] trace 写入失败: %s", data.get("id", "?"), exc_info=True)


def write_batch(batch: list[dict]) -> int:
    """批量写入，返回成功数。"""
    if not TRACE_PG_MIRROR_ENABLED:
        return 0
    ok = 0
    for data in batch:
        try:
            write_trace(data)
            ok += 1
        except Exception:
            logger.debug("[PGSink] batch item failed: %s", data.get("id", "?"), exc_info=True)
    return ok
