"""travel/services/llm_intent_service.py — 理解层 LLM（D 批，验收 #97/#98/#103）

slot_filler 词表分类器（classify_intent）不认识的消息，交 LLM 补判一次。
三条铁律（重构计划「三层防线」+ #103）：

  1. **只在词表返回 None 时触发**（词表能接住的永不过 LLM——执行链零
     LLM 不动摇，LLM 成本只花在词表盲区）；
  2. **输出是枚举不是行程**：LLM 只产 intent 家族，字段级抽取仍归词表
     正则——理解层在结构上不可能改行程（#103「回答层不得越权修改」的
     结构化实现，不靠提示词自觉）；
  3. **失败回落**：超时/非法 JSON/枚举外值 → 返回 None → 走既有兜底链
     （slot_filler 退让），不比现状差。

模型角色 = main（DB 绑定 doubao-seed-2.0-mini，解析链实机活着）。
选型记录：tool_selector/fallback 角色在本库当前解析为空（registry 注入
状态问题，单独登记）——「解析链能出可用实例」是选型先决条件。
灰度：TRAVEL_LLM_INTENT_ENABLED 默认关；开启后 golden 门禁
（tests/travel/test_intent_llm_golden.py）双模式跑。
"""
from __future__ import annotations

import json
import re

from backend.shared.logger import logger

# 允许 LLM 判出的 intent 家族 → TravelIntent 值映射。
# 注意**没有 plan**：「去厦门玩3天」这类词表已能接住（到不了 LLM）；而
# 词表接不住却像规划的消息（「帮我安排个说走就走的旅行」缺目的地），
# slot_filler 本来就会追问 destination——LLM 强判 plan 反而会跳过追问。
_FAMILY_TO_INTENT: dict[str, str] = {
    "query": "query_dynamic",       # 询问/问答（先答不改）
    "modify": "modify",             # 改单诉求（逐条改单通道）
    "out_of_scope": "out_of_scope", # 出域引导
}

_SYSTEM_PROMPT = """你是旅游助手的意图分类器。把用户消息分成四类之一，只输出 JSON：
{"family": "..."}，family 取值：
- "query"：提问/询问/对比/求推荐（想了解信息，不是要改行程）
- "modify"：对当前行程的修改诉求（改天数/节奏/加减地点/换掉某处）
- "out_of_scope"：与旅游规划无关（订单、退款、写代码、闲聊其他业务）
- "unknown"：无法判断
只输出 JSON，不要解释。"""

_JSON_RE = re.compile(r"\{[^{}]*\}", re.S)


def classify_intent_llm(
    message: str,
    *,
    has_itinerary: bool = False,
    has_destination: bool = False,
    timeout_ms: int | None = None,
) -> str | None:
    """词表盲区的 LLM 补判。返回 intent 值（TravelIntent 家族）或 None（回落）。

    返回值是字符串家族映射（query_dynamic/modify/out_of_scope），
    调用方按现有 intent 通道消费——无 plan 映射（见模块 docstring 铁律 2）。
    """
    from backend.config.travel import (
        TRAVEL_LLM_INTENT_ENABLED,
        TRAVEL_LLM_INTENT_TIMEOUT_MS,
    )

    if not TRAVEL_LLM_INTENT_ENABLED or not (message or "").strip():
        return None
    timeout = (timeout_ms or TRAVEL_LLM_INTENT_TIMEOUT_MS) / 1000.0
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        from backend.infra.async_utils import sync_call_with_timeout

        # 构建路径选型（2026-10-04 实机甄别）：get_llm_for_role 与全局
        # proxy 的 default 通道在本库均落到无凭据构造（x-api-key=None，
        # 全角色复现，登记模型治理线）；_build_llm_for 是唯一带 DB 托管
        # 凭据解析的构建出口（P1a-2 契约）——按 main 角色的 DB 绑定名直构。
        from backend.config import model_roles
        from backend.infra.llm.proxy import _build_llm_for

        model_name = str((model_roles.resolve_effective("main") or {})
                         .get("value") or "")
        if not model_name:
            logger.info("[TravelLLMIntent] main 角色无模型绑定，回落词表")
            return None
        intent_llm = _build_llm_for(model_name).bind(temperature=0,
                                                     max_tokens=1024)
        context_hint = (
            f"[当前有行程: {has_itinerary}; 已知目的地: {has_destination}]")
        response = sync_call_with_timeout(
            intent_llm.invoke, timeout,
            [SystemMessage(content=_SYSTEM_PROMPT),
             HumanMessage(content=f"{context_hint}\n用户消息：{message}")],
        )
        raw = str(getattr(response, "content", "") or "")
        payload = None
        try:  # 整体即 JSON（含嵌套对象）优先
            payload = json.loads(raw)
        except (ValueError, TypeError):
            match = _JSON_RE.search(raw)  # 前后带说明文字时抽首个平衡块
            if match:
                try:
                    payload = json.loads(match.group(0))
                except ValueError:
                    payload = None
        if not isinstance(payload, dict):
            logger.info("[TravelLLMIntent] 输出无 JSON，回落词表: %r", raw[:80])
            return None
        family = str(payload.get("family") or "").strip().lower()
        # #103 守卫：只取 family 字段——LLM 输出里的任何其他键（行程/回复/
        # 参数）在结构上被丢弃，不存在被消费的通道。
        intent = _FAMILY_TO_INTENT.get(family)
        if intent is None:
            logger.info("[TravelLLMIntent] family=%r 不在白名单，回落词表", family)
            return None
        logger.info("[TravelLLMIntent] %r → %s", message[:40], intent)
        return intent
    except Exception as e:  # noqa: BLE001 — 理解层失败必须回落词表
        logger.info("[TravelLLMIntent] LLM 判定失败（回落词表）: %s: %s",
                    type(e).__name__, str(e)[:120])
        return None
