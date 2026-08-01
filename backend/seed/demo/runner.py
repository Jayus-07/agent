"""seed/demo/runner.py — Demo 场景编排 + daily_reports 存储

设计：
- DailyReportStore：SQLite 单表，存日报完整内容 + KPI 摘要
- DemoRunner：编排 demo 场景（seed / trigger workflow / agent prompt）
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

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS daily_reports (
    id              TEXT PRIMARY KEY,
    report_date     TEXT NOT NULL,
    report_type     TEXT NOT NULL DEFAULT 'daily_report',
    status          TEXT NOT NULL DEFAULT 'success',
    kpi_summary     TEXT,
    report_content  TEXT NOT NULL,
    trace_id        TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_daily_reports_date
    ON daily_reports(report_date DESC);
CREATE INDEX IF NOT EXISTS idx_daily_reports_type
    ON daily_reports(report_type, report_date DESC);
"""


class DailyReportStore:
    """日报 SQLite 存储（线程安全）"""

    def __init__(self, db_path: str = "data/daily_reports.db"):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        with self._lock, self._conn() as conn:
            conn.executescript(SCHEMA_SQL)
            conn.commit()

    def save(self, report: dict[str, Any]) -> str:
        """保存日报，返回 report id"""
        # 安全转换：确保所有参数是 SQLite 兼容类型
        def _str(v: Any) -> str:
            if v is None:
                return ""
            if isinstance(v, str):
                return v
            return str(v)

        with self._lock, self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO daily_reports
                   (id, report_date, report_type, status, kpi_summary,
                    report_content, trace_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    _str(report.get("id", "")),
                    _str(report.get("report_date", "")),
                    _str(report.get("report_type", "daily_report")),
                    _str(report.get("status", "success")),
                    _json.dumps(report.get("kpi_summary", {}), ensure_ascii=False),
                    _str(report.get("report_content", "")),
                    _str(report.get("trace_id", "")),
                    _str(report.get("created_at", datetime.now().isoformat())),
                ),
            )
            conn.commit()
        logger.debug(f"[DailyReportStore] 保存日报 {report['id']}")
        return report["id"]

    def get(self, report_id: str) -> dict[str, Any] | None:
        """获取单条日报详情"""
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM daily_reports WHERE id = ?", (report_id,)
            ).fetchone()
        if row is None:
            return None
        data = dict(row)
        try:
            data["kpi_summary"] = _json.loads(data.get("kpi_summary") or "{}")
        except Exception:
            data["kpi_summary"] = {}
        return data

    def list(
        self,
        report_type: str = "daily_report",
        page: int = 1,
        page_size: int = 20,
    ) -> list[dict[str, Any]]:
        """列出日报列表（不含完整 content，仅摘要）"""
        offset = (page - 1) * page_size
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                """SELECT id, report_date, report_type, status, kpi_summary,
                          trace_id, created_at
                   FROM daily_reports
                   WHERE report_type = ?
                   ORDER BY report_date DESC LIMIT ? OFFSET ?""",
                (report_type, page_size, offset),
            ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            try:
                d["kpi_summary"] = _json.loads(d.get("kpi_summary") or "{}")
            except Exception:
                d["kpi_summary"] = {}
            results.append(d)
        return results

    def get_latest(self, report_type: str = "daily_report") -> dict[str, Any] | None:
        """获取最新一条日报（含完整 content）"""
        with self._lock, self._conn() as conn:
            row = conn.execute(
                """SELECT * FROM daily_reports
                   WHERE report_type = ? AND status = 'success'
                   ORDER BY report_date DESC LIMIT 1""",
                (report_type,),
            ).fetchone()
        if row is None:
            return None
        data = dict(row)
        try:
            data["kpi_summary"] = _json.loads(data.get("kpi_summary") or "{}")
        except Exception:
            data["kpi_summary"] = {}
        return data


# 模块级单例
_store: DailyReportStore | None = None


def get_daily_report_store() -> DailyReportStore:
    global _store
    if _store is None:
        _store = DailyReportStore()
    return _store
