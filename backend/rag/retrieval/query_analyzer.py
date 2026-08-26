"""QueryAnalyzer — pure-rule structured query understanding, ~5ms, zero LLM calls.

Reuses existing preprocessing modules and config rules:
  - preprocessing/entity.py   → extract_person_names() + extract_sku_codes() + extract_platforms()
  - preprocessing/keyword.py  → extract_chunk_keywords()
  - config.TIME_PATTERNS      → time expression regex
  - config.DOMAIN_RULES       → domain classification (9 e-commerce domains)
  - config.DOC_TYPE_RULES     → doc type classification
  - config.SIGNAL_RULES       → e-commerce domain signal keywords
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from backend.shared.logger import logger


@dataclass
class ParsedQuery:
    """Structured result of query analysis."""

    original: str = ""

    # Entities
    persons: list[str] = field(default_factory=list)       # 品牌/平台/关键实体
    organizations: list[str] = field(default_factory=list)  # 平台/渠道名
    technologies: list[str] = field(default_factory=list)   # 领域信号词
    sku_codes: list[str] = field(default_factory=list)      # SKU 编码

    # Time
    time_expressions: list[str] = field(default_factory=list)
    time_range_start: str = ""
    time_range_end: str = ""

    # Classification
    domains: list[str] = field(default_factory=list)
    doc_types: list[str] = field(default_factory=list)

    # Intent (rule-based, ~0ms) — 跨境电商场景
    intent: str = "summary_query"

    # Financial metrics (财务指标提取)
    financial_metrics: list[str] = field(default_factory=list)  # 识别到的财务指标 key
    numeric_conditions: list[dict] = field(default_factory=list)  # 数值条件 [{metric, op, value, unit}]
    reporting_period: str = ""  # 报告期（如 "2026-Q3"），从查询中提取或时间表达式推导

    def to_metadata_filter(self) -> dict:
        """Convert parsed entities into a ChromaDB-compatible filter dict."""
        f: dict = {}
        if self.persons:
            f["person_names"] = self.persons[0] if len(self.persons) == 1 else self.persons
        if self.organizations:
            f["organization"] = self.organizations[0] if len(self.organizations) == 1 else self.organizations
        if self.doc_types:
            f["doc_type"] = self.doc_types[0] if len(self.doc_types) == 1 else self.doc_types
        if self.domains:
            # 多 domain 兼容（2026-08-10）：用 $in 而非取第一个，避免误判
            # 例："差评怎么处理" → customer + order（兼容售后流程的 order 标注）
            if len(self.domains) == 1:
                f["business_domain"] = self.domains[0]
            else:
                f["business_domain"] = {"$in": self.domains}
        if self.time_range_start:
            f["time_start"] = self.time_range_start
            f["time_end"] = self.time_range_end
        # 财务文档版本快照：有明确报告期 → 按 reporting_period 过滤；
        # 无明确报告期但有财务指标 → 只查最新版本（is_latest=True）
        if self.reporting_period:
            f["reporting_period"] = self.reporting_period
        elif self.financial_metrics:
            f["is_latest"] = True
        return f


# ────────────────────────────────────────────────
# Intent classifier — 跨境电商 7 类意图
# ────────────────────────────────────────────────

_ENTITY_SIGNALS = ["是什么", "哪个品牌", "哪个平台", "哪个渠道", "规格", "参数", "属性"]
_FACT_SIGNALS = ["多少", "几个", "总共", "统计", "平均", "最高", "最低", "合计", "汇总"]
_ORDER_SIGNALS = ["订单", "发货", "物流", "签收", "退款", "取消", "追踪号"]
_INVENTORY_SIGNALS = ["库存", "FBA", "缺货", "还有多少", "够不够", "快没了"]
_AD_SIGNALS = ["ACoS", "ROAS", "广告", "Campaign", "投放", "竞价", "曝光"]
_REPORT_SIGNALS = ["报告", "分析报告", "生成报告", "总结", "概述", "日报", "周报", "月报"]


def classify_intent(query: str) -> str:
    """Rule-based intent classification. Zero LLM cost.

    跨境电商意图分类：
      entity_query   → 商品/品牌/平台信息查询
      order_query    → 订单状态/物流追踪
      inventory_query→ 库存查询/预警
      ad_query       → 广告效果分析
      fact_query     → 数据统计/聚合查询
      report_query   → 报告生成
      summary_query  → 通用问答
    """
    for w in _REPORT_SIGNALS:
        if w in query:
            return "report_query"
    for w in _AD_SIGNALS:
        if w in query:
            return "ad_query"
    for w in _ORDER_SIGNALS:
        if w in query:
            return "order_query"
    for w in _INVENTORY_SIGNALS:
        if w in query:
            return "inventory_query"
    for w in _FACT_SIGNALS:
        if w in query:
            return "fact_query"
    for w in _ENTITY_SIGNALS:
        if w in query:
            return "entity_query"
    return "summary_query"


# ────────────────────────────────────────────────
# Time resolution
# ────────────────────────────────────────────────

_TIME_UNITS = {
    "去年": (-365, -1), "今年": (0, 0), "上季度": (-90, -1),
    "最近一个月": (-30, 0), "最近两周": (-14, 0), "昨天": (-1, -1),
    "最近一周": (-7, 0), "本周": (-7, 0), "本月": (-30, 0),
    "第一季度": (0, 89), "第二季度": (91, 181), "第三季度": (182, 273), "第四季度": (274, 365),
}


def _resolve_time_range(expressions: list[str]) -> tuple[str, str]:
    """Convert natural-language time expressions to ISO dates."""
    today = datetime.now()
    for expr in expressions:
        if expr in _TIME_UNITS:
            start_delta, end_delta = _TIME_UNITS[expr]
            start = today + timedelta(days=start_delta)
            end = today + timedelta(days=end_delta)
            start_str = start.replace(month=1, day=1).isoformat()[:10] if start_delta < -180 else start.isoformat()[:10]
            end_str = end.isoformat()[:10]
            return start_str, end_str
        # Numeric: "2024年"
        m = re.search(r"(\d{4})年", expr)
        if m:
            year = int(m.group(1))
            return f"{year}-01-01", f"{year}-12-31"
    return "", ""


# ────────────────────────────────────────────────
# Platform / channel patterns (跨境电商平台)
# ────────────────────────────────────────────────

_PLATFORM_PATTERNS = [
    r"(Amazon|Shopify|TikTok\s?Shop|eBay|Walmart)",
    r"(美国|欧洲|日本|英国|德国|北美|欧盟)",
    r"(美西|美东|德国仓|日本仓|深圳仓)",
]


def _extract_platforms(query: str) -> list[str]:
    """Regex-based platform/channel extraction."""
    results = []
    for pat in _PLATFORM_PATTERNS:
        results.extend(re.findall(pat, query, re.IGNORECASE))
    return list(set(results))


# ────────────────────────────────────────────────
# QueryAnalyzer
# ────────────────────────────────────────────────

class QueryAnalyzer:
    """Pure-rule query analyzer for cross-border e-commerce. Reuses existing preprocessing + config modules."""

    def analyze(self, query: str) -> ParsedQuery:
        pq = ParsedQuery(original=query)

        # ── Entities: 品牌/平台名（复用 entity.py） ──
        try:
            from backend.rag.preprocessing.entity import extract_person_names
            pq.persons = extract_person_names(query)
            if isinstance(pq.persons, str):
                pq.persons = [pq.persons]
        except Exception as e:
            # 单信号提取失败 → 跳过该信号继续分析（软降级），留痕
            logger.debug(f"[QueryAnalyzer] 实体提取失败: {e}", exc_info=True)

        # ── SKU codes ──
        try:
            from backend.rag.preprocessing.entity import extract_sku_codes
            pq.sku_codes = extract_sku_codes(query)
        except Exception as e:
            logger.debug(f"[QueryAnalyzer] SKU 提取失败: {e}", exc_info=True)

        # ── Platforms ──
        pq.organizations = _extract_platforms(query)

        # ── Domain signals (via SIGNAL_RULES) ──
        try:
            from backend.config import SIGNAL_RULES
            ql = query.lower()
            techs = []
            for _domain, keywords in SIGNAL_RULES.items():
                for kw in keywords:
                    if kw.lower() in ql:
                        techs.append(kw)
            pq.technologies = techs
        except Exception as e:
            logger.debug(f"[QueryAnalyzer] 领域信号提取失败: {e}", exc_info=True)

        # ── Time ──
        try:
            from backend.config import TIME_PATTERNS
            for pat in TIME_PATTERNS:
                matches = pat.findall(query)
                if matches:
                    pq.time_expressions.extend(matches)
            if pq.time_expressions:
                pq.time_range_start, pq.time_range_end = _resolve_time_range(pq.time_expressions)
        except Exception as e:
            logger.debug(f"[QueryAnalyzer] 时间解析失败: {e}", exc_info=True)

        # ── Domain ──
        try:
            from backend.config import DOMAIN_RULES
            ql = query.lower()
            domain_scores: dict[str, int] = {}
            for domain, keywords in DOMAIN_RULES.items():
                score = 0
                for kw, weight in keywords.items():
                    if kw.lower() in ql:
                        score += weight
                if score > 0:
                    domain_scores[domain] = score

            # 2026-08-10 改进：保留 top N domains（阈值 = top_score * 0.6），
            # 而不是只取 1 个。解决"差评怎么处理"只推 customer 而遗漏 order 域售后流程的问题。
            if domain_scores:
                sorted_domains = sorted(domain_scores.items(), key=lambda x: x[1], reverse=True)
                top_score = sorted_domains[0][1]
                threshold = top_score * 0.6
                pq.domains = [d for d, s in sorted_domains if s >= threshold][:3]
        except Exception as e:
            logger.debug(f"[QueryAnalyzer] 领域分类失败: {e}", exc_info=True)

        # ── Doc type（兼容 V2 (pattern, weight) 元组格式）──
        try:
            from backend.config import DOC_TYPE_RULES
            for dtype, rules in DOC_TYPE_RULES.items():
                for rule in rules:
                    pat = rule[0] if isinstance(rule, (tuple, list)) else rule
                    if re.search(pat, query):
                        pq.doc_types.append(dtype)
                        break
        except Exception as e:
            logger.debug(f"[QueryAnalyzer] 文档类型识别失败: {e}", exc_info=True)

        # ── Intent ──
        pq.intent = classify_intent(query)

        # ── Financial metrics ──
        try:
            pq.financial_metrics = _extract_financial_metrics(query)
            pq.numeric_conditions = _extract_numeric_conditions(query)
        except Exception as e:
            logger.debug(f"[QueryAnalyzer] 财务指标提取失败: {e}", exc_info=True)

        # ── Reporting period（财务文档版本快照过滤） ──
        try:
            pq.reporting_period = _extract_reporting_period_from_query(
                query, pq.time_expressions,
            )
        except Exception as e:
            logger.debug(f"[QueryAnalyzer] 报告期提取失败: {e}", exc_info=True)

        return pq


# ────────────────────────────────────────────────
# Financial metric extraction
# ────────────────────────────────────────────────

def _extract_financial_metrics(query: str) -> list[str]:
    """从查询中提取财务指标 key（revenue/net_profit/gross_margin 等）。

    复用 domain_data.FINANCIAL_METRIC_PATTERNS 正则匹配。
    """
    try:
        from backend.config import FINANCIAL_METRIC_PATTERNS
    except ImportError:
        return []
    metrics: list[str] = []
    for pattern, metric_key in FINANCIAL_METRIC_PATTERNS:
        if re.search(pattern, query, re.IGNORECASE):
            if metric_key not in metrics:
                metrics.append(metric_key)
    return metrics


def _extract_numeric_conditions(query: str) -> list[dict]:
    """从查询中提取数值比较条件。

    匹配 "毛利率超过30%"、"净利润低于100万" 等表达，
    返回 [{metric_text, op, value, unit}]。

    op 值：'gte'(≥) 或 'lte'(≤)
    unit 值：'percent' / '万' / '亿' / ''
    """
    try:
        from backend.config import (
            FINANCIAL_NUMERIC_CONDITION_RE,
            FINANCIAL_NUMERIC_CONDITION_RE_LTE,
        )
    except ImportError:
        return []

    conditions: list[dict] = []

    # ≥ 条件
    for m in FINANCIAL_NUMERIC_CONDITION_RE.finditer(query):
        metric_text = m.group(1).strip()
        value_str = m.group(3)
        unit = m.group(4) or ""
        value = _parse_cond_value(value_str, unit)
        if value is not None:
            conditions.append({
                "metric_text": metric_text,
                "op": "gte",
                "value": value,
                "unit": "percent" if unit == "%" else unit,
            })

    # ≤ 条件
    for m in FINANCIAL_NUMERIC_CONDITION_RE_LTE.finditer(query):
        metric_text = m.group(1).strip()
        value_str = m.group(3)
        unit = m.group(4) or ""
        value = _parse_cond_value(value_str, unit)
        if value is not None:
            conditions.append({
                "metric_text": metric_text,
                "op": "lte",
                "value": value,
                "unit": "percent" if unit == "%" else unit,
            })

    return conditions


def _parse_cond_value(value_str: str, unit: str):
    """将条件值字符串解析为 float，考虑单位。"""
    from decimal import Decimal, InvalidOperation
    try:
        d = Decimal(value_str)
        if unit == "%":
            d = d / Decimal("100")
        elif unit == "万":
            d = d * Decimal("10000")
        elif unit == "亿":
            d = d * Decimal("100000000")
        elif unit == "百万":
            d = d * Decimal("1000000")
        elif unit == "千万":
            d = d * Decimal("10000000")
        return float(d)
    except (InvalidOperation, ValueError):
        return None


# ────────────────────────────────────────────────
# Reporting period extraction（版本快照过滤）
# ────────────────────────────────────────────────

# 查询中的报告期模式
_QUERY_QUARTER_RE = re.compile(r"(20\d{2})?[-_]?Q([1-4])", re.IGNORECASE)
_QUERY_CN_QUARTER_RE = re.compile(r"(20\d{2})?年第([一二三四])季度")
_QUERY_YEAR_MONTH_RE = re.compile(r"(20\d{2})年(\d{1,2})月")
_QUERY_YEAR_RE = re.compile(r"(20\d{2})年度?")

_QUERY_CN_QUARTER_MAP = {"一": "1", "二": "2", "三": "3", "四": "4"}


def _current_quarter() -> tuple[str, str]:
    """返回当前年份和季度。"""
    now = datetime.now()
    year = str(now.year)
    q = (now.month - 1) // 3 + 1
    return year, f"Q{q}"


def _extract_reporting_period_from_query(
    query: str, time_expressions: list[str],
) -> str:
    """从查询文本中提取报告期，用于版本快照过滤。

    优先级：
      1. 明确的季度/月份/年份模式（2026-Q3 / 第三季度 / 2026年7月）
      2. 相对时间表达式（上季度 / 本季度 / 去年）

    返回 reporting_period 字符串（如 "2026-Q3" / "2026-07" / "2025"），
    无法提取时返回空串。
    """
    # 1. 明确季度模式：2026-Q3 / Q3
    m = _QUERY_QUARTER_RE.search(query)
    if m:
        year = m.group(1) if m.group(1) else _current_quarter()[0]
        quarter = m.group(2)
        return f"{year}-Q{quarter}"

    # 2. 中文季度：第三季度
    m = _QUERY_CN_QUARTER_RE.search(query)
    if m:
        year = m.group(1) if m.group(1) else _current_quarter()[0]
        quarter = _QUERY_CN_QUARTER_MAP.get(m.group(2), m.group(2))
        return f"{year}-Q{quarter}"

    # 3. 年月模式：2026年7月
    m = _QUERY_YEAR_MONTH_RE.search(query)
    if m:
        year = m.group(1)
        month = m.group(2).zfill(2)
        return f"{year}-{month}"

    # 4. 年度模式：2026年度
    m = _QUERY_YEAR_RE.search(query)
    if m:
        return m.group(1)

    # 5. 相对时间表达式 → 推导报告期
    year, quarter = _current_quarter()
    for expr in time_expressions:
        if expr == "上季度":
            q_num = int(quarter[1:])
            if q_num == 1:
                prev_year = str(int(year) - 1)
                return f"{prev_year}-Q4"
            return f"{year}-Q{q_num - 1}"
        if expr == "本季度" or expr == "本季度":
            return f"{year}-{quarter}"
        if expr == "去年":
            prev_year = str(int(year) - 1)
            return prev_year
        if expr == "今年":
            return year
        # 2024年 → 直接用年份
        ym = re.search(r"(20\d{2})年", expr)
        if ym:
            return ym.group(1)

    return ""
