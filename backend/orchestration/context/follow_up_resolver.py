"""FollowUpResolver — 会话级 follow-up 解析（P2.3~P2.6/D1）。

调用链（P2.7）：
    raw_query → load ConversationContext → FollowUpResolver
              → standalone_query → router_node → 各域

原则：
- 规则优先，确定性解析；LLM 仅在规则不可靠且开关开启时兜底（P2.6）。
- 无足够上下文时禁止瞎猜 → need_clarification=true（P2.9）。
- 输出统一 dict（raw_query/standalone_query/follow_up_detected/resolved/
  used_context/rewrite_method/need_clarification/clarification_question）。
- 不覆盖原始用户输入；raw_query 原样保留供 trace / UI。
"""
from __future__ import annotations

import re
import time

from backend.config.conversation_context import (
    FOLLOWUP_LLM_REWRITE_ENABLED,
    FOLLOWUP_LLM_REWRITE_TIMEOUT_SECONDS,
)
from backend.orchestration.context.conversation_context import ConversationContext
from backend.shared.logger import logger

# ── 指代/追问标记（P2.4 词表）─────────────────────────────────
# 强标记：单独出现即可判定为 follow-up（含澄清路径）
_STRONG_LOCATION_MARKERS = ("当地", "那里", "那边", "这个地方", "这边", "附近")
_DAY_MARKER_RE = re.compile(r"第[一二三四五六七八九十\d]+天")
_CONTINUATION_MARKERS = (
    "然后呢", "还有呢", "还有什么", "便宜一点呢", "贵一点呢",
    "推荐几个呢", "再推荐几个", "换一个",
)
# 弱标记：仅在上下文有明确实体时参与改写，绝不单独触发澄清
_WEAK_PRONOUNS = ("这个地方", "它", "这个")

# 澄清话术（P2.9）：走既有 done/answer 通道，不新增 SSE 事件类型
CLARIFICATION_QUESTION = "你指的是哪个城市或地区？"

# 续接类标记 → 语义后缀模板（确定性，无随机）
_CONTINUATION_TEMPLATES = {
    "然后呢": "然后怎么安排？",
    "还有呢": "还有什么推荐？",
    "还有什么": "还有什么推荐？",
    "便宜一点呢": "有没有便宜一点的推荐？",
    "贵一点呢": "有没有更好一点的推荐？",
    "推荐几个呢": "推荐几个",
    "再推荐几个": "再推荐几个",
    "换一个": "换一个推荐",
}

# P2.5 显式切换（overwrite）正则 ──
_OVERWRITE_PATTERNS = (
    # 算了，改成成都 ｜ 改成成都吧
    re.compile(r"改成\s*([^\s，,。.!！?？]{2,12})"),
    # 不去云南了，去成都 ｜ 不去云南了去成都
    re.compile(r"不去[^\s，,]{1,8}了[，,]?\s*去\s*([^\s，,。.!！?？]{2,12})"),
    # 换成都吧 ｜ 换成成都吧
    re.compile(r"换(?:成|到)?\s*([^\s，,。.!！?？]{2,12})(?:吧)?"),
)

# 非「目的地」候选：天数/数字/日期类短语（2026-09-22 实测踩坑）。
# 「改成3天」是改天数不是改目的地，曾被当 overwrite 写脏
# destination="3天"，污染后续跨轮上下文。目的地至少要含一个非数字字符
# 且不匹配天数短语。
_RE_OVERWRITE_DAY = re.compile(r"^[\d一二两三四五六七八九十百]+\s*(?:天|日|晚|号|日[早晚]|月|点分?)?$")


def _has_strong_marker(query: str) -> bool:
    if any(m in query for m in _STRONG_LOCATION_MARKERS):
        return True
    if _DAY_MARKER_RE.search(query):
        return True
    return any(m in query for m in _CONTINUATION_MARKERS)


def _detect_overwrite(query: str) -> str | None:
    """P2.5：显式切换目的地 → 返回新目的地；无切换返回 None。"""
    for pattern in _OVERWRITE_PATTERNS:
        m = pattern.search(query)
        if m:
            candidate = m.group(1).strip()
            # 「不去X了，去Y」里排除把旧目的地当新目的地
            if candidate and not any(
                m2 in candidate for m2 in ("然后", "还有")
            ) and not _RE_OVERWRITE_DAY.match(candidate):
                return candidate
    return None


