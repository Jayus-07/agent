"""orchestration/router/query_understanding.py — 统一问题理解层（QueryRouter）

治理改造（2026-09-22）。目标：给主图一个结构化的「用户想干什么」判断，
并为 Planner/Reporter 提供复杂度元数据 —— 不为此新增任何固定 LLM 调用。

三级识别（用户规格 §12，全部复用既有设施，不另起炉灶）：
  Level 1  确定性规则   —— 本模块的正则实体/意图关键词（~µs 级）
  Level 2  RoutingEngine —— router_node 已算好的 RouteDecision
                            （域判断 / 能力候选 / 执行方式）
  Level 3  LLM fallback —— 由 RoutingEngine 在基础设施降级时承担；
                            本模块**绝不**自己再调一次 LLM

输出（state["query_understanding"]）：
  {
    "intent":           "knowledge_query | sql_analysis | business_analysis |
                         report_generation | tool_operation | general_chat",
    "domain":           候选 capability 的域前缀（sql/rag/business/...）或 "general",
    "query_type":       "fact_lookup | procedural | analysis | composite | chat",
    "complexity":       "simple | moderate | complex",
    "need_rag":         bool,
    "need_sql":         bool,
    "need_tool":        bool,   # 除 sql/rag 之外的业务工具
    "need_planner":     bool,   # 供 Planner/Reporter 理解复杂度，不改写 RouteDecision
    "entities":         {...},  # sku / order_id / error_code / url / date_range ...
    "time_range":       str|None,
    "confidence":       float,  # 0-1
    "decision_layer":   "rule | router_vector | router_llm | rules_only",
    "downgrade":        None | {"to": "direct", "capability": "sql.query"},
  }

硬约束：纯 stdlib、零 IO、零 LLM —— 处在 router_node 热路径上，
与 config/model_roles.py 同一纪律（该模块注释记录过 torch 6.3s 的教训）。
"""
from __future__ import annotations

import re
from typing import Any

__all__ = ["understand_query", "ROUTE_CAPABILITY_NEEDS"]

# ── Level 1：确定性实体规则 ──────────────────────────────────

_ENTITY_PATTERNS: dict[str, str] = {
    "sku": r"\bSKU[-A-Za-z0-9]{2,}\b|[A-Z]{1,4}-?\d{3,6}\b",
    "order_id": r"\b(?:订单号?|order)\s*[:：#]?\s*([A-Z0-9-]{6,})\b",
    "error_code": r"\b(?:ERR?|ERROR|E)[-_]?\d{3,6}\b",
    "url": r"https?://[^\s，。]+",
    "clause_no": r"第[一二三四五六七八九十百\d]+条",
    "user_id": r"\b(?:用户|user)[_ ]?id\s*[:：]?\s*\w+\b",
}

_TIME_RANGE_PATTERNS: tuple[str, ...] = (
    r"(最近|近|过去)\s*[一二两三四五六七八九十\d]+\s*(天|日|周|个?月|年)",
    r"(本月|上月|上个月|本季度|上季度|今年|去年|本周|上周)",
    r"\d{4}[-/年]\d{1,2}([-/月]\d{1,2})?[日号]?(至|到|~|—|—)?\d{0,4}[-/年]?\d{0,2}[-/月]?\d{0,2}[日号]?",
)

# 多意图 / 组合任务信号 → complexity 抬升
_COMPOSITE_MARKERS: tuple[str, ...] = (
    "然后", "再根据", "并且根据", "同时", "结合", "根据.{0,12}(判断|评估|分析)",
    "先.{1,20}再", "基于.{1,20}(判断|评估|分析)",
)

_CHAT_MARKERS: tuple[str, ...] = (
    "你好", "您好", "谢谢", "再见", "你是谁", "你能做什么", "哈啰", "hi", "hello",
)

_FACT_SQL_MARKERS: tuple[str, ...] = (
    "多少", "几个", "数量", "库存", "余额", "金额是多少", "还剩", "存量",
)

