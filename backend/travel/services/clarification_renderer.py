"""travel/services/clarification_renderer.py — 追问表达层（2026-10-08 STOP 4）

ClarificationPlan（规则产物）+ 用户原话 + 已确认事实 → 一句自然的追问。
**LLM 只决定「怎么问」，永远不决定「问什么」**：ask_slot / options /
背景事实全部来自 clarification_service 的确定性计划，本模块只把
「第 N 号追问意图」换成人话，失败立即模板接管（不重试）。

降级链：LLM（flag 开）→ 任何失败（timeout/非法 JSON/为空/过长/越权内容）
→ render_template（确定性、零 LLM）。追问是低价值高频链路，重试无意义。

输出校验（Schema Validate）：只认 {"question": str}；单行、4~160 字、
禁 JSON 残渣与 URL —— 越权内容（选项/承诺/多问）直接落模板。
"""
from __future__ import annotations

import json

from backend.shared.logger import logger

# 模块常量为注册表模板（travel_clarification_renderer.yaml）的渲染等价形，
# 占位符在降级路径经 .format() 填充；漂移守卫见
# tests/prompts/test_bare_prompt_collection.py。
_SYSTEM_PROMPT = """你是旅游助手的追问表达层。系统已经确定：
- 已知信息：{known_facts}
- 本轮只问这一个槽位：{ask_slot}（标准问法：{slot_question}）
- 点击选项已由系统生成，与你无关

你的唯一任务：把这条固定追问说得自然、简洁、像真人旅游助理，只输出 JSON：
{{"question": "..."}}

你不得：
1. 新增事实；2. 修改已知事实；3. 猜测用户没说的信息；4. 改变要问的槽位；
5. 生成、删除或修改选项；6. 决定下一步或路由；7. 修改行程或需求；
8. 执行业务动作；9. 承诺实时价格、库存或天气；10. 输出 JSON 之外的内容。
写法要求：1~2 句、只问一个核心问题、可自然承接用户上一句话，结尾引导用户回答。"""

# 渲染产物硬上限（超长即视为越权/跑偏，落模板）
_QUESTION_MAX_LEN = 160
_QUESTION_MIN_LEN = 4
_FORBIDDEN_MARKERS = ("{", "}", "http://", "https://", "```")


def _metric(source: str = "", reason: str = "", latency_ms: int = 0) -> None:
    """指标软失败。"""
    try:
        from backend.observability.metrics import (
            travel_clarification_fallback_total,
            travel_clarification_latency_seconds,
            travel_clarification_total,
        )

        if source:
            travel_clarification_total.labels(source=source).inc()
        if reason:
            travel_clarification_fallback_total.labels(reason=reason).inc()
        if latency_ms:
            travel_clarification_latency_seconds.observe(latency_ms / 1000.0)
    except Exception:  # noqa: BLE001
        pass


def _prompt(known_facts: str, ask_slot: str, question: str) -> tuple[str, str]:
    """注册表优先渲染 prompt（变量当场填充），异常降级模块常量。

    返回 (渲染后的系统提示词, 版本标签)。双源漂移由
    tests/prompts/test_bare_prompt_collection.py 逐字锁定。
    """
    try:
        from backend.prompts.service import prompt_service

        rendered = prompt_service.render_sync(
            "travel.clarification_renderer",
            known_facts=known_facts,
            ask_slot=ask_slot,
            slot_question=question,
        )
        version = getattr(rendered, "version", None)
        return (rendered.text,
                f"travel.clarification_renderer@{version}" if version else "")
    except Exception as exc:  # noqa: BLE001 — prompt 读取失败回落常量
        logger.warning(f"[TravelClarifyLLM] 注册表渲染失败，降级内置常量: {exc}")
        return (
            _SYSTEM_PROMPT.format(
                known_facts=known_facts, ask_slot=ask_slot,
                slot_question=question),
            "",
        )


def _validate_question(raw: str) -> tuple[str, str]:
    """校验 LLM question → (合法文本, 失败原因)。"""
    text = (raw or "").strip().strip('"')
    if not text:
        return "", "empty"
    if len(text) > _QUESTION_MAX_LEN:
        return "", "too_long"
    if any(marker in text for marker in _FORBIDDEN_MARKERS):
        return "", "forbidden_content"
    if "\n" in text:
        # 多行=多半在输出解释/列表，越权落模板
        return "", "forbidden_content"
    if len(text) < _QUESTION_MIN_LEN:
        return "", "too_long"
    return text, ""


