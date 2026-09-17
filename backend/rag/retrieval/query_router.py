"""§7 查询类型路由（附录二 R4-P3）。

任务书 §7 定义 7 类查询与策略。本模块负责其中可从 query 文本判定的 5 类
（exact_id / table_value / multi_condition / cross_doc / faq），输出 RRF
加权策略供两条融合路径消费：

  - enhanced 三路（enhanced_hybrid_retrieval.py）：dense 3.0×vw、sparse 1.0×bw
  - fallback 双路（hybrid.py）：vector 累加×vw、bm25 累加×bw

其余 2 类非路由职责（此处仅保留类型常量与中性策略占位，便于 trace 语义统一）：
  - low_confidence（低置信）：检索后由置信度门控（enhanced 复杂度阈值）与
    evidence gate 判定，见 backend/rag/evidence_gate.py；
  - no_evidence（无证据）：evidence gate 拒答路径处理。

单一事实来源（治理 B，参照 COMPLEX_PATTERNS 先例）：
EXACT_IDENTIFIER_PATTERNS 由本模块持有，hybrid.py 的 _classify_query_tier
复用本表判定 Tier 2，勿在两处各维护一份。

kill-switch：RAG_QUERY_ROUTER（默认 true）。route() 每次调用时读取 env，
运维可不重启直接关闭；关闭时返回 faq + 中性权重，行为与 P3 之前完全一致
（权重 1.0 乘法不改变 RRF 分数）。
"""
import os
import re

# =====================================================
# 精确标识符正则：检测到任一则 exact_id（Tier 2 同表复用）
# 注意：
# 1. 全部要求大写/明确边界，避免误伤自然语言中的英文单词
# 2. 使用 [A-Za-z0-9] 而非 \w，因为 \w 在 Python 中默认匹配 Unicode（含中文）
# =====================================================
EXACT_IDENTIFIER_PATTERNS = [
    re.compile(r'\b[A-Z]{2,}[-_]\d{3,}\b'),                      # SKU/型号: AB-1234, XC-HT-2025（须带连字符/下划线）
    re.compile(r'(?:订单|单号|流水号)[号:]?\s*[A-Za-z0-9]{6,}'),   # 订单号（仅英文数字，不匹配中文）
    re.compile(r'(?:错误码|错误号|error\s*code)[号:]?\s*[A-Za-z0-9]+', re.I),  # 错误码（仅英文数字）
    re.compile(r'(?:保单|合同)(?:(?:编号|号|ID)[:：]?\s*|[:：]\s*)[A-Z0-9][A-Z0-9_-]{3,}', re.I),  # 保单/合同编号（须有"编号/号/ID"标签或冒号分隔）
    re.compile(r'\bv\d+\.\d+(?:\.\d+)?\b', re.I),                 # 版本号: v2.1, v3.0.1（须 v 前缀）
    re.compile(r'\b[A-Z]{1,4}\d{3,6}\b'),                         # 型号: A1234, AB5678（大写+3位以上数字+词边界）
    re.compile(r'(?:条款|条例|法规)\s*第?\s*\d+[条款章节]'),        # 法律条款引用
    re.compile(r'\b0x[A-Fa-f0-9]{4,}\b'),                         # 十六进制错误码: 0x80004005
    re.compile(r'\b[A-Z]{2,}_\d{2,}\b'),                          # 下划线格式: ERR_1234, CODE_5678
    re.compile(r'(?:编号|文号|单号)\s*[:：]?\s*[A-Z]{2,}-[\dA-Z-]{3,}'),  # 显式编号标签 + 字母前缀格式: 编号 XC-HT-2025-041
]

# 表格数值查询：数值/指标名词 + 疑问词，或显式数值条件
# （贴近评测集语料：上限是多少 / 金额是多少 / 不合格率超过多少 / RPO/RTO）
_TABLE_VALUE_RE = re.compile(
    r"(?:标准|上限|下限|限额|额度|金额|总额|比例|比率|合格率|不合格率|差异率|"
    r"阈值|时限|周期|指标|分界|节点|折扣|利率|费率|积分)"
    r"[^。？！?！]{0,8}?(?:是多少|多少|如何计算|怎么计算)"
    r"|(?:RPO|RTO|SLO|QPS|SLA)"
    r"|(?:超过|不超过|不低于)\s*[\d,.]+\s*(?:%|％|元|万|分|天|小时)"
)

# 多条件查询：逻辑连接词 + 条件动词（"同时出现安全事故"、"且需要满足"）
_MULTI_CONDITION_RE = re.compile(
    r"(?:同时|且|并且|以及)[^。？！?！]{0,15}?(?:满足|符合|需要|要求|出现|超过|低于)"
    r"|(?:满足|符合)[^。？！?！]{0,10}?(?:条件|要求)"
)