# Level 1 关键词兜底（无路由候选时从问题本身推断 need_*）。
# ⚠️ capabilities.yaml 的 rule_keywords 是路由层唯一事实源；这里是理解层的
# 只读镜像（选高频核心词），两处语义不一致时以 yaml 为准 —— 改 yaml 时
# 请顺手核对这张表（manifest 加载依赖 pyyaml，违反本模块纯 stdlib 纪律，故不直接读）。
_KEYWORD_NEEDS: dict[str, tuple[str, ...]] = {
    "need_rag": (
        "制度", "规定", "规范", "政策", "流程", "标准", "时效", "SLA",
        "如何", "怎么", "是什么", "什么叫", "定义", "审核", "退款", "退货",
        "换货", "售后", "规则", "条例", "SOP", "指南",
    ),
    "need_sql": (
        "多少", "几个", "统计", "数量", "金额", "总和", "排名", "库存",
        "销量", "销售额", "环比", "同比", "增长率", "余额", "存量",
    ),
}


def _keyword_needs(query: str) -> dict[str, bool]:
    """Level 1 关键词推断（仅在路由候选缺失时使用）。"""
    return {
        flag: any(k in query for k in words)
        for flag, words in _KEYWORD_NEEDS.items()
    }


def _extract_entities(query: str) -> dict[str, Any]:
    """Level 1 实体抽取：SKU / 订单号 / 错误码 / URL / 条款 / 时间范围。"""
    entities: dict[str, Any] = {}
    for key, pattern in _ENTITY_PATTERNS.items():
        match = re.search(pattern, query, re.IGNORECASE)
        if match:
            # 有捕获组取组 1（剥掉「订单号：」前缀），无捕获组取整体匹配
            entities[key] = (
                match.group(1) if match.groups() else match.group(0)
            ).strip()
    time_range = None
    for pattern in _TIME_RANGE_PATTERNS:
        match = re.search(pattern, query)
        if match:
            time_range = match.group(0)
            break
    if time_range:
        entities["time_range"] = time_range
    return entities


def _is_composite(query: str) -> bool:
    """是否带组合任务信号（「查库存并根据制度判断要不要补货」类）。"""
    return any(re.search(p, query) for p in _COMPOSITE_MARKERS)


def _is_smalltalk(query: str) -> bool:
    return any(marker in query.lower() for marker in _CHAT_MARKERS)


# ── Level 2：capability → need 映射（复用既有路由候选）────────

# candidate capability 名 → need 布尔位。未列出的 routed capability 一律
# 视为 need_tool=True（业务工具）。
ROUTE_CAPABILITY_NEEDS: dict[str, dict[str, bool]] = {
    "sql.query": {"need_sql": True},
    "rag.search": {"need_rag": True},
    "business.analyze": {"need_tool": True},
    "report.generate": {"need_tool": True},
}


def _needs_from_candidates(candidates: list) -> dict[str, bool]:
    needs = {"need_rag": False, "need_sql": False, "need_tool": False}
    for candidate in candidates or []:
        mapping = ROUTE_CAPABILITY_NEEDS.get(str(getattr(candidate, "name", "")))
        if mapping:
            for key, value in mapping.items():
                needs[key] = needs[key] or value
        else:
            needs["need_tool"] = True
    return needs


def _decision_layer(decision: Any) -> str:
    """从 RoutingEngine 的 RouteDecision 推断证据来源。

    保留旧输出枚举，兼容评测报告；最终拍板只属于 RoutingEngine。
    """
    confidence = float(getattr(decision, "confidence", 0.0) or 0.0)
    if confidence >= 0.8:
        return "rule"
    if confidence >= 0.5:
        return "router_vector"
    return "router_llm"


# ── 主入口 ──────────────────────────────────────────────────

