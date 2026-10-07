"""travel/services/clarification_service.py — 追问计划层（2026-10-08 STOP 3）

确定性唯一事实源：**问什么、问哪个槽、选项是什么、点完去哪**全部由本模块
的纯规则决定，LLM（clarification_renderer）只允许把「已确定的追问意图」
说得自然，无权修改本层的任何决策。

职责边界（冻结）：
  - ClarificationPlan / ClarificationOption 契约（Pydantic，可序列化进 state）；
  - build_clarification_plan()：missing_slots → 一次只问优先级最高的一个槽
    （P0-11）+ 规则生成的可路由点击选项（P0-12）+ 模板渲染所需的确定性
    背景行（数据源说明/不支持城市/推荐行）；
  - render_template()：模板渲染（LLM Renderer 失败时的唯一兜底，也是
    TRAVEL_LLM_CLARIFICATION_ENABLED=false 时的唯一渲染通道）。

不做：槽位判定（missing_slots 归 brief）、槽位抽取（归 requirement_agent）、
LLM 调用（归 clarification_renderer）、图路由（归 supervisor）。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from backend.travel.models.brief import (
    PREFERENCE_KEYWORDS,
    SLOT_QUESTIONS,
    TravelBrief,
)

# 追问优先级（任务书 八）：missing 含多个时一次只问第一个；用户补齐后
# 下一轮再问下一个。只收 required 槽；顺序显式声明，禁止散落多个 if。
CLARIFICATION_PRIORITY: tuple[str, ...] = ("destination", "days")


class ClarificationOption(BaseModel):
    """点击选项（规则生成；前端点击即原样重发 message）。

    message 必须能重新进入旅游域并被 RequirementAgent 正确解析（可路由性
    由单测锁定）；value/slot 是语义字段供 Plan 消费方溯源，不进前端契约。
    """

    label: str
    message: str
    slot: str | None = None
    value: int | str | None = None


class ClarificationPlan(BaseModel):
    """一轮追问的完整计划（规则产物，LLM 无权修改）。

    context_notes 是模板渲染追加的确定性背景行（数据源说明/不支持城市/
    推荐行），LLM Renderer 也会原样拼接它们 —— 背景事实不交给 LLM 生成。
    """

    reason: str
    missing_slots: list[str]
    ask_slots: list[str]
    known_facts: dict[str, Any] = Field(default_factory=dict)
    options: list[ClarificationOption] = Field(default_factory=list)
    allow_free_text: bool = True
    context_notes: list[str] = Field(default_factory=list)


def _ask_slot(missing: list[str]) -> str | None:
    """missing ∩ 优先级 的首个槽位（一次只问一个）。"""
    for slot in CLARIFICATION_PRIORITY:
        if slot in missing:
            return slot
    # 防御：missing 出现优先级表之外的 required 新槽时按 missing 首位兜底，
    # 不静默丢问题（新槽进入 REQUIRED_SLOTS 时应同步扩优先级表）。
    return missing[0] if missing else None


def _destination_notes(brief: TravelBrief, unsupported_city: str) -> list[str]:
    """destination 追问的确定性背景行（原 build_clarification 既有口径）。"""
    from backend.config.travel import TRAVEL_POI_SOURCE

    notes: list[str] = []
    if TRAVEL_POI_SOURCE == "seed":
        from backend.tools.travel import poi_seed

        cities_line = "、".join(poi_seed.all_cities())
        if unsupported_city:
            notes.append(
                f"你提到的「{unsupported_city}」暂时无法规划（还没有当地的地点数据），"
                f"当前可规划的城市：{cities_line}"
            )
        else:
            notes.append(f"（当前可规划的城市：{cities_line}）")
    else:
        if unsupported_city:
            notes.append(
                f"你提到的「{unsupported_city}」暂时无法规划（暂不支持境外及"
                "该目的地），境内主要城市都可以试。"
            )
        else:
            notes.append("（全国主要城市均可规划，地点信息来自实时地图检索）")
    try:
        from backend.travel.recommend import (
            recommend_cities,
            render_recommendation_line,
        )

        rec_line = render_recommendation_line(recommend_cities(brief.preferences))
        if rec_line:
            notes.append(f"{rec_line}，回复城市名即可开始规划。")
    except Exception:  # noqa: BLE001 — 推荐是增强项，失败不影响追问
        pass
    return notes


def _destination_options() -> list[ClarificationOption]:
    """目的地点击选项：热门城市 chips，点击即重发「规划X的行程」。

    可路由性：`规划` 命中 _RE_PLAN_VERB，城市名可被 extract_destination
    识别 → 重发后必回旅游域并填 destination（test_clarification_plan 锁定）。
    """
    try:
        from backend.travel.recommend import recommend_cities

        recs = recommend_cities([], top=3)
    except Exception:  # noqa: BLE001 — 选项是增强项，失败留空仍可自由输入
        return []
    return [
        ClarificationOption(
            label=f"规划{rec.city}",
            message=f"规划{rec.city}的行程",
            slot="destination",
            value=rec.city,
        )
        for rec in recs
    ]


def _days_options(destination: str) -> list[ClarificationOption]:
    """天数点击选项：快捷接受 3 天 + 自由输入（存量前端契约 days 字段）。

    「规划{dest}3天行程」重发后经规则抽取 days=3（既有验收路径）；
    message 为空的「自己填天数」由前端聚焦输入框（days=null 语义）。
    """
    dest = destination or ""
    return [
        ClarificationOption(
            label="按 3 天参考规划",
            message=f"规划{dest}3天行程",
            slot="days",
            value=3,
        ),
        ClarificationOption(
            label="自己填天数",
            message="",
            slot="days",
            value=None,
        ),
    ]


def build_clarification_plan(
    brief: TravelBrief,
    user_message: str = "",
    *,
    unsupported_city: str | None = None,
) -> ClarificationPlan | None:
    """构建追问计划；无缺失槽位返回 None。

    unsupported_city 由调用方传入（requirement_agent 抽取层的判定结果），
    本模块不做抽取 —— 计划层保持零业务抽取依赖，避免与 agents 层成环。
    """
    missing = brief.missing_slots()
    if not missing:
        return None
    ask = _ask_slot(missing)
    known_facts: dict[str, Any] = {}
    if brief.destination:
        known_facts["destination"] = brief.destination
    if brief.days is not None:
        known_facts["days"] = brief.days
    if brief.party_size:
        known_facts["party_size"] = brief.party_size
    if brief.pace:
        known_facts["pace"] = brief.pace
    if brief.preferences:
        known_facts["preferences"] = list(brief.preferences)

    options: list[ClarificationOption] = []
    notes: list[str] = []
    if ask == "destination":
        options = _destination_options()
        notes = _destination_notes(
            brief,
            unsupported_city if unsupported_city is not None else "",
        )
    elif ask == "days":
        options = _days_options(brief.destination)
    return ClarificationPlan(
        reason="必填槽位缺失，需要用户补充",
        missing_slots=list(missing),
        ask_slots=[ask],
        known_facts=known_facts,
        options=options,
        allow_free_text=True,
        context_notes=notes,
    )


def slot_question(slot: str) -> str:
    """槽位 → 追问话术（SLOT_QUESTIONS 单一事实源）。"""
    return SLOT_QUESTIONS.get(slot, slot)


def preferences_summary(brief: TravelBrief) -> str:
    """已知偏好 → 人类可读短语（Renderer 上下文用，确定性拼接）。"""
    valid = [p for p in (brief.preferences or []) if p in PREFERENCE_KEYWORDS]
    return "、".join(valid) if valid else ""


def render_template(plan: ClarificationPlan) -> str:
    """模板渲染（兜底与关闭态的唯一渲染通道；确定性、零 LLM）。

    版式与收编前 build_clarification 一致，只是按 P0-11 一次只列
    ask_slots 里的一个问题。
    """
    questions = [slot_question(s) for s in plan.ask_slots]
    lines = ["为了把行程排准，还需要确认："]
    lines += [f"{i}. {q}" for i, q in enumerate(questions, 1)]
    for note in plan.context_notes:
        lines.append(f"\n{note}")
    return "\n".join(lines)


def frontend_options(plan: ClarificationPlan) -> list[dict]:
    """Plan.options → 前端 clarification_options 契约（存量格式）。

    days 选项保留 `days` 数值键（前端 days===null 聚焦输入框的既有语义）；
    其余槽位选项只给 label/message（点击重发），不带多余键 —— 存量测试
    （test_missing_days_options_require_explicit_acceptance）锁定逐字段相等。
    """
    result: list[dict] = []
    for opt in plan.options:
        if opt.slot == "days":
            result.append(
                {"label": opt.label, "days": opt.value, "message": opt.message})
        else:
            result.append({"label": opt.label, "message": opt.message})
    return result