def _pick_entity(ctx: ConversationContext) -> tuple[str, str]:
    """确定性实体优先级（P2.4）：最近明确实体 > city > destination > topic。

    「最近明确实体」= cities 末位（slot filler 按追加序维护，末位即最近
    点名/落地的城市，如 Q2「大理住两天」→ cities=[大理]）。
    Returns:
        (entity, used_context_key)
    """
    if ctx.cities:
        return ctx.cities[-1], "cities"
    if ctx.destination:
        return ctx.destination, "destination"
    if ctx.current_topic:
        return ctx.current_topic, "current_topic"
    return "", ""


def _strip_leading_connectives(query: str) -> str:
    return re.sub(r"^[那还再就]{1,2}(?=[一-龥])", "", query, count=1)


def _rule_rewrite(query: str, entity: str, used: list[str]) -> str | None:
    """规则改写：把指代/省略替换为实体。返回 None = 规则无法处理。"""
    q = query
    if not entity:
        return None

    # 1) 续接类：整体换成「实体 + 语义后缀」
    for marker, suffix in _CONTINUATION_TEMPLATES.items():
        if marker in q:
            if suffix.endswith("推荐") or "推荐几个" in marker or marker == "换一个":
                return f"{entity}{suffix}？"
            return f"{entity}{suffix}"

    # 2) 天次追问：「第二天呢」→「{entity}行程第2天的安排」
    day_m = _DAY_MARKER_RE.search(q)
    if day_m:
        day_text = day_m.group(0)
        return f"{entity}行程{day_text}有什么安排？"

    # 3) 地点指代替换（首个命中即止，保持确定性）
    for marker in _STRONG_LOCATION_MARKERS:
        if marker in q:
            replacement = f"{entity}附近" if marker == "附近" else entity
            rewritten = q.replace(marker, replacement, 1)
            return _strip_leading_connectives(rewritten)

    # 4) 弱代词：仅当存在实体时替换（不触发澄清）
    for pronoun in _WEAK_PRONOUNS:
        if pronoun in q:
            return q.replace(pronoun, entity, 1)

    return None


def _render_rewrite_prompt(
    last_user_turn: str, structured_context: str, raw_query: str,
) -> str:
    """追问改写 prompt：注册表优先（key=context.followup_rewrite，2026-10-06
    收编，版本治理/trace 记账/请求级 pin），异常降级内联构造（内容逐字节一致，
    守卫见 tests/prompts/test_bare_prompt_collection.py）。"""
    try:
        from backend.prompts.service import prompt_service

        return prompt_service.render_sync(
            "context.followup_rewrite",
            last_user_turn=last_user_turn,
            structured_context=structured_context,
            raw_query=raw_query,
        ).text
    except Exception as exc:  # noqa: BLE001 — prompt 读取失败不得阻断追问解析
        logger.warning(f"[FollowUpResolver] 注册表渲染失败，降级内置拼接: {exc}")
        return (
            "你是 query 改写器。把依赖上下文的用户问题改写成独立完整的问题。\n"
            f"上一轮用户发言：{last_user_turn}\n"
            f"结构化上下文：{structured_context}\n"
            f"当前问题：{raw_query}\n"
            "只输出 JSON，不要输出其他内容：\n"
            '{"standalone_query": "", "resolved": true, "used_context": [],'
            ' "need_clarification": false, "clarification_question": null}\n'
            "上下文不足以改写时 resolved=false 且 need_clarification=true。"
        )


