"""travel/services/llm_slot_enrichment_service.py — 槽位语义 LLM 富化（STOP 1）

Rule First 的受限 LLM 兜底层：规则抽取（requirement_agent 纯正则词典）
**之后**，仅对「合并后仍缺失的 required 槽」让 LLM 提结构化候选。与
llm_intent_service 同一三条铁律的槽位版：

  1. **只在规则盲区触发**：合并 brief 的 missing_slots 为空时调用次数 = 0
     （P0-01）；非规划轨意图（问答/寒暄/出域/改单）不触发；
  2. **输出是候选不是状态**：LLM 只产白名单槽位的 SlotCandidate，经
     Schema Validate + 值域校验后由 apply_candidates() 确定性合并 ——
     高置信规则值永不被覆盖（explicit_rule > llm_candidate），LLM 在
     结构上接触不到 TravelGraphState / 路由 / 行程（P0-03/04/05/08）；
  3. **失败回落规则**：超时/非法 JSON/schema 不符/置信不足 → 返回空结果
     → 正常走 missing_slots + 模板追问，绝不阻断主链（P0-06/07）。

成本纪律：单轮 ≤1 次调用、禁自动重试、main 角色（低成本）、温度 0；
计量走 record_llm_result 补账（绕过 _LLMProxy 的直构出口，与
rag/preprocessing/llm_enrichment.py 同口径），归因 agent_domain=travel。

白名单（首期，审计决策 #1）：destination / days / party_size / pace /
preferences。**刻意不开放 start_date**（模糊时间表达已有透明 note 链路，
LLM 定日 = 幻觉源）与预算/必去/避雷等高风险槽。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from backend.shared.logger import logger

# 模块常量与 defaults YAML（travel_slot_enrichment.yaml）逐字一致，
# 漂移守卫见 tests/prompts/test_bare_prompt_collection.py。
_SYSTEM_PROMPT = """你是旅游助手的需求理解器。用户消息由规则解析后仍缺少以下槽位：
{missing_slots}
你的唯一任务：从用户原话里提取这些槽位的候选值，输出 JSON：
{{"candidates": [{{"slot": "...", "value": "...", "confidence": 0.0到1.0, "evidence_text": "用户原话依据"}}]}}
规则：
- 只输出 missing_slots 里列出的槽位，禁止输出其他任何字段
- value 取值约束：destination=中文城市名；days=1到15的整数；party_size=1到20的整数；pace=relaxed或moderate或intense；preferences=字符串数组
- 用户没有明确表达该槽位时，宁可漏掉也不要猜（"玩几天"不是具体天数；"月初/月底"不是具体日期）
- 不确定就输出空数组 {{"candidates": []}}
只输出 JSON，不要解释。"""

# 允许 LLM 补全的槽位白名单（任务书 6.2；审计决策 #1）
ALLOWED_SLOTS: frozenset[str] = frozenset({
    "destination", "days", "party_size", "pace", "preferences",
})


class SlotCandidate(BaseModel):
    """单个槽位候选（schema validate 的第一道门）。"""

    slot: str
    value: str | int | float | list[str] | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_text: str = ""


class LLMSlotEnrichmentResult(BaseModel):
    """富化输出契约：只含白名单内、schema 合法的候选。"""

    candidates: list[SlotCandidate] = Field(default_factory=list)


@dataclass
class SlotEnrichmentOutcome:
    """一次富化的完整结局（含 trace/meta 所需全部字段）。"""

    # LLM 返回且通过 Schema+值域校验的候选（待 apply_candidates 确定性合并）
    candidates: list[SlotCandidate] = field(default_factory=list)
    # 最终被确定性合并接受的候选（由调用方经 apply_candidates 回填）
    accepted: list[SlotCandidate] = field(default_factory=list)
    # LLM 返回且 schema 合法、但因值域/置信/白名单被拒的数量
    rejected: int = 0
    # 调用状态：ok（LLM 正常返回）/ empty / schema_invalid / error / disabled
    status: str = "disabled"
    # 未采用 LLM 结果的原因（"" = 采用了）
    fallback_reason: str = ""
    model: str = ""
    prompt_version: str = ""
    latency_ms: int = 0

    @property
    def used(self) -> bool:
        """LLM 是否实际贡献了槽位（slot_parse_source=rule+llm 的判据）。"""
        return bool(self.accepted)

    def meta(self) -> dict:
        """trace/metadata 投影（全部标量，禁敏感原文）。"""
        return {
            "used": self.used,
            "status": self.status,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "latency_ms": self.latency_ms,
            "candidate_count": len(self.candidates),
            "accepted_count": len(self.accepted),
            "rejected_count": self.rejected,
            "fallback_reason": self.fallback_reason,
        }


def _metric(status: str = "", reason: str = "", latency_ms: int = 0) -> None:
    """指标软失败（埋点异常绝不反噬主链）。"""
    try:
        from backend.observability.metrics import (
            travel_slot_llm_fallback_total,
            travel_slot_llm_latency_seconds,
            travel_slot_llm_total,
        )

        if status:
            travel_slot_llm_total.labels(status=status).inc()
        if reason:
            travel_slot_llm_fallback_total.labels(reason=reason).inc()
        if latency_ms:
            travel_slot_llm_latency_seconds.observe(latency_ms / 1000.0)
    except Exception:  # noqa: BLE001
        pass


def _prompt(missing_line: str) -> tuple[str, str]:
    """注册表优先渲染 prompt（变量当场填充），异常降级模块常量。

    返回 (渲染后的系统提示词, 版本标签)。注册表与常量是同一模板的双源，
    漂移由 tests/prompts/test_bare_prompt_collection.py 逐字锁定。
    """
    try:
        from backend.prompts.service import prompt_service

        rendered = prompt_service.render_sync(
            "travel.slot_enrichment", missing_slots=missing_line)
        version = getattr(rendered, "version", None)
        return (rendered.text,
                f"travel.slot_enrichment@{version}" if version else "")
    except Exception as exc:  # noqa: BLE001 — prompt 读取失败回落常量
        logger.warning(f"[TravelSlotLLM] 注册表渲染失败，降级内置常量: {exc}")
        return _SYSTEM_PROMPT.format(missing_slots=missing_line), ""


def _validate_destination(value) -> str:
    """destination 候选值域校验：必须命中城市名录（与 POI 数据键对齐）。"""
    if not isinstance(value, str):
        return ""
    name = value.strip()
    if not 2 <= len(name) <= 12:
        return ""
    from backend.travel.services.requirement_service import KNOWN_MAJOR_CITIES
    from backend.tools.travel import poi_seed
    from backend.travel.data import cities as city_directory

    if name in city_directory.all_directory_cities() or name in poi_seed.all_cities() or name in KNOWN_MAJOR_CITIES:
        return name
    return ""


def _validate_days(value) -> int | None:
    from backend.config.travel import TRAVEL_MAX_DAYS

    if isinstance(value, bool):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if not isinstance(value, int):
        return None
    return value if 1 <= value <= TRAVEL_MAX_DAYS else None


def _validate_party_size(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if not isinstance(value, int):
        return None
    return value if 1 <= value <= 20 else None


def _validate_pace(value) -> str | None:
    from backend.travel.models.brief import PACE_LEVELS

    return value if isinstance(value, str) and value in PACE_LEVELS else None


def _validate_preferences(value) -> list[str]:
    from backend.travel.models.brief import PREFERENCE_KEYWORDS

    if not isinstance(value, list):
        return []
    valid = [
        v for v in value
        if isinstance(v, str) and v in PREFERENCE_KEYWORDS
    ]
    return valid[:5]


_VALIDATORS = {
    "destination": _validate_destination,
    "days": _validate_days,
    "party_size": _validate_party_size,
    "pace": _validate_pace,
    "preferences": _validate_preferences,
}


def parse_candidates(payload) -> tuple[list[SlotCandidate], int]:
    """LLM 原始 JSON → (合法候选, 拒绝数)。

    拒绝维度：白名单外槽位（越权字段在结构上丢弃，P0-08/任务 Golden K）、
    值域校验失败、置信度低于门槛。
    """
    from backend.config.travel import TRAVEL_LLM_SLOT_MIN_CONFIDENCE

    if not isinstance(payload, dict):
        return [], 0
    raw = payload.get("candidates")
    if not isinstance(raw, list):
        return [], 0
    valid: list[SlotCandidate] = []
    rejected = 0
    for item in raw:
        if not isinstance(item, dict):
            rejected += 1
            continue
        try:
            candidate = SlotCandidate.model_validate(item)
        except Exception:  # noqa: BLE001 — schema 不符按拒绝计
            rejected += 1
            continue
        if candidate.slot not in ALLOWED_SLOTS:
            rejected += 1
            continue
        if candidate.confidence < TRAVEL_LLM_SLOT_MIN_CONFIDENCE:
            rejected += 1
            continue
        validator = _VALIDATORS.get(candidate.slot)
        value = validator(candidate.value)
        if value is None or value == "" or value == []:
            rejected += 1
            continue
        valid.append(candidate.model_copy(update={"value": value}))
    return valid, rejected


def apply_candidates(fresh, candidates: list[SlotCandidate]) -> tuple[object, list[str]]:
    """确定性合并：**只填空槽，永不覆盖规则值**（P0-03，审计决策 #4）。

    返回 (新 fresh 副本, 接受的槽位名列表)。纯函数：不改传入对象、
    不碰 state/路由/行程（LLM 在结构上无法影响任何业务状态）。
    合并优先级：explicit_rule > llm_candidate > previous_context > default
    —— 前两者由「只填空槽」保证，后两者由既有 merge_brief 语义保证。
    """
    filled: dict = {}
    for candidate in candidates:
        slot = candidate.slot
        if slot in filled:
            continue
        current = getattr(fresh, slot, None)
        if slot == "party_size":
            # fresh.party_size 缺省 1 表示「未表达」；显式 1 不可与缺省区分
            # 时按可覆盖处理（规则显式表达过会有 adults/party 来源链），
            # 但**绝不覆盖显式总数/成人儿童派生**（非 1 即规则结果）。
            if current is not None and current != 1:
                continue
        elif slot in ("destination", "pace"):
            if current:
                continue
        elif slot == "preferences":
            if current:
                continue
        elif current is not None:
            continue
        filled[slot] = candidate.value
    if not filled:
        return fresh, []
    updated = fresh.model_copy(update=filled)
    return updated, list(filled)


def enrich_slots(
    message: str,
    *,
    missing_slots: list[str],
    timeout_ms: int | None = None,
) -> SlotEnrichmentOutcome:
    """规则盲区的槽位候选补全（单轮 ≤1 次，任何失败返回空结局不抛错）。

    调用方（slot_filler）负责触发门禁（规划轨 + missing 非空）；本函数
    只负责「调一次、验一次、返回结局」。
    """
    from backend.config.travel import (
        TRAVEL_LLM_SLOT_ENRICHMENT_ENABLED,
        TRAVEL_LLM_SLOT_TIMEOUT_MS,
    )

    outcome = SlotEnrichmentOutcome()
    open_slots = [s for s in (missing_slots or []) if s in ALLOWED_SLOTS]
    if not TRAVEL_LLM_SLOT_ENRICHMENT_ENABLED:
        outcome.status = "disabled"
        outcome.fallback_reason = "disabled"
        _metric(status="disabled")
        return outcome
    if not (message or "").strip() or not open_slots:
        outcome.status = "empty"
        outcome.fallback_reason = "no_candidates"
        _metric(status="empty")
        return outcome

    import time

    started = time.monotonic()
    try:
        prompt_text, prompt_version = _prompt("、".join(open_slots))
        outcome.prompt_version = prompt_version
        timeout = (timeout_ms or TRAVEL_LLM_SLOT_TIMEOUT_MS) / 1000.0

        from langchain_core.messages import HumanMessage, SystemMessage

        from backend.infra.async_utils import sync_call_with_timeout
        from backend.infra.llm.proxy import _build_llm_for, record_llm_result
        from backend.observability.llm_context import llm_attribution_scope

        # 构建出口与 llm_intent_service 同款：main 角色 DB 绑定名直构
        # （_build_llm_for 是唯一带 DB 托管凭据解析的构建出口，P1a-2）。
        from backend.config import model_roles

        model_name = str((model_roles.resolve_effective("main") or {})
                         .get("value") or "")
        outcome.model = model_name
        if not model_name:
            outcome.status = "error"
            outcome.fallback_reason = "no_model"
            _metric(status="error", reason="no_model")
            return outcome
        slot_llm = _build_llm_for(model_name).bind(temperature=0, max_tokens=384)
        with llm_attribution_scope(agent_domain="travel"):
            response = sync_call_with_timeout(
                slot_llm.invoke, timeout,
                [SystemMessage(content=prompt_text),
                 HumanMessage(content=f"用户消息：{message}")],
            )
            # 直构实例不经 _LLMProxy —— 补一次统一计量（llm_usage/计费唯一口径）
            record_llm_result(
                response,
                duration_ms=(time.monotonic() - started) * 1000,
                model_name=model_name,
            )
        outcome.latency_ms = int((time.monotonic() - started) * 1000)
        raw = str(getattr(response, "content", "") or "")
        from backend.shared.json_extractor import extract_json

        payload = extract_json(raw, source="travel.slot_enrichment")
        if payload is None:
            outcome.status = "schema_invalid"
            outcome.fallback_reason = "invalid_json"
            _metric(status="schema_invalid", reason="invalid_json",
                    latency_ms=outcome.latency_ms)
            return outcome
        candidates, rejected = parse_candidates(payload)
        outcome.rejected = rejected
        outcome.candidates = candidates
        if not candidates:
            outcome.status = "empty"
            outcome.fallback_reason = "no_candidates"
            _metric(status="empty", reason="no_candidates",
                    latency_ms=outcome.latency_ms)
            return outcome
        outcome.status = "ok"
        return outcome
    except Exception as exc:  # noqa: BLE001 — 富化失败必须回落规则链路
        outcome.status = "error"
        outcome.fallback_reason = "llm_error"
        outcome.latency_ms = int((time.monotonic() - started) * 1000)
        _metric(status="error", reason="llm_error", latency_ms=outcome.latency_ms)
        logger.info("[TravelSlotLLM] LLM 富化失败（回落规则）: %s: %s",
                    type(exc).__name__, str(exc)[:120])
        return outcome
