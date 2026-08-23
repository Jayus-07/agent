"""competitor/extractor.py — LLM 结构化抽取（带规则兜底）

策略: LLM 按 JSON schema 抽取 → 解析失败或字段可信度低时降级正则规则。
LLM 优势在标题/促销/卖点等非结构化字段；价格数字正则反而更稳，两者互备。
"""
import json
import re
from typing import Any

from backend.competitor.adapters import confidence, detect_currency, extract_by_rules
from backend.shared.logger import logger

_EXTRACT_PROMPT = """你是电商商品页数据抽取器。从下面的网页正文中抽取商品信息，只输出 JSON，不要任何解释。

输出 JSON schema:
{{
  "title": "商品标题（string，无则空字符串）",
  "price": 现价（number，找不到则 null）,
  "original_price": 原价或划线价（number，无则 null）,
  "currency": "CNY" 或 "USD" 等,
  "promo_text": "促销活动文案（string，无则空字符串）",
  "rating": 评分（number，无则 null）,
  "review_count": 评价数（integer，无则 null）,
  "in_stock": 有货 true / 无货 false,
  "highlights": "商品卖点，逗号分隔，最多5个（string）"
}}

网页正文（可能被截断）:
---
{content}
---

只输出 JSON。"""


# 智能截断锚点关键词：评分/评价/卖点/促销通常位于页面折叠区，简单 [:6000] 会丢失
_WINDOW_KEYWORDS = (
    "out of 5", "stars", "ratings", "reviews", "评价", "评论", "好评率",
    "about this item", "item highlights", "商品介绍", "卖点", "规格参数",
    "coupon", "促销", "优惠", "满减",
)


def _smart_window(markdown: str, limit: int = 8000) -> str:
    """智能截断：保留头部 + 关键信息段（评分/评价/卖点/促销），避免折叠区信息丢失"""
    if len(markdown) <= limit:
        return markdown
    head = markdown[:4000]
    body = markdown[4000:]
    low = body.lower()
    spans: list[tuple[int, int]] = []
    for kw in _WINDOW_KEYWORDS:
        idx = low.find(kw)
        if idx >= 0:
            spans.append((max(0, idx - 100), idx + 700))
    spans.sort()
    merged: list[tuple[int, int]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    parts = [head]
    used = len(head)
    for s, e in merged:
        if used + (e - s) > limit:
            break
        parts.append(body[s:e])
        used += e - s
    return "\n...\n".join(parts)


def _parse_llm_json(text: str) -> dict[str, Any] | None:
    """从 LLM 回复中解析 JSON（容忍 ```json 包裹 / 前后杂讯）"""
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def _normalize(data: dict[str, Any]) -> dict[str, Any]:
    """字段类型归一（LLM 可能返回字符串数字 / "无" 等）"""

    def _num(v):
        if v is None or v == "" or v == "null":
            return None
        if isinstance(v, bool):
            return None
        try:
            return float(str(v).replace(",", "").replace("¥", "").replace("￥", ""))
        except (ValueError, TypeError):
            return None

    return {
        "title": str(data.get("title") or "")[:120],
        "price": _num(data.get("price")),
        "original_price": _num(data.get("original_price")),
        "currency": str(data.get("currency") or "CNY"),
        "promo_text": str(data.get("promo_text") or "")[:200],
        "rating": _num(data.get("rating")),
        "review_count": int(_num(data.get("review_count")) or 0) or None,
        "in_stock": 1 if data.get("in_stock") in (True, 1, "true", "有货") else 0,
        "highlights": str(data.get("highlights") or "")[:300],
    }


def extract_fields(platform: str, markdown: str, use_llm: bool = True) -> dict[str, Any]:
    """结构化抽取入口: LLM 优先 → 可信度校验 → 正则兜底"""
    rule_result = extract_by_rules(platform, markdown)
    # 币种符号比 LLM 判断更可靠（LLM 常默认 CNY）
    rule_currency = rule_result.get("currency", "CNY")

    if not use_llm:
        return {**rule_result, "extract_method": "regex"}

    try:
        from backend.infra.llm import llm

        content = _smart_window(markdown)  # 智能截断：头部 + 评分/评价/卖点关键段
        resp = llm.invoke(_EXTRACT_PROMPT.format(content=content))
        text = resp.content if hasattr(resp, "content") else str(resp)
        data = _parse_llm_json(text)
        if data:
            llm_result = _normalize(data)
            # 可信度校验: LLM 没抽出价格但正则抽到了 → 用正则价格补
            if llm_result.get("price") is None and rule_result.get("price") is not None:
                llm_result["price"] = rule_result["price"]
            if rule_currency != "CNY":
                llm_result["currency"] = rule_currency
            if confidence(llm_result) >= 0.5:
                return {**llm_result, "extract_method": "llm"}
            logger.warning(f"[Competitor:extractor] LLM 抽取可信度低，降级规则抽取")
    except Exception as e:
        logger.warning(f"[Competitor:extractor] LLM 抽取失败，降级规则抽取: {e}")

    return {**rule_result, "extract_method": "regex"}
