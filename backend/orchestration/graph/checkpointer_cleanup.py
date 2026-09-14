"""orchestration/graph/checkpointer_cleanup.py — LangGraph checkpoint TTL 清理（共享）

**归属说明**：本模块原先寄居在 `backend/customer_service/` 下，但清理逻辑与
客服业务毫无关系 —— 它操作的是 LangGraph 在 agent_memory 库里建的三张共享表
（checkpoints / checkpoint_blobs / checkpoint_writes），主图、客服域、旅游域
**都在往里写**。放在编排层（与主图 checkpointer.py 同目录）才是它该待的位置；
客服域侧保留了一个向后兼容薄壳，不破坏既有 import。

背景：PostgresSaver 按 thread_id 累积每轮 checkpoint，无清理会无限膨胀。
方案：每日守护线程删除超过 TTL 的 checkpoint 行，并清掉随之孤儿化的
blob / writes（其 thread_id+checkpoint_ns 已无任何 checkpoint 引用）。

**全进程单例，TTL 取首个调用方**：三张表共用，因此只能有一个 TTL 生效。
`start_cleanup_daemon` 的 `_started` 守卫是模块级全局，谁先调用谁的
`ttl_days` 生效（后来的调用只记 debug 日志）。所以各域配置里的 TTL
必须保持一致（当前统一 7 天）；真要按域区分 TTL，就得先让 thread_id
能标识所属域，那是另一件事，别在这里偷偷各调各的。

全程软失败：清理是非关键路径，失败仅告警，绝不影响主链路。
"""
from __future__ import annotations

import threading
import time

from backend.shared.logger import logger

# 守护线程只允许启动一次（多个域图并发首次调用时防重复起线程）
_started = False
_start_lock = threading.Lock()
# 首个调用方标识 + 其 TTL（供日志与状态查询）
_owner = ""
_ttl_days: int | None = None

# 可被测试覆盖的循环参数（模块级，便于 monkeypatch）
FIRST_RUN_DELAY_SECONDS = 60  # 启动后 1 分钟先跑一轮，之后每 24h
CLEANUP_INTERVAL_SECONDS = 24 * 3600

# 统一默认 TTL（天）。主图没有自己的 TTL 配置项，用这个默认值；
# 客服域 / 旅游域各自的配置默认值也是 7，三方天然一致。
# 想改就改这一处 + 两个域配置，别只改其中一个。
DEFAULT_TTL_DAYS = 7


def _dsn() -> str:
    from backend.config.database import MEMORY_DB_CONFIG
    c = MEMORY_DB_CONFIG
    return (f"postgresql://{c['user']}:{c['password']}"
            f"@{c['host']}:{c['port']}/{c['dbname']}")


def cleanup_stale_checkpoints(max_age_days: int) -> dict:
    """删除超过 TTL 的 checkpoint 及孤儿 blob/writes。

    Returns:
        {"checkpoints": n1, "blobs": n2, "writes": n3}（各表删除行数）

    短连接：每轮清理单独建连/释放，不与 checkpointer 主连接争用。
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


def _run_once(max_age_days: int, owner: str) -> None:
    try:
        deleted = cleanup_stale_checkpoints(max_age_days)
        total = sum(deleted.values())
        if total:
            logger.info("[Checkpoint] TTL 清理完成 owner=%s (>%d天): %s",
                        owner, max_age_days, deleted)
    except Exception as e:
        # 常见：表未建（checkpointer 从未启用）、连接失败 —— 软失败
        logger.warning("[Checkpoint] TTL 清理失败（非致命）: %s", e)


def start_cleanup_daemon(ttl_days: int, owner: str) -> bool:
    """启动每日清理守护线程（全进程单例）。

    Args:
        ttl_days: 过期天数
        owner: 调用方标识（main / cs / travel），仅用于日志，便于排查
               「到底是谁起的这个守护、用的哪个 TTL」

    Returns:
        True  本次调用启动了守护
        False 之前已有守护在跑（本次为幂等空操作，调用方 TTL 不生效）
    """
    global _started, _owner, _ttl_days

    with _start_lock:
        if _started:
            if ttl_days != _ttl_days:
                logger.warning(
                    "[Checkpoint] 已有 TTL 清理守护在运行 (owner=%s, TTL=%s天)，"
                    "本次请求 owner=%s 的 TTL=%s天 不生效 —— "
                    "三张 checkpoint 表共用，只能有一个 TTL，"
                    "请把各域配置调成一致",
                    _owner, _ttl_days, owner, ttl_days,
                )
            else:
                logger.debug("[Checkpoint] TTL 清理守护已在运行 (owner=%s)", _owner)
            return False

        _started = True
        _owner = owner
        _ttl_days = ttl_days

    def _loop():
        time.sleep(FIRST_RUN_DELAY_SECONDS)
        while True:
            _run_once(ttl_days, owner)
            time.sleep(CLEANUP_INTERVAL_SECONDS)

    threading.Thread(target=_loop, daemon=True,
                     name=f"checkpoint-cleanup-{owner}").start()
    logger.info("[Checkpoint] TTL 清理守护已启动 (owner=%s, TTL=%d天, 每24h一轮)",
                owner, ttl_days)
    return True


def daemon_state() -> dict:
    """守护线程状态（日志/健康检查/测试用）。"""
    return {"started": _started, "owner": _owner, "ttl_days": _ttl_days}
