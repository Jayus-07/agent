"""travel/validator.py — 硬约束校验器（P0 核心）

定位：旅游域的 Evidence Gate。

RAG 的 Evidence Gate 解决「没有依据就不要作答」；旅游域的等价问题不是
「有没有依据」，而是**「这份行程在物理上成不成立」**。前者靠拒答保护用户，
这里靠结构化 violations 保护用户 —— 排一份「9 点去 17 点才开门的博物馆」
或「一天跨城来回四趟」的行程，比答不出来更糟：它看起来是完整的。

三条硬纪律：
  1. **纯规则、零 LLM、零 IO**。校验的正确性不能押在模型上，也不能有
     网络抖动 —— 与主图 critique「规则校验 0ms 优先」是同一取舍。
  2. **只判定、不修改**。修复动作在 repair.py。两个模块分开，是为了让
     「什么算错」和「怎么补救」各自可独立演进和单测。
  3. **error 与 warning 分治**。error 阻塞交付并触发修复；warning 只是
     提示（如晚于 21:30 收尾），会写进行程单但不推翻它 —— 夜里逛夜市
     是合理需求，不该被判成错误。

四个校验轴：时间（轴一）· 地理（轴二）· 体力（轴三）· 预算（轴四）。
覆盖度（必去地点是否落地）单独作为提示轴，不属于硬约束。
"""
from __future__ import annotations

from typing import Callable

from backend.config import travel as T
from backend.shared.logger import logger
from backend.travel.models.itinerary import (
    PLAN_STATUS_DEGRADED,
    PLAN_STATUS_NEEDS_USER_DECISION,
    PLAN_STATUS_READY,
    Itinerary,
)
from backend.travel.models.validation import (
    CODE_BUDGET_OVER,
    CODE_BUDGET_TIGHT,
    CODE_GEO_FAR_LEG,
    CODE_GEO_REVISIT,
    CODE_GEO_SCATTER,
    CODE_MUST_GO_MISSING,
    CODE_PACE_TOO_INTENSE,
    CODE_PACE_TOO_MANY_POIS,
    CODE_TIME_CLOSED,
    CODE_TIME_CLOSED_WEEKDAY,
    CODE_TIME_DAY_OVERRUN,
    CODE_TIME_LONG_WAIT,
    CODE_TIME_OVERLAP,
    LEVEL_DECISION_REQUIRED,
    LEVEL_ERROR,
    LEVEL_WARNING,
    ValidationReport,
    Violation,
)
from backend.travel.timeutil import from_min, to_min

# 校验轴清单（顺序即执行顺序，也是埋点与文档的呈现顺序）
AXES: tuple[str, ...] = ("time", "geo", "pace", "budget", "coverage")


def check_itinerary(itinerary: Itinerary) -> ValidationReport:
    """对一份行程执行全部约束校验。

    这是 validator 的唯一对外入口 —— 域图、修复器、测试都从这里进，
    内部各轴函数不单独对外暴露，避免调用方漏检某一轴。
    """
    violations: list[Violation] = []
    violations += _run_axis("time_check", lambda: check_time(itinerary))
    violations += _run_axis("geo_check", lambda: check_geo(itinerary))
    violations += _run_axis("pace_check", lambda: check_pace(itinerary))
    violations += _run_axis("budget_check", lambda: check_budget(itinerary))
    violations += _run_axis("coverage_check", lambda: check_coverage(itinerary))

    report = ValidationReport(violations=violations, checked_days=len(itinerary.days))
    logger.info(
        "[TravelValidator] 校验完成: days=%d errors=%d warnings=%d codes=%s",
        report.checked_days, len(report.errors), len(report.warnings), report.codes(),
    )
    return report


