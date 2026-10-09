"""semantic.py — CS LLM 语义理解层编排入口（任务书 §五 Understanding Layer）。

Rule First（任务书 §七）：
  规则明确（intent 非 unknown 且 confidence ≥ 闸门）→ 意图补判 LLM = 0 次；
  仅 rule miss / 低置信 / 意图需要业务对象且无显式实体时才调用 LLM。

接线点：orchestration/graph/routing/cs_understanding.py::_enrich_with_understanding
在规则理解层（build_understanding，零 LLM）之后调用本模块；锁域/全局命中/
兜底三条 CS 入口全覆盖。LLM 产出的一切 candidate 经 validator 白名单后才
允许写入 cs_route.metadata —— LLM 本身不触碰 CSGraphState（P0-03）。

语义槽位（metadata.semantic_slots）的真实对象解析在专家层完成
（context/semantic_slots.py，用真实 user_id 查业务服务），router 层零
新增 IO。
"""
from __future__ import annotations

from backend.customer_service.understanding.contracts import (
    CSUnderstandingResult,
    CSUnderstandingSource,
)
from backend.customer_service.understanding.llm_intent_fallback import (
    llm_intent_candidate,
    rule_intent_is_confident,
)
from backend.customer_service.understanding.llm_runtime import (
    mask_for_llm,
    tag_trace,
)
from backend.customer_service.understanding.llm_slot_enrichment import (
    intent_wants_slots,
    llm_slot_candidates,
)
from backend.customer_service.understanding.task_plan import build_rule_task_plan
from backend.customer_service.understanding.validator import validate_task_plan_candidate
from backend.shared.logger import logger

# semantic_slots 在 metadata 中的键名（专家层消费方：experts/action.py、
# experts/query.py、context/semantic_slots.py）
SEMANTIC_SLOTS_KEY = "semantic_slots"


def _metrics_inc(counter_name: str, status: str) -> None:
    try:
        from backend.observability import metrics as m

        getattr(m, counter_name).labels(status=status).inc()
    except Exception:  # noqa: BLE001 — 观测旁路软失败
        pass


def _adopt_intent(cs_route: dict, intent: str) -> None:
    """LLM 意图候选采纳：按 INTENT_PROFILES 画像派生全部路由字段。

    置信度取固定保守值（CS_LLM_INTENT_CONFIDENCE，默认 0.65 > 闸门 0.6），
    不采信 LLM 自报分数驱动路由（确定性 Runtime 原则）；原 confidence
    保留在 metadata 供审计。
    """
    from backend.config.customer_service import CS_LLM_INTENT_CONFIDENCE
    from backend.customer_service.router.intents import (
        INTENT_PROFILES,
        kb_ids_for,
        resolve_route_path,
    )

    profile = INTENT_PROFILES.get(intent)
    cs_route["intent"] = intent
    cs_route["confidence"] = CS_LLM_INTENT_CONFIDENCE
    if profile is not None:
        cs_route["requires_auth"] = profile.requires_auth
        cs_route["requires_action"] = profile.requires_action
        cs_route["risk_level"] = profile.risk_level
        cs_route["route_path"] = resolve_route_path(intent)
        cs_route["kb_ids"] = kb_ids_for(intent, profile.domain)
    metadata = cs_route.setdefault("metadata", {})
    metadata["intent_source"] = "llm_fallback"