def understand_query(query: str, decision: Any = None) -> dict[str, Any]:
    """结构化问题理解。decision 是 RoutingEngine 的 RouteDecision（可为 None）。

    纯函数：不调 LLM、不碰网络/DB —— Level 3 LLM fallback 已由 decision
    的产生过程（RoutingEngine 内部）承担，这里只做规则合成。
    """
    query = (query or "").strip()
    if not query:
        return _empty()

    entities = _extract_entities(query)
    composite = _is_composite(query)
    smalltalk = _is_smalltalk(query)

    candidates = list(getattr(decision, "candidates", None) or [])
    needs = _needs_from_candidates(candidates)
    if not candidates:
        # Level 1 兜底：路由无候选（规则/向量全 miss 或未传 decision）时，
        # 直接从问题关键词推断 —— 「年假规则是什么」不该因为路由 miss 就丢掉 need_rag。
        needs.update(_keyword_needs(query))

    # ── intent / domain / query_type ──
    top = candidates[0] if candidates else None
    top_name = str(getattr(top, "name", "") or "")
    domain = top_name.split(".", 1)[0] if top_name else "general"
    if smalltalk and not candidates:
        intent, query_type = "general_chat", "chat"
    elif "need_sql" in needs and needs.get("need_sql") and composite:
        intent, query_type = "sql_analysis", "composite"
    elif top_name == "sql.query":
        intent = "sql_analysis"
        query_type = "fact_lookup" if any(m in query for m in _FACT_SQL_MARKERS) else "analysis"
    elif top_name == "rag.search":
        intent, query_type = "knowledge_query", (
            "procedural" if any(m in query for m in ("怎么", "如何", "流程", "SOP")) else "fact_lookup"
        )
    elif top_name == "report.generate":
        intent, query_type = "report_generation", "composite"
    elif top_name:
        intent, query_type = "tool_operation", "analysis"
    else:
        intent, query_type = "knowledge_query", "analysis"

    # ── complexity / need_planner ──
    need_count = sum(1 for v in needs.values() if v)
    if composite and need_count >= 2:
        complexity = "complex"
    elif composite or need_count >= 2:
        complexity = "moderate"
    elif need_count == 1:
        complexity = "simple"
    else:
        complexity = "simple" if not candidates else "moderate"

    mode = str(getattr(getattr(decision, "execution_mode", ""), "value", "") or "")
    # need_planner = 组合任务信号（「根据 X 判断 Y」）或多能力才要 Planner；
    # 单一能力的简单事实查询不值得拆解（用户规格 §13/§14：FAQ、单步 RAG、
    # 单 SQL 不进 Planner）。注意：组合措辞信号比候选数更可信 —— 路由候选
    # 可能只抓到一个能力（向量路由单选），但「根据制度判断」明确要求多步。
    need_planner = composite or need_count >= 2
    # workflow 是已固化的多步编排，保持既有路径（由 workflow_executor 执行）
    if mode == "workflow":
        need_planner = False

    # ── 兼容性建议：路由给了 plan 但理解层判定简单 ──
    # 该字段保留给评测/观测；统一 RouteDecision 已由 RoutingEngine 拍板，
    # router_node 不消费它改写 route_mode。
    downgrade = None
    if mode == "plan" and not need_planner and complexity == "simple" and top is not None:
        downgrade = {"to": "direct", "capability": top_name}

    confidence = float(getattr(decision, "confidence", 0.0) or 0.0)
    if confidence == 0.0 and not candidates:
        # 关键词命中给中等置信（Level 1 可信但弱于路由强信号）；全 miss 给低置信
        confidence = 0.9 if smalltalk else (0.7 if any(needs.values()) else 0.3)

    return {
        "intent": intent,
        "domain": domain,
        "query_type": query_type,
        "complexity": complexity,
        "need_rag": needs["need_rag"],
        "need_sql": needs["need_sql"],
        "need_tool": needs["need_tool"],
        "need_planner": bool(need_planner),
        "entities": entities,
        "time_range": entities.get("time_range"),
        "confidence": round(confidence, 2),
        "decision_layer": _decision_layer(decision) if decision is not None else "rules_only",
        "downgrade": downgrade,
    }


def _empty() -> dict[str, Any]:
    return {
        "intent": "general_chat",
        "domain": "general",
        "query_type": "chat",
        "complexity": "simple",
        "need_rag": False,
        "need_sql": False,
        "need_tool": False,
        "need_planner": False,
        "entities": {},
        "time_range": None,
        "confidence": 0.0,
        "decision_layer": "rules_only",
        "downgrade": None,
    }
