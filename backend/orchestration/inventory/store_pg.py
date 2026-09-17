"""PostgresInventoryStore — 库存告警 4 表的 PostgreSQL 连接层（迁移计划 Batch C）。

与 SQLite 版 `InventoryStore` 对外接口完全一致，仅替换连接层与 SQL 方言。
选型开关见 `backend/config/database.py::INVENTORY_DB_BACKEND`
（env `INVENTORY_DB_BACKEND=postgres` 启用；默认 sqlite，即回滚开关）。

库归属：agent_business（业务属性，对 NL2SQL 可见；`agent_readonly` 已挂该库）。
schema 与 backend/sql/migrations/015_orchestration_pg.sql 保持一致。

方言映射：
  - `?`                          → `%s`
  - `INTEGER AUTOINCREMENT`      → `BIGSERIAL`（INSERT ... RETURNING id 取 lastrowid）
  - `INSERT OR REPLACE`          → `INSERT ... ON CONFLICT (id) DO UPDATE`
  - `INTEGER` 布尔列             → `BOOLEAN`（写入前显式 bool() 转换）
  - sqlite3 隐式事务             → psycopg2 显式 commit/rollback（per-op 连接）
"""

from __future__ import annotations

import json as _json
import os
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator

import psycopg2
import psycopg2.extras

from backend.config.database import INVENTORY_DB_PG_CONFIG
from backend.orchestration.inventory.store import InventoryStore
from backend.shared.logger import logger

# 表名前缀（测试隔离用；生产保持空串）
_PREFIX = os.getenv("INVENTORY_DB_PG_TABLE_PREFIX", "")

_T_THRESHOLDS = f"{_PREFIX}inventory_threshold_rules"
_T_CASES = f"{_PREFIX}inventory_alert_cases"
_T_EVENTS = f"{_PREFIX}inventory_alert_events"
_T_POLICIES = f"{_PREFIX}notification_policies"

_SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {_T_THRESHOLDS} (
    id                  BIGSERIAL PRIMARY KEY,
    rule_type           TEXT NOT NULL,
    product_id          TEXT,
    category            TEXT,

    min_qty             INTEGER NOT NULL,
    days_of_stock       INTEGER DEFAULT 7,
    sales_window_days   INTEGER DEFAULT 30,
    alert_level         TEXT DEFAULT 'warning',
    enabled             BOOLEAN DEFAULT true,

    created_at          TEXT,
    updated_at          TEXT
);
CREATE INDEX IF NOT EXISTS idx_thresholds_match
    ON {_T_THRESHOLDS}(rule_type, product_id, category, enabled);

CREATE TABLE IF NOT EXISTS {_T_CASES} (
    id                  BIGSERIAL PRIMARY KEY,
    product_id          TEXT NOT NULL UNIQUE,
    current_state       TEXT,
    current_level       TEXT,
    status              TEXT,
    resolution_type     TEXT,

    first_detected_at   TEXT,
    last_detected_at    TEXT,
    last_notified_at    TEXT,

    created_at          TEXT,
    updated_at          TEXT
);
CREATE INDEX IF NOT EXISTS idx_cases_status
    ON {_T_CASES}(status, product_id);

