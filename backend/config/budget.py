"""预算与价格表配置；金额策略本身只存 PostgreSQL，不从环境变量读取。"""
import os
from decimal import Decimal, InvalidOperation


BUDGET_TIMEZONE = os.getenv("BUDGET_TIMEZONE", "Asia/Shanghai")
BUDGET_PG_TABLE_PREFIX = os.getenv("BUDGET_PG_TABLE_PREFIX", "")
BUDGET_PLATFORM_DEFAULT_TENANT_ID = os.getenv(
    "BUDGET_PLATFORM_DEFAULT_TENANT_ID", "platform-default"
)
BUDGET_INTERNAL_TENANT_ID = os.getenv("BUDGET_INTERNAL_TENANT_ID", "internal")
BUDGET_TEST_TENANT_PREFIX = os.getenv("BUDGET_TEST_TENANT_PREFIX", "test")

# ── 记账本位币（2026-10-01 拍板：以人民币为主）───────────────────────
# 预算层（策略/账本/预占）与成本统计一律以 CNY 记账与展示；供应商价格行
# （model_price）保留原生报价币种，在定价出口（pricing stage1）按下方汇率
# 折算为本位币。汇率缺失/币种不受支持时快速失败，禁止 1:1 静默兜底。
BUDGET_BASE_CURRENCY = "CNY"
# 1 USD = ? CNY；定价折算唯一入口，改汇率只影响折算后的新用量
BUDGET_FX_USD_CNY_RAW = os.getenv("BUDGET_FX_USD_CNY", "7.20")
try:
    BUDGET_FX_USD_CNY = Decimal(BUDGET_FX_USD_CNY_RAW)
except InvalidOperation as exc:
    raise ValueError(
        f"BUDGET_FX_USD_CNY 不是合法数字: {BUDGET_FX_USD_CNY_RAW!r}"
    ) from exc
if BUDGET_FX_USD_CNY <= 0:
    raise ValueError("BUDGET_FX_USD_CNY 必须大于 0")

# 预占滞留判定阈值（小时）：超过即视为调用已结束但结算/释放未发生，
# 进入待对账（needs_review），不再无限占额
BUDGET_STALE_RESERVATION_HOURS = float(
    os.getenv("BUDGET_STALE_RESERVATION_HOURS", "6")
)


def to_base_currency(amount: Decimal | int | float | str, currency: str) -> Decimal:
    """把任意币种金额折算为记账本位币；未登记汇率的币种快速失败。"""
    value = Decimal(str(amount))
    normalized = (currency or "").strip().upper()
    if normalized == BUDGET_BASE_CURRENCY:
        return value
    if normalized == "USD":
        return value * BUDGET_FX_USD_CNY
    raise ValueError(
        f"币种 {currency!r} 未登记折算汇率，无法计入本位币 {BUDGET_BASE_CURRENCY}"
    )
