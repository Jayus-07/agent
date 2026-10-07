"""composer.py — CS Response Composer（任务书 §十七~§二十三）。

定位：Expert 结构化结果 + 模板初稿 → 按 Expert 策略生成自然回复：
  knowledge  → 直通（RAG 链已是 LLM 组织 + 证据门禁，不二次烧模型）
  query      → LLM 转写（Tool 事实 → 自然语言，禁止改事实/新增事实）
  complaint  → LLM 安抚（政策红线：不承诺赔偿/退款/时效，OutputGuard 后置兜底）
  action     → 模板直出（确认卡/成功/失败/幂等/冲突是高风险话术，禁 LLM 改写）
  handoff    → 模板直出（固定话术无需 LLM）

降级铁律（任务书 §二十二/§二十三）：LLM timeout/provider error/非法
JSON/guard fail 一律回模板初稿，绝不 500/断流/空回复；一次调用失败立即
fallback，禁止自动多次 retry。
"""
from __future__ import annotations

import re
import time

from backend.customer_service.response.contracts import (
    EXPERT_RESPONSE_POLICY,
    CSRenderedResponse,
    ResponsePolicy,
)
from backend.shared.logger import logger

# 订单号形态（守卫用，与 validator 同款定义）：含字母的字母数字连字段
# （DEMO-1001/MO-3C052B3A）。纯数字-纯数字（10-10、2026-10-10 日期）不算
# 订单号，避免日期误杀。
from backend.customer_service.understanding.validator import ORDER_ID_LIKE

_ORDER_NO_LIKE = ORDER_ID_LIKE


def _is_order_no(token: str) -> bool:
    return bool(re.search(r"[A-Za-z]", token) and re.search(r"\d", token))


def _tag_trace(**tags: object) -> None:
    try:
        from backend.observability.tracer import trace_collector

        tracer = trace_collector.current()
        if tracer is None:
            return
        for key, value in tags.items():
            if value not in (None, ""):
                tracer.tags[key] = value
    except Exception:  # noqa: BLE001 — 观测旁路
        pass


def _metrics_inc(counter_name: str, labels: dict) -> None:
    try:
        from backend.observability import metrics as m

        getattr(m, counter_name).labels(**labels).inc()
    except Exception:  # noqa: BLE001 — 观测旁路软失败
        pass


def compose_reply(
    expert_name: str,
    draft: str,
    expert_result: dict,
    cs_route: dict,
    state: dict,
) -> tuple[str, str]:
    """生成最终回复。返回 (text, source)；source ∈ template/llm/llm_fallback。

    模板路径零 LLM 零开销；LLM 路径失败软降级回 draft。开关关闭时全部
    走 template（行为与改造前一致）。
    """
    from backend.config.customer_service import CS_RESPONSE_COMPOSER_ENABLED

    policy = EXPERT_RESPONSE_POLICY.get(expert_name, ResponsePolicy.TEMPLATE)

    # knowledge：RAG 上游已是 LLM 生成 + 证据门禁，直通（source=llm）
    if policy is ResponsePolicy.LLM_UPSTREAM:
        _metrics_inc("cs_response_total", {"source": "llm"})
        _tag_trace(cs_response_source="llm")
        return draft, "llm"

    # action / handoff / 未登记 expert / 开关关：模板直出（P0-19）
    if policy is ResponsePolicy.TEMPLATE or not CS_RESPONSE_COMPOSER_ENABLED:
        _metrics_inc("cs_response_total", {"source": "template"})
        _tag_trace(cs_response_source="template")
        return draft, "template"

    if not draft.strip():
        _metrics_inc("cs_response_total", {"source": "template"})
        _tag_trace(cs_response_source="template")
        return draft, "template"

    # query / complaint：LLM 转写（一次调用，失败回模板）
    t0 = time.monotonic()
    outcome = _compose_with_llm(expert_name, draft, expert_result, cs_route, state)
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    if outcome is None:
        _metrics_inc("cs_response_total", {"source": "llm_fallback"})
        _metrics_inc("cs_response_llm_fallback_total", {"reason": "llm_error"})
        _tag_trace(cs_response_source="llm_fallback",
                   cs_response_latency_ms=elapsed_ms)
        return draft, "llm_fallback"

    rendered, prompt_version = outcome

    # P0-18 守卫：输出中的订单号集合 ⊆ 输入基线中的订单号集合
    if not _guard_no_new_facts(rendered.answer, draft, expert_result):
        logger.warning(
            "[CS ResponseComposer] 守卫拦截（输出含未知订单号），回模板: "
            "expert=%s", expert_name,
        )
        _metrics_inc("cs_response_total", {"source": "llm_fallback"})
        _metrics_inc("cs_response_llm_fallback_total", {"reason": "guard_fail"})
        _tag_trace(
            cs_response_source="llm_fallback",
            cs_response_guard_result="rejected_new_facts",
            cs_response_latency_ms=elapsed_ms,
        )
        return draft, "llm_fallback"

    _metrics_inc("cs_response_total", {"source": "llm"})
    _tag_trace(
        cs_response_source="llm",
        cs_response_latency_ms=elapsed_ms,
        cs_response_guard_result="passed",
        cs_response_prompt_version=prompt_version,
    )
    return rendered.answer, "llm"


