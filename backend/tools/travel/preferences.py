"""tools/travel/preferences.py — 旅游偏好持久化（P1-1，2026-09-22）

按用户维度把「这次没说也会影响规划」的偏好存下来（出发地/兴趣标签/节奏/
饮食忌口/住宿与交通倾向/预算档），新会话规划时预填 —— 用户不用每次重复
「我不吃辣、带娃、喜欢人文」。

设计决策（与既有机制对齐，不引新模式）：
  - **存储**：agent_memory 库一张单表（与 feedback 同款 psycopg2 + per-op
    连接模式），表结构幂等创建（CREATE TABLE IF NOT EXISTS + 双检锁），
    不走 alembic 迁移链 —— 属运行时自动建表的功能表，与 feedback 先例一致。
  - **写入时机**：slot_filler 每轮抽取完 brief 后 upsert（只写用户明确
    表达过的字段，未表达的不覆盖旧值）。
  - **读取时机**：跨轮首轮（无上一轮 brief）时预填 —— 只填「本轮没说」
    的字段，本轮显式表达永远优先（merge_brief 的字段优先级不变）。
  - **高并发**：单行 upsert（INSERT ... ON CONFLICT DO UPDATE），无
    读改写竞态；失败全部软降级 —— 偏好读写失败绝不能挡住出行程。
  - **不进指纹**：偏好预填只影响 preferences/pace 等规划字段，而这些
    字段本来就在 brief 指纹内 —— 预填发生在指纹计算**之前**（同轮内
    一次性完成），不会造成「每轮都变、每轮重排」的抖动。
"""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from typing import Any, Iterator

from backend.config.database import MEMORY_DB_CONFIG
from backend.config import travel as T
from backend.infra.db import engine_for
from backend.shared.logger import logger

_TABLE = "travel_preferences"

_init_lock = threading.Lock()
_initialized = False

_SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    user_id        TEXT PRIMARY KEY,
    origin         TEXT NOT NULL DEFAULT '',
    preferences    JSONB NOT NULL DEFAULT '[]'::jsonb,
    pace           TEXT NOT NULL DEFAULT '',
    diet           TEXT NOT NULL DEFAULT '',
    lodging        TEXT NOT NULL DEFAULT '',
    transport      TEXT NOT NULL DEFAULT '',
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def _enabled() -> bool:
    return T.TRAVEL_PREFS_ENABLED


@contextmanager
def _conn() -> Iterator[Any]:
    """per-op 连接：成功 commit、异常 rollback、退出必关（与 feedback/pg.py 同款）。"""
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
    """V1 长期偏好表已退役；旧 Agent 仅按空偏好继续运行。"""
    return False


def _row_to_dict(row: tuple) -> dict:
    (user_id, origin, preferences, pace, diet, lodging, transport, _updated) = row
    try:
        tags = json.loads(preferences) if isinstance(preferences, str) else (preferences or [])
    except (TypeError, ValueError):
        tags = []
    return {
        "user_id": user_id,
        "origin": origin or "",
        "preferences": [t for t in (tags or []) if t],
        "pace": pace or "",
        "diet": diet or "",
        "lodging": lodging or "",
        "transport": transport or "",
    }


def get_preferences(user_id: str) -> dict:
    """读取用户偏好；无记录/失败返回空 dict（调用方按未填处理）。"""
    if not user_id or not _enabled() or not _ensure_table():
        return {}
    try:
        with _conn() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""SELECT user_id, origin, preferences::text, pace, diet,
                           lodging, transport, updated_at
                    FROM {_TABLE} WHERE user_id = %s""",
                (user_id[:128],),
            )
            row = cur.fetchone()
            return _row_to_dict(row) if row else {}
    except Exception as e:  # noqa: BLE001 — 读取失败软降级
        logger.warning("[TravelPrefs] 偏好读取失败（按未填处理）: %s", e)
        return {}


def upsert_preferences(user_id: str, **fields) -> bool:
    """按字段 upsert 偏好。只写非空字段，空/缺失字段保留旧值。

    合法字段：origin / preferences(list) / pace / diet / lodging / transport。
    Returns: 是否成功（失败不影响调用方主流程）。
    """
    if not user_id or not _enabled() or not _ensure_table():
        return False

    allowed = {}
    if fields.get("origin"):
        allowed["origin"] = str(fields["origin"])[:64]
    if fields.get("preferences"):
        allowed["preferences"] = json.dumps(
            [str(p)[:32] for p in fields["preferences"]][:16],
            ensure_ascii=False)
    for key in ("pace", "diet", "lodging", "transport"):
        if fields.get(key):
            allowed[key] = str(fields[key])[:64]
    if not allowed:
        return False

    set_clause = ", ".join(f"{k} = EXCLUDED.{k}" for k in allowed)
    columns = ["user_id", *allowed.keys()]
    values = [user_id[:128], *allowed.values()]
    try:
        with _conn() as conn:
            conn.cursor().execute(
                f"""INSERT INTO {_TABLE} ({", ".join(columns)})
                    VALUES ({", ".join(["%s"] * len(columns))})
                    ON CONFLICT (user_id) DO UPDATE
                    SET {set_clause}, updated_at = now()""",
                values,
            )
        return True
    except Exception as e:  # noqa: BLE001 — 写入失败软降级
        logger.warning("[TravelPrefs] 偏好写入失败（已跳过）: %s", e)
        return False
