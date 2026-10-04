"""travel/core/decision_store.py — 用户决策留痕（M4/G1，2026-10-04）

旅游页五类用户决策（草案应用/放弃、画布确认替换、档位切换、删减协商）
逐条落库：此前只存在于前端瞬时 state，事后无法回答「用户当时对哪一版
行程做了什么决定」。设计完全对齐 plan_store.py 先例：

  - **存储**：agent_memory 库单表 travel_decision_audit，运行时幂等建表
    （CREATE TABLE IF NOT EXISTS + 双检锁）；migration 072 为权威 DDL，
    幂等建表只是「migration 漏跑也不挂」的双保险。
  - **scope**：读写强制携带 user_id（越权 = 查不到 = 空列表），与版本
    账本同一口径。
  - **软降级**：留痕写失败绝不挡 UI 动作（record 返回 False，调用方
    照常继续本地更新——决策留痕是旁路，不是业务门禁）。

本模块只做薄 SQL；端点编排在 app/api/routes/travel.py。
"""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from typing import Any, Iterator

from backend.config.database import MEMORY_DB_CONFIG
from backend.infra.db import engine_for
from backend.shared.logger import logger

_TABLE = "travel_decision_audit"

# 决策类型白名单：非白名单值拒绝落库（防御性——前端枚举演进时先扩这里）
DECISION_TYPES = (
    "apply_draft",
    "discard_draft",
    "canvas_replace",
    "tier_switch",
    "budget_negotiate",
)

_init_lock = threading.Lock()
_initialized = False

_SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    id              BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    tenant_id       VARCHAR(128) NOT NULL DEFAULT '',
    user_id         VARCHAR(128) NOT NULL DEFAULT '',
    conversation_id VARCHAR(128) NOT NULL,
    decision        VARCHAR(32)  NOT NULL,
    plan_version    INTEGER      NOT NULL DEFAULT 0,
    tier_from       VARCHAR(32)  NOT NULL DEFAULT '',
    tier_to         VARCHAR(32)  NOT NULL DEFAULT '',
    payload         JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    source          VARCHAR(32)  NOT NULL DEFAULT '',
    client_run_id   VARCHAR(64)  NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_travel_decision_conv_time
    ON {_TABLE} (conversation_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_travel_decision_user_time
    ON {_TABLE} (user_id, created_at DESC);
"""


@contextmanager
def _conn() -> Iterator[Any]:
    """per-op 连接：成功 commit、异常 rollback、退出必关（与 plan_store 同款）。"""
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
    """幂等建表（进程内双检锁）；失败返回 False（调用方按留痕不可用降级）。"""
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
            logger.warning("[TravelDecisionStore] 决策留痕表初始化失败（本轮跳过）: %s", e)
            return False


def record_decision(
    user_id: str,
    conversation_id: str,
    decision: str,
    *,
    tenant_id: str = "",
    plan_version: int = 0,
    tier_from: str = "",
    tier_to: str = "",
    payload: dict | None = None,
    source: str = "",
    client_run_id: str = "",
) -> int:
    """追加一条决策留痕。

    Returns: 落库行 id；留痕不可用/参数非法/写失败返回 0（软失败，调用方
    继续本地动作——留痕不挡 UI）。
    """
    decision = str(decision or "").strip()
    if decision not in DECISION_TYPES:
        logger.warning("[TravelDecisionStore] 非法决策类型被拒绝: %r", decision)
        return 0
    if not user_id or not conversation_id or not _ensure_table():
        return 0
    try:
        with _conn() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""INSERT INTO {_TABLE}
                    (tenant_id, user_id, conversation_id, decision, plan_version,
                     tier_from, tier_to, payload, source, client_run_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                    RETURNING id""",
                (
                    tenant_id[:128],
                    user_id[:128],
                    conversation_id[:128],
                    decision,
                    int(plan_version or 0),
                    str(tier_from or "")[:32],
                    str(tier_to or "")[:32],
                    json.dumps(payload or {}, ensure_ascii=False, default=str),
                    str(source or "")[:32],
                    str(client_run_id or "")[:64],
                ),
            )
            return int(cur.fetchone()[0])
    except Exception as e:  # noqa: BLE001 — 留痕写失败不挡 UI 动作
        logger.warning("[TravelDecisionStore] 决策留痕写入失败（跳过）: %s", e)
        return 0


def list_decisions(user_id: str, conversation_id: str, *, limit: int = 100) -> list[dict]:
    """某会话的决策链（时间新→旧）；越权/留痕不可用返回空列表。"""
    if not user_id or not conversation_id or not _ensure_table():
        return []
    try:
        with _conn() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""SELECT id, created_at, conversation_id, decision, plan_version,
                           tier_from, tier_to, payload::text, source, client_run_id
                    FROM {_TABLE}
                    WHERE user_id = %s AND conversation_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s""",
                (user_id[:128], conversation_id[:128],
                 max(1, min(int(limit), 500))),
            )
            rows = []
            for row in cur.fetchall():
                try:
                    payload = json.loads(row[7]) if isinstance(row[7], str) else (row[7] or {})
                except (TypeError, ValueError):
                    payload = {}
                rows.append({
                    "id": int(row[0]),
                    "created_at": row[1].isoformat() if row[1] else "",
                    "conversation_id": row[2],
                    "decision": row[3],
                    "plan_version": int(row[4]),
                    "tier_from": row[5],
                    "tier_to": row[6],
                    "payload": payload,
                    "source": row[8],
                    "client_run_id": row[9],
                })
            return rows
    except Exception as e:  # noqa: BLE001 — 读失败按无留痕处理
        logger.warning("[TravelDecisionStore] 决策留痕读取失败: %s", e)
        return []
