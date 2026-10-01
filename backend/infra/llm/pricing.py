"""Q8 模型价格表：PostgreSQL 权威记录与 USD 六位精度计算。"""
from __future__ import annotations

import os
from contextlib import contextmanager
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
    currency: str = "USD"


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
                    currency=str(row.get("currency") or "USD"),
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

    def currency_for(self, model_name: str, component: str) -> str:
        """该模型该组件当前价格行的币种；无行时回退 USD。"""
        for (model, row_component, _dimension), row in self._prices.items():
            if model == model_name and row_component == component:
                return row.currency or "USD"
        return "USD"

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
    @contextmanager
    def _connect():
        """池化连接（统一 Engine）：块结束自动 commit、异常 rollback、归还池。

        语义对齐 psycopg2 原生 ``with conn``（块结束 commit / 异常 rollback），
        但连接归还池，不再每次新建。
        """
        from backend.config.database import MEMORY_DB_CONFIG  # noqa: F401  (engine_for 归一)

        from backend.infra.db import engine_for

        conn = engine_for(MEMORY_DB_CONFIG).raw_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_current(self, model_name: str, component: str) -> PriceTable:
        try:
            with self._connection_factory() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        SELECT model_name, component, dimension,
                               price_per_unit, unit, price_table_version,
                               currency
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


def _to_base(amount: Decimal, currency: str) -> Decimal:
    """按记账本位币折算（config/budget.py 唯一汇率出口）。"""
    from backend.config.budget import to_base_currency

    return to_base_currency(amount, currency).quantize(
        _SIX_PLACES, rounding=ROUND_HALF_UP,
    )


def calculate_current_cost(
    model_name: str,
    component: str,
    quantities: dict[str, int | float | Decimal],
    *,
    enforce: bool,
) -> Decimal:
    """按 PG 价格表计费；硬模式缺价直接抛出，关闭时兼容旧估算。

    2026-10-01 起返回值恒为记账本位币 CNY（价格行原生币种在此折算）。
    """
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
                return _to_base(result, table.currency_for(model_name, component))
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
            return _to_base(
                calculate_fallback_cost(
                    model_name,
                    int(quantities.get("input", 0)),
                    int(quantities.get("output", 0)),
                ),
                "USD",
            )
        from backend.infra.llm.models import compute_embedding_cost

        return _to_base(
            Decimal(str(compute_embedding_cost(
                model_name, int(sum(quantities.values()))
            ))),
            "USD",
        )
    table = get_current_price_table(model_name, component)
    result = table.calculate_cost(model_name, component, quantities)
    _record_price_metric(component, "hit")
    return _to_base(result, table.currency_for(model_name, component))