def _run_axis(name: str, fn: Callable[[], list[Violation]]) -> list[Violation]:
    """在独立 span 下执行一个校验轴。

    每个轴单独埋点，是为了让「哪条约束最常被违反、耗时多少」可归因 ——
    与其他模块一样，埋点软失败，绝不影响校验结果。
    """
    try:
        from backend.observability.tracer import trace_collector
        span = trace_collector.start_span(
            f"travel_{name}", name=f"旅游约束校验:{name}",
            type="tool_call", input={},
        )
    except Exception:
        span = None

    try:
        result = fn()
    except Exception:
        # 校验器自身出错必须是 P0 可见：宁可让流程失败，也不能悄悄放行
        logger.exception("[TravelValidator] 校验轴 %s 执行异常", name)
        if span is not None:
            try:
                trace_collector.end_span(span, status="error",
                                         metrics={"axis": name, "error": "exception"})
            except Exception:
                pass
        raise

    if span is not None:
        try:
            trace_collector.end_span(
                span, output={"count": len(result)},
                metrics={"axis": name, "violations": len(result),
                         "errors": sum(1 for v in result if v.level == LEVEL_ERROR)},
            )
        except Exception:
            logger.debug("[TravelValidator] 校验轴 %s span 收尾失败", name, exc_info=True)
    return result


# ============================================================
# 轴一：时间可行性
# ============================================================
def check_time(itinerary: Itinerary) -> list[Violation]:
    """时间闭合（营业时段 / 闭馆日）、区间重叠、日内收尾过晚。

    核心语义：**每一项都必须落在它自己的可访问窗口内**。
    这里不做「自动顺延到开门」这种修补 —— 顺应是 repair 的职责，
    校验器若既判定又修补，就再也测不出真实的违反率了。
    """
    out: list[Violation] = []
    day_end_limit = to_min(T.TRAVEL_DAY_END, 21 * 60 + 30)

    for day in itinerary.days:
        prev_end: int | None = None

        for item in day.items:
            start = to_min(item.start, 0)
            end = to_min(item.end, 0)

            if end <= start:
                out.append(Violation(
                    code=CODE_TIME_OVERLAP, level=LEVEL_ERROR, day_index=day.day_index,
                    message=f"第{day.day_index}天「{item.title}」时间区间非法（{item.start}-{item.end}）",
                    detail={"title": item.title, "start": item.start, "end": item.end},
                ))
                continue

            if prev_end is not None and start < prev_end:
                out.append(Violation(
                    code=CODE_TIME_OVERLAP, level=LEVEL_ERROR, day_index=day.day_index,
                    message=(f"第{day.day_index}天「{item.title}」与上一项时间重叠"
                             f"（{item.start} 早于上一项结束 {from_min(prev_end)}）"),
                    detail={"title": item.title, "start": item.start,
                            "prev_end": from_min(prev_end)},
                ))
            prev_end = max(prev_end or 0, end)

            if item.poi is None:
                continue

            open_min = to_min(item.poi.open_time, 0)
            close_min = to_min(item.poi.close_time, 23 * 60 + 59)
            if start < open_min or end > close_min:
                out.append(Violation(
                    code=CODE_TIME_CLOSED,
                    # 必去项的时段冲突是用户的明确诉求与事实的对抗，
                    # 只能由用户裁决（改时间/换日期/保留冲突），不进自动修复
                    level=(LEVEL_DECISION_REQUIRED if item.poi.required
                           else LEVEL_ERROR),
                    day_index=day.day_index,
                    message=(f"第{day.day_index}天「{item.poi.name}」安排在 "
                             f"{item.start}-{item.end}，但其开放时段为 "
                             f"{item.poi.open_time}-{item.poi.close_time}"),
                    detail={
                        "poi_id": item.poi.poi_id, "poi_name": item.poi.name,
                        "visit": [item.start, item.end],
                        "window": [item.poi.open_time, item.poi.close_time],
                        "required": item.poi.required,
                    },
                ))

            if day.day_date is not None and not item.poi.is_open_on(day.day_date.weekday()):
                closed = "、".join(item.poi.closed_weekday_names())
                out.append(Violation(
                    code=CODE_TIME_CLOSED_WEEKDAY,
                    level=(LEVEL_DECISION_REQUIRED if item.poi.required
                           else LEVEL_ERROR),
                    day_index=day.day_index,
                    message=(f"第{day.day_index}天（{day.day_date.isoformat()}）安排了"
                             f"「{item.poi.name}」，但该馆{closed}闭馆"),
                    detail={"poi_id": item.poi.poi_id, "poi_name": item.poi.name,
                            "closed_weekdays": item.poi.closed_weekdays,
                            "required": item.poi.required},
                ))

            if item.wait_minutes >= T.TRAVEL_LONG_WAIT_MINUTES:
                out.append(Violation(
                    code=CODE_TIME_LONG_WAIT, level=LEVEL_WARNING, day_index=day.day_index,
                    message=(f"第{day.day_index}天到「{item.poi.name}」时尚未开放"
                             f"（{item.poi.open_time} 开门），需等候 "
                             f"{item.wait_minutes} 分钟"),
                    detail={"poi_id": item.poi.poi_id, "poi_name": item.poi.name,
                            "wait_minutes": item.wait_minutes,
                            "open_time": item.poi.open_time},
                ))

        if day.items:
            last_end = max(to_min(i.end, 0) for i in day.items)
            if last_end > day_end_limit:
                out.append(Violation(
                    code=CODE_TIME_DAY_OVERRUN, level=LEVEL_WARNING, day_index=day.day_index,
                    message=(f"第{day.day_index}天结束于 {from_min(last_end)}，"
                             f"晚于设定的一天收尾时刻 {T.TRAVEL_DAY_END}"),
                    detail={"end": from_min(last_end), "limit": T.TRAVEL_DAY_END},
                ))
    return out


