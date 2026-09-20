"""Q8 模型价格表：PostgreSQL 权威记录与 USD 六位精度计算。"""
from __future__ import annotations

import os
import threading
import time
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any


PRICE_DIMENSIONS = frozenset({
    "input", "output", "cache_read", "cache_write", "reasoning", "tool_call",
})
_SIX_PLACES = Decimal("0.000001")
_PRICE_CACHE_TTL_SECONDS = 30.0
_price_cache: dict[tuple[str, str], tuple[float, PriceTable]] = {}
_price_cache_lock = threading.RLock()
_logger = logging.getLogger(__name__)
_REQUIRED_DIMENSIONS = {
    "llm": frozenset({"input", "output"}),
    "embedding": frozenset({"input"}),
    "rerank": frozenset({"input"}),
}


def _record_price_metric(component: str, result: str) -> None:
    try:
        from backend.observability import metrics

        metrics.budget_price_total.labels(
            component=component or "unknown", result=result
        ).inc()
    except Exception as exc:
        _logger.debug("[Pricing] price metric write failed: %s", exc)


class MissingModelPrice(RuntimeError):
    """受硬预算约束的模型缺少已审核生效价格。"""

    code = "BUDGET_EXCEEDED"
    retryable = False

    def __init__(self, model_name: str, component: str):
        self.model_name = model_name
        self.component = component
        super().__init__(f"模型价格表缺少已生效价格: {model_name}/{component}")


class PriceTableUnavailable(RuntimeError):
    """价格权威存储不可用；硬预算模式必须拒绝继续调用。"""

    code = "BUDGET_EXCEEDED"
    retryable = False


@dataclass(frozen=True)
class PriceLine:
    model_name: str
    component: str
    dimension: str
    price_per_unit: Decimal
    unit: str = "per_1m_tokens"
    price_table_version: str = ""


class PriceTable:
    """不可变价格快照；调用链只依赖快照，不直接拼 SQL。"""

    def __init__(self, rows: list[PriceLine]):
        self._prices: dict[tuple[str, str, str], PriceLine] = {}
        for row in rows:
            # 同一版本在数据库中按生效时间倒序返回，首条是当前有效价；
            # setdefault 同时避免历史行覆盖当前行。
            self._prices.setdefault(
                (row.model_name, row.component, row.dimension), row
            )

    @classmethod
    def from_rows(cls, rows: list[dict[str, Any]]) -> "PriceTable":
        normalized: list[PriceLine] = []
        for row in rows:
            dimension = str(row.get("dimension") or "")
            if dimension not in PRICE_DIMENSIONS:
                raise ValueError(f"非法价格维度: {dimension}")
            price = Decimal(str(row.get("price_per_unit") or "0"))
            if price < 0:
                raise ValueError("价格不能为负数")
            normalized.append(
                PriceLine(
                    model_name=str(row.get("model_name") or ""),
                    component=str(row.get("component") or "llm"),
                    dimension=dimension,
                    price_per_unit=price.quantize(_SIX_PLACES, rounding=ROUND_HALF_UP),
                    unit=str(row.get("unit") or "per_1m_tokens"),
                    price_table_version=str(row.get("price_table_version") or ""),
                )
            )
        return cls(normalized)

    def require(self, model_name: str, component: str, *, enforce: bool) -> dict[str, PriceLine] | None:
        rows = {
            dimension: row
            for (model, row_component, dimension), row in self._prices.items()
            if model == model_name and row_component == component
        }
        if not rows:
            if enforce:
                _record_price_metric(component, "missing")
                raise MissingModelPrice(model_name, component)
            return None
        missing = _REQUIRED_DIMENSIONS.get(component, frozenset()) - rows.keys()
        if missing and enforce:
            _record_price_metric(component, "missing")
            raise MissingModelPrice(model_name, component)
        return rows

    def calculate_cost(
        self,
        model_name: str,
        component: str,
        quantities: dict[str, int | float | Decimal],
    ) -> Decimal:
        rows = self.require(model_name, component, enforce=True) or {}
        total = Decimal("0")
        for dimension, quantity in quantities.items():
            if dimension not in PRICE_DIMENSIONS:
                raise ValueError(f"非法用量维度: {dimension}")
            line = rows.get(dimension)
            if line is None:
                continue
            count = Decimal(str(quantity or 0))
            if count < 0:
                raise ValueError("用量不能为负数")
            if line.unit == "per_call":
                total += count * line.price_per_unit
            else:
                total += count / Decimal("1000000") * line.price_per_unit
        return total.quantize(_SIX_PLACES, rounding=ROUND_HALF_UP)