def calculate_fallback_cost(
    model_name: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> Decimal:
    """硬门关闭时的兼容统计：沿用旧注册表价格，不作为硬预算依据。"""
    from backend.infra.llm.models import compute_cost_usd

    return Decimal(str(compute_cost_usd(model_name, prompt_tokens, completion_tokens))
                   ).quantize(_SIX_PLACES, rounding=ROUND_HALF_UP)


# 成本状态（第一阶段 2026-09-22 拍板）：exact / estimated / unpriced
# STOP C（2026-09-23）补登记 price_unknown：预算硬门缺价时 proxy 按注册表
# 估价记账的显式标记（此前只写在 proxy 注释里，未进枚举声明，按状态过滤
# 的对账查询会漏行）。落库见 migration 046 注释。
COST_STATUS_EXACT = "exact"
COST_STATUS_ESTIMATED = "estimated"
COST_STATUS_UNPRICED = "unpriced"
COST_STATUS_PRICE_UNKNOWN = "price_unknown"


def _llm_cost_breakdown(
    rows: dict[str, PriceLine],
    billable_input: int,
    cached_input: int,
    output_tokens: int,
) -> tuple[Decimal, dict[str, Decimal]]:
    """按价格行分项计算（全部 Decimal，1M tokens 口径）。

    缓存价格行缺失时，缓存部分按普通 input 价计算 —— 这正是
    ``cost_status='estimated'`` 的语义（保守估算，不猜价）。
    """
    in_p = rows["input"].price_per_unit
    out_p = rows["output"].price_per_unit
    cache_line = rows.get("cache_read")
    cache_p = cache_line.price_per_unit if cache_line is not None else in_p
    input_cost = (Decimal(billable_input) / Decimal("1000000") * in_p)
    cached_cost = (Decimal(cached_input) / Decimal("1000000") * cache_p)
    output_cost = (Decimal(output_tokens) / Decimal("1000000") * out_p)
    total = (input_cost + cached_cost + output_cost).quantize(
        _SIX_PLACES, rounding=ROUND_HALF_UP
    )
    breakdown = {
        "input_cost": input_cost.quantize(_SIX_PLACES, rounding=ROUND_HALF_UP),
        "cached_input_cost": cached_cost.quantize(_SIX_PLACES, rounding=ROUND_HALF_UP),
        "output_cost": output_cost.quantize(_SIX_PLACES, rounding=ROUND_HALF_UP),
    }
    return total, breakdown


def _unit_price_snapshot(
    rows: dict[str, PriceLine],
) -> dict[str, float]:
    """调用时单价快照（Billing Snapshot / C6）：随 usage 行落库，事后可审计
    历史成本用的是哪一版价格（per_1m_tokens 口径；缓存价缺行时按当时实际
    参与计算的 input 价记录，与 cost_status='estimated' 语义对齐）。"""
    cache_line = rows.get("cache_read")
    return {
        "input_unit_price": float(rows["input"].price_per_unit),
        "output_unit_price": float(rows["output"].price_per_unit),
        "cache_input_unit_price": float(
            cache_line.price_per_unit if cache_line is not None
            else rows["input"].price_per_unit
        ),
    }


_MONEY_KEYS = (
    "input_cost", "cached_input_cost", "output_cost",
    "input_unit_price", "output_unit_price", "cache_input_unit_price",
)


def _to_base_or_unpriced(
    total: Decimal,
    payload: dict[str, float],
    currency: str,
    status: str,
) -> tuple[Decimal, str, str, dict[str, float]]:
    """把原生币种金额折算为记账本位币（CNY）后返回；折算失败降级 unpriced。

    本入口契约永不抛错：币种未登记汇率属于价格配置错误，降级为 unpriced
    （只记 token 不计费）并告警，绝不把金额按错误汇率静默计入。
    """
    from backend.config.budget import BUDGET_BASE_CURRENCY, to_base_currency
    from backend.shared.logger import logger

    try:
        total_base = to_base_currency(total, currency)
        converted = dict(payload)
        for key in _MONEY_KEYS:
            converted[key] = float(
                to_base_currency(payload.get(key) or 0.0, currency)
            )
        return total_base, status, BUDGET_BASE_CURRENCY, converted
    except ValueError as exc:
        logger.warning(
            "[Pricing] 币种 %r 无折算汇率，本次按 unpriced 记账"
            "（请在价格治理登记汇率或改报价币种）: %s", currency, exc,
        )
        return Decimal("0"), COST_STATUS_UNPRICED, BUDGET_BASE_CURRENCY, {
            key: 0.0 for key in _MONEY_KEYS
        }


def calculate_llm_cost_with_status(
    model_name: str,
    quantities: dict[str, int | float | Decimal],
) -> tuple[Decimal, str, str, dict[str, float]]:
    """第一阶段统一成本入口（软统计）：返回 (total, status, currency, breakdown)。

    与 ``calculate_current_cost``（预算硬门，缺价抛异常）不同，本入口**永不抛错**
    ——成本统计失败不能影响模型主链路（2026-09-22 拍板）。

    quantities 口径（proxy 已标准化）：
      - ``input``   = **billable** input tokens（不含缓存命中部分）
      - ``cache_read`` = 缓存命中 tokens
      - ``output``  = output tokens

    状态判定：
      - exact     ：PG 有审核生效的 input+output 价格；缓存命中为 0 或已有
                    cache_read 价格行。
      - estimated ：缓存命中 > 0 但模型未配置 cache_read 价格 —— 缓存部分按
                    普通 input 价保守估算；或 PG 价格表不可用/缺行，退回注册表
                    内置估价。
      - unpriced  ：PG 无价格且注册表内置估价也为 0 —— 只记 token，不计费。

    分项成本（breakdown）恒给出：input_cost / cached_input_cost / output_cost
    （USD float，6 位小数）。货币取自价格行；fallback 场景注册表价固定 USD。

    STOP C（C6 Billing Snapshot）：PG 价格行在场时 breakdown 额外携带
    input_unit_price / output_unit_price / cache_input_unit_price（per 1M
    tokens 调用时单价）—— proxy 原样落 llm_usage，改价不污染历史对账。
    fallback 估价场景无单价可快照（按 0 记，cost_status 已标 estimated/unpriced）。
    """
    billable = max(int(quantities.get("input", 0) or 0), 0)
    cached = max(int(quantities.get("cache_read", 0) or 0), 0)
    output = max(int(quantities.get("output", 0) or 0), 0)
    zero: dict[str, float] = {
        "input_cost": 0.0, "cached_input_cost": 0.0, "output_cost": 0.0,
        "input_unit_price": 0.0, "output_unit_price": 0.0,
        "cache_input_unit_price": 0.0,
    }

    rows: dict[str, PriceLine] | None
    currency = "USD"
    try:
        table = get_current_price_table(model_name, "llm")
        rows = table.require(model_name, "llm", enforce=False)
        currency = table.currency_for(model_name, "llm")
    except PriceTableUnavailable:
        rows = None
    except Exception:
        # 本入口的契约是**永不抛错**（成本统计失败不能影响主链路）：
        # 价格层的意外异常（连接池耗尽等）与"表不可用"同语义，退注册表估价
        # 并打日志，绝不把异常透给调用方（STOP C 测试 test_billing_never_raises 锁定）。
        from backend.shared.logger import logger

        logger.warning(
            "[Pricing] 价格表读取异常，退注册表估价 model=%s", model_name,
            exc_info=True,
        )
        rows = None

    if rows is None or "input" not in rows or "output" not in rows:
        # PG 无审核生效价格：退回注册表内置估价（语义 = estimated），无内置价则 unpriced。
        fallback = calculate_fallback_cost(model_name, billable + cached, output)
        status = COST_STATUS_ESTIMATED if fallback > 0 else COST_STATUS_UNPRICED
        return _to_base_or_unpriced(fallback, zero, "USD", status)

    if cached > 0 and "cache_read" not in rows:
        # 缓存命中但无缓存价：缓存部分按普通 input 价保守估算。
        total, breakdown = _llm_cost_breakdown(rows, billable, cached, output)
        payload = {key: float(value) for key, value in breakdown.items()}
        payload.update(_unit_price_snapshot(rows))
        return _to_base_or_unpriced(total, payload, currency, COST_STATUS_ESTIMATED)

    total, breakdown = _llm_cost_breakdown(rows, billable, cached, output)
    payload = {key: float(value) for key, value in breakdown.items()}
    payload.update(_unit_price_snapshot(rows))
    return _to_base_or_unpriced(total, payload, currency, COST_STATUS_EXACT)


__all__ = [
    "COST_STATUS_ESTIMATED",
    "COST_STATUS_EXACT",
    "COST_STATUS_UNPRICED",
    "COST_STATUS_PRICE_UNKNOWN",
    "MissingModelPrice",
    "PRICE_DIMENSIONS",
    "PriceLine",
    "PriceTable",
    "PriceTableUnavailable",
    "PostgresPriceRepository",
    "calculate_current_cost",
    "calculate_fallback_cost",
    "calculate_llm_cost_with_status",
    "clear_price_cache",
    "get_current_price_table",
]
