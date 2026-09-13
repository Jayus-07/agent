"""checkpointer_cleanup.py — CS PostgresSaver checkpoint TTL 清理

背景: LangGraph PostgresSaver 按 thread_id（CS conversation_id）累积
每轮对话的 checkpoints / checkpoint_blobs / checkpoint_writes 行，此前
无任何清理策略，长期运行会无限膨胀。

方案: 每日守护线程清理 checkpoint 时间戳超过 TTL 的行，并删除随之
孤儿化的 blob/writes（thread_id+checkpoint_ns 不再被任何 checkpoint 引用）。

说明:
  - checkpoint JSONB 内含 "ts"（ISO 时间戳，langgraph 写入），以其为过期依据；
  - 短连接：每轮清理单独建连/释放，不与 checkpointer 主连接争用；
  - 全程软失败：清理是非关键路径，失败仅告警。
"""
from __future__ import annotations

import threading
import time

from backend.shared.logger import logger

# 守护线程只允许启动一次（_build_checkpointer 可能被并发调用）
_started = False
_start_lock = threading.Lock()

_CLEANUP_INTERVAL_SECONDS = 24 * 3600
_FIRST_RUN_DELAY_SECONDS = 60  # 启动后 1 分钟先跑一轮，之后每 24h


def _dsn() -> str:
    from backend.config.database import MEMORY_DB_CONFIG
    c = MEMORY_DB_CONFIG
    return (f"postgresql://{c['user']}:{c['password']}"
            f"@{c['host']}:{c['port']}/{c['dbname']}")


def cleanup_stale_checkpoints(max_age_days: int) -> dict:
    """删除超过 TTL 的 checkpoint 及孤儿 blob/writes。

    Returns:
        {"checkpoints": n1, "blobs": n2, "writes": n3}（各表删除行数）
    """
    import psycopg

    deleted = {"checkpoints": 0, "blobs": 0, "writes": 0}
    with psycopg.connect(_dsn(), autocommit=True) as conn:
        with conn.cursor() as cur:
            # 1) 过期 checkpoint（langgraph 在 checkpoint JSONB 里写 ts）
            cur.execute(
                """
                DELETE FROM checkpoints
                WHERE (checkpoint->>'ts')::timestamptz
                      < now() - (%s || ' days')::interval
                """,
                (str(max_age_days),),
            )
            deleted["checkpoints"] = cur.rowcount or 0

            # 2) 孤儿 blob/writes：其 (thread_id, checkpoint_ns) 已无任何 checkpoint
            for table in ("checkpoint_blobs", "checkpoint_writes"):
                cur.execute(
                    f"""
                    DELETE FROM {table} t
                    WHERE NOT EXISTS (
                        SELECT 1 FROM checkpoints c
                        WHERE c.thread_id = t.thread_id
                          AND c.checkpoint_ns = t.checkpoint_ns
                    )
                    """
                )
                deleted["blobs" if table == "checkpoint_blobs" else "writes"] = \
                    cur.rowcount or 0
    return deleted


def _run_once(max_age_days: int) -> None:
    try:
        deleted = cleanup_stale_checkpoints(max_age_days)
        total = sum(deleted.values())
        if total:
            logger.info(f"[CS Checkpoint] TTL 清理完成 (>{max_age_days}天): {deleted}")
    except Exception as e:
        # 常见：表未建（checkpointer 从未启用）、连接失败——软失败
        logger.warning(f"[CS Checkpoint] TTL 清理失败（非致命）: {e}")


def start_cleanup_daemon(max_age_days: int | None = None) -> None:
    """启动每日清理守护线程（幂等；仅 Postgres 后端有意义）。"""
    global _started
    with _start_lock:
        if _started:
            return
        _started = True

    from backend.config.customer_service import CS_CHECKPOINT_TTL_DAYS

    days = max_age_days if max_age_days is not None else CS_CHECKPOINT_TTL_DAYS

    def _loop():
        time.sleep(_FIRST_RUN_DELAY_SECONDS)
        while True:
            _run_once(days)
            time.sleep(_CLEANUP_INTERVAL_SECONDS)

    threading.Thread(target=_loop, daemon=True, name="cs-checkpoint-cleanup").start()
    logger.info(f"[CS Checkpoint] TTL 清理守护已启动 (TTL={days}天, 每24h一轮)")
