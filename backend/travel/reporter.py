"""travel/reporter.py — 行程单生成器

域图内部的最终输出节点。只做「把结构化结果渲染成人能读的东西」，
不做决策、不改行程、不补造事实。

渲染原则（与 risk 专家同一立场）：**缺口要说出来**。
  - 校验没通过的项，逐条列在「需要你确认」里，不藏进小字
  - 没有可核验来源的费用、门票、路线、坐标只写「暂无数据」
  - 自动修复过的行程，说明改了哪些地方 —— 用户有权知道行程被调整过
"""
from __future__ import annotations

from backend.shared.logger import logger
from backend.travel.graph_state import (
    build_travel_context,
    load_brief,
    load_itinerary,
    load_validation,
)
from backend.travel.models.itinerary import KIND_MEAL
from backend.travel.models.poi import CATEGORY_MEAL, PLAYABLE_MEAL_MARKERS, source_provider
from backend.travel.planning import names_match
from backend.travel.slot_filler import build_clarification

# 数据来源标识 → 面向用户的说明。
# **新增数据源必须在此登记**，否则用户会看到「tencent:lbs — tencent:lbs」
# 这种等于没说的输出（实测踩过：接入腾讯位置服务后来源段就变成了复读）。
_SOURCE_LABELS: dict[str, str] = {
    "seed": "本地示例数据（未经实时校验）",
    "tencent:lbs": "腾讯位置服务 —— 坐标与路线为实时数据；营业时间与票价为默认占位，未核实",
    "estimate:local": "本地估算（直线距离 × 绕行系数，非真实路况）",
    "rag:travel": "旅游知识库检索摘录 —— 原文引用，时效未核实，请以官方最新公布为准",
}


def describe_source(source: str) -> str:
    """来源标识 → 人话描述。

    先精确匹配，再按 ``:`` 前缀匹配（``seed:local`` → ``seed``）；
    都没命中时给一句可执行的兜底，而不是把标识原样重复一遍。
    """
    if source in _SOURCE_LABELS:
        return _SOURCE_LABELS[source]
    # 提供方段的解析口径与 POI.source 的判定共用一份（models/poi.source_provider），
    # 避免「这里按 : 切、那里按 startswith」两套口径（结构病审查 P3-1）
    prefix = source_provider(source)
    if prefix in _SOURCE_LABELS:
        return _SOURCE_LABELS[prefix]
    return "来源未登记（请在 reporter._SOURCE_LABELS 中补充说明）"


# 这些内容可以留在内部 warnings/trace 中帮助排障，但不能原样进入助手回复。
# 用户需要的是「哪里没数据」，不是 seed 标识、估算公式或内部评分。
_HIDDEN_USER_DATA_MARKERS = (
    "本地示例数据",
    "seed:local",
    "本地估算",
    "费用预估",
    "常见消费水平估算",
    "置信度",
)


def _is_verified_poi(poi) -> bool:
    """只有非种子来源且明确核验的 POI 才能进入用户数据状态。"""
    return bool(
        poi
        and poi.verification_status == "verified"
        and source_provider(poi.source) not in {"", "seed", "estimate"}
    )


def _is_verified_leg(leg) -> bool:
    """路线必须来自实时/官方来源，且不能标记为估算。"""
    return bool(
        leg
        and not leg.is_estimate
        and source_provider(leg.source) in {"official", "tencent", "live"}
    )


def _append_data_status(lines: list[str], itinerary) -> None:
    """渲染字段级数据状态，禁止把本地占位值包装成参考价。"""
    pois = itinerary.all_pois()
    legs = [leg for day in itinerary.days for leg in day.legs]
    poi_verified = bool(pois) and all(_is_verified_poi(poi) for poi in pois)
    route_verified = bool(legs) and all(_is_verified_leg(leg) for leg in legs)

    # M2 验收反馈：只报已核验项（「暂无数据」行是噪音）；全未核验则整节不渲染
    verified_rows = [
        "- **门票票价：已核验**" if poi_verified else None,
        "- **路线：已核验**" if route_verified else None,
        "- **坐标：已核验**" if poi_verified else None,
    ]
    verified_rows = [row for row in verified_rows if row]
    if verified_rows:
        lines.extend(["## 数据说明", ""] + verified_rows + [""])


def _user_visible_items(values) -> list[str]:
    """过滤内部来源/评分文案，保留可执行的用户提示。"""
    return [
        str(value) for value in values
        if value and not any(marker in str(value) for marker in _HIDDEN_USER_DATA_MARKERS)
    ]


