"""Q8 模型价格表 + 唯一计费入口（Model Billing Unified Closure 2026-10-07）。

计费事实链（唯一算法 ``price_usage``）：

    Provider Usage → NormalizedUsage → price_usage() → BillingResult
                                                         ├ llm_usage
                                                         ├ Budget settle
                                                         ├ Trace
                                                         └ Dashboard/Grafana

不变量：同 Usage + 同价格版本 + 同 FX → 只有一个成本答案。
observe / enforce 的区别只在门禁策略（是否预占/阻断），金额与状态恒出自同一
BillingResult；硬模式缺价不再在结算层抛错（缺价放行 + price_unknown 语义，
预占期的缺价探测仍由 budget.reserve 的 require(enforce=True) 负责）。
``calculate_current_cost`` / ``calculate_llm_cost_with_status`` 降级为兼容
wrapper（历史签名不变），内部全部走 ``price_usage``。
"""
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


def calculate_current_cost(
    model_name: str,
    component: str,
    quantities: dict[str, int | float | Decimal],
    *,
    enforce: bool,
) -> Decimal:
    """兼容 wrapper（2026-10-07 收口）：返回 ``price_usage().billed_cost_cny``。

    历史契约保留：``enforce=True`` 时缺价/价格表不可用仍抛
    ``MissingModelPrice`` / ``PriceTableUnavailable``（预占期探测依赖该信号）；
    金额算法与 observe 同源（BillingResult.billed_cost_cny，恒 CNY）。
    ``quantities['input']`` 沿用历史口径 = billable input（不含缓存命中），
    缓存命中量放 ``cache_read``。
    """
    if enforce:
        get_current_price_table(model_name, component).require(
            model_name, component, enforce=True,
        )
    usage = NormalizedUsage(
        input_tokens=int(quantities.get("input", 0) or 0)
        + int(quantities.get("cache_read", 0) or 0),
        cached_input_tokens=int(quantities.get("cache_read", 0) or 0),
        cache_write_tokens=int(quantities.get("cache_write", 0) or 0),
        output_tokens=int(quantities.get("output", 0) or 0),
        reasoning_tokens=int(quantities.get("reasoning", 0) or 0),
        tool_calls=int(quantities.get("tool_call", 0) or 0),
    )
    return price_usage(model_name, component, usage, enforce=enforce).billed_cost_cny


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

# 用量可信度（Billing V2）：token 数字从哪来。
USAGE_SOURCE_PROVIDER = "provider"
USAGE_SOURCE_ESTIMATED = "estimated"
USAGE_SOURCE_UNAVAILABLE = "unavailable"

# 计价来源（Billing V2）：金额按哪份价格算的。
PRICING_SOURCE_APPROVED_TABLE = "approved_price_table"
PRICING_SOURCE_REGISTRY_FALLBACK = "registry_fallback"
PRICING_SOURCE_UNPRICED = "unpriced"


# =====================================================
# Billing Contract（2026-10-07 冻结，唯一事实链）
# =====================================================