def enrich_semantic_understanding(cs_route: dict, query: str) -> CSUnderstandingResult:
    """单轮 LLM 语义增强（在规则理解层之后调用）。

    就地更新 cs_route（intent 补判采纳 / semantic_slots 注入 metadata），
    返回理解结果契约供调用方落 trace。任何失败软降级：cs_route 保持
    规则层原样，绝不阻塞路由（P0-16/17：timeout/非法 JSON 业务不断）。
    """
    from backend.config.customer_service import (
        CS_SLOT_LLM_ENABLED,
        CS_UNDERSTANDING_LLM_ENABLED,
    )

    intent = str(cs_route.get("intent", "unknown"))
    confidence = float(cs_route.get("confidence", 0.0) or 0.0)
    metadata = cs_route.setdefault("metadata", {})

    # 既有显式实体（规则层产出）——有显式订单号 = Rule First，槽位 LLM 0 次
    has_explicit_order = bool(
        metadata.get("order_id")
        or any(e.get("type") == "order_id" for e in (metadata.get("entities") or []))
    )

    result = CSUnderstandingResult(source=CSUnderstandingSource.UNCHANGED)

    # 该小型复合场景由有限规则生成候选计划，执行仍受 Supervisor 条件门
    # 与 ActionExpert 双重校验；不额外调用 LLM 或查询业务数据。
    plan_candidate = validate_task_plan_candidate(
        build_rule_task_plan(query),
    )
    if plan_candidate is not None:
        metadata["task_plan_candidate"] = plan_candidate
        metadata["task_plan_source"] = "rule_candidate"
        result.task_plan_candidate = plan_candidate
        result.task_plan_source = "rule_candidate"

    if not CS_UNDERSTANDING_LLM_ENABLED:
        _metrics_inc("cs_understanding_llm_total", "disabled")
        if plan_candidate is not None:
            result.source = CSUnderstandingSource.RULE
            tag_trace(**result.to_trace_fields())
        return result

    # ── 1. 意图补判（rule miss / 低置信才触发）────────────────────
    intent_adopted = False
    if not rule_intent_is_confident(intent, confidence):
        masked = mask_for_llm(query)
        candidate = llm_intent_candidate(masked) if masked else None
        if candidate is not None:
            intent_llm, llm_self_conf = candidate
            _adopt_intent(cs_route, intent_llm)
            intent = intent_llm
            intent_adopted = True
            result.intent_candidate = intent_llm
            result.confidence = llm_self_conf
            _metrics_inc("cs_understanding_llm_total", "accepted")
            logger.info(
                "[CS Semantic] intent fallback adopted: %s (llm_self_conf=%.2f)",
                intent_llm, llm_self_conf,
            )
        else:
            _metrics_inc("cs_understanding_llm_total", "no_candidate")
    else:
        _metrics_inc("cs_understanding_llm_total", "rule_skipped")

    # ── 2. 语义槽位补全（意图需要业务对象且无显式订单号才触发）────
    # 槽位段独立 try：槽位失败不连累已采纳的 intent 补判结果
    if (
        CS_SLOT_LLM_ENABLED
        and intent_wants_slots(intent)
        and not has_explicit_order
    ):
        try:
            masked = mask_for_llm(query)
            if masked:
                slots, cand_count, rejected, reason = llm_slot_candidates(masked, intent)
                if slots:
                    metadata[SEMANTIC_SLOTS_KEY] = [s.model_dump() for s in slots]
                    result.slots = slots
                    result.source = (
                        CSUnderstandingSource.LLM_FALLBACK if intent_adopted
                        else CSUnderstandingSource.RULE_LLM
                    )
                    _metrics_inc("cs_slot_llm_total", "accepted")
                elif cand_count:
                    _metrics_inc("cs_slot_llm_total", "rejected")
                    _metrics_inc("cs_slot_llm_fallback_total", reason or "schema_fail")
                else:
                    _metrics_inc("cs_slot_llm_total", "no_candidate")
                    _metrics_inc("cs_slot_llm_fallback_total", reason or "empty")
                tag_trace(
                    cs_llm_slot_candidate_count=cand_count,
                    cs_llm_slot_accepted_count=len(slots),
                    cs_llm_slot_rejected_count=rejected,
                    cs_llm_slot_fallback_reason=reason,
                )
        except Exception as exc:  # noqa: BLE001 — 槽位增强失败软降级
            logger.warning("[CS Semantic] slot enrichment 软降级: %s", exc)
            _metrics_inc("cs_slot_llm_total", "no_candidate")
            _metrics_inc("cs_slot_llm_fallback_total", "llm_error")

    # ── 3. 汇总来源（trace cs_understanding_source）───────────────
    if result.source is CSUnderstandingSource.UNCHANGED:
        result.source = (
            CSUnderstandingSource.LLM_FALLBACK if intent_adopted
            else CSUnderstandingSource.RULE
        )
    tag_trace(**result.to_trace_fields())
    return result


def semantic_layer_enabled() -> bool:
    """语义层总闸（接线方短路用；关闭 = 行为与改造前一致）。"""
    from backend.config.customer_service import CS_UNDERSTANDING_LLM_ENABLED

    return CS_UNDERSTANDING_LLM_ENABLED