class PostgresPriceRepository:
    """从 memory PG 读取当前已审核生效价格。"""

    def __init__(self, connection_factory=None):
        self._connection_factory = connection_factory or self._connect
        self._table = os.getenv("BUDGET_PG_TABLE_PREFIX", "") + "model_price"

    @staticmethod
    def _connect():
        import psycopg2

        from backend.config.database import MEMORY_DB_CONFIG

        return psycopg2.connect(**MEMORY_DB_CONFIG)

    def get_current(self, model_name: str, component: str) -> PriceTable:
        try:
            with self._connection_factory() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        SELECT model_name, component, dimension,
                               price_per_unit, unit, price_table_version
                        FROM {self._table}
                        WHERE model_name = %s AND component = %s
                          AND approval_status = 'approved'
                          AND effective_from <= %s
                          AND (effective_to IS NULL OR effective_to > %s)
                        ORDER BY dimension, effective_from DESC, id DESC
                        """,
                        (model_name, component, datetime.now(timezone.utc),
                         datetime.now(timezone.utc)),
                    )
                    columns = [item[0] for item in cur.description]
                    rows = [dict(zip(columns, row)) for row in cur.fetchall()]
            return PriceTable.from_rows(rows)
        except (MissingModelPrice,):
            raise
        except Exception as exc:
            raise PriceTableUnavailable("模型价格表不可用") from exc

    def import_pending(
        self,
        rows: list[dict[str, Any]],
        *,
        price_table_version: str,
        source: str,
        effective_from: datetime | None = None,
    ) -> int:
        """导入待审核价格；只 INSERT，不覆盖历史版本。"""
        if not price_table_version.strip() or not source.strip():
            raise ValueError("价格版本和来源不能为空")
        PriceTable.from_rows(rows)
        effective = effective_from or datetime.now(timezone.utc)
        try:
            with self._connection_factory() as conn:
                with conn.cursor() as cur:
                    for row in rows:
                        cur.execute(
                            f"""INSERT INTO {self._table} (
                                model_name, component, dimension, price_per_unit,
                                unit, currency, price_table_version, source,
                                effective_from, effective_to, approval_status
                            ) VALUES (%s, %s, %s, %s, %s, 'USD', %s, %s, %s, %s, 'pending')""",
                            (
                                row["model_name"], row["component"], row["dimension"],
                                row["price_per_unit"], row.get("unit", "per_1m_tokens"),
                                price_table_version, source, effective,
                                row.get("effective_to"),
                            ),
                        )
            return len(rows)
        except Exception as exc:
            raise PriceTableUnavailable("价格导入失败") from exc

    def approve_version(
        self,
        price_table_version: str,
        *,
        reviewer_1: str,
        reviewer_2: str,
    ) -> int:
        """双人审核：以 approved 新行替代 pending 行，保留追加式历史。"""
        if not reviewer_1 or not reviewer_2 or reviewer_1 == reviewer_2:
            raise ValueError("价格生效必须由两名不同审核人批准")
        try:
            with self._connection_factory() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""SELECT model_name, component, dimension,
                                   price_per_unit, unit, currency, source,
                                   price_table_version, effective_from,
                                   effective_to
                            FROM {self._table}
                            WHERE price_table_version = %s
                              AND approval_status = 'pending'""",
                        (price_table_version,),
                    )
                    rows = cur.fetchall()
                    for row in rows:
                        cur.execute(
                            f"""INSERT INTO {self._table} (
                                model_name, component, dimension, price_per_unit,
                                unit, currency, price_table_version, source,
                                effective_from, effective_to, approval_status,
                                reviewer_1, reviewer_2
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                      'approved', %s, %s)""",
                            (*row, reviewer_1, reviewer_2),
                        )
            return len(rows)
        except Exception as exc:
            raise PriceTableUnavailable("价格审核生效失败") from exc


def get_current_price_table(model_name: str, component: str) -> PriceTable:
    """读取并短缓存当前价格快照；失败不静默降级。"""
    key = (model_name, component)
    now = time.monotonic()
    with _price_cache_lock:
        cached = _price_cache.get(key)
        if cached is not None and cached[0] > now:
            return cached[1]
    table = PostgresPriceRepository().get_current(model_name, component)
    with _price_cache_lock:
        _price_cache[key] = (now + _PRICE_CACHE_TTL_SECONDS, table)
    return table


def clear_price_cache() -> None:
    with _price_cache_lock:
        _price_cache.clear()


def calculate_current_cost(
    model_name: str,
    component: str,
    quantities: dict[str, int | float | Decimal],
    *,
    enforce: bool,
) -> Decimal:
    """按 PG 价格表计费；硬模式缺价直接抛出，关闭时兼容旧估算。"""
    if not enforce:
        try:
            table = get_current_price_table(model_name, component)
            if table.require(model_name, component, enforce=False) is None:
                _record_price_metric(component, "missing")
                _logger.warning(
                    "model_price_missing: model=%s component=%s; "
                    "budget hard gate is disabled, token-only alert path",
                    model_name,
                    component,
                )
            else:
                result = table.calculate_cost(model_name, component, quantities)
                _record_price_metric(component, "hit")
                return result
        except PriceTableUnavailable:
            _record_price_metric(component, "unavailable")
            _logger.warning(
                "model_price_unavailable: model=%s component=%s; "
                "budget hard gate is disabled, token-only alert path",
                model_name,
                component,
            )
        except MissingModelPrice:
            _record_price_metric(component, "missing")
            _logger.warning(
                "model_price_incomplete: model=%s component=%s; "
                "budget hard gate is disabled, token-only alert path",
                model_name,
                component,
            )
        if component == "llm":
            return calculate_fallback_cost(
                model_name,
                int(quantities.get("input", 0)),
                int(quantities.get("output", 0)),
            )
        from backend.infra.llm.models import compute_embedding_cost

        return Decimal(str(compute_embedding_cost(
            model_name, int(sum(quantities.values()))
        ))).quantize(_SIX_PLACES, rounding=ROUND_HALF_UP)
    result = get_current_price_table(model_name, component).calculate_cost(
        model_name, component, quantities,
    )
    _record_price_metric(component, "hit")
    return result


def calculate_fallback_cost(
    model_name: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> Decimal:
    """硬门关闭时的兼容统计：沿用旧注册表价格，不作为硬预算依据。"""
    from backend.infra.llm.models import compute_cost_usd

    return Decimal(str(compute_cost_usd(model_name, prompt_tokens, completion_tokens))
                   ).quantize(_SIX_PLACES, rounding=ROUND_HALF_UP)


__all__ = [
    "MissingModelPrice",
    "PRICE_DIMENSIONS",
    "PriceLine",
    "PriceTable",
    "PriceTableUnavailable",
    "PostgresPriceRepository",
    "calculate_current_cost",
    "calculate_fallback_cost",
    "clear_price_cache",
    "get_current_price_table",
]