@dataclass(frozen=True)
class NormalizedUsage:
    """归一化后的单次调用用量（计费唯一入参）。

    口径铁律：``input_tokens`` 是 provider 回传的 prompt/input **总量**
    （含缓存命中部分）；``cached_input_tokens`` 是其子集。计费输入恒为
    ``billable_input_tokens = input_tokens - cached_input_tokens``，
    缓存部分单独按 cache_read 价计——禁止 input 全量 × input 价 + 缓存
    × 缓存价的双算。
    """

    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    tool_calls: int = 0

    def __post_init__(self):
        for name in (
            "input_tokens", "cached_input_tokens", "cache_write_tokens",
            "output_tokens", "reasoning_tokens", "tool_calls",
        ):
            object.__setattr__(self, name, max(int(getattr(self, name) or 0), 0))

    @property
    def billable_input_tokens(self) -> int:
        return max(self.input_tokens - self.cached_input_tokens, 0)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class BillingResult:
    """一次模型调用的唯一计费事实（observe/enforce/trace/账本共消费）。

    - ``native_cost``/``native_currency``：供应商原始报价币种成本（审计用）；
    - ``billed_cost_cny``：平台记账本位币金额，预算结算 / 管理端 / 看板
      唯一取用值，恒为 CNY；
    - ``fx_rate``：本调用实际使用的汇率快照；原生币种即 CNY 时为 None。
    """

    model_name: str
    component: str

    # Usage 回显
    input_tokens: int = 0
    billable_input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cache_write_tokens: int = 0
    tool_calls: int = 0

    # 供应商原始成本
    native_cost: Decimal = Decimal("0")
    native_currency: str = "USD"

    # 平台记账成本（恒 CNY）
    billed_cost_cny: Decimal = Decimal("0")
    base_currency: str = "CNY"

    # 汇率快照（未换汇 = None）
    fx_rate: Decimal | None = None

    # 分项成本（CNY）
    input_cost_cny: Decimal = Decimal("0")
    cached_input_cost_cny: Decimal = Decimal("0")
    output_cost_cny: Decimal = Decimal("0")
    reasoning_cost_cny: Decimal = Decimal("0")
    cache_write_cost_cny: Decimal = Decimal("0")
    tool_call_cost_cny: Decimal = Decimal("0")

    # 调用时单价快照（原生币种 per_1m_tokens / per_call；无价 = None）
    input_unit_price: Decimal | None = None
    output_unit_price: Decimal | None = None
    cache_input_unit_price: Decimal | None = None
    reasoning_unit_price: Decimal | None = None
    cache_write_unit_price: Decimal | None = None
    tool_call_unit_price: Decimal | None = None

    # 价格治理
    price_version: str | None = None
    pricing_source: str = PRICING_SOURCE_UNPRICED

    # 可信度
    usage_source: str = USAGE_SOURCE_PROVIDER
    cost_status: str = COST_STATUS_UNPRICED


# 计价维度 → NormalizedUsage 取量函数（固定序，禁止调用方自由拼维度）
_BILLING_DIMENSIONS: tuple[tuple[str, str], ...] = (
    ("input", "billable_input_tokens"),
    ("cache_read", "cached_input_tokens"),
    ("output", "output_tokens"),
    ("cache_write", "cache_write_tokens"),
    ("reasoning", "reasoning_tokens"),
    ("tool_call", "tool_calls"),
)
_UNIT_PRICE_FIELDS = {
    "input": "input_unit_price",
    "output": "output_unit_price",
    "cache_read": "cache_input_unit_price",
    "reasoning": "reasoning_unit_price",
    "cache_write": "cache_write_unit_price",
    "tool_call": "tool_call_unit_price",
}


def _dimension_amount(line: PriceLine, quantity: int) -> Decimal:
    """单维度金额（原生币种）：per_1m_tokens 千万级换算 / per_call 直乘。"""
    count = Decimal(quantity)
    if line.unit == "per_call":
        return (count * line.price_per_unit).quantize(
            _SIX_PLACES, rounding=ROUND_HALF_UP,
        )
    return (count / Decimal("1000000") * line.price_per_unit).quantize(
        _SIX_PLACES, rounding=ROUND_HALF_UP,
    )