# ============================================================
# 轴二：地理连通性
# ============================================================
def check_geo(itinerary: Itinerary) -> list[Violation]:
    """单段通勤过长、单日在途时间超标、同一天重复到访。

    为什么用「在途总时长」而非「单日跨度」度量折返：跨城但地铁直达是可
    接受的，同城反复横跳才是问题，而后者必然表现为在途时长堆积 ——
    前者会被误判，后者不会。
    """
    out: list[Violation] = []
    warn_min = T.TRAVEL_MAX_LEG_MINUTES_WARN
    err_min = T.TRAVEL_MAX_LEG_MINUTES_ERR

    for day in itinerary.days:
        for leg in day.legs:
            if leg.minutes >= err_min:
                level = LEVEL_ERROR
            elif leg.minutes >= warn_min:
                level = LEVEL_WARNING
            else:
                continue
            out.append(Violation(
                code=CODE_GEO_FAR_LEG, level=level, day_index=day.day_index,
                message=(f"第{day.day_index}天「{leg.from_title}」→「{leg.to_title}」"
                         f"需 {leg.minutes} 分钟（约 {leg.distance_km}km），通勤偏长"),
                detail={"from": leg.from_title, "to": leg.to_title,
                        "minutes": leg.minutes, "distance_km": leg.distance_km,
                        "mode": leg.mode},
            ))

        transit_total = sum(leg.minutes for leg in day.legs)
        if transit_total > T.TRAVEL_DAY_MAX_TRANSIT_MINUTES:
            out.append(Violation(
                code=CODE_GEO_SCATTER, level=LEVEL_ERROR, day_index=day.day_index,
                message=(f"第{day.day_index}天在途通勤合计 {transit_total} 分钟，"
                         f"超过上限 {T.TRAVEL_DAY_MAX_TRANSIT_MINUTES} 分钟（来回折返）"),
                detail={"transit_minutes": transit_total,
                        "limit": T.TRAVEL_DAY_MAX_TRANSIT_MINUTES},
            ))

        visited: list[str] = []
        for item in day.items:
            if item.poi is None:
                continue
            if item.poi.poi_id in visited:
                out.append(Violation(
                    code=CODE_GEO_REVISIT, level=LEVEL_WARNING, day_index=day.day_index,
                    message=f"第{day.day_index}天重复安排了「{item.poi.name}」",
                    detail={"poi_id": item.poi.poi_id, "poi_name": item.poi.name},
                ))
                continue
            visited.append(item.poi.poi_id)
    return out