def _llm_question(
    plan, user_message: str,
) -> tuple[str, str, int, str]:
    """调一次 LLM 渲染。返回 (question, prompt_version, latency_ms, fallback_reason)。"""
    from backend.config.travel import TRAVEL_LLM_CLARIFICATION_TIMEOUT_MS

    import time

    started = time.monotonic()

    def _fail(reason: str) -> tuple[str, str, int, str]:
        return "", "", int((time.monotonic() - started) * 1000), reason

    try:
        timeout = TRAVEL_LLM_CLARIFICATION_TIMEOUT_MS / 1000.0

        from langchain_core.messages import HumanMessage, SystemMessage

        from backend.infra.async_utils import sync_call_with_timeout
        from backend.infra.llm.proxy import _build_llm_for, record_llm_result
        from backend.config import model_roles
        from backend.observability.llm_context import llm_attribution_scope

        model_name = str((model_roles.resolve_effective("main") or {})
                         .get("value") or "")
        if not model_name:
            return _fail("no_model")
        clarify_llm = _build_llm_for(model_name).bind(
            temperature=0, max_tokens=128)
        known = json.dumps(plan.known_facts, ensure_ascii=False) or "{}"
        ask_slot = plan.ask_slots[0] if plan.ask_slots else ""
        from backend.travel.services.clarification_service import slot_question

        system_text, prompt_version = _prompt(
            known, ask_slot, slot_question(ask_slot))
        user_text = f"用户上一句话：{(user_message or '')[:120]}\n生成追问。"
        with llm_attribution_scope(agent_domain="travel"):
            response = sync_call_with_timeout(
                clarify_llm.invoke, timeout,
                [SystemMessage(content=system_text),
                 HumanMessage(content=user_text)],
            )
            # 直构实例不经 _LLMProxy —— 补一次统一计量
            record_llm_result(
                response,
                duration_ms=(time.monotonic() - started) * 1000,
                model_name=model_name,
            )
        latency_ms = int((time.monotonic() - started) * 1000)
        raw = str(getattr(response, "content", "") or "")
        from backend.shared.json_extractor import extract_json

        payload = extract_json(raw, source="travel.clarification_renderer")
        if not isinstance(payload, dict) or "question" not in payload:
            return "", prompt_version, latency_ms, "invalid_json"
        question, reason = _validate_question(str(payload.get("question") or ""))
        if reason:
            return "", prompt_version, latency_ms, reason
        return question, prompt_version, latency_ms, ""
    except Exception as exc:  # noqa: BLE001 — 渲染失败立即模板接管
        logger.info("[TravelClarifyLLM] LLM 渲染失败（落模板）: %s: %s",
                    type(exc).__name__, str(exc)[:120])
        return _fail("llm_error")


def render_clarification(plan, user_message: str = "") -> tuple[str, dict]:
    """追问渲染唯一入口。返回 (最终文案, meta)。

    meta 契约（进 state/trace，全部标量）：source=llm|template、
    prompt_version、latency_ms、fallback_reason、slot。
    """
    from backend.config.travel import TRAVEL_LLM_CLARIFICATION_ENABLED
    from backend.travel.services.clarification_service import render_template

    ask_slot = plan.ask_slots[0] if plan.ask_slots else ""
    meta = {
        "source": "template",
        "prompt_version": "",
        "latency_ms": 0,
        "fallback_reason": "",
        "slot": ask_slot,
    }
    if not TRAVEL_LLM_CLARIFICATION_ENABLED:
        meta["fallback_reason"] = "disabled"
        _metric(source="template", reason="disabled")
        return render_template(plan), meta

    question, prompt_version, latency_ms, reason = _llm_question(
        plan, user_message)
    meta["prompt_version"] = prompt_version
    meta["latency_ms"] = latency_ms
    if question:
        meta["source"] = "llm"
        _metric(source="llm", latency_ms=latency_ms)
        body = question
        # 确定性背景行（数据源说明/不支持城市/推荐）不交给 LLM，恒拼在尾部
        for note in plan.context_notes:
            body += f"\n\n{note}"
        return body, meta
    # render_template 自带 context_notes 拼接，此处不再重复追加
    meta["fallback_reason"] = reason or "llm_error"
    _metric(source="template", reason=meta["fallback_reason"],
            latency_ms=latency_ms)
    return render_template(plan), meta