def price_usage(
    model_name: str,
    component: str,
    usage: NormalizedUsage,
    *,
    enforce: bool,
    usage_source: str = USAGE_SOURCE_PROVIDER,
) -> BillingResult:
    """唯一计费入口：同 Usage + 同价格版本 + 同 FX → 只有一个成本答案。

    契约（永不抛错）：
      - PG 有完整必选维度价格行 → 按 PriceLine 逐维计价（全部六维覆盖，
        有量无价的维度降级 estimated，不静默跳过）；缓存命中且无 cache_read
        价行时缓存按 input 价保守计（estimated，与既有策略一致）。
      - PG 缺价 / 价格表不可用 → 注册表内置估价（pricing_source=
        registry_fallback）：observe 下 estimated，enforce 下 price_unknown
        （缺价放行政策，2026-09-22 拍板）；估价为 0 → unpriced（不冒充免费）。
      - 原生币种 → CNY 只折一次（fx_rate 随结果固化）；币种未登记汇率 =
        价格配置错误，降级 unpriced 并告警，绝不按错误汇率静默计入。
      - ``usage_source='estimated'`` 时最终状态不为 exact（用量近似污染
        精确性，P0-12）。
    """
    from backend.config.budget import BUDGET_BASE_CURRENCY, to_base_currency
    from backend.shared.logger import logger

    rows: dict[str, PriceLine] | None
    currency = "USD"
    try:
        table = get_current_price_table(model_name, component)
        rows = table.require(model_name, component, enforce=False)
        currency = table.currency_for(model_name, component)
    except PriceTableUnavailable:
        rows = None
    except Exception:
        # 计费永不抛错：价格层意外异常与"表不可用"同语义，退注册表估价。
        logger.warning(
            "[Pricing] 价格表读取异常，退注册表估价 model=%s/%s",
            model_name, component, exc_info=True,
        )
        rows = None

    required = _REQUIRED_DIMENSIONS.get(component, frozenset())
    priced_from_table = rows is not None and required.issubset(rows.keys())

    native_per_dim: dict[str, Decimal] = {dim: Decimal("0") for dim, _ in _BILLING_DIMENSIONS}
    unit_prices: dict[str, Decimal | None] = {dim: None for dim, _ in _BILLING_DIMENSIONS}
    status = COST_STATUS_EXACT
    price_version: str | None = None

    if priced_from_table:
        assert rows is not None
        pricing_source = PRICING_SOURCE_APPROVED_TABLE
        versions = {
            line.price_table_version for line in rows.values()
            if line.price_table_version
        }
        # 同快照内版本一致（按生效时间倒序 setdefault 的首行优先）
        input_line = rows.get("input")
        price_version = (
            input_line.price_table_version if input_line is not None and input_line.price_table_version
            else next(iter(versions), None)
        )
        for dimension, attr in _BILLING_DIMENSIONS:
            quantity = getattr(usage, attr)
            line = rows.get(dimension)
            if line is not None:
                native_per_dim[dimension] = _dimension_amount(line, quantity)
                unit_prices[dimension] = line.price_per_unit
                continue
            if dimension == "cache_read" and quantity > 0:
                # 缓存命中无缓存价：按普通 input 价保守估算（§7.2 既有策略）。
                native_per_dim[dimension] = _dimension_amount(
                    rows["input"], quantity,
                )
                unit_prices[dimension] = rows["input"].price_per_unit
                status = COST_STATUS_ESTIMATED
            elif quantity > 0:
                # 有量无价：不静默跳过，显式降级 estimated。
                status = COST_STATUS_ESTIMATED
        native_cost = sum(native_per_dim.values(), Decimal("0")).quantize(
            _SIX_PLACES, rounding=ROUND_HALF_UP,
        )
        native_currency = currency
    else:
        # PG 无完整必选维度：注册表内置估价（input+output 两维口径）。
        if component == "llm":
            fallback = calculate_fallback_cost(
                model_name,
                usage.input_tokens,
                usage.output_tokens,
            )
        else:
            from backend.infra.llm.models import compute_embedding_cost

            fallback = Decimal(str(compute_embedding_cost(
                model_name, usage.total_tokens,
            ))).quantize(_SIX_PLACES, rounding=ROUND_HALF_UP)
        if enforce:
            status = COST_STATUS_PRICE_UNKNOWN
            _record_price_metric(component, "missing")
        elif fallback > 0:
            status = COST_STATUS_ESTIMATED
        else:
            status = COST_STATUS_UNPRICED
        pricing_source = (
            PRICING_SOURCE_UNPRICED if fallback == 0
            else PRICING_SOURCE_REGISTRY_FALLBACK
        )
        native_cost = fallback
        native_currency = "USD"
        _record_price_metric(component, "fallback")

    # ── 原生币种 → 记账本位币（只折一次，fx 快照固化）──────────────────
    fx_rate: Decimal | None = None
    if native_currency != BUDGET_BASE_CURRENCY:
        try:
            fx_rate = Decimal(str(to_base_currency(Decimal("1"), native_currency)))
            billed_cost = to_base_currency(native_cost, native_currency)
        except ValueError as exc:
            logger.warning(
                "[Pricing] 币种 %r 无折算汇率，本次按 unpriced 记账"
                "（请在价格治理登记汇率或改报价币种）: %s", native_currency, exc,
            )
            status = COST_STATUS_UNPRICED
            pricing_source = PRICING_SOURCE_UNPRICED
            fx_rate = None
            billed_cost = Decimal("0")
            native_per_dim = {dim: Decimal("0") for dim in native_per_dim}
    else:
        billed_cost = native_cost

    # 用量近似污染精确性：估算用量即使价格精确也不标 exact（P0-12）。
    if status == COST_STATUS_EXACT and usage_source == USAGE_SOURCE_ESTIMATED:
        status = COST_STATUS_ESTIMATED

    def _cny(amount: Decimal) -> Decimal:
        if fx_rate is not None:
            return (amount * fx_rate).quantize(_SIX_PLACES, rounding=ROUND_HALF_UP)
        return amount.quantize(_SIX_PLACES, rounding=ROUND_HALF_UP)

    return BillingResult(
        model_name=model_name,
        component=component,
        input_tokens=usage.input_tokens,
        billable_input_tokens=usage.billable_input_tokens,
        cached_input_tokens=usage.cached_input_tokens,
        output_tokens=usage.output_tokens,
        reasoning_tokens=usage.reasoning_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        tool_calls=usage.tool_calls,
        native_cost=native_cost.quantize(_SIX_PLACES, rounding=ROUND_HALF_UP),
        native_currency=native_currency,
        billed_cost_cny=billed_cost.quantize(_SIX_PLACES, rounding=ROUND_HALF_UP),
        base_currency=BUDGET_BASE_CURRENCY,
        fx_rate=fx_rate,
        input_cost_cny=_cny(native_per_dim["input"]),
        cached_input_cost_cny=_cny(native_per_dim["cache_read"]),
        output_cost_cny=_cny(native_per_dim["output"]),
        reasoning_cost_cny=_cny(native_per_dim["reasoning"]),
        cache_write_cost_cny=_cny(native_per_dim["cache_write"]),
        tool_call_cost_cny=_cny(native_per_dim["tool_call"]),
        input_unit_price=unit_prices["input"],
        output_unit_price=unit_prices["output"],
        cache_input_unit_price=unit_prices["cache_read"],
        reasoning_unit_price=unit_prices["reasoning"],
        cache_write_unit_price=unit_prices["cache_write"],
        tool_call_unit_price=unit_prices["tool_call"],
        price_version=price_version,
        pricing_source=pricing_source,
        usage_source=usage_source,
        cost_status=status,
    )


