"""领域同义词字典（2026-08-20）。

用途：query 检索时扩展关键词，覆盖用户口语化表达与文档书面用语之间的语义鸿沟。
例：用户问 "电水壶异味" → 扩展 ["电水壶味道", "电水壶发臭", "电水壶不正常"]，
让仅含 "味道"/"发臭" 的文档也能被命中。

设计原则：
1. 只放高频、业务强相关的同义词（避免过度扩展稀释信号）
2. 同义词组内词频相近（保证扩展合理）
3. 不放语义有偏差的词（如 "保修" 不放 "保养"，含义有距离）
4. 优先覆盖历史失败 case (RT-006/013/014/032) 涉及的关键词

扩展策略：精确匹配 query 中的 key，按 value 列表生成变体。
"""
from __future__ import annotations

# ── 售后/退换类（同义词组）────────────────────────────────────
SYNONYMS: dict[str, list[str]] = {
    # 保修类
    "保修": ["保修", "维修", "售后", "质保"],
    "维修": ["维修", "保修", "售后"],
    "售后": ["售后", "保修", "维修"],

    # 退款/退货
    "退款": ["退款", "退货", "退钱", "返还"],
    "退货": ["退货", "退款", "退钱"],
    "退换": ["退换", "换货", "退货", "更换"],
    "换货": ["换货", "退换", "更换"],

    # 商品状态类
    "破损": ["破损", "损坏", "坏掉", "碎了"],
    "损坏": ["损坏", "破损", "坏掉"],
    "异味": ["异味", "味道", "发臭", "不正常"],
    "故障": ["故障", "坏掉", "出问题"],

    # 物流类
    "物流": ["物流", "快递", "配送", "运输"],
    "快递": ["快递", "物流", "配送"],
    "丢件": ["丢件", "丢失", "未到货"],
    "签收": ["签收", "收货", "收到"],

    # 时间/流程类
    "交期": ["交期", "交货", "到货时间", "交付"],
    "流程": ["流程", "步骤", "操作"],
    "审批": ["审批", "审核", "批准"],

    # 财务类
    "报销": ["报销", "费用", "开支"],
    "付款": ["付款", "支付", "打款"],

    # 合同/法律类
    "合同": ["合同", "协议", "契约"],
    "保密": ["保密", "机密", "隐私"],
}


def expand_query(query: str, max_expansions: int = 4) -> list[str]:
    """扩展 query 为同义词变体列表。

    Args:
        query: 原始 query
        max_expansions: 最大变体数（控制 RRF 融合成本）

    Returns:
        变体列表（包含原始 query）。最多 max_expansions + 1 个。

    示例:
        "电水壶有异味" → ["电水壶有异味", "电水壶有味道", "电水壶有发臭", ...]
    """
    expansions: list[str] = [query]
    seen: set[str] = {query}

    # 按 SYNONYMS 顺序遍历（命中即扩展）
    for word, synonyms in SYNONYMS.items():
        if word not in query:
            continue
        for syn in synonyms:
            if syn == word:
                continue
            expanded = query.replace(word, syn)
            if expanded in seen:
                continue
            expansions.append(expanded)
            seen.add(expanded)
            if len(expansions) >= max_expansions + 1:
                return expansions
    return expansions


__all__ = ["SYNONYMS", "expand_query"]