# ── 会话意图问答出口（v3 §2.1，P0-A）────────────────────────────
# 渲染纪律不变：只把 state 里的事实（inspiration 包/推荐结果）组织成
# 人话，不检索、不补造观点；检索类信息缺失时如实说「不可用/没有找到」。


def _answer_out_of_scope() -> str:
    """OUT_OF_SCOPE（M2 出域引导）：明确非旅游域诉求，不硬解析不硬排。

    口径：如实说清能力边界 + 给出去处（主页 AI 助手），不留死胡同。
    """
    return (
        "这个问题超出了行程规划的范围——我只懂旅游：排行程、改行程、查车票、"
        "找美食景点。\n\n"
        "订单、退款、数据查询、写代码这类问题，请到主页的「AI 助手」提问，"
        "那里能路由到对应的能力。"
    )


def _answer_static(state: dict) -> str:
    """QUERY_STATIC：知乎攻略观点（inspiration 包）+ 轻规划引导。"""
    brief = load_brief(state)
    inspiration = state.get("inspiration") or {}
    destination = (brief.destination or inspiration.get("destination")
                   or "目的地").strip()
    status = inspiration.get("status") or "unavailable"
    guides = inspiration.get("guides") or []
    lines: list[str] = [f"关于「{destination}」的玩法，给你有出处的参考：", ""]
    if status == "available" and guides:
        for guide in guides[:3]:
            lines.append(f"- **{guide.get('title', '')}**")
            if guide.get("summary"):
                lines.append(f"  {guide['summary']}")
            if guide.get("url"):
                lines.append(f"  来源：{guide['url']}")
        lines.append("")
        lines.append(
            "> 以上是攻略作者的主观体验，不代表普遍事实；营业时间、票价等"
            "事实请以官方渠道为准。"
        )
    elif status == "empty":
        lines.append(
            f"没有找到与「{destination}」相关的攻略观点。"
            "可以直接说「做一份 N 天行程」先排起来，或者换个目的地聊聊。"
        )
    else:
        lines.append(
            "攻略检索暂时不可用（来源超时或未启用），没法给你有出处的观点。"
            f"不想等的话，直接说「做一份{destination} N 天行程」，我先排起来。"
        )
    lines.append("")
    lines.append(
        f"想直接做行程：说「做一份{destination} N 天行程」，"
        "我会先确认天数再开工。"
    )
    return "\n".join(lines)


def _answer_dynamic(state: dict) -> str:
    """QUERY_DYNAMIC：实时状态无可靠来源 → 如实告知（v3 §2.1 优先级 2）。"""
    brief = load_brief(state)
    destination = (brief.destination or "").strip()
    dest_label = destination or "目的地"
    return (
        "「是否开门 / 当前票价 / 实时余票」这类随时会变的事实，"
        "我还没有可核验的实时来源，不能拿旧攻略冒充现状——"
        "建议出行前通过景区或官方渠道核实。\n\n"
        f"我能帮上的：规划一份{dest_label}的行程（说「做一份{dest_label} N 天行程」），"
        "行程里的门票与开放时段会如实标注数据状态，不编数字。"
    )


def _answer_discover(state: dict) -> str:
    """DISCOVER：还在选目的地 → 可解释候选 + 一次引导（不启动规划链）。"""
    recs = (state.get("inspiration") or {}).get("recommendations") or []
    if not recs:
        return (
            "告诉我一个想去的城市（或出发地 + 假期长度），"
            "我来帮你定方向、直接排行程。"
        )
    lines = ["先给你几个排得出行程的方向：", ""]
    for rec in recs:
        highlights = "、".join(rec.get("highlights") or []) or "地点候选可供参考"
        lines.append(f"- **{rec['city']}**：{highlights}")
    lines.append("")
    lines.append("回复城市名即可开始；也可以直接说「去 XX 玩 N 天」。")
    return "\n".join(lines)


def _answer_modify() -> str:
    """MODIFY（抽取器未理解的逐条改单）：如实说明当前支持的改法。"""
    return (
        "逐条改单（换某天的安排、调整顺序）还在建设中。目前可以直接支持的改法：\n\n"
        "- 「不去 / 避开 XX」——把该地点从行程里排除并重排\n"
        "- 「重新规划」——按当前条件重新生成一份\n"
        "- 直接补充「X 天 / 预算 XX / 必去 XX」——我会重排并保留其他要求"
    )


