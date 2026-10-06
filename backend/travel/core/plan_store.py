"""travel/core/plan_store.py — 行程版本账本（方案 v2 §7：带版本的服务端状态）

每次 /plan 成功产出新版本后落一行；「确认整份行程 / 恢复历史版本 / 版本
历史」三个生命周期动作都以此为准。设计对齐 tools/travel/preferences.py
先例（feedback 同款）：

  - **存储**：agent_memory 库单表，运行时幂等建表（CREATE TABLE IF NOT
    EXISTS + 双检锁），不走 alembic —— 功能表先例；权威业务库仍在
    ai/travel schema 演进范围之外，版本账本属会话级产物，保留条数即
    方案 §10.2 的「版本历史承诺边界」。
  - **scope**：所有读写强制携带 user_id（越权 = 查不到 = 404，不泄露
    存在性）—— 方案验收「越权读取别人的行程 → 服务端拒绝」。
  - **软降级**：账本写失败绝不挡规划主链（save 返回 False，调用方照常
    返回行程）；读失败按「无账本」处理，前端隐藏历史入口。
  - **CAS**：confirm 用条件 UPDATE（WHERE plan_status='waiting_confirmation'）
    单语句原子；restore 的乐观并发由「base_version 必须等于当前最新版 +
    主键 (conversation_id, plan_version) 冲突即失败」实现，并发提交只有
    一个成功。

业务编排（生命周期迁移校验、rollback 盖章、diff 组装）在 plan_service.py，
本模块只做薄 SQL —— 依赖注入后可整层替换为内存实现做单元测试。
"""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from typing import Any, Iterator

from backend.config.database import MEMORY_DB_CONFIG
from backend.config.travel import TRAVEL_PLAN_VERSIONS_ENABLED, TRAVEL_PLAN_VERSIONS_KEEP
from backend.infra.db import engine_for
from backend.shared.logger import logger

_TABLE = "travel_plan_versions"

_init_lock = threading.Lock()
_initialized = False

_SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    conversation_id TEXT NOT NULL,
    plan_version    INTEGER NOT NULL,
    user_id         TEXT NOT NULL DEFAULT '',
    plan_status     TEXT NOT NULL DEFAULT 'waiting_confirmation',
    destination     TEXT NOT NULL DEFAULT '',
    itinerary       JSONB NOT NULL,
    change          JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (conversation_id, plan_version)
);
"""

# 会话内保留的最大版本数（超出裁最旧； rollback_record 的历史永不删除语义
# 在保留窗口内成立，窗口外按方案 §10.2 明确告知不可恢复）
_KEEP = max(2, TRAVEL_PLAN_VERSIONS_KEEP)


@contextmanager
def _conn() -> Iterator[Any]:
    """per-op 连接：成功 commit、异常 rollback、退出必关（与 preferences 同款）。"""
    conn = engine_for(MEMORY_DB_CONFIG).raw_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ensure_table() -> bool:
    """幂等建表（进程内双检锁）；失败返回 False（调用方按无账本降级）。"""
    global _initialized
    if _initialized:
        return True
    with _init_lock:
        if _initialized:
            return True
        try:
            with _conn() as conn:
                conn.cursor().execute(_SCHEMA_SQL)
            _initialized = True
            return True
        except Exception as e:  # noqa: BLE001 — 建表失败软降级
            logger.warning("[TravelPlanStore] 版本账本表初始化失败（本轮跳过）: %s", e)
            return False


def enabled() -> bool:
    return TRAVEL_PLAN_VERSIONS_ENABLED


def _version_row(row: tuple, *, with_itinerary: bool) -> dict:
    (cid, version, plan_status, destination, change_raw, created_at) = row[:6]
    try:
        change = json.loads(change_raw) if isinstance(change_raw, str) else (change_raw or {})
    except (TypeError, ValueError):
        change = {}
    entry: dict = {
        "conversation_id": cid,
        "plan_version": int(version),
        "plan_status": plan_status,
        "destination": destination,
        "change": change,
        "created_at": created_at.isoformat() if created_at else "",
    }
    if with_itinerary:
        itinerary_raw = row[6]
        try:
            entry["itinerary"] = (
                json.loads(itinerary_raw) if isinstance(itinerary_raw, str) else itinerary_raw
            )
        except (TypeError, ValueError):
            entry["itinerary"] = None
    return entry


_VERSION_COLS = (
    "conversation_id, plan_version, plan_status, destination, "
    "change::text, created_at"
)


def save_version(
    conversation_id: str,
    user_id: str,
    itinerary: dict,
    *,
    plan_status: str = "waiting_confirmation",
    change: dict | None = None,
) -> bool:
    """追加一个版本（主键冲突 = 并发提交败方，返回 False 由调用方裁决）。"""
    if not conversation_id or not user_id or not enabled() or not _ensure_table():
        return False
    brief = itinerary.get("brief") or {}
    try:
        with _conn() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""INSERT INTO {_TABLE}
                    (conversation_id, plan_version, user_id, plan_status,
                     destination, itinerary, change)
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                    ON CONFLICT (conversation_id, plan_version) DO NOTHING""",
                (
                    conversation_id[:128],
                    int(itinerary.get("plan_version") or 0),
                    user_id[:128],
                    plan_status,
                    str(brief.get("destination") or "")[:64],
                    json.dumps(itinerary, ensure_ascii=False, default=str),
                    json.dumps(change or {}, ensure_ascii=False, default=str),
                ),
            )
            if cur.rowcount == 0:
                return False  # 版本号已存在：并发败方
            # 保留窗口：只留最近 _KEEP 版（窗口外按承诺边界不可恢复）
            cur.execute(
                f"""DELETE FROM {_TABLE}
                    WHERE conversation_id = %s AND user_id = %s
                      AND plan_version <= (
                          SELECT max(plan_version) - %s FROM {_TABLE}
                          WHERE conversation_id = %s AND user_id = %s)""",
                (conversation_id[:128], user_id[:128], _KEEP,
                 conversation_id[:128], user_id[:128]),
            )
        return True
    except Exception as e:  # noqa: BLE001 — 账本写失败不挡规划主链
        logger.warning("[TravelPlanStore] 版本写入失败（跳过）: %s", e)
        return False


def latest_version(conversation_id: str, user_id: str) -> dict | None:
    """当前最新版本（含完整 itinerary）；无账本/越权/失败返回 None。"""
    if not conversation_id or not user_id or not enabled() or not _ensure_table():
        return None
    try:
        with _conn() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""SELECT {_VERSION_COLS}, itinerary::text
                    FROM {_TABLE}
                    WHERE conversation_id = %s AND user_id = %s
                    ORDER BY plan_version DESC LIMIT 1""",
                (conversation_id[:128], user_id[:128]),
            )
            row = cur.fetchone()
            return _version_row(row, with_itinerary=True) if row else None
    except Exception as e:  # noqa: BLE001 — 读失败按无账本处理
        logger.warning("[TravelPlanStore] 最新版本读取失败: %s", e)
        return None


def active_version(conversation_id: str, user_id: str) -> dict | None:
    """读取当前 Active 内容；待确认 draft 不得遮蔽最近已确认版本。"""
    if not conversation_id or not user_id or not enabled() or not _ensure_table():
        return None
    try:
        with _conn() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""SELECT {_VERSION_COLS}, itinerary::text
                    FROM {_TABLE}
                    WHERE conversation_id = %s AND user_id = %s
                      AND plan_status = 'confirmed'
                    ORDER BY plan_version DESC LIMIT 1""",
                (conversation_id[:128], user_id[:128]),
            )
            row = cur.fetchone()
            if row:
                return _version_row(row, with_itinerary=True)
    except Exception as e:  # noqa: BLE001 — 读失败按无账本处理
        logger.warning("[TravelPlanStore] Active 版本读取失败: %s", e)
        return None
    return latest_version(conversation_id, user_id)


