"""gateway_log_ingest.py — 网关访问日志消费落库（Redis Streams → ai.gateway_access_logs）

数据链路：
    APISIX gateway-access-log.lua（log 阶段，global_rules 全路由）
      → XADD agent:gw:access-log（MAXLEN ~ 100000）
      → 本模块 worker（daemon 线程，server.py startup 启动）
      → 批量 INSERT ai.gateway_access_logs（memory 库 ai schema，psycopg2 同步）
      → 管理端 /api/observability/gateway-access-logs 查询

可靠性设计：
  - 幂等：entry_id（Stream 条目 id）UNIQUE + ON CONFLICT DO NOTHING。
    消费位点持久化在 Redis key（agent:gw:access-log:last-id），进程重启续读；
    位点丢失时从 "0" 重放历史段，由幂等约束兜底不重复。
  - fail-open：Redis / PG 故障只影响日志入库（5s 退避重试，不退出），
    绝不影响业务请求；stdout 访问日志独立存在，互为冗余。
  - 线程模型：单 worker 全同步（redis-py + psycopg2），无事件循环桥接——
    刻意不走 AsyncSessionLocal：它绑定主 loop，跨 loop 复用会污染连接池。

保留策略：每小时清理一次超过 GATEWAY_LOG_RETENTION_DAYS（默认 14 天）的行。
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone

from backend.config.observability import (
    GATEWAY_LOG_INGEST_ENABLED,
    GATEWAY_LOG_RETENTION_DAYS,
)
from backend.config.redis import REDIS_SOCKET_TIMEOUT
from backend.shared.logger import logger

_STREAM_KEY = "agent:gw:access-log"
_LAST_ID_KEY = "agent:gw:access-log:last-id"
_BATCH = 200
# xread 的 block 必须严格小于共享客户端的 socket_timeout（REDIS_SOCKET_TIMEOUT，
# 默认 5s），否则每次阻塞读都撞 socket 超时（实测 "Timeout reading from socket"）。
# 取 80% 留出余量；客户端 socket_timeout 被调小时按比例收缩。
_BLOCK_MS = max(1000, int(REDIS_SOCKET_TIMEOUT * 1000 * 0.8))
_RETRY_SLEEP_S = 5.0
_CLEANUP_INTERVAL_S = 3600.0

# 防御性截断：网关侧变量最长可达 URI/UA 的 pathological 值，入库前裁齐
_TRUNC = {"uri": 2048, "query": 2048, "ua": 512, "user_id": 128,
          "auth_type": 32, "trace_id": 128, "method": 16, "client_ip": 64}

_conn = None  # psycopg2 常驻连接（worker 线程独占）


# ═══ 解析（纯函数，供单测）══════════════════════════════════

def _parse_ts(raw: str) -> datetime | None:
    """网关时间戳 → UTC aware datetime。

    lua ngx.utctime() 输出 "YYYY-MM-DD HH:MM:SS"（UTC 值但无时区后缀），
    必须显式视为 UTC，否则 psycopg2 会按会话时区解释造成时间漂移。
    """
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _row_from_entry(entry_id: str, fields: dict) -> tuple | None:
    """Stream 条目 → 行元组；data 缺失 / JSON 损坏 / 时间非法返回 None（丢弃）。"""
    try:
        data = json.loads(fields.get("data", ""))
    except (ValueError, TypeError):
        logger.warning(f"[GatewayLogIngest] 条目 {entry_id} JSON 损坏，丢弃")
        return None
    ts = _parse_ts(data.get("time", ""))
    if ts is None:
        logger.warning(f"[GatewayLogIngest] 条目 {entry_id} 时间非法，丢弃")
        return None

    def s(key: str) -> str:
        val = str(data.get(key, "") or "")
        limit = _TRUNC.get(key)
        return val[:limit] if limit else val

    return (
        entry_id, ts,
        s("client_ip"), s("user_id"), s("auth_type"), s("trace_id"),
        s("method"), s("uri"), s("query"),
        int(data.get("status") or 0),
        int(data.get("bytes") or 0),
        float(data.get("duration_ms") or 0),
        s("ua"),
    )


# ═══ 落库 ═══════════════════════════════════════════════════

def _get_conn():
    """worker 线程独占的 psycopg2 连接（断线时重建）。"""
    global _conn
    if _conn is not None and not _conn.closed:
        return _conn
    import psycopg2
    from backend.config.database import MEMORY_DB_CONFIG
    _conn = psycopg2.connect(
        host=MEMORY_DB_CONFIG["host"], port=MEMORY_DB_CONFIG["port"],
        user=MEMORY_DB_CONFIG["user"], password=MEMORY_DB_CONFIG["password"],
        dbname=MEMORY_DB_CONFIG["dbname"], connect_timeout=5,
    )
    _conn.autocommit = False
    return _conn


def _close_conn() -> None:
    global _conn
    if _conn is not None:
        try:
            _conn.close()
        except Exception:
            pass
        _conn = None


def _insert_many(rows: list[tuple]) -> int:
    """批量 INSERT（幂等）；返回影响行数。失败抛出让调用方重试（位点未推进）。"""
    from psycopg2.extras import execute_values
    conn = _get_conn()
    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO ai.gateway_access_logs
                (entry_id, ts, client_ip, user_id, auth_type, trace_id,
                 method, uri, query, status, bytes, duration_ms, ua)
            VALUES %s
            ON CONFLICT (entry_id) DO NOTHING
            """,
            rows, page_size=len(rows) or 1,
        )
        inserted = cur.rowcount
    conn.commit()
    return inserted