# ============================================================
# 轴三：体力强度
# ============================================================
def check_pace(itinerary: Itinerary) -> list[Violation]:
    """单日 POI 数与有效活动时长是否超出该用户的节奏档位。

    有效活动时长只计到访项：既不含通勤（由轴二单独约束），也不含用餐
    （餐饮是必需开销而非游玩强度）。三者若混在一起统计，会互相掩盖真实
    超量 —— 「在车上 3 小时但只逛了 2 小时」的问题在轴二，不在轴三。
    """
    out: list[Violation] = []
    pace = itinerary.brief.normalized_pace()
    max_pois = T.TRAVEL_PACE_MAX_POIS.get(pace, 5)
    max_minutes = T.TRAVEL_PACE_MINUTES.get(pace, 360)

    for day in itinerary.days:
        count = len(day.visit_items())
        if count > max_pois:
            out.append(Violation(
                code=CODE_PACE_TOO_MANY_POIS, level=LEVEL_ERROR, day_index=day.day_index,
                message=(f"第{day.day_index}天安排了 {count} 个地点，"
                         f"超出「{pace}」节奏上限 {max_pois} 个"),
                detail={"count": count, "limit": max_pois, "pace": pace},
            ))
        if day.active_minutes > max_minutes:
            out.append(Violation(
                code=CODE_PACE_TOO_INTENSE, level=LEVEL_ERROR, day_index=day.day_index,
                message=(f"第{day.day_index}天有效活动 {day.active_minutes} 分钟，"
                         f"超出「{pace}」节奏上限 {max_minutes} 分钟"),
                detail={"active_minutes": day.active_minutes,
                        "limit": max_minutes, "pace": pace},
            ))
    return out


# ============================================================
# 轴四：预算收敛
# ============================================================
def check_budget(itinerary: Itinerary) -> list[Violation]:
    """总花费是否超出预算；未超但逼近上限时提前预警。

    预算缺失时不做任何判定 —— 用户没说预算不等于预算为零，
    此处静默跳过，由 reporter 在行程单里如实写明「未提供预算」。
    """
    budget = itinerary.brief.budget_cny
    if budget is None or budget <= 0:
        return []

    total = itinerary.cost.total
    if total > budget:
        return [Violation(
            code=CODE_BUDGET_OVER, level=LEVEL_ERROR, day_index=0,
            message=(f"预估总花费 ¥{total:.0f} 超出预算 ¥{budget:.0f}"
                     f"（超 ¥{total - budget:.0f}）"),
            detail={"total": total, "budget": budget,
                    "over": round(total - budget, 2),
                    "breakdown": itinerary.cost.model_dump()},
        )]

    if total > budget * T.TRAVEL_BUDGET_WARN_RATIO:
        return [Violation(
            code=CODE_BUDGET_TIGHT, level=LEVEL_WARNING, day_index=0,
            message=(f"预估总花费 ¥{total:.0f} 已用掉预算的 "
                     f"{total / budget * 100:.0f}%，余量偏紧"),
            detail={"total": total, "budget": budget,
                    "breakdown": itinerary.cost.model_dump()},
        )]
    return []


# ============================================================
# 覆盖度：必去地点是否落地（提示轴，不属于硬约束）
# ============================================================
def check_coverage(itinerary: Itinerary) -> list[Violation]:
    """用户点名必去的地点是否真的落进了行程。

    命中不了只给 warning：常见原因是数据源没有这个地点（例如用户说的是
    某个小众点位），此时应当如实告知，而不是伪造一个同名条目充数。
    """
    present = [p.name for p in itinerary.all_pois()]
    out: list[Violation] = []
    for want in itinerary.brief.must_go:
        needle = (want or "").strip()
        if not needle:
            continue
        if any(needle in name or name in needle for name in present):
            continue
        out.append(Violation(
            code=CODE_MUST_GO_MISSING, level=LEVEL_WARNING, day_index=0,
            message=f"必去地点「{needle}」未能排入行程（当前候选数据中未匹配到）",
            detail={"must_go": needle},
        ))
    return out