def travel_reporter_node(state: dict) -> dict:
    """行程单节点。"""
    answer = _assemble(state)
    _stamp_plan_run(state)
    logger.info("[TravelReporter] final_answer length=%d", len(answer))
    return {
        "final_answer": answer,
        "travel_context": build_travel_context(state),
        "rationale": _build_rationale(state),
    }


def _build_rationale(state: dict) -> dict:
    """结构化「为什么这样排」（M2 验收反馈）：前端 RationaleCard 消费。

    只搬运 state 里既存的事实（修复记录/取舍/核验状态/版本谱系），
    不做新的推断；文本版 final_answer 仍是降级兜底，两者同源不矛盾。
    """
    itinerary = load_itinerary(state)
    if itinerary is None:
        return {}
    report = load_validation(state)
    must_go = [w.strip() for w in itinerary.brief.must_go if (w or "").strip()]

    # 修复/取舍：来自 repair_log（自动调整的真实记录）
    dropped = []
    kept_required = []
    for action in state.get("repair_log", []):
        day_idx = action.get("day_index", "")
        for s in action.get("weather_swaps") or []:
            dropped.append({"name": f"{s.get('from', '')}", "reason": f"第 {day_idx} 天天气调整 → {s.get('to', '')}"})
        for name in action.get("dropped") or []:
            dropped.append({"name": name, "reason": action.get("reason", "")})
        for name in action.get("kept_required") or []:
            kept_required.append(name)

    # 未排入候选：candidates 与行程 POI 的差集（按名称匹配，≤8 条）
    scheduled_names = [poi.name for poi in itinerary.all_pois()]
    from backend.travel.planning import names_match  # 复用既有匹配口径

    scheduled_norm = scheduled_names
    unscheduled = []
    for c in state.get("candidates") or []:
        name = str(c.get("name") or c.get("title") or "")
        if not name:
            continue
        if not any(names_match(name, s) for s in scheduled_norm):
            unscheduled.append(name)
    unscheduled_extra = max(0, len(state.get("candidates") or []) - len(scheduled_names) - len(unscheduled))

    verified = []
    pois = itinerary.all_pois()
    legs = [leg for day in itinerary.days for leg in day.legs]
    poi_verified = bool(pois) and all(_is_verified_poi(poi) for poi in pois)
    route_verified = bool(legs) and all(_is_verified_leg(leg) for leg in legs)
    if poi_verified:
        verified.append("坐标：已核验")
    if route_verified:
        verified.append("路线：已核验")

    pace_rules = {"relaxed": "每天最多 4 个地点 / 240 分钟活动",
                  "moderate": "每天最多 5 个地点 / 300 分钟活动",
                  "intense": "每天最多 6 个地点 / 360 分钟活动"}

    return {
        "headline": {
            "days": len(itinerary.days),
            "spots": len([p for p in pois]),
            "must_go": must_go,
        },
        "tradeoffs": {
            "dropped": dropped[:6],
            "kept_required": kept_required[:6],
            "unscheduled": unscheduled[:8],
            "unscheduled_extra": unscheduled_extra,
        },
        "verified": verified,
        "pace_rule": pace_rules.get(itinerary.brief.pace, pace_rules["moderate"]),
        "version_note": (
            f"行程 v{itinerary.plan_version}（需求 v{itinerary.brief_version}）"
        ),
        "_has_decision_required": bool(report and report.decision_required),
    }


def _stamp_plan_run(state: dict) -> None:
    """运行级汇总 span（任务书 §11 TravelPlanRun，Phase 4）。

    以一条 span 承载本次规划的运行事实（目的地/版本链/状态/校验计数/
    修复轮数/置信度/持久化状态），评测归因与质量面板据此聚合 ——
    复用现有 tracer 不新建存储体系，也不把大对象塞进 LangGraph State。
    无活跃 trace 时 start_span 返回 noop（软失败），不影响出单。
    """
    try:
        from backend.observability.tracer import trace_collector

        itinerary = load_itinerary(state)
        brief = load_brief(state)
        report = load_validation(state)
        metrics = {
            "destination": brief.destination,
            "days": len(itinerary.days) if itinerary else 0,
            "brief_version": brief.version,
            "plan_version": itinerary.plan_version if itinerary else None,
            "plan_status": itinerary.status if itinerary else "",
            "repair_rounds": state.get("repair_rounds", 0),
            "errors": len(report.errors) if report else 0,
            "warnings": len(report.warnings) if report else 0,
            "decision_required": len(report.decision_required) if report else 0,
            "confidence": itinerary.confidence if itinerary else None,
            "persistence_status": state.get("persistence_status", ""),
        }
        span = trace_collector.start_span(
            "travel_plan_run", name="旅游规划运行汇总",
            type="workflow", kind="workflow",
            input={"destination": brief.destination,
                   "missing": state.get("brief_missing", [])},
        )
        trace_collector.end_span(span, metrics=metrics, status="success")
    except Exception:
        logger.debug("[TravelReporter] 运行汇总 span 写入失败", exc_info=True)