def get_version(conversation_id: str, user_id: str, plan_version: int) -> dict | None:
    """取指定版本（含完整 itinerary）；不存在/越权返回 None。"""
    if not conversation_id or not user_id or not enabled() or not _ensure_table():
        return None
    try:
        with _conn() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""SELECT {_VERSION_COLS}, itinerary::text
                    FROM {_TABLE}
                    WHERE conversation_id = %s AND user_id = %s AND plan_version = %s""",
                (conversation_id[:128], user_id[:128], int(plan_version)),
            )
            row = cur.fetchone()
            return _version_row(row, with_itinerary=True) if row else None
    except Exception as e:  # noqa: BLE001
        logger.warning("[TravelPlanStore] 版本读取失败: %s", e)
        return None


def list_versions(conversation_id: str, user_id: str) -> list[dict]:
    """版本元数据列表（不含 itinerary，按版本号新→旧）。"""
    if not conversation_id or not user_id or not enabled() or not _ensure_table():
        return []
    try:
        with _conn() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""SELECT {_VERSION_COLS}
                    FROM {_TABLE}
                    WHERE conversation_id = %s AND user_id = %s
                    ORDER BY plan_version DESC""",
                (conversation_id[:128], user_id[:128]),
            )
            return [_version_row(r, with_itinerary=False) for r in cur.fetchall()]
    except Exception as e:  # noqa: BLE001
        logger.warning("[TravelPlanStore] 版本列表读取失败: %s", e)
        return []


def list_conversations(user_id: str, limit: int = 30) -> list[dict]:
    """某用户的历史规划列表：每个会话取最新版元数据（不含 itinerary），
    按最近更新新→旧。账本不可用/参数为空返回空列表（前端隐藏入口）。"""
    if not user_id or not enabled() or not _ensure_table():
        return []
    try:
        with _conn() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""SELECT v.conversation_id, v.plan_version, v.plan_status,
                           v.destination, v.created_at,
                           (SELECT count(*) FROM {_TABLE} t2
                            WHERE t2.conversation_id = v.conversation_id
                              AND t2.user_id = %s) AS versions_count
                    FROM {_TABLE} v
                    JOIN (
                        SELECT conversation_id, max(plan_version) AS max_version
                        FROM {_TABLE}
                        WHERE user_id = %s
                        GROUP BY conversation_id
                    ) m ON v.conversation_id = m.conversation_id
                       AND v.plan_version = m.max_version
                    WHERE v.user_id = %s
                    ORDER BY v.created_at DESC
                    LIMIT %s""",
                (user_id[:128], user_id[:128], user_id[:128],
                 max(1, min(int(limit), 100))),
            )
            return [
                {
                    "conversation_id": row[0],
                    "plan_version": int(row[1]),
                    "plan_status": row[2],
                    "destination": row[3],
                    "created_at": row[4].isoformat() if row[4] else "",
                    "versions_count": int(row[5]),
                }
                for row in cur.fetchall()
            ]
    except Exception as e:  # noqa: BLE001 — 列表读失败按无账本处理
        logger.warning("[TravelPlanStore] 历史规划列表读取失败: %s", e)
        return []


def confirm_version(conversation_id: str, user_id: str, plan_version: int) -> str | None:
    """确认整份行程（CAS）：仅 waiting_confirmation → confirmed 单语句原子。

    Returns: 成功返回 "confirmed"；版本不存在/已确认/越权返回 None
    （调用方区分 404 与 409 需先查当前态）。
    """
    if not conversation_id or not user_id or not enabled() or not _ensure_table():
        return None
    try:
        with _conn() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""UPDATE {_TABLE} SET plan_status = 'confirmed'
                    WHERE conversation_id = %s AND user_id = %s
                      AND plan_version = %s
                      AND plan_status = 'waiting_confirmation'
                    RETURNING plan_status""",
                (conversation_id[:128], user_id[:128], int(plan_version)),
            )
            row = cur.fetchone()
            return row[0] if row else None
    except Exception as e:  # noqa: BLE001
        logger.warning("[TravelPlanStore] 确认失败: %s", e)
        return None
