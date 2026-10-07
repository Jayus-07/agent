"""validator.py — LLM 输出白名单校验（任务书 §六/§十/§十五）。

所有 LLM candidate 进入业务层前的唯一闸门：
  - intent：INTENT_PROFILES 白名单（单一事实源动态校验，prompt 不维护第二份表）
  - slots：SLOT_NAME_WHITELIST 键名白名单 + 真实 ID 形态拒绝（双保险）
  - severity：low/medium/high
  - confirm decision：confirm/cancel/unknown

被拒绝的字段一律丢弃并计数（P0-15：只消费 Schema 白名单合法字段，
其他字段全部丢弃），绝不部分采纳非法输出。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from backend.customer_service.understanding.contracts import (
    FORBIDDEN_SLOT_NAMES,
    SLOT_NAME_WHITELIST,
    CSSlotCandidate,
)

# 真实业务 ID 形态（值级拒绝，双保险之二）：
#  - 显式订单号：字母数字混合连字段（DEMO-1001 / MO-3C052B3A，与
#    understanding.entities / context_resolver._EXPLICIT_ORDER 同族语义）
#  - 工单号：HANDOFF-/COMPLAINT-/GD- 前缀形态
#  - UUID / 超长 token
_ORDER_ID_LIKE = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z0-9]{1,12}(?:-[A-Za-z0-9]{2,12})+(?![A-Za-z0-9])"
)
# 公开别名（response/composer 守卫复用同款形态定义，避免两处漂移）
ORDER_ID_LIKE = _ORDER_ID_LIKE
_TICKET_ID_LIKE = re.compile(r"\b(?:HANDOFF|COMPLAINT|GD)-[A-Za-z0-9]{4,}\b", re.IGNORECASE)
_UUID_LIKE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)

_MAX_SLOT_VALUE_LEN = 60


@dataclass
class SlotValidationReport:
    """槽位校验报告（trace cs_llm_slot_* 计数与 fallback_reason 的数据源）。"""
    accepted: list[CSSlotCandidate] = field(default_factory=list)
    rejected_count: int = 0
    reject_reasons: list[str] = field(default_factory=list)

    @property
    def fallback_reason(self) -> str:
        """首个拒绝原因（可观测；无拒绝返回空串）。"""
        return self.reject_reasons[0] if self.reject_reasons else ""


def _looks_like_real_id(value: str) -> bool:
    if len(value) > _MAX_SLOT_VALUE_LEN:
        return True
    if _UUID_LIKE.search(value) or _TICKET_ID_LIKE.search(value):
        return True
    # 含连字段的字母数字混合段（订单号形态）——纯中文语义值不会命中
    return bool(_ORDER_ID_LIKE.search(value) and re.search(r"[A-Za-z]", value)
                and re.search(r"\d", value))


def validate_intent_candidate(
    raw: object,
    valid_intents: frozenset[str] | set[str],
) -> tuple[str, float] | None:
    """LLM 意图候选校验：必须命中 INTENT_PROFILES 白名单。

    返回 (intent, confidence) 或 None（无效输出整体丢弃，不做模糊匹配）。
    """
    if not isinstance(raw, str):
        return None
    intent = raw.strip()
    return (intent, 1.0) if intent in valid_intents else None


def validate_slots(raw_slots: object) -> SlotValidationReport:
    """LLM 槽位候选清洗：键名白名单 + 值形态拒绝 + 空值丢弃。

    raw_slots 期望形态：list[dict{name, value, confidence?, evidence_text?}]；
    非 list / 元素非 dict 按整体无效处理（返回空报告）。
    """
    report = SlotValidationReport()
    if not isinstance(raw_slots, list):
        report.reject_reasons.append("invalid_structure")
        report.rejected_count += 1
        return report

    seen_names: set[str] = set()
    for item in raw_slots:
        if not isinstance(item, dict):
            report.rejected_count += 1
            report.reject_reasons.append("non_dict_entry")
            continue
        name = str(item.get("name", "")).strip()
        value = str(item.get("value", "")).strip()

        if name in FORBIDDEN_SLOT_NAMES:
            report.rejected_count += 1
            report.reject_reasons.append(f"forbidden_name:{name}")
            continue
        if name not in SLOT_NAME_WHITELIST:
            report.rejected_count += 1
            report.reject_reasons.append(f"unknown_name:{name[:24]}")
            continue
        if not value:
            report.rejected_count += 1
            report.reject_reasons.append("empty_value")
            continue
        if _looks_like_real_id(value):
            report.rejected_count += 1
            report.reject_reasons.append(f"real_id_like_value:{name}")
            continue
        if name in seen_names:
            # 同名槽位只保留首个（LLM 重复产出不放大）
            report.rejected_count += 1
            report.reject_reasons.append(f"duplicate_name:{name}")
            continue

        seen_names.add(name)
        try:
            confidence = float(item.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        report.accepted.append(CSSlotCandidate(
            name=name,
            value=value,
            confidence=max(0.0, min(confidence, 1.0)),
            evidence_text=str(item.get("evidence_text", ""))[:120],
        ))
    return report


def validate_severity(raw: object) -> str | None:
    """投诉严重度候选校验（任务书 §十五：LLM candidate → validator → rule policy）。"""
    if not isinstance(raw, str):
        return None
    sev = raw.strip().lower()
    return sev if sev in ("low", "medium", "high") else None


def validate_confirm_decision(raw: object) -> str | None:
    """确认/取消语义决策校验（任务书 §十三：LLM 只能输出三值白名单）。"""
    if not isinstance(raw, str):
        return None
    decision = raw.strip().lower()
    return decision if decision in ("confirm", "cancel", "unknown") else None


def extract_json_object(content: str) -> dict | None:
    """LLM 返回文本 → JSON object（容 ```json 包裹）；无效返回 None。"""
    import json

    text = (content or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None