def _llm_rewrite(
    raw_query: str,
    ctx: ConversationContext,
    last_user_turn: str,
) -> dict | None:
    """P2.6：轻量 LLM rewrite（规则不可靠时兜底，开关默认关）。

    只喂 current_query + structured_context + last_user_turn，不塞全量
    history。输出严格 JSON；解析失败不猜 → need_clarification。
    """
    try:
        from backend.infra.llm import llm

        prompt = _render_rewrite_prompt(
            last_user_turn or "（无）", ctx.snapshot(), raw_query,
        )
        start = time.perf_counter()
        resp = llm.invoke(
            prompt,
            max_tokens=200,
            timeout=FOLLOWUP_LLM_REWRITE_TIMEOUT_SECONDS,
        )
        text = resp.content if hasattr(resp, "content") else str(resp)
        logger.info(
            "[FollowUpResolver] LLM rewrite 耗时 %.0fms",
            (time.perf_counter() - start) * 1000,
        )
        # 统一策略链（2026-10-06 批次 C）：此前裸 re+json.loads，修复能力
        # 只增不减；全失败记 llm_json_parse_fail_total{source} 后回落澄清
        from backend.shared.json_extractor import extract_json

        data = extract_json(text, source="context.followup_rewrite")
        if not isinstance(data, dict):
            return None
        return {
            "standalone_query": str(data.get("standalone_query") or "").strip(),
            "resolved": bool(data.get("resolved")),
            "used_context": [str(u) for u in (data.get("used_context") or [])][:5],
            "need_clarification": bool(data.get("need_clarification")),
            "clarification_question": data.get("clarification_question"),
        }
    except Exception as exc:
        logger.warning("[FollowUpResolver] LLM rewrite 失败（不猜，走澄清）: %s", exc)
        return None


def resolve_followup(
    raw_query: str,
    context: ConversationContext,
    last_user_turn: str = "",
) -> dict:
    """P2.3 统一入口：raw_query + 上下文 → 独立问题（规则优先）。

    Returns:
        统一结构 dict：
        {
            "raw_query", "standalone_query", "follow_up_detected",
            "resolved", "used_context", "rewrite_method",
            "need_clarification", "clarification_question",
            # 附加：overwrite 动作（P2.5，供调用方同步清理）
            "overwrite_destination": str | None,
        }
    """
    raw_query = (raw_query or "").strip()
    result = {
        "raw_query": raw_query,
        "standalone_query": raw_query,
        "follow_up_detected": False,
        "resolved": False,
        "used_context": [],
        "rewrite_method": "none",
        "need_clarification": False,
        "clarification_question": None,
        "overwrite_destination": None,
    }
    if not raw_query:
        result["need_clarification"] = True
        result["clarification_question"] = CLARIFICATION_QUESTION
        return result

    # ── P2.5 显式切换优先于一切指代解析 ──
    new_dest = _detect_overwrite(raw_query)
    if new_dest:
        result.update(
            follow_up_detected=True,
            resolved=True,
            used_context=["overwrite"],
            rewrite_method="rule",
            overwrite_destination=new_dest,
        )
        return result

    # 无强标记 → 明确问题，零额外开销（P2.6：首轮不 rewrite）
    if not _has_strong_marker(raw_query):
        return result

    result["follow_up_detected"] = True
    entity, used_key = _pick_entity(context)

    # 规则改写
    if entity:
        rewritten = _rule_rewrite(raw_query, entity, result["used_context"])
        if rewritten:
            result.update(
                standalone_query=rewritten,
                resolved=True,
                used_context=[used_key],
                rewrite_method="rule",
            )
            return result

    # 规则不可靠 → LLM 兜底（默认关）；仍失败 → 澄清（禁止瞎猜，P2.9）
    if FOLLOWUP_LLM_REWRITE_ENABLED:
        llm_result = _llm_rewrite(raw_query, context, last_user_turn)
        if llm_result is not None and llm_result["resolved"] and llm_result["standalone_query"]:
            result.update(
                standalone_query=llm_result["standalone_query"],
                resolved=True,
                used_context=llm_result["used_context"] or ([used_key] if used_key else []),
                rewrite_method="llm",
            )
            return result

    result["need_clarification"] = True
    result["clarification_question"] = CLARIFICATION_QUESTION
    return result


def apply_resolution_to_context(context: ConversationContext, resolution: dict) -> None:
    """把 resolver 产生的状态变化写回上下文（overwrite / topic / evidence 清理）。

    与 resolve_followup 分离：调用方（runner）在确认采用该解析结果后才应用。
    """
    new_dest = resolution.get("overwrite_destination")
    if new_dest:
        context.overwrite_destination(new_dest)
        return
    if resolution.get("follow_up_detected") and resolution.get("resolved"):
        context.set_topic(resolution["standalone_query"][:40])
