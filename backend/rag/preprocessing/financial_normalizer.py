"""财务数值规范化工具。

处理财务报表中多样的数值格式，保证精度不丢失：
  - 会计括号负数：(1,234.56) → -1234.56
  - 货币符号：¥12,345 / $1,234.56 → 12345.0 / 1234.56
  - 百分比：12.3% → 0.123
  - 数量级后缀：1.2M / 3.5B → 1200000 / 3500000000
  - 千分位分隔：1,234,567 → 1234567

使用 Decimal 保证精度，避免 float IEEE 754 舍入误差。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Optional

from backend.shared.logger import logger

# ── 数量级后缀映射 ──────────────────────────────────
_MAGNITUDE_SUFFIXES = {
    "K": Decimal("1E3"),
    "k": Decimal("1E3"),
    "千": Decimal("1E3"),
    "M": Decimal("1E6"),
    "百万": Decimal("1E6"),
    "B": Decimal("1E9"),
    "亿": Decimal("1E8"),
    "W": Decimal("1E4"),
    "万": Decimal("1E4"),
}

# ── 货币符号 → 统一单位标记 ────────────────────────
_CURRENCY_SYMBOLS = {
    "¥": "CNY", "￥": "CNY", "RMB": "CNY",
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
}

# ── 数值特征正则 ──────────────────────────────────
# 会计括号负数：(1,234.56) 或 (1234)
_PAREN_NEGATIVE_RE = re.compile(r"^\(\s*([\d,]+\.?\d*)\s*\)$")
# 千分位数字：1,234,567.89
_THOUSANDS_RE = re.compile(r"^[\d,]+\.?\d*$")
# 百分比
_PERCENT_RE = re.compile(r"^([\d,]+\.?\d*)\s*%$")
# 带后缀的科学计数：1.2M, 3.5B
_MAGNITUDE_RE = re.compile(r"^([\d,]+\.?\d*)\s*([A-Za-z\u4e00-\u9fff]+)$")


@dataclass
class NormalizedFinancialValue:
    """规范化后的财务数值。"""
    raw: str               # 原始文本
    normalized: Optional[str]  # Decimal 字符串（保留 2 位小数），不可解析时为 None
    unit: str = ""         # 单位标记：CNY/USD/EUR/percent/M/B/""
    is_negative: bool = False
    is_numeric: bool = False  # 是否为可解析的数值


def _clean_number_str(s: str) -> str:
    """移除千分位分隔符和空白，保留纯数字和小数点。"""
    return s.replace(",", "").replace(" ", "").replace("\u3000", "")


def normalize_financial_value(val: str | int | float | None) -> NormalizedFinancialValue:
    """解析财务数值，返回规范化结果。

    支持格式：
      '(1,234.56)' → -1234.56  （括号负数，会计惯例）
      '¥12,345'    → 12345.00, unit='CNY'
      '12.3%'      → 0.12, unit='percent'
      '1.2M'       → 1200000.00, unit='M'
      '1,234,567'  → 1234567.00
      'N/A' / '-'  → NormalizedFinancialValue(is_numeric=False)

    Returns:
        NormalizedFinancialValue: 含 raw / normalized / unit / is_negative / is_numeric
    """
    if val is None:
        return NormalizedFinancialValue(raw="", normalized=None, is_numeric=False)

    raw = str(val).strip()
    if not raw or raw in ("N/A", "n/a", "NA", "—", "-", "null", "NULL", "None"):
        return NormalizedFinancialValue(raw=raw, normalized=None, is_numeric=False)

    # 如果传入的就是数值类型
    if isinstance(val, (int, float)):
        try:
            d = Decimal(str(val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            return NormalizedFinancialValue(
                raw=raw, normalized=str(d), is_numeric=True,
                is_negative=d < 0,
            )
        except (InvalidOperation, ValueError):
            return NormalizedFinancialValue(raw=raw, normalized=None, is_numeric=False)

    unit = ""
    is_negative = False
    num_str = raw

    # 1. 会计括号负数 (1,234.56)
    m = _PAREN_NEGATIVE_RE.match(num_str)
    if m:
        is_negative = True
        num_str = m.group(1)
    else:
        # 2. 检测前缀货币符号
        for sym, curr in _CURRENCY_SYMBOLS.items():
            if num_str.startswith(sym):
                unit = curr
                num_str = num_str[len(sym):].strip()
                break

        # 3. 前导负号
        if num_str.startswith("-") or num_str.startswith("−") or num_str.startswith("－"):
            is_negative = True
            num_str = num_str.lstrip("-−－").strip()

    # 4. 百分比后缀
    m = _PERCENT_RE.match(num_str)
    if m:
        unit = "percent"
        num_str = m.group(1)
    else:
        # 5. 数量级后缀 (1.2M, 3.5万)
        m = _MAGNITUDE_RE.match(num_str)
        if m and not _THOUSANDS_RE.match(num_str):
            suffix = m.group(2)
            if suffix in _MAGNITUDE_SUFFIXES:
                unit = suffix
                num_str = m.group(1)

    # 6. 清洗千分位
    num_str = _clean_number_str(num_str)

    # 7. 尝试 Decimal 解析
    try:
        d = Decimal(num_str)
        if unit == "percent":
            d = d / Decimal("100")
        elif unit in _MAGNITUDE_SUFFIXES:
            d = d * _MAGNITUDE_SUFFIXES[unit]
        if is_negative:
            d = -d
        d = d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return NormalizedFinancialValue(
            raw=raw, normalized=str(d), unit=unit,
            is_negative=is_negative, is_numeric=True,
        )
    except (InvalidOperation, ValueError) as e:
        logger.debug(f"[FinancialNormalizer] 无法解析 '{raw}': {e}")
        return NormalizedFinancialValue(raw=raw, normalized=None, is_numeric=False)


def safe_number_str(val) -> str:
    """Decimal 精确保留数值字符串，避免 float 精度损失。

    用于 ExcelParser 将单元格值转为字符串时：
      - 数值类型 → Decimal 量化 2 位小数字符串
      - 非数值 → str(val)
    """
    if isinstance(val, (int, float)):
        try:
            return str(Decimal(str(val)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            ))
        except (InvalidOperation, ValueError):
            return str(val)
    return str(val) if val is not None else ""


def extract_numeric_cells(
    header: list[str], row: list[str],
) -> dict[str, str]:
    """从表格行中提取数值单元格，返回 {列名: 规范化数值字符串}。

    用于行级 chunk 的 metadata.numeric_values，支持后续按数值范围检索。
    """
    result: dict[str, str] = {}
    for col_name, cell in zip(header, row):
        col = str(col_name).strip()
        cell_str = str(cell).strip() if cell is not None else ""
        if not col or not cell_str:
            continue
        norm = normalize_financial_value(cell_str)
        if norm.is_numeric and norm.normalized is not None:
            result[col] = norm.normalized
    return result


def is_numeric_cell(val) -> bool:
    """判断单元格值是否为数值类型。"""
    if isinstance(val, (int, float)):
        return True
    if isinstance(val, str):
        norm = normalize_financial_value(val)
        return norm.is_numeric
    return False


# ── 报告期提取（用于版本快照） ──────────────────────────

# 常见财务文件名中的报告期模式
_REPORTING_PERIOD_PATTERNS = [
    # 2026-Q3 / 2025-Q1 / 2026Q4
    re.compile(r"(20\d{2})[-_]?Q([1-4])", re.IGNORECASE),
    # 2026年第三季度 / 2025年第一季度
    re.compile(r"20(\d{2})年第([一二三四])季度"),
    # 2026-07 / 2025-03 / 202607 （年月）
    re.compile(r"(20\d{2})[-_]?(\d{2})[._-]?"),
    # 2026年度 / 2025年报
    re.compile(r"20(\d{2})年度?报?"),
]

_CN_QUARTER_MAP = {"一": "1", "二": "2", "三": "3", "四": "4"}


def extract_reporting_period(file_path: str, section_title: str = "") -> tuple[str, str]:
    """从文件名或 section 标题中提取报告期。

    返回 (reporting_period, fiscal_year)。
    无法提取时返回 ("", "")。

    示例：
      "2026-Q3财务报表.xlsx" → ("2026-Q3", "2026")
      "2025年第一季度利润表.xlsx" → ("2025-Q1", "2025")
      "2026-07月度销售.csv" → ("2026-07", "2026")
      "2025年度财务报告.xlsx" → ("2025", "2025")
    """
    import os

    # 合并文件名和 section 标题作为搜索源
    filename = os.path.basename(file_path)
    search_text = f"{filename} {section_title}"

    # 模式 1: 2026-Q3 / 2026Q3
    m = _REPORTING_PERIOD_PATTERNS[0].search(search_text)
    if m:
        year = f"20{m.group(1)}" if len(m.group(1)) == 2 else m.group(1)
        quarter = m.group(2)
        return f"{year}-Q{quarter}", year

    # 模式 2: 2026年第三季度
    m = _REPORTING_PERIOD_PATTERNS[1].search(search_text)
    if m:
        year = f"20{m.group(1)}"
        quarter = _CN_QUARTER_MAP.get(m.group(2), m.group(2))
        return f"{year}-Q{quarter}", year

    # 模式 3: 2026-07 （年月）
    m = _REPORTING_PERIOD_PATTERNS[2].search(search_text)
    if m:
        year = f"20{m.group(1)}" if len(m.group(1)) == 2 else m.group(1)
        month = m.group(2)
        if 1 <= int(month) <= 12:
            return f"{year}-{month}", year

    # 模式 4: 2026年度
    m = _REPORTING_PERIOD_PATTERNS[3].search(search_text)
    if m:
        year = f"20{m.group(1)}"
        return year, year

    return "", ""
