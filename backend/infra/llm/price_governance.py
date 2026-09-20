"""模型价格版本治理：导入、分离审核和 24 小时灰度生效。"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg2.extras

from backend.app.api.routes.price_dto import coverage_report, parse_price_rows
from backend.config.database import MEMORY_DB_CONFIG


class PriceGovernanceUnavailable(RuntimeError):
    """价格治理存储不可用。"""


class PostgresPriceGovernanceRepository:
    """价格版本状态机的 PostgreSQL 实现。"""

    def __init__(self, connection_factory=None):
        self._connection_factory = connection_factory or self._connect
        prefix = os.getenv("BUDGET_PG_TABLE_PREFIX", "")
        self._price_table = f"{prefix}model_price"
        self._version_table = f"{prefix}model_price_versions"
        self._review_table = f"{prefix}model_price_reviews"

    @staticmethod
    def _connect():
        import psycopg2

        return psycopg2.connect(**MEMORY_DB_CONFIG)

    def list_versions(self, limit: int = 100) -> list[dict[str, Any]]:
        try:
            with self._connection_factory() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        f"""SELECT version, source, imported_by, status,
                                   effective_from, reviewer_1, reviewer_2,
                                   canary_started_at, canary_completed_at,
                                   coverage_ratio, missing_price_count,
                                   price_calculation_error_count,
                                   created_at, updated_at
                            FROM {self._version_table}
                            ORDER BY created_at DESC LIMIT %s""",
                        (max(1, min(limit, 200)),),
                    )
                    return [dict(row) for row in cur.fetchall()]
        except Exception as exc:
            raise PriceGovernanceUnavailable("价格版本读取失败") from exc

    def import_version(
        self,
        rows: list[dict[str, Any]],
        *,
        version: str,
        source: str,
        imported_by: str,
        effective_from: datetime | None = None,
    ) -> dict[str, Any]:
        normalized = parse_price_rows(rows)
        if not version.strip() or not source.strip() or not imported_by.strip():
            raise ValueError("价格版本、来源和导入人不能为空")
        coverage = coverage_report(normalized)
        effective = effective_from or datetime.now(timezone.utc)
        try:
            with self._connection_factory() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""INSERT INTO {self._version_table}
                            (version, source, imported_by, effective_from,
                             coverage_ratio, missing_price_count,
                             price_calculation_error_count)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                        (
                            version, source, imported_by, effective,
                            coverage["coverage_ratio"], coverage["missing_price_count"],
                            coverage["price_calculation_error_count"],
                        ),
                    )
                    for row in normalized:
                        cur.execute(
                            f"""INSERT INTO {self._price_table}
                                (model_name, component, dimension, price_per_unit,
                                 unit, currency, price_table_version, source,
                                 effective_from, approval_status)
                                VALUES (%s, %s, %s, %s, %s, 'USD', %s, %s, %s,
                                        'pending')""",
                            (
                                row["model_name"], row["component"], row["dimension"],
                                row["price_per_unit"], row["unit"], version,
                                source, effective,
                            ),
                        )
            return {
                "version": version,
                "status": "pending",
                "row_count": len(normalized),
                **coverage,
            }
        except ValueError:
            raise
        except Exception as exc:
            raise PriceGovernanceUnavailable("价格版本导入失败") from exc

    def review_version(
        self,
        version: str,
        *,
        reviewer: str,
        decision: str,
        reason: str = "",
    ) -> dict[str, Any]:
        if decision not in {"approve", "reject"}:
            raise ValueError("审核决定必须是 approve 或 reject")
        if not reviewer.strip():
            raise ValueError("审核人不能为空")
        try:
            with self._connection_factory() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        f"SELECT * FROM {self._version_table} WHERE version=%s FOR UPDATE",
                        (version,),
                    )
                    row = cur.fetchone()
                    if row is None:
                        raise LookupError("价格版本不存在")
                    if reviewer in {row["imported_by"], row["reviewer_1"], row["reviewer_2"]}:
                        raise ValueError("导入人不能审核，且同一审核人不能重复审核")
                    if row["status"] not in {"pending", "reviewed_1"}:
                        raise ValueError(f"当前版本状态不允许审核: {row['status']}")
                    cur.execute(
                        f"""INSERT INTO {self._review_table}
                            (version, reviewer, decision, reason)
                            VALUES (%s, %s, %s, %s)""",
                        (version, reviewer, decision, reason[:1000]),
                    )
                    if decision == "reject":
                        next_status = "rejected"
                        cur.execute(
                            f"""UPDATE {self._version_table}
                                SET status=%s, updated_at=now()
                                WHERE version=%s""",
                            (next_status, version),
                        )
                    elif row["status"] == "pending":
                        next_status = "reviewed_1"
                        cur.execute(
                            f"""UPDATE {self._version_table}
                                SET status=%s, reviewer_1=%s, updated_at=now()
                                WHERE version=%s""",
                            (next_status, reviewer, version),
                        )
                    else:
                        next_status = "scheduled"
                        cur.execute(
                            f"""UPDATE {self._version_table}
                                SET status=%s, reviewer_2=%s, updated_at=now()
                                WHERE version=%s""",
                            (next_status, reviewer, version),
                        )
                    return {"version": version, "status": next_status, "reviewer": reviewer}
        except (LookupError, ValueError):
            raise
        except Exception as exc:
            raise PriceGovernanceUnavailable("价格审核失败") from exc

    def canary_version(
        self,
        version: str,
        *,
        action: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current_time = now or datetime.now(timezone.utc)
        try:
            with self._connection_factory() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        f"SELECT * FROM {self._version_table} WHERE version=%s FOR UPDATE",
                        (version,),
                    )
                    row = cur.fetchone()
                    if row is None:
                        raise LookupError("价格版本不存在")
                    if action == "start":
                        if row["status"] != "scheduled":
                            raise ValueError("只有完成双人审核的版本才能进入灰度")
                        cur.execute(
                            f"""UPDATE {self._version_table}
                                SET status='canary', canary_started_at=%s,
                                    updated_at=now()
                                WHERE version=%s""",
                            (current_time, version),
                        )
                        return {"version": version, "status": "canary"}
                    if action != "complete":
                        raise ValueError("灰度动作必须是 start 或 complete")
                    if row["status"] != "canary":
                        raise ValueError("只有 canary 状态才能完成灰度")
                    started_at = row["canary_started_at"]
                    if not started_at or current_time - started_at < timedelta(hours=24):
                        raise ValueError("价格版本必须灰度满 24 小时后才能生效")
                    if (
                        float(row["coverage_ratio"] or 0) < 1.0
                        or row["missing_price_count"] != 0
                        or row["price_calculation_error_count"] != 0
                    ):
                        raise ValueError("覆盖率必须 100%，缺价和价格计算错误必须为 0")
                    cur.execute(
                        f"""SELECT model_name, component, dimension,
                                   price_per_unit, unit, currency,
                                   price_table_version, source,
                                   effective_from, effective_to
                            FROM {self._price_table}
                            WHERE price_table_version=%s AND approval_status='pending'""",
                        (version,),
                    )
                    price_rows = cur.fetchall()
                    for price_row in price_rows:
                        price_values = tuple(
                            price_row[field]
                            for field in (
                                "model_name", "component", "dimension",
                                "price_per_unit", "unit", "currency",
                                "price_table_version", "source",
                                "effective_from", "effective_to",
                            )
                        )
                        cur.execute(
                            f"""INSERT INTO {self._price_table}
                                (model_name, component, dimension, price_per_unit,
                                 unit, currency, price_table_version, source,
                                 effective_from, effective_to, approval_status,
                                 reviewer_1, reviewer_2)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                        'approved', %s, %s)""",
                            (*price_values, row["reviewer_1"], row["reviewer_2"]),
                        )
                    cur.execute(
                        f"""UPDATE {self._version_table}
                            SET status='expired', updated_at=now()
                            WHERE status='active' AND version <> %s""",
                        (version,),
                    )
                    cur.execute(
                        f"""UPDATE {self._version_table}
                            SET status='active', canary_completed_at=%s,
                                updated_at=now()
                            WHERE version=%s""",
                        (current_time, version),
                    )
                    return {"version": version, "status": "active", "row_count": len(price_rows)}
        except (LookupError, ValueError):
            raise
        except Exception as exc:
            raise PriceGovernanceUnavailable("价格灰度生效失败") from exc


__all__ = ["PostgresPriceGovernanceRepository", "PriceGovernanceUnavailable"]