def _cleanup() -> None:
    """删除超过保留期的访问日志（由 worker 周期触发）。"""
    conn = _get_conn()
    cutoff = datetime.now(timezone.utc) - timedelta(days=GATEWAY_LOG_RETENTION_DAYS)
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM ai.gateway_access_logs WHERE ts < %s", (cutoff,)
        )
        deleted = cur.rowcount
    conn.commit()
    if deleted:
        logger.info(f"[GatewayLogIngest] 清理过期访问日志 {deleted} 条（>{GATEWAY_LOG_RETENTION_DAYS} 天）")


# ═══ worker ═════════════════════════════════════════════════

def _run() -> None:
    last_id: str | None = None
    next_cleanup = time.monotonic() + _CLEANUP_INTERVAL_S
    logger.info(
        f"[GatewayLogIngest] worker 启动 stream={_STREAM_KEY} "
        f"retention={GATEWAY_LOG_RETENTION_DAYS}d"
    )
    while True:
        try:
            from backend.infra.redis.client import get_redis
            r = get_redis()
            if r is None:
                raise RuntimeError("Redis 不可用")

            if last_id is None:
                # 位点持久化在 Redis：重启续读；缺失（首次/被清）从 0 重放，
                # 依赖 entry_id 幂等约束去重
                last_id = r.get(_LAST_ID_KEY) or "0"

            resp = r.xread({_STREAM_KEY: last_id}, count=_BATCH, block=_BLOCK_MS)
            rows: list[tuple] = []
            for _stream, entries in resp or []:
                for entry_id, fields in entries:
                    row = _row_from_entry(entry_id, fields)
                    if row is not None:
                        rows.append(row)
                    last_id = entry_id
            if rows:
                _insert_many(rows)
                r.set(_LAST_ID_KEY, last_id)

            if time.monotonic() >= next_cleanup:
                _cleanup()
                next_cleanup = time.monotonic() + _CLEANUP_INTERVAL_S
        except Exception as e:
            logger.warning(f"[GatewayLogIngest] 消费失败，{_RETRY_SLEEP_S:.0f}s 后重试: {e}")
            _close_conn()
            last_id = None  # 连接状态未知，位点回 Redis 重读
            time.sleep(_RETRY_SLEEP_S)


def start_gateway_log_ingest() -> None:
    """server.py startup 调用：GATEWAY_LOG_INGEST_ENABLED=false 时不启动。"""
    if not GATEWAY_LOG_INGEST_ENABLED:
        logger.info("[GatewayLogIngest] 未启用（GATEWAY_LOG_INGEST_ENABLED=false）")
        return
    threading.Thread(target=_run, daemon=True, name="gateway-log-ingest").start()