CREATE TABLE IF NOT EXISTS {_T_EVENTS} (
    id              BIGSERIAL PRIMARY KEY,
    case_id         BIGINT NOT NULL,
    event_type      TEXT NOT NULL,
    from_state      TEXT,
    to_state        TEXT,
    qty             INTEGER,
    stock_days      DOUBLE PRECISION,
    reason          TEXT,
    notified        BOOLEAN DEFAULT false,
    created_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_case
    ON {_T_EVENTS}(case_id, created_at);

CREATE TABLE IF NOT EXISTS {_T_POLICIES} (
    id                  BIGSERIAL PRIMARY KEY,
    policy_name         TEXT NOT NULL,
    alert_level         TEXT,
    inventory_state     TEXT,
    category            TEXT,
    notify_email        TEXT,
    notify_on_upgrade   BOOLEAN DEFAULT true,
    notify_on_remind    BOOLEAN DEFAULT true,
    notify_on_resolve   BOOLEAN DEFAULT true,
    enabled             BOOLEAN DEFAULT true,
    created_at          TEXT,
    updated_at          TEXT
);
CREATE INDEX IF NOT EXISTS idx_policies_match
    ON {_T_POLICIES}(enabled, alert_level, inventory_state);
"""


class PostgresInventoryStore(InventoryStore):
    """库存告警数据访问（4 张表共用 store）— PostgreSQL 实现。"""

    def __init__(self, db_path: str = "data/inventory_alerts.db"):
        self._db_path = db_path  # 兼容保留，PG 模式下无意义
        self._lock = threading.RLock()
        self._init_db()
        logger.debug("[PostgresInventoryStore] 初始化完成（agent_business）")

    # ---- 连接层 ----

    @contextmanager
    def _conn(self) -> Iterator[Any]:
        """每次操作独立连接：成功 commit、异常 rollback、退出必关。"""
        conn = psycopg2.connect(**INVENTORY_DB_PG_CONFIG)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _exec(self, conn: Any, sql: str, params: tuple = ()) -> Any:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params)
        return cur

    def _insert_returning_id(self, conn: Any, sql: str, params: tuple) -> int:
        """INSERT ... RETURNING id，标量游标取回自增主键（等价 sqlite lastrowid）。"""
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchone()[0]

    def _init_db(self):
        with self._lock, self._conn() as conn:
            conn.cursor().execute(_SCHEMA_SQL)

    # ──────────── Thresholds ────────────

    def save_threshold(self, rule: dict) -> int:
        """保存阈值规则（INSERT OR REPLACE 语义）"""
        now = datetime.now().isoformat()
        params = (
            rule["rule_type"],
            rule.get("product_id"),
            rule.get("category"),
            rule["min_qty"],
            rule.get("days_of_stock", 7),
            rule.get("sales_window_days", 30),
            rule.get("alert_level", "warning"),
            bool(rule.get("enabled", True)),
            rule.get("created_at", now),
            now,
        )
        with self._lock, self._conn() as conn:
            rule_id = rule.get("id")
            if rule_id is None:
                return self._insert_returning_id(
                    conn,
                    f"""INSERT INTO {_T_THRESHOLDS}
                       (rule_type, product_id, category, min_qty, days_of_stock,
                        sales_window_days, alert_level, enabled, created_at, updated_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       RETURNING id""",
                    params,
                )
            self._exec(
                conn,
                f"""INSERT INTO {_T_THRESHOLDS}
                   (id, rule_type, product_id, category, min_qty, days_of_stock,
                    sales_window_days, alert_level, enabled, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (id) DO UPDATE SET
                        rule_type = EXCLUDED.rule_type,
                        product_id = EXCLUDED.product_id,
                        category = EXCLUDED.category,
                        min_qty = EXCLUDED.min_qty,
                        days_of_stock = EXCLUDED.days_of_stock,
                        sales_window_days = EXCLUDED.sales_window_days,
                        alert_level = EXCLUDED.alert_level,
                        enabled = EXCLUDED.enabled,
                        created_at = EXCLUDED.created_at,
                        updated_at = EXCLUDED.updated_at""",
                (rule_id,) + params,
            )
            return int(rule_id)

    def list_thresholds(self, enabled_only: bool = True) -> list[dict]:
        """列出所有阈值规则"""
        sql = f"SELECT * FROM {_T_THRESHOLDS}"
        if enabled_only:
            sql += " WHERE enabled = true"
        sql += " ORDER BY id"
        with self._conn() as conn:
            rows = self._exec(conn, sql).fetchall()
        return [dict(r) for r in rows]

    # ──────────── Cases ────────────

    def upsert_case(self, case: dict) -> int:
        """insert or update case（按 product_id UNIQUE 约束）"""
        now = datetime.now().isoformat()
        with self._lock, self._conn() as conn:
            row = self._exec(
                conn,
                f"SELECT id FROM {_T_CASES} WHERE product_id = %s",
                (case["product_id"],),
            ).fetchone()

            if row:
                self._exec(
                    conn,
                    f"""UPDATE {_T_CASES}
                       SET current_state=%s, current_level=%s, status=%s, resolution_type=%s,
                           last_detected_at=%s, updated_at=%s
                       WHERE product_id=%s""",
                    (
                        case.get("current_state"),
                        case.get("current_level"),
                        case.get("status", "open"),
                        case.get("resolution_type"),
                        case.get("last_detected_at", now),
                        now,
                        case["product_id"],
                    ),
                )
                case_id = row["id"]
            else:
                case_id = self._insert_returning_id(
                    conn,
                    f"""INSERT INTO {_T_CASES}
                       (product_id, current_state, current_level, status, resolution_type,
                        first_detected_at, last_detected_at, last_notified_at, created_at, updated_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       RETURNING id""",
                    (
                        case["product_id"],
                        case.get("current_state"),
                        case.get("current_level"),
                        case.get("status", "open"),
                        case.get("resolution_type"),
                        case.get("first_detected_at", now),
                        case.get("last_detected_at", now),
                        case.get("last_notified_at"),
                        now,
                        now,
                    ),
                )
            return case_id

    def get_cases_by_products(self, product_ids: list[str]) -> dict[str, dict]:
        """批量查 case（一次 SQL 连接）"""
        if not product_ids:
            return {}
        placeholders = ",".join("%s" for _ in product_ids)
        with self._conn() as conn:
            rows = self._exec(
                conn,
                f"SELECT * FROM {_T_CASES} WHERE product_id IN ({placeholders})",
                tuple(product_ids),
            ).fetchall()
        return {r["product_id"]: dict(r) for r in rows}

    def get_last_events_by_cases(self, case_ids: list[int]) -> dict[int, dict]:
        """批量查每个 case 的最后一条事件"""
        if not case_ids:
            return {}
        placeholders = ",".join("%s" for _ in case_ids)
        with self._conn() as conn:
            rows = self._exec(
                conn,
                f"""SELECT e.* FROM {_T_EVENTS} e
                    INNER JOIN (
                        SELECT case_id, MAX(created_at) as max_created
                        FROM {_T_EVENTS}
                        WHERE case_id IN ({placeholders})
                        GROUP BY case_id
                    ) latest ON e.case_id = latest.case_id AND e.created_at = latest.max_created""",
                tuple(case_ids),
            ).fetchall()
        result: dict[int, dict] = {}
        for r in rows:
            d = dict(r)
            try:
                d["reason"] = _json.loads(d.get("reason") or "[]")
            except Exception:
                d["reason"] = []
            result[d["case_id"]] = d
        return result

    def get_case_by_product(self, product_id: str) -> dict | None:
        """按 product_id 查 case"""
        with self._conn() as conn:
            row = self._exec(
                conn,
                f"SELECT * FROM {_T_CASES} WHERE product_id = %s",
                (product_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_case(self, case_id: int) -> dict | None:
        """按 case_id 查"""
        with self._conn() as conn:
            row = self._exec(
                conn,
                f"SELECT * FROM {_T_CASES} WHERE id = %s",
                (case_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_open_cases(self) -> list[dict]:
        """列所有 open case"""
        with self._conn() as conn:
            rows = self._exec(
                conn,
                f"SELECT * FROM {_T_CASES} WHERE status = 'open' ORDER BY id",
            ).fetchall()
        return [dict(r) for r in rows]

    def list_all_cases(
        self,
        status: str = "",
        level: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict], int]:
        """列出所有 case（带过滤 + 分页），返回 (cases, total)"""
        offset = (page - 1) * page_size
        where_clauses: list[str] = []
        params: list[Any] = []

        if status:
            if status == "active":
                where_clauses.append("status IN ('open', 'acknowledged')")
            elif status == "history":
                where_clauses.append("status IN ('resolved', 'closed')")
            else:
                where_clauses.append("status = %s")
                params.append(status)
        if level:
            where_clauses.append("current_level = %s")
            params.append(level)

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        order_sql = (
            "ORDER BY CASE current_level "
            "WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, "
            "last_detected_at DESC"
        )

        with self._conn() as conn:
            count_row = self._exec(
                conn,
                f"SELECT COUNT(*) AS cnt FROM {_T_CASES} {where_sql}",
                tuple(params),
            ).fetchone()
            total = count_row["cnt"] if count_row else 0

            rows = self._exec(
                conn,
                f"SELECT * FROM {_T_CASES} {where_sql} "
                f"{order_sql} LIMIT %s OFFSET %s",
                tuple(params) + (page_size, offset),
            ).fetchall()

        return [dict(r) for r in rows], total

    def get_stats(self) -> dict[str, int]:
        """告警统计：按 level 分组计数"""
        with self._conn() as conn:
            rows = self._exec(
                conn,
                f"""SELECT current_level, COUNT(*) as cnt
                   FROM {_T_CASES}
                   WHERE status IN ('open', 'acknowledged')
                   GROUP BY current_level""",
            ).fetchall()
            row = self._exec(
                conn,
                f"SELECT COUNT(*) AS cnt FROM {_T_CASES} "
                "WHERE status IN ('resolved', 'closed')",
            ).fetchone()
        stats: dict[str, int] = {"critical": 0, "warning": 0, "info": 0, "resolved": 0}
        for r in rows:
            level: str = r["current_level"] or "info"
            if level in stats:
                stats[level] = r["cnt"]
        stats["resolved"] = row["cnt"] if row else 0
        return stats

    def update_case_status(
        self,
        case_id: int,
        status: str,
        resolution_type: str | None = None,
    ) -> None:
        """更新 case 状态（人工 resolve / re-open）"""
        now = datetime.now().isoformat()
        with self._lock, self._conn() as conn:
            if resolution_type is not None:
                self._exec(
                    conn,
                    f"""UPDATE {_T_CASES}
                       SET status=%s, resolution_type=%s, updated_at=%s
                       WHERE id=%s""",
                    (status, resolution_type, now, case_id),
                )
            else:
                self._exec(
                    conn,
                    f"UPDATE {_T_CASES} SET status=%s, updated_at=%s WHERE id=%s",
                    (status, now, case_id),
                )

    def update_event_case_id(self, event_id: int, case_id: int) -> None:
        """回填事件的 case_id（CREATE 场景时序修补）"""
        with self._lock, self._conn() as conn:
            self._exec(
                conn,
                f"UPDATE {_T_EVENTS} SET case_id = %s WHERE id = %s",
                (case_id, event_id),
            )

    def set_case_notified(self, case_id: int, notified_at: str) -> None:
        """更新 case 的 last_notified_at（通知触达）"""
        with self._lock, self._conn() as conn:
            self._exec(
                conn,
                f"UPDATE {_T_CASES} SET last_notified_at = %s WHERE id = %s",
                (notified_at, case_id),
            )

    # ──────────── Events ────────────

    def insert_event(self, event: dict) -> int:
        """插入事件"""
        now = datetime.now().isoformat()
        with self._lock, self._conn() as conn:
            return self._insert_returning_id(
                conn,
                f"""INSERT INTO {_T_EVENTS}
                   (case_id, event_type, from_state, to_state, qty, stock_days,
                    reason, notified, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (
                    event["case_id"],
                    event["event_type"],
                    event.get("from_state"),
                    event.get("to_state"),
                    event.get("qty"),
                    event.get("stock_days"),
                    _json.dumps(event.get("reason", []), ensure_ascii=False),
                    bool(event.get("notified", False)),
                    now,
                ),
            )

    def list_events_by_case(self, case_id: int) -> list[dict]:
        """列 case 的所有事件（按时间排序）"""
        with self._conn() as conn:
            rows = self._exec(
                conn,
                f"SELECT * FROM {_T_EVENTS} WHERE case_id = %s ORDER BY created_at, id",
                (case_id,),
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["reason"] = _json.loads(d.get("reason") or "[]")
            except (ValueError, TypeError):
                d["reason"] = []
            result.append(d)
        return result

    def get_last_event(self, case_id: int) -> dict | None:
        """获取 case 的最后一条事件"""
        with self._conn() as conn:
            row = self._exec(
                conn,
                f"""SELECT * FROM {_T_EVENTS}
                   WHERE case_id = %s ORDER BY created_at DESC, id DESC LIMIT 1""",
                (case_id,),
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["reason"] = _json.loads(d.get("reason") or "[]")
        except (ValueError, TypeError):
            d["reason"] = []
        return d

    # ──────────── Policies ────────────

    def save_policy(self, policy: dict) -> int:
        """保存通知策略（INSERT OR REPLACE 语义）"""
        now = datetime.now().isoformat()
        params = (
            policy["policy_name"],
            policy.get("alert_level"),
            policy.get("inventory_state"),
            policy.get("category"),
            policy.get("notify_email"),
            bool(policy.get("notify_on_upgrade", 1)),
            bool(policy.get("notify_on_remind", 1)),
            bool(policy.get("notify_on_resolve", 1)),
            bool(policy.get("enabled", 1)),
            policy.get("created_at", now),
            now,
        )
        with self._lock, self._conn() as conn:
            policy_id = policy.get("id")
            if policy_id is None:
                return self._insert_returning_id(
                    conn,
                    f"""INSERT INTO {_T_POLICIES}
                       (policy_name, alert_level, inventory_state, category, notify_email,
                        notify_on_upgrade, notify_on_remind, notify_on_resolve, enabled,
                        created_at, updated_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       RETURNING id""",
                    params,
                )
            self._exec(
                conn,
                f"""INSERT INTO {_T_POLICIES}
                   (id, policy_name, alert_level, inventory_state, category, notify_email,
                    notify_on_upgrade, notify_on_remind, notify_on_resolve, enabled,
                    created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (id) DO UPDATE SET
                        policy_name = EXCLUDED.policy_name,
                        alert_level = EXCLUDED.alert_level,
                        inventory_state = EXCLUDED.inventory_state,
                        category = EXCLUDED.category,
                        notify_email = EXCLUDED.notify_email,
                        notify_on_upgrade = EXCLUDED.notify_on_upgrade,
                        notify_on_remind = EXCLUDED.notify_on_remind,
                        notify_on_resolve = EXCLUDED.notify_on_resolve,
                        enabled = EXCLUDED.enabled,
                        created_at = EXCLUDED.created_at,
                        updated_at = EXCLUDED.updated_at""",
                (policy_id,) + params,
            )
            return int(policy_id)

    def list_policies(self, enabled_only: bool = True) -> list[dict]:
        """列所有 policy"""
        sql = f"SELECT * FROM {_T_POLICIES}"
        if enabled_only:
            sql += " WHERE enabled = true"
        sql += " ORDER BY id"
        with self._conn() as conn:
            rows = self._exec(conn, sql).fetchall()
        return [dict(r) for r in rows]