def _assemble(state: dict) -> str:
    # 0) 会话意图问答出口（v3 §2.1，P0-A）：问答/探索/未接线的改单诉求
    #    不走行程渲染。分支必须在 brief_missing 之前——「丽江好玩吗」
    #    抽得到目的地、缺天数，落到追问分支就变成了「误规划」。
    intent = state.get("intent") or ""
    if intent == "out_of_scope":
        return _answer_out_of_scope()
    if intent == "query_static":
        return _answer_static(state)
    if intent == "query_dynamic":
        return _answer_dynamic(state)
    if intent == "discover":
        return _answer_discover(state)
    if intent == "modify":
        return _answer_modify()

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


def _render_rationale(state: dict, itinerary) -> list[str]:
    """「为什么这样排」段（2026-10-03）：把规划依据讲给用户。

    只陈述候选池/骨架层的既存事实（检索规模、类别取舍、排法规则、
    攻略提及），不评价不补造——评价性语言留给攻略原文。
    """
    candidates = state.get("candidates") or []
    if not candidates:
        return []
    meals = [c for c in candidates if c.get("category") == CATEGORY_MEAL
             and not any(m in str(c.get("name") or "") for m in PLAYABLE_MEAL_MARKERS)]
    mentioned = [c for c in candidates
                 if "知乎攻略" in str(c.get("reason") or "")]
    lines = ["## 为什么这样排", ""]
    lines.append(
        f"- 候选池：腾讯位置服务实时检索到 {len(candidates)} 个地点"
        f"（游玩类 {len(candidates) - len(meals)}、餐饮类 {len(meals)}），"
        "来自你填的目的地与偏好"
    )
    if itinerary.brief.must_go:
        lines.append("- 你点名的必去地点优先排入，永不被静默丢弃")
    lines.append(
        "- 排法：先必去、再按地理就近把地点串成每天路线，"
        f"受「{itinerary.brief.pace_label()}」节奏上限约束（每天地点数/活动时长）"
    )
    if meals:
        lines.append(
            f"- {len(meals)} 个餐饮类候选没有排进行程：白天时间留给游玩，"
            "吃什么看下方美食推荐和右栏商户卡"
        )
    if mentioned:
        names = "、".join(str(c.get("name") or "") for c in mentioned[:6])
        more = f" 等 {len(mentioned)} 处" if len(mentioned) > 6 else ""
        lines.append(f"- 知乎攻略提及其中 {len(mentioned)} 处（{names}{more}），逐条出处见各日「为什么选它」")
    lines.append("")
    return lines


def _render_food_picks(state: dict) -> list[str]:
    """「美食推荐」段（2026-10-03）：高德商户检索结果进行程单正文。

    吃的不进行程条目（类别策略），但用户明确关心「选的美食店为什么是
    这些」——理由=与偏好匹配 + 高德实时检索 + 评分，只列检索返回的
    事实字段，缺的不补造。
    """
    food = (state.get("live_search") or {}).get("food") or {}
    merchants = food.get("merchants") or []
    if not merchants:
        return []
    lines = ["## 美食推荐", ""]
    lines.append("> 高德实时检索、与你的美食偏好匹配；营业与价位以到店为准。")
    lines.append("")
    for m in merchants[:3]:
        if not isinstance(m, dict):
            continue
        name = str(m.get("name") or "").strip()
        if not name:
            continue
        extras = []
        if m.get("rating"):
            extras.append(f"评分 {m['rating']}")
        if m.get("address"):
            extras.append(str(m["address"]))
        tail = f"（{' · '.join(extras)}）" if extras else ""
        lines.append(f"- **{name}**{tail}")
    lines.append("")
    return lines


