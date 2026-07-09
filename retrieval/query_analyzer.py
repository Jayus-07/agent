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
            f["domain"] = self.domains[0]
        if self.time_range_start:
            f["time_start"] = self.time_range_start
            f["time_end"] = self.time_range_end
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
            from preprocessing.entity import extract_person_names
            pq.persons = extract_person_names(query)
            if isinstance(pq.persons, str):
                pq.persons = [pq.persons]
        except Exception:
            pass

        # ── SKU codes ──
        try:
            from preprocessing.entity import extract_sku_codes
            pq.sku_codes = extract_sku_codes(query)
        except Exception:
            pass

        # ── Platforms ──
        pq.organizations = _extract_platforms(query)

        # ── Domain signals (via SIGNAL_RULES) ──
        try:
            from config import SIGNAL_RULES
            ql = query.lower()
            techs = []
            for _domain, keywords in SIGNAL_RULES.items():
                for kw in keywords:
                    if kw.lower() in ql:
                        techs.append(kw)
            pq.technologies = techs
        except Exception:
            pass

        # ── Time ──
        try:
            from config import TIME_PATTERNS
            for pat in TIME_PATTERNS:
                matches = pat.findall(query)
                if matches:
                    pq.time_expressions.extend(matches)
            if pq.time_expressions:
                pq.time_range_start, pq.time_range_end = _resolve_time_range(pq.time_expressions)
        except Exception:
            pass

        # ── Domain ──
        try:
            from config import DOMAIN_RULES
            ql = query.lower()
            domain_scores: dict[str, int] = {}
            for domain, keywords in DOMAIN_RULES.items():
                score = 0
                for kw, weight in keywords.items():
                    if kw.lower() in ql:
                        score += weight
                if score > 0:
                    domain_scores[domain] = score
            if domain_scores:
                pq.domains = [max(domain_scores, key=lambda k: domain_scores[k])]
        except Exception:
            pass

        # ── Doc type ──
        try:
            from config import DOC_TYPE_RULES
            for dtype, patterns in DOC_TYPE_RULES.items():
                for pat in patterns:
                    if re.search(pat, query):
                        pq.doc_types.append(dtype)
                        break
        except Exception:
            pass

        # ── Intent ──
        pq.intent = classify_intent(query)

        return pq
