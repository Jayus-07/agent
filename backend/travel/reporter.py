"""travel/reporter.py — 行程单生成器

域图内部的最终输出节点。只做「把结构化结果渲染成人能读的东西」，
不做决策、不改行程、不补造事实。

渲染原则（与 risk 专家同一立场）：**缺口要说出来**。
  - 校验没通过的项，逐条列在「需要你确认」里，不藏进小字
  - 数据来源单独成段，让人一眼看到「这是示例数据」
  - 自动修复过的行程，说明改了哪些地方 —— 用户有权知道行程被调整过
"""
from __future__ import annotations

from backend.config import travel as T
from backend.shared.logger import logger
from backend.travel.graph_state import (
    build_travel_context,
    load_brief,
    load_itinerary,
    load_validation,
)
from backend.travel.models.itinerary import KIND_MEAL
from backend.travel.slot_filler import build_clarification

# 数据来源标识 → 面向用户的说明。
# **新增数据源必须在此登记**，否则用户会看到「tencent:lbs — tencent:lbs」
# 这种等于没说的输出（实测踩过：接入腾讯位置服务后来源段就变成了复读）。
_SOURCE_LABELS: dict[str, str] = {
    "seed": "本地示例数据（未经实时校验）",
    "tencent:lbs": "腾讯位置服务 —— 坐标与路线为实时数据；营业时间与票价为默认占位，未核实",
    "estimate:local": "本地估算（直线距离 × 绕行系数，非真实路况）",
}


def describe_source(source: str) -> str:
    """来源标识 → 人话描述。

    先精确匹配，再按 ``:`` 前缀匹配（``seed:local`` → ``seed``）；
    都没命中时给一句可执行的兜底，而不是把标识原样重复一遍。
    """
    if source in _SOURCE_LABELS:
        return _SOURCE_LABELS[source]
    prefix = (source or "").split(":")[0]
    if prefix in _SOURCE_LABELS:
        return _SOURCE_LABELS[prefix]
    return "来源未登记（请在 reporter._SOURCE_LABELS 中补充说明）"


# 通勤降级原因 → 面向用户的说明（Phase 1：降级要可解释，不能让用户对着来源标签猜）
_FALLBACK_HINTS: dict[str, str] = {
    "trip_date_beyond_horizon":
        "出行日期距今天较远，实时路况对那天没有参考意义，"
        "此段为经验估算 —— 建议临近出发时让我重新规划路线。",
}


def travel_reporter_node(state: dict) -> dict:
    """行程单节点。"""
    answer = _assemble(state)
    logger.info("[TravelReporter] final_answer length=%d", len(answer))
    return {
        "final_answer": answer,
        "travel_context": build_travel_context(state),
    }


def _assemble(state: dict) -> str:
    brief = load_brief(state)

    # 1) 必填槽位缺失 → 追问（不猜、不硬排）
    if state.get("brief_missing"):
        return build_clarification(brief, state.get("user_message", ""))

    # 2) 候选池为空 → 如实说明数据覆盖范围
    if not state.get("candidates"):
        notes = state.get("notes", [])
        detail = ("\n".join(f"- {n}" for n in notes)) if notes else ""
        return (
            f"暂时无法为「{brief.destination}」规划行程：当前没有该城市的地点数据。\n\n"
            f"{detail}\n\n"
            "可以换一个目的地，或等数据源接入后再试。"
        ).strip()

    itinerary = load_itinerary(state)
    if itinerary is None:
        return (
            "行程未能生成（排程阶段未产出结果），请补充或调整需求后重试。"
        )

    return _render_itinerary(state, itinerary)