def _render_itinerary(state: dict, itinerary) -> str:
    brief = itinerary.brief
    report = load_validation(state)
    lines: list[str] = []

    # ── 头部：一眼看清前提 ──
    date_label = (f"{brief.start_date.isoformat()} 起"
                  if brief.start_date else "未指定出发日期")
    lines.append(f"# {brief.destination} {len(itinerary.days)} 天行程")
    lines.append("")
    # M2 验收反馈：无费用数据不展示「费用数据 暂无数据」（零信息量）；预算上限保留
    lines.append(
        f"**人数** {brief.party_size} 人 ｜ "
        f"**节奏** {brief.pace_label()} ｜ "
        f"**出发** {date_label}"
        + (f" ｜ **预算上限** ¥{brief.budget_cny:.0f}" if brief.budget_cny else "")
    )
    if brief.preferences:
        lines.append(f"**偏好** {'、'.join(brief.preferences)}")
    if brief.must_go:
        lines.append(f"**必去** {'、'.join(brief.must_go)}")
    lines.append("")

    # ── 逐日逐时段（M2 验收反馈裁剪）：三栏 UI 下中栏时间轴就是逐日安排的
    # 结构化展示，文本版逐时行程在这里纯重复——这是「规划说明又长又乱」的
    # 主因。说明文本只讲「为什么」与「要注意」，逐时安排看行程卡。
    # 「为什么选它」等逐条说明已由中栏行程卡展示（reason/note 字段）。

    # ── 规划依据 + 美食推荐（2026-10-03）：「为什么这样排/为什么选这些」──
    lines += _render_rationale(state, itinerary)
    lines += _render_food_picks(state)

    # ── 费用拆分（M2 验收反馈）：接通可核验费用来源前整节不渲染——
    # 一节「暂无数据」对用户是噪音；预算协商（M3）接真实费用后恢复。

    _append_data_status(lines, itinerary)

    # ── 自动调整说明 ──
    repair_log = state.get("repair_log", [])
    if repair_log:
        lines.append("## 行程自动调整说明")
        lines.append("")
        rounds = itinerary.repair_rounds
        weather_rounds = sum(1 for a in repair_log
                             if a.get("code") == "weather_swap")
        if rounds > 0 or weather_rounds > 0:
            seg: list[str] = []
            if rounds > 0:
                seg.append(f"约束校验自动调整 {rounds} 轮")
            if weather_rounds > 0:
                seg.append(f"天气调整 {weather_rounds} 处")
            lines.append("首版行程经自动调整：" + "、".join(seg) + "：")
        for action in repair_log:
            swaps = action.get("weather_swaps") or []
            if swaps:
                day_idx = action.get("day_index", "")
                for s in swaps:
                    lines.append(
                        f"- 第 {day_idx} 天「{s.get('from', '')}」→ "
                        f"「{s.get('to', '')}」：{action.get('reason', '')}"
                    )
                continue
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
    items += _user_visible_items(itinerary.warnings)
    items += _user_visible_items(state.get("notes", []))
    if items:
        lines += [f"- {i}" for i in dict.fromkeys(items)]
    else:
        lines.append("- 无")
    lines.append("")

    # ── 知识库参考（P0-1）：risk 专家检索的原文摘录，独立成段不与提示混排 ──
    knowledge_refs = state.get("knowledge_refs") or []
    if knowledge_refs:
        lines.append("## 知识库参考")
        lines.append("")
        lines.append("> 以下为知识库检索摘录（原文引用，时效未核实）：")
        lines.append("")
        for ref in knowledge_refs:
            lines.append(f"- {ref}")
        lines.append("")

    # 版本脚注（任务书 §4）：让「这版行程基于哪个需求、哪一版改来」可追溯。
    # 修复产生的版本明示 parent；重规划首版（无 parent）只报需求版本。
    lineage = (f"自 v{itinerary.parent_plan_version} 修复而来"
               if itinerary.parent_plan_version else "首版")
    lines.append(
        f"\n*行程 v{itinerary.plan_version}（{lineage}，需求 v{itinerary.brief_version}，"
        f"状态 {itinerary.status}）。*"
    )

    # 持久化降级披露（任务书 §10，Phase 4）：跨轮改单的可信度受损必须让
    # 用户知道，而不是只留在服务端日志里。disabled 属配置选择，不作事故披露。
    if state.get("persistence_status", "") == "degraded":
        lines.append(
            "\n*注：多轮对话记忆当前为临时存储（持久化降级）——服务重启后"
            "「跨轮修改行程」将不可用。*"
        )
    return "\n".join(lines)