def calculate_llm_cost_with_status(
    model_name: str,
    quantities: dict[str, int | float | Decimal],
) -> tuple[Decimal, str, str, dict[str, float]]:
    """兼容 wrapper（2026-10-07 收口）：``price_usage`` 的四元组投影。

    返回 ``(billed_cost_cny, cost_status, 'CNY', breakdown)``——金额恒为
    记账本位币（price_usage 只折一次汇），breakdown 为 CNY 分项成本 +
    原生币种单价快照（无价按 0.0 记，不伪造）。``quantities['input']``
    沿用历史口径 = billable input，缓存命中量放 ``cache_read``。
    调用方建议迁移到 ``price_usage`` 直接消费 BillingResult。
    """
    result = price_usage(
        model_name,
        "llm",
        NormalizedUsage(
            input_tokens=int(quantities.get("input", 0) or 0)
            + int(quantities.get("cache_read", 0) or 0),
            cached_input_tokens=int(quantities.get("cache_read", 0) or 0),
            output_tokens=int(quantities.get("output", 0) or 0),
        ),
        enforce=False,
    )
    breakdown = {
        "input_cost": float(result.input_cost_cny),
        "cached_input_cost": float(result.cached_input_cost_cny),
        "output_cost": float(result.output_cost_cny),
        "input_unit_price": float(result.input_unit_price or 0),
        "output_unit_price": float(result.output_unit_price or 0),
        "cache_input_unit_price": float(result.cache_input_unit_price or 0),
    }
    return result.billed_cost_cny, result.cost_status, result.base_currency, breakdown


__all__ = [
    "BillingResult",
    "COST_STATUS_ESTIMATED",
    "COST_STATUS_EXACT",
    "COST_STATUS_UNPRICED",
    "COST_STATUS_PRICE_UNKNOWN",
    "MissingModelPrice",
    "NormalizedUsage",
    "PRICE_DIMENSIONS",
    "PRICING_SOURCE_APPROVED_TABLE",
    "PRICING_SOURCE_REGISTRY_FALLBACK",
    "PRICING_SOURCE_UNPRICED",
    "PriceLine",
    "PriceTable",
    "PriceTableUnavailable",
    "PostgresPriceRepository",
    "USAGE_SOURCE_ESTIMATED",
    "USAGE_SOURCE_PROVIDER",
    "USAGE_SOURCE_UNAVAILABLE",
    "calculate_current_cost",
    "calculate_fallback_cost",
    "calculate_llm_cost_with_status",
    "clear_price_cache",
    "get_current_price_table",
    "price_usage",
]
