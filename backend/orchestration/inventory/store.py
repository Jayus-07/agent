"""orchestration/inventory/store.py — 库存告警数据访问层

设计：
- 4 张 SQLite 表：thresholds / cases / events / policies
- 线程安全（threading.RLock）
- 模块级单例（get_inventory_store()）
- 所有时间戳 ISO 格式字符串（与 workflow_runs.db 保持一致）
"""
from __future__ import annotations

import json as _json
import os
import sqlite3
import threading
from datetime import datetime
from typing import Any

from backend.shared.logger import logger


# ─────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────

# 阈值规则（决策 1）
THRESHOLDS_SCHEMA = """
CREATE TABLE IF NOT EXISTS inventory_threshold_rules (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_type           TEXT NOT NULL,           -- sku / category / global
    product_id          TEXT,                    -- sku 规则专用
    category            TEXT,                    -- category 规则专用

    min_qty             INTEGER NOT NULL,
    days_of_stock       INTEGER DEFAULT 7,      -- 预计售罄天数
    sales_window_days   INTEGER DEFAULT 30,     -- 销售速度统计窗口
    alert_level         TEXT DEFAULT 'warning', -- info/warning/critical
    enabled             BOOLEAN DEFAULT true,

    created_at          TEXT,
    updated_at          TEXT
);
CREATE INDEX IF NOT EXISTS idx_thresholds_match
    ON inventory_threshold_rules(rule_type, product_id, category, enabled);
"""

# 告警 case（决策 2.5 当前工单）
CASES_SCHEMA = """
CREATE TABLE IF NOT EXISTS inventory_alert_cases (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id          TEXT NOT NULL UNIQUE,  -- 一件商品永远一个 case
    current_state       TEXT,                 -- low/critical/out_of_stock
    current_level       TEXT,                 -- info/warning/critical
    status              TEXT,                 -- open/acknowledged/resolved/closed
    resolution_type     TEXT,                 -- AUTO_RECOVERED / MANUAL_RESOLVED / MANUAL_IGNORED

    first_detected_at   TEXT,
    last_detected_at    TEXT,
    last_notified_at    TEXT,

    created_at          TEXT,
    updated_at          TEXT
);
CREATE INDEX IF NOT EXISTS idx_cases_status
    ON inventory_alert_cases(status, product_id);
"""

# 事件溯源（决策 2.5）
EVENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS inventory_alert_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id         INTEGER NOT NULL,
    event_type      TEXT NOT NULL,    -- created/upgraded/reminded/resolved/reopened
    from_state      TEXT,
    to_state        TEXT,
    qty             INTEGER,
    stock_days      FLOAT,
    reason          TEXT,            -- JSON
    notified        BOOLEAN DEFAULT false,
    created_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_case
    ON inventory_alert_events(case_id, created_at);
"""

# 通知策略（决策 3）
POLICIES_SCHEMA = """
CREATE TABLE IF NOT EXISTS notification_policies (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_name         TEXT NOT NULL,
    alert_level         TEXT,            -- NULL = 全部
    inventory_state     TEXT,            -- NULL = 全部
    category            TEXT,            -- NULL = 全部
    notify_email        TEXT,            -- 多个用 ; 分隔
    notify_on_upgrade   INTEGER DEFAULT 1,
    notify_on_remind    INTEGER DEFAULT 1,
    notify_on_resolve   INTEGER DEFAULT 1,  -- 恢复通知默认发
    enabled             INTEGER DEFAULT 1,
    created_at          TEXT,
    updated_at          TEXT
);
CREATE INDEX IF NOT EXISTS idx_policies_match
    ON notification_policies(enabled, alert_level, inventory_state);