# ============================================================
# 置信度
# ============================================================
def compute_confidence(itinerary: Itinerary, report: ValidationReport) -> float:
    """把「数据完备度 × 校验通过度」折算成 0-1 的置信度。

    刻意不用 LLM 自评 —— 自评是主观的且不可复现。这里的每一分都来自
    可核对的事实：违反几条、数据是不是示例数据、有没有提供日期、
    是不是经过修复才通过。
    """
    score = 1.0
    score -= 0.15 * len(report.errors)
    # decision_required 介于 error 与 warning 之间：是未满足的硬事实（比
    # 提示重），但已如实摆明取舍（比纯 error 轻）
    score -= 0.10 * len(report.decision_required)
    score -= 0.05 * len(report.warnings)
    if any(p.source.startswith("seed") for p in itinerary.all_pois()):
        score -= 0.20
    if itinerary.brief.start_date is None:
        score -= 0.05
    score -= 0.05 * itinerary.repair_rounds
    return round(max(0.05, min(1.0, score)), 2)


# ============================================================
# 图节点（唯一的非纯函数入口，把校验结果写回状态）
# ============================================================
def travel_validator_node(state: dict) -> dict:
    """校验节点：执行校验 → 回写报告与置信度。

    置信度在这里算而不是在 risk 专家：它是「校验通过度」的函数，
    必须在校验之后才能得出；放在专家侧只能拿到一个不完整的视图。
    """
    from backend.travel.graph_state import (
        load_itinerary, save_itinerary, save_validation,
    )

    itinerary = load_itinerary(state)
    if itinerary is None:
        logger.warning("[TravelValidator] 无行程可校验")
        return {
            "validation": None,
            "notes": list(state.get("notes", [])) + ["行程未生成，已跳过约束校验"],
        }

    report = check_itinerary(itinerary)
    itinerary.confidence = compute_confidence(itinerary, report)
    # plan 状态机判定（任务书 §4/§7）：状态在事实产生处落库。
    # errors → degraded；无 error 但有必去冲突等 → needs_user_decision
    #（行程可交付，取舍选项摆明）；全过 → ready。
    if report.errors:
        itinerary.status = PLAN_STATUS_DEGRADED
    elif report.decision_required:
        # Phase 6（任务书 §13）：interrupt 模式下必去冲突暂停图等用户决策；
        # 开关未开或无持久化支撑时回退 Phase 3 软处理（出单+请决定）。
        if _interrupt_enabled():
            return _interrupt_for_decisions(state, itinerary, report)
        itinerary.status = PLAN_STATUS_NEEDS_USER_DECISION
    else:
        itinerary.status = PLAN_STATUS_READY

    return {
        "itinerary": save_itinerary(itinerary),
        "validation": save_validation(report),
    }


# ============================================================
# 用户决策中断（任务书 §13，Phase 6）
# ============================================================
# 语义：必去项与事实冲突（闭馆/超窗口）不是机器能替用户决定的事。
# interrupt 把图暂停在 validator，payload 结构化列出待决项；用户以
# Command(resume=...) 恢复——决策值结构化进状态（不可依赖聊天记录重推），
# 恢复后 validator 按决策改行程/降级违反，继续走 supervisor → reporter。
# 注意：interrupt resume 后本节点会从头重跑，interrupt() 之前的计算
#（check_itinerary 等）必须确定性 —— 现状满足。
def _interrupt_enabled() -> bool:
    """开关开且持久化可用才允许 interrupt（暂停态靠 checkpointer 存活）。

    延迟 import graph_builder：图装配时 import 本模块，顶层互相引用会循环
    （slot_filler 同款处理）。无持久化（DISABLED）时回退软处理——中断无处
    悬挂，等于把用户卡死在半路。
    """
    from backend.config import travel as T

    if not T.TRAVEL_USER_DECISION_INTERRUPT:
        return False
    try:
        from backend.travel.graph_builder import (
            PERSISTENCE_DISABLED, get_persistence_status,
        )
        return get_persistence_status() != PERSISTENCE_DISABLED
    except Exception:  # noqa: BLE001 — 探测失败按软处理走，绝不阻塞出单
        return False