def _render_itinerary(state: dict, itinerary) -> str:
    brief = itinerary.brief
    report = load_validation(state)
    lines: list[str] = []

    # ── 头部：一眼看清前提 ──
    date_label = (f"{brief.start_date.isoformat()} 起"
                  if brief.start_date else "未指定出发日期")
    lines.append(f"# {brief.destination} {len(itinerary.days)} 天行程")
    lines.append("")
    lines.append(
        f"**人数** {brief.party_size} 人 ｜ "
        f"**节奏** {brief.pace_label()} ｜ "
        f"**出发** {date_label} ｜ "
        f"**预估总花费** ¥{itinerary.cost.total:.0f}"
        + (f"（预算 ¥{brief.budget_cny:.0f}）" if brief.budget_cny else "（未提供预算）")
    )
    if brief.preferences:
        lines.append(f"**偏好** {'、'.join(brief.preferences)}")
    if brief.must_go:
        lines.append(f"**必去** {'、'.join(brief.must_go)}")
    lines.append("")

    # ── 逐日 ──
    for day in itinerary.days:
        header = f"## 第 {day.day_index} 天"
        if day.day_date:
            weekday = "一二三四五六日"[day.day_date.weekday()]
            header += f"（{day.day_date.isoformat()} 周{weekday}）"
        lines.append(header)
        lines.append("")

        legs = list(day.legs)
        leg_cursor = 0
        for item in day.items:
            tag = "" if item.kind != KIND_MEAL else "（用餐）"
            lines.append(f"- **{item.start}-{item.end}** {item.title}{tag}")
            if item.note:
                lines.append(f"  - 提示：{item.note}")
            # 通勤段插在「到达项」之前展示会很乱，这里统一挂在离开上一项之后
            if leg_cursor < len(legs) and legs[leg_cursor].to_title == item.title:
                leg = legs[leg_cursor]
                leg_cursor += 1
                mode = "步行" if leg.mode == "walk" else "乘车"
                lines.append(
                    f"  - 前往下一站：{mode} {leg.minutes} 分钟"
                    f"（约 {leg.distance_km}km"
                    + (f"，约 ¥{leg.cost_cny:.0f}" if leg.cost_cny else "")
                    + "）"
                )
                # Phase 1：通勤降级要可解释 —— 为什么这一段不是实时数据
                if leg.fallback_reason:
                    hint = _FALLBACK_HINTS.get(leg.fallback_reason)
                    if hint:
                        lines.append(f"  - {hint}")
        lines.append(
            f"\n*当日：活动 {day.active_minutes} 分钟、在途 {day.transit_minutes} 分钟、"
            f"花费约 ¥{day.cost_cny:.0f}*"
        )
        lines.append("")

    # ── 费用拆分 ──
    cost = itinerary.cost
    lines.append("## 费用预估")
    lines.append("")
    lines.append(f"- 门票 ¥{cost.tickets:.0f}")
    lines.append(f"- 餐饮 ¥{cost.meals:.0f}")
    lines.append(f"- 住宿 ¥{cost.lodging:.0f}")
    lines.append(f"- 通勤 ¥{cost.transit:.0f}")
    lines.append(f"- **合计 ¥{cost.total:.0f}**")
    lines.append("")

    # ── 自动调整说明 ──
    repair_log = state.get("repair_log", [])
    if repair_log:
        lines.append("## 行程自动调整说明")
        lines.append("")
        rounds = itinerary.repair_rounds
        lines.append(f"首版行程未通过约束校验，已自动调整 {rounds} 轮：")
        for action in repair_log:
            dropped = "、".join(action.get("dropped", []))
            kept = "、".join(action.get("kept_required", []))
            if dropped:
                lines.append(f"- 移除「{dropped}」：{action.get('reason', '')}")
            if kept:
                lines.append(f"- 保留「{kept}」：{action.get('reason', '')}")
        lines.append("")

    # ── 需确认 / 风险 / 来源 ──
    lines.append("## 需要你确认")
    lines.append("")
    items: list[str] = []
    if report is not None:
        items += [v.message for v in report.errors]
        # USER_DECISION 层级（任务书 §7）：必去项冲突等由用户取舍的约束，
        # 单列并给出行动选项 —— 与「自动调整说明」混在一起会被当噪音略过
        for d in report.decision_required:
            items.append(f"需要你决定：{d.message}（可改日期/换时段，或保留此安排并接受风险）")
    items += [f"{w}" for w in itinerary.warnings]
    items += state.get("notes", [])
    # Phase 1：占位事实字段级披露 —— unverified 的营业时间/票价不再只靠
    # notes 文案一次性提及，凡出现在行程里的都逐一点名。
    unverified = [p.name for p in itinerary.all_pois()
                  if p.verification_status == "unverified"]
    if unverified:
        items.append(
            "以下地点的营业时间与票价未经核实，请出行前自行确认："
            + "、".join(dict.fromkeys(unverified))
        )
    if items:
        lines += [f"- {i}" for i in dict.fromkeys(items)]
    else:
        lines.append("- 无")
    lines.append("")

    if itinerary.sources:
        lines.append("## 数据来源")
        lines.append("")
        for source in itinerary.sources:
            lines.append(f"- {source} — {describe_source(source)}")
        lines.append("")

    confidence = itinerary.confidence
    level = "高" if confidence >= 0.75 else ("中" if confidence >= 0.5 else "低")
    # 版本脚注（任务书 §4）：让「这版行程基于哪个需求、哪一版改来」可追溯。
    # 修复产生的版本明示 parent；重规划首版（无 parent）只报需求版本。
    lineage = (f"自 v{itinerary.parent_plan_version} 修复而来"
               if itinerary.parent_plan_version else "首版")
    lines.append(
        f"\n*行程 v{itinerary.plan_version}（{lineage}，需求 v{itinerary.brief_version}，"
        f"状态 {itinerary.status}）—— 置信度 {confidence:.2f}（{level}），"
        f"由约束通过度与数据完备度计算，非模型自评。"
        f"修复轮数 {itinerary.repair_rounds}，硬约束上限 "
        f"{T.TRAVEL_MAX_REPAIR_ROUNDS} 轮。*"
    )
    return "\n".join(lines)