def _compose_with_llm(
    expert_name: str,
    draft: str,
    expert_result: dict,
    cs_route: dict,
    state: dict,
) -> tuple[CSRenderedResponse, int | None] | None:
    """LLM 转写（一次调用）。任何失败返回 None（调用方回模板）。"""
    import json

    from backend.config.customer_service import CS_RESPONSE_COMPOSER_TIMEOUT_MS
    from backend.customer_service.understanding.llm_runtime import (
        llm_invoke_once,
        mask_for_llm,
    )
    from backend.customer_service.understanding.validator import (
        extract_json_object,
    )

    data = expert_result.get("data") or {}
    facts = {
        "intent": cs_route.get("intent", ""),
        "expert_data": _sanitize_facts(data),
    }
    masked_message = mask_for_llm(str(state.get("user_message", "") or ""))

    try:
        from backend.customer_service.prompting import render_prompt_with_version

        prompt, prompt_version = render_prompt_with_version(
            "customer_service.response_composer",
            expert_type=expert_name,
            user_message=masked_message[:300],
            facts=json.dumps(facts, ensure_ascii=False, default=str)[:1500],
            draft=draft[:1200],
        )
    except Exception as exc:  # noqa: BLE001 — prompt 链故障回模板
        logger.warning("[CS ResponseComposer] prompt 渲染失败: %s", exc)
        return None

    content = llm_invoke_once(prompt, CS_RESPONSE_COMPOSER_TIMEOUT_MS)
    if content is None:
        return None

    # 输出协议：{"answer": "..."}；纯文本输出也接受（防过度格式化脆弱）
    data_out = extract_json_object(content)
    answer = ""
    if data_out is not None and isinstance(data_out.get("answer"), str):
        answer = data_out["answer"].strip()
    elif content and not content.startswith("{"):
        answer = content.strip()

    if not answer:
        return None
    return CSRenderedResponse(answer=answer), prompt_version


def _sanitize_facts(data: dict) -> dict:
    """facts 白名单化：剥离内部审计/状态机/身份字段，只留可展示业务事实。"""
    if not isinstance(data, dict):
        return {}
    blocked = {
        "audit_entry", "pending_action", "confirmation_state",
        "handoff_state", "handling_mode", "_clarify",
        "user_id", "tenant_id",
    }
    return {k: v for k, v in data.items() if k not in blocked}


def _guard_no_new_facts(answer: str, draft: str, expert_result: dict) -> bool:
    """P0-18：LLM 输出不得引入 draft/事实之外的订单号。

    抽取输出与基线（draft + expert_result.data）两侧订单号集合，输出侧
    出现基线没有的订单号 = 新增业务事实 → 拒绝回模板。
    """
    baseline = _collect_order_nos(draft)
    baseline |= _collect_order_nos(str(expert_result.get("data") or {}))
    return _collect_order_nos(answer) <= baseline


def _collect_order_nos(text: str) -> set[str]:
    return {
        m for m in _ORDER_NO_LIKE.findall(text or "") if _is_order_no(m)
    }