def _decision_payload(report: ValidationReport) -> dict:
    """把待决项抽成结构化 payload（前端渲染与 resume 匹配的唯一依据）。"""
    return {
        "items": [
            {
                "code": v.code,
                "message": v.message,
                "poi_name": v.detail.get("poi_name", ""),
                "poi_id": v.detail.get("poi_id", ""),
                "day_index": v.day_index,
            }
            for v in report.decision_required
        ],
        "options": {
            "keep": "保留此安排并接受风险（行程按 degraded 交付）",
            "drop": "把它从行程移除（需求同步移除，重新交付）",
        },
    }


def _normalize_decision(decision, report: ValidationReport) -> tuple[set, set]:
    """把用户决策归一为 (drop_names, keep_names)。

    三种合法形态：
      {"action": "keep"}            — 全部保留
      {"action": "drop"}            — 全部移除
      {"drop": ["福建博物院", ...]}  — 指名移除，其余保留
    非法/空决策一律按 keep（宁可保守交付，不把用户悬在中断里）。
    """
    names = {v.detail.get("poi_name", "") for v in report.decision_required}
    names.discard("")
    if not isinstance(decision, dict):
        return set(), names
    if decision.get("action") == "drop":
        return set(names), set()
    drop_raw = decision.get("drop")
    if isinstance(drop_raw, (list, tuple)):
        drops = {str(n) for n in drop_raw} & names
        return drops, names - drops
    return set(), names


def _interrupt_for_decisions(state: dict, itinerary, report: ValidationReport) -> dict:
    """interrupt 暂停 → 用户决策 → 按决策改造行程与违反记录。"""
    from langgraph.types import interrupt

    from backend.travel.graph_state import save_itinerary, save_validation
    from backend.travel.models.brief import TravelBrief

    decision = interrupt(_decision_payload(report))
    drops, keeps = _normalize_decision(decision, report)

    # 1) drop：从行程移除 + 从需求 must_go 移除（用户决策=需求变更，结构化落库）
    brief = TravelBrief(**(state.get("brief") or {}))
    if drops:
        for day in itinerary.days:
            # poi 可为 None（餐食等非 POI 占位项），先判空
            day.items = [i for i in day.items
                         if i.poi is None or i.poi.name not in drops
                         or not i.poi.required]
        brief.must_go = [n for n in brief.must_go if n not in drops]

    # 2) keep：违反降级为 warning（不阻塞、不再重复询问），detail 留档决策
    for v in report.violations:
        if v.level != LEVEL_DECISION_REQUIRED:
            continue
        if v.detail.get("poi_name") in drops:
            report.violations.remove(v)
        else:
            v.level = LEVEL_WARNING
            v.detail["user_decision"] = "keep"

    notes = ["你选择保留冲突项，行程按降级状态交付（风险自担）。"] if keeps else []
    if drops:
        notes.append("已按你的决定移除：" + "、".join(sorted(drops)) + "；对应必去需求同步解除。")

    # 3) 保留风险一律如实降级（即使违反只剩 warning）——闭馆是事实
    itinerary.status = PLAN_STATUS_DEGRADED if keeps else itinerary.status
    if not report.decision_required and not keeps:
        # drop 清空待决项后按剩余违反定状态：与主状态机口径一致
        itinerary.status = (PLAN_STATUS_DEGRADED if report.errors
                            else PLAN_STATUS_READY)
    itinerary.confidence = compute_confidence(itinerary, report)

    update = {
        "itinerary": save_itinerary(itinerary),
        "validation": save_validation(report),
        "notes": list(state.get("notes", [])) + notes,
    }
    # 需求变更（must_go 移除）写回 state，保证后续轮指纹/重排基于新需求
    if drops:
        update["brief"] = brief.model_dump()
    return update