# 跨文档查询：分别/各自 + 疑问，或 A 和 B 的对比
_CROSS_DOC_RE = re.compile(
    r"(?:分别|各自)[^。？！?！]{0,10}?(?:需要|是什么|如何|怎样)"
    r"|[^。？！?！]{1,12}和[^。？！?！]{1,12}的(?:区别|不同|差异)"
    r"|(?:两种|三种|几)种(?:方式|方法|类型|情况|物流|渠道)"
)

# 分类优先级：exact_id（最强信号）→ multi_condition → cross_doc → table_value → faq
# 说明：
# - exact_id 优先：编号是无歧义锚点，权重策略最激进（bm25 1.5）
# - multi_condition 优先于 table_value：多条件查询常含数字（"评分低于60分"），
#   若 table_value 先命中会把多条件查询误判为单点数值查询
# - cross_doc 优先于 table_value："A和B的上限分别是多少"语义重心在跨文档对比
_CLASSIFY_PRIORITY = ("exact_id", "multi_condition", "cross_doc", "table_value")

# 查询类型常量（含 2 个非路由职责占位，供 trace 语义统一）
QT_EXACT_ID = "exact_id"
QT_TABLE_VALUE = "table_value"
QT_MULTI_CONDITION = "multi_condition"
QT_CROSS_DOC = "cross_doc"
QT_FAQ = "faq"
QT_LOW_CONFIDENCE = "low_confidence"
QT_NO_EVIDENCE = "no_evidence"

_ALL_QUERY_TYPES = (
    QT_EXACT_ID, QT_TABLE_VALUE, QT_MULTI_CONDITION, QT_CROSS_DOC,
    QT_FAQ, QT_LOW_CONFIDENCE, QT_NO_EVIDENCE,
)

# 策略表：RRF 各路权重的乘性修正（1.0 = 中性，与 P3 之前行为一致）
# 保守原则：除 exact_id（评测集 0 命中、无回归风险）外均收敛在 [0.9, 1.15]
_STRATEGIES: dict[str, dict[str, float]] = {
    QT_EXACT_ID: {"vector_weight": 0.85, "bm25_weight": 1.5},
    QT_TABLE_VALUE: {"vector_weight": 1.0, "bm25_weight": 1.15},
    QT_MULTI_CONDITION: {"vector_weight": 1.05, "bm25_weight": 1.0},
    QT_CROSS_DOC: {"vector_weight": 1.1, "bm25_weight": 0.9},
    QT_FAQ: {"vector_weight": 1.0, "bm25_weight": 1.0},
    # 占位（非路由判定）：保持中性，实际处理在 evidence gate / 置信度门控
    QT_LOW_CONFIDENCE: {"vector_weight": 1.0, "bm25_weight": 1.0},
    QT_NO_EVIDENCE: {"vector_weight": 1.0, "bm25_weight": 1.0},
}

_NEUTRAL_STRATEGY = {"vector_weight": 1.0, "bm25_weight": 1.0}


def _router_enabled() -> bool:
    """kill-switch：每次调用读取 env，运维可不重启关闭路由。"""
    return os.getenv("RAG_QUERY_ROUTER", "true").lower() == "true"


def classify_query_type(query: str) -> dict:
    """规则分类 5 类查询，返回 {"query_type", "signals"}。

    signals 记录命中依据（供 trace 调试），无命中时为空列表。
    """
    q = (query or "").strip()
    signals: list[str] = []

    # exact_id：任一标识符模式命中
    for pat in EXACT_IDENTIFIER_PATTERNS:
        m = pat.search(q)
        if m:
            signals.append(f"exact_id:{m.group(0)}")
            return {"query_type": QT_EXACT_ID, "signals": signals}

    if _MULTI_CONDITION_RE.search(q):
        return {"query_type": QT_MULTI_CONDITION,
                "signals": [f"multi_condition:{_MULTI_CONDITION_RE.search(q).group(0)}"]}

    if _CROSS_DOC_RE.search(q):
        return {"query_type": QT_CROSS_DOC,
                "signals": [f"cross_doc:{_CROSS_DOC_RE.search(q).group(0)}"]}

    if _TABLE_VALUE_RE.search(q):
        return {"query_type": QT_TABLE_VALUE,
                "signals": [f"table_value:{_TABLE_VALUE_RE.search(q).group(0)}"]}

    return {"query_type": QT_FAQ, "signals": signals}


def strategy_for(query_type: str) -> dict:
    """查询类型 → RRF 权重策略。未知类型（含非路由占位）返回中性。"""
    return dict(_STRATEGIES.get(query_type, _NEUTRAL_STRATEGY))


def route(query: str) -> dict:
    """路由入口：classify + strategy + kill-switch。

    Returns:
        {"query_type", "signals", "vector_weight", "bm25_weight", "enabled"}
        enabled=False（kill-switch 关闭）时 query_type="disabled"、权重中性。
    """
    if not _router_enabled():
        return {"query_type": "disabled", "signals": [],
                **_NEUTRAL_STRATEGY, "enabled": False}
    result = classify_query_type(query)
    strategy = strategy_for(result["query_type"])
    return {**result, **strategy, "enabled": True}