"""


# ─────────────────────────────────────────────────────────────
# Store
# ─────────────────────────────────────────────────────────────

class InventoryStore:
    """库存告警数据访问（4 张表共用 store）"""

    def __init__(self, db_path: str = "data/inventory_alerts.db"):
        self._db_path = db_path
        self._lock = threading.RLock()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        # enable FK + WAL
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        with self._lock, self._conn() as conn:
            conn.executescript(THRESHOLDS_SCHEMA)
            conn.executescript(CASES_SCHEMA)
            conn.executescript(EVENTS_SCHEMA)
            conn.executescript(POLICIES_SCHEMA)
            conn.commit()
        logger.debug(f"[InventoryStore] 初始化: {self._db_path}")

    # ──────────── Thresholds ────────────

    def save_threshold(self, rule: dict) -> int:
        """保存阈值规则"""
        now = datetime.now().isoformat()
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                """INSERT OR REPLACE INTO inventory_threshold_rules
                   (id, rule_type, product_id, category, min_qty, days_of_stock,
                    sales_window_days, alert_level, enabled, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    rule.get("id"),
                    rule["rule_type"],
                    rule.get("product_id"),
                    rule.get("category"),
                    rule["min_qty"],
                    rule.get("days_of_stock", 7),
                    rule.get("sales_window_days", 30),
                    rule.get("alert_level", "warning"),
                    rule.get("enabled", True),
                    rule.get("created_at", now),
                    now,
                ),
            )
            conn.commit()
            return cur.lastrowid

    def list_thresholds(self, enabled_only: bool = True) -> list[dict]:
        """列出所有阈值规则"""
        sql = "SELECT * FROM inventory_threshold_rules"
        if enabled_only:
            sql += " WHERE enabled = true"
        sql += " ORDER BY id"
        with self._lock, self._conn() as conn:
            rows = conn.execute(sql).fetchall()
        return [dict(r) for r in rows]

    def find_threshold(
        self,
        product_id: str | None = None,
        category: str | None = None,
    ) -> dict | None:
        """按优先级找最佳规则：sku > category > global

        优先：sku 匹配 → category 匹配 → global
        """
        rules = self.list_thresholds(enabled_only=True)
        # 1. sku 匹配
        if product_id:
            for r in rules:
                if r["rule_type"] == "sku" and r["product_id"] == product_id:
                    return r
        # 2. category 匹配
        if category:
            for r in rules:
                if r["rule_type"] == "category" and r["category"] == category:
                    return r
        # 3. global 兜底
        for r in rules:
            if r["rule_type"] == "global":
                return r
        return None

    # ──────────── Cases ────────────

    def upsert_case(self, case: dict) -> int:
        """insert or update case（按 product_id UNIQUE 约束）"""
        now = datetime.now().isoformat()
        with self._lock, self._conn() as conn:
            # 看是否存在
            row = conn.execute(
                "SELECT id FROM inventory_alert_cases WHERE product_id = ?",
                (case["product_id"],),
            ).fetchone()

            if row:
                # update
                conn.execute(
                    """UPDATE inventory_alert_cases
                       SET current_state=?, current_level=?, status=?, resolution_type=?,
                           last_detected_at=?, updated_at=?
                       WHERE product_id=?""",
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
                # insert
                cur = conn.execute(
                    """INSERT INTO inventory_alert_cases
                       (product_id, current_state, current_level, status, resolution_type,
                        first_detected_at, last_detected_at, last_notified_at, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                case_id = cur.lastrowid
            conn.commit()
            return case_id

    def get_cases_by_products(self, product_ids: list[str]) -> dict[str, dict]:
        """批量查 case（一次 SQL 连接）"""
        if not product_ids:
            return {}
        placeholders = ",".join("?" for _ in product_ids)
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM inventory_alert_cases WHERE product_id IN ({placeholders})",
                product_ids,
            ).fetchall()
        return {r["product_id"]: dict(r) for r in rows}

    def get_last_events_by_cases(self, case_ids: list[int]) -> dict[int, dict]:
        """批量查每个 case 的最后一条事件"""
        if not case_ids:
            return {}
        placeholders = ",".join("?" for _ in case_ids)
        with self._lock, self._conn() as conn:
            # 子查询取每个 case 最大的 created_at，再 JOIN 回去拿完整行
            rows = conn.execute(
                f"""SELECT e.* FROM inventory_alert_events e
                    INNER JOIN (
                        SELECT case_id, MAX(created_at) as max_created
                        FROM inventory_alert_events
                        WHERE case_id IN ({placeholders})
                        GROUP BY case_id
                    ) latest ON e.case_id = latest.case_id AND e.created_at = latest.max_created""",
                case_ids,
            ).fetchall()
        result = {}
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
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM inventory_alert_cases WHERE product_id = ?",
                (product_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_case(self, case_id: int) -> dict | None:
        """按 case_id 查"""
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM inventory_alert_cases WHERE id = ?",
                (case_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_open_cases(self) -> list[dict]:
        """列所有 open case"""
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM inventory_alert_cases WHERE status = 'open' ORDER BY id"
            ).fetchall()
        return [dict(r) for r in rows]

    def list_all_cases(
        self,
        status: str = "",
        level: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict], int]:
        """列出所有 case（带过滤 + 分页）

        Returns:
            (cases, total)
        """
        offset = (page - 1) * page_size
        where_clauses: list[str] = []
        params: list[Any] = []

        if status:
            if status == "active":
                where_clauses.append("status IN ('open', 'acknowledged')")
            elif status == "history":
                where_clauses.append("status IN ('resolved', 'closed')")
            else:
                where_clauses.append("status = ?")
                params.append(status)
        if level:
            where_clauses.append("current_level = ?")
            params.append(level)

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        order_sql = (
            "ORDER BY CASE current_level "
            "WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, "
            "last_detected_at DESC"
        )

        with self._lock, self._conn() as conn:
            count_row = conn.execute(
                f"SELECT COUNT(*) FROM inventory_alert_cases {where_sql}",
                params,
            ).fetchone()
            total = count_row[0] if count_row else 0

            rows = conn.execute(
                f"SELECT * FROM inventory_alert_cases {where_sql} "
                f"{order_sql} LIMIT ? OFFSET ?",
                params + [page_size, offset],
            ).fetchall()

        return [dict(r) for r in rows], total

    def get_stats(self) -> dict[str, int]:
        """告警统计：按 level 分组计数"""
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                """SELECT current_level, COUNT(*) as cnt
                   FROM inventory_alert_cases
                   WHERE status IN ('open', 'acknowledged')
                   GROUP BY current_level"""
            ).fetchall()
        stats: dict[str, int] = {"critical": 0, "warning": 0, "info": 0, "resolved": 0}
        for r in rows:
            level: str = r[0] or "info"
            if level in stats:
                stats[level] = r[1]
        # resolved 计数
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM inventory_alert_cases "
                "WHERE status IN ('resolved', 'closed')"
            ).fetchone()
        stats["resolved"] = row[0] if row else 0
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
                conn.execute(
                    """UPDATE inventory_alert_cases
                       SET status=?, resolution_type=?, updated_at=?
                       WHERE id=?""",
                    (status, resolution_type, now, case_id),
                )
            else:
                conn.execute(
                    "UPDATE inventory_alert_cases SET status=?, updated_at=? WHERE id=?",
                    (status, now, case_id),
                )
            conn.commit()

    # ──────────── Events ────────────

    def insert_event(self, event: dict) -> int:
        """插入事件"""
        now = datetime.now().isoformat()
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO inventory_alert_events
                   (case_id, event_type, from_state, to_state, qty, stock_days,
                    reason, notified, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event["case_id"],
                    event["event_type"],
                    event.get("from_state"),
                    event.get("to_state"),
                    event.get("qty"),
                    event.get("stock_days"),
                    _json.dumps(event.get("reason", []), ensure_ascii=False),
                    event.get("notified", False),
                    now,
                ),
            )
            conn.commit()
            return cur.lastrowid

    def list_events_by_case(self, case_id: int) -> list[dict]:
        """列 case 的所有事件（按时间排序）"""
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM inventory_alert_events WHERE case_id = ? ORDER BY created_at, id",
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
        with self._lock, self._conn() as conn:
            row = conn.execute(
                """SELECT * FROM inventory_alert_events
                   WHERE case_id = ? ORDER BY created_at DESC, id DESC LIMIT 1""",
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
        """保存通知策略"""
        now = datetime.now().isoformat()
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                """INSERT OR REPLACE INTO notification_policies
                   (id, policy_name, alert_level, inventory_state, category, notify_email,
                    notify_on_upgrade, notify_on_remind, notify_on_resolve, enabled,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    policy.get("id"),
                    policy["policy_name"],
                    policy.get("alert_level"),
                    policy.get("inventory_state"),
                    policy.get("category"),
                    policy.get("notify_email"),
                    policy.get("notify_on_upgrade", 1),
                    policy.get("notify_on_remind", 1),
                    policy.get("notify_on_resolve", 1),
                    policy.get("enabled", 1),
                    policy.get("created_at", now),
                    now,
                ),
            )
            conn.commit()
            return cur.lastrowid

    def list_policies(self, enabled_only: bool = True) -> list[dict]:
        """列所有 policy"""
        sql = "SELECT * FROM notification_policies"
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY id"
        with self._lock, self._conn() as conn:
            rows = conn.execute(sql).fetchall()
        return [dict(r) for r in rows]

    def find_matching_policies(
        self,
        alert_level: str,
        inventory_state: str,
        category: str | None = None,
    ) -> list[dict]:
        """多维 OR 匹配（决策 3 选 C）：命中所有满足条件的 Policy

        每个字段：相等 OR NULL（NULL=全部）
        """
        all_p = self.list_policies(enabled_only=True)
        matched = []
        for p in all_p:
            # alert_level: 相等 or NULL
            if p["alert_level"] is not None and p["alert_level"] != alert_level:
                continue
            # inventory_state: 相等 or NULL
            if p["inventory_state"] is not None and p["inventory_state"] != inventory_state:
                continue
            # category: 相等 or NULL
            if p["category"] is not None and p["category"] != category:
                continue
            matched.append(p)
        return matched


# ─────────────────────────────────────────────────────────────
# 模块级单例
# ─────────────────────────────────────────────────────────────

_store: InventoryStore | None = None


def get_inventory_store() -> InventoryStore:
    """获取 InventoryStore 单例（默认 data/inventory_alerts.db）"""
    global _store
    if _store is None:
        _store = InventoryStore()
    return _store