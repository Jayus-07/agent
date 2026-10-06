"""travel/experts/weather.py — 天气专家节点（Phase 3 改造：节点编排 + 兼容入口）

真身已迁 services/weather_service.py（fetch_forecast 七态映射/坏天气判定/
plan_weather_swaps，逐字搬运零改动）。本文件保留 LangGraph 节点
`weather_expert_node`（前置闸/预报窗口交集判定/repair_log 组装/state 读写/
run_expert_safely/遥测——Node 边界冻结）并 re-export 历史公开符号
（test_weather_expert/test_scenarios/test_provider_layer 零改动）。

编排位置（原样保留）：transit 之后、budget 之前——换 POI 需重排当日时刻表，
复用 rebuild_days（与 repair 同一重排路径，真身在 transit_service）。
"""
from __future__ import annotations

from datetime import timedelta

from backend.config import travel as T
from backend.shared.logger import logger
from backend.travel.core.events import run_travel_tool
from backend.travel.experts.base import run_expert_safely
from backend.travel.graph_state import load_brief, load_itinerary, save_itinerary

# 兼容面 + 节点调用面（本模块命名空间 = 测试补丁缝：存量测试
# monkeypatch.setattr(W, "fetch_forecast", ...) 必须可拦截——委托函数
# 整体可被替换，补丁语义不变）；纯解析器直通 service
from backend.travel.agents.optimization_agent import OptimizationAgent
from backend.travel.agents.research_agent import ResearchAgent
from backend.travel.services.weather_service import (  # noqa: F401
    bad_weather_dates,
    is_bad_weather,
    mild_weather_dates,
)

_research = ResearchAgent()
_optimization = OptimizationAgent()


def fetch_forecast(destination):
    """预报获取（Research 能力）：委托 ResearchAgent。"""
    return _research.fetch_forecast(destination)


def fetch_forecast_evidence(destination):
    """预报获取+Evidence（Research 能力）：委托 ResearchAgent（补丁缝——
    Phase 4 起节点走本通道，返回 (forecast, reason, evidence) 三元组）。"""
    return _research.fetch_forecast_evidence(destination)


def plan_weather_swaps(itinerary, candidates, bad_dates):
    """坏天气适应（Optimization 能力）：委托 OptimizationAgent。"""
    return _optimization.plan_weather_swaps(itinerary, candidates, bad_dates)


def weather_expert_node(state: dict) -> dict:
    """天气专家节点：预报（Research 面）→ 坏天气日室内替换（Optimization 面）。"""

    def _run(_state: dict) -> dict:
        brief = load_brief(state)
        itinerary = load_itinerary(state)

        # 前置闸：开关关 / 无行程 / 无出发日期 → 跳过（不花一次网络调用）
        if not T.TRAVEL_WEATHER_ENABLED:
            return {"status": "success", "data": {}, "notes": []}
        if itinerary is None:
            return {"status": "failed", "data": {}, "notes": [],
                    "error": "行程尚未生成，无法执行天气检查"}
        if brief.start_date is None:
            return {"status": "success", "data": {}, "notes": [
                "未提供出发日期，已跳过天气检查；提供日期后可重新规划以纳入天气因素"
            ]}

        # #9b 区县级（2026-10-04）：行程质心 → 逆地理区县 → 按区县查预报；
        # 区县解析失败/预报未收录 → 回退城市级（现行为）。软失败全程。
        district = ""
        if T.TRAVEL_WEATHER_DISTRICT_ENABLED:
            try:
                from backend.travel.services.weather_service import (
                    district_for_point, itinerary_centroid)

                centroid = itinerary_centroid(itinerary)
                if centroid is not None:
                    district = district_for_point(*centroid)
            except Exception:  # noqa: BLE001 — 区县锚点失败即城市级
                district = ""
        query_dest = district or brief.destination
        forecast, degrade_reason, evidence = run_travel_tool(
            "travel.weather.query",
            "research",
            lambda: fetch_forecast_evidence(query_dest),
            result_summary=lambda value: {
                "data_status": "available" if value[0] else "unavailable",
                "reason": value[1] or "",
                # M2 验收反馈：工具行展开可见内容（逐日预报，缺字段不补造）
                "category": "weather",
                "preview": [
                    {
                        "date": rec.get("date") or "",
                        "weather": ((rec.get("day") or {}).get("weather") or "").strip(),
                    }
                    for rec in ((value[0] or {}).get("days") or [])[:5]
                    if isinstance(rec, dict)
                ],
            },
        )
        if not forecast and district:
            # 区县预报未收录/不可用 → 回退城市级再试一次（如实降级）
            district = ""
            forecast, degrade_reason, evidence = run_travel_tool(
                "travel.weather.query",
                "research",
                lambda: fetch_forecast_evidence(brief.destination),
                result_summary=lambda value: {
                    "data_status": "available" if value[0] else "unavailable",
                    "reason": value[1] or "",
                    "category": "weather",
                    "preview": [
                        {
                            "date": rec.get("date") or "",
                            "weather": ((rec.get("day") or {}).get("weather") or "").strip(),
                        }
                        for rec in ((value[0] or {}).get("days") or [])[:5]
                        if isinstance(rec, dict)
                    ],
                },
            )
        if not forecast:
            # §44：Provider down 行程仍出单，只披露；降级原因来自
            # Provider 状态分类（timeout/配额/不可用），不再笼统一句话
            note = "天气预报暂时不可用"
            if degrade_reason and degrade_reason != "天气服务暂时不可用":
                note = f"天气检查已跳过（{degrade_reason}）"
            return {"status": "success", "data": {}, "notes": [
                f"{note}，本次未做天气检查；出行前请自行确认天气"
            ]}

        # 预报窗口与行程日期求交：预报只覆盖未来几天，远期行程按日匹配，
        # 匹配不到的日期自然不触发替换。
        # §43 OUT_OF_HORIZON（STOP J5）：预报与行程日期**零交集**说明出行
        # 日期超出 Provider 可信窗口——如实披露，绝不拿今天的天气伪装未来。
        bad_dates = bad_weather_dates(forecast)
        forecast_dates = {(rec.get("date") or "") for rec in (forecast.get("days") or [])}
        trip_dates = {
            (brief.start_date + timedelta(days=i)).isoformat()
            for i in range(brief.resolved_days())
        }
        if trip_dates and forecast_dates and not (trip_dates & forecast_dates):
            horizon = len(forecast_dates)
            return {"status": "success", "data": {}, "notes": [
                f"出行日期超出天气预报的可信范围（当前预报仅覆盖未来约 {horizon} 天），"
                "本次未做天气检查；临近出发时可让我重新评估"
            ]}
        hit_dates = sorted(set(bad_dates) & trip_dates)
        conditions = brief.weather_conditions or []
        if conditions:
            # 条件天气只约束被点名的日期；没有条件的日期不因同一份预报
            # 被连带改排，满足“只影响必要日期”的局部语义。
            target_days = {
                int(item.get("day_index") or 0)
                for item in conditions
                if item.get("action") == "indoor"
            }
            if target_days and 0 not in target_days:
                date_to_day = {
                    (brief.start_date + timedelta(days=index - 1)).isoformat(): index
                    for index in range(1, brief.resolved_days() + 1)
                }
                hit_dates = sorted(
                    day for day in hit_dates
                    if date_to_day.get(day) in target_days
                )
        # 天气分级（2026-10-04）：仅「小雨」的 mild 日提示带伞不替换——
        # 替换的扰动比小雨对出游的实际损失大；强天气仍走户外→室内替换。
        mild_hit = sorted(set(mild_weather_dates(forecast)) & trip_dates)
        # state.evidences 的契约是 {fact_id: evidence_dict}（graph_state.Evidences，
        # validator/risk/poi 都按此形态读写）；service 返回的是**单条扁平**
        # evidence dict，直接展开合并会把 fact_id/source 等字符串字段污染进
        # evidences，validator is_stale 遍历 `.get()` 即 AttributeError
        # （实测 2026-10-01：带出发日期的规划 100% 复现）——按 fact_id 分键后再并入
        evidence_map = {evidence["fact_id"]: evidence} if evidence else None
        mild_note = (
            f"出行期间预报有小雨（{'、'.join(mild_hit)}），建议携带雨具；行程无需调整"
            if mild_hit else "")
        if district and (mild_note or hit_dates):
            # 区县级查询如实在提示里留痕（用户知道天气是按哪片查的）
            scope = f" 天气按景点集中区域 {district} 查询。"
            mild_note = f"{mild_note}{scope}" if mild_note else scope.strip()
        if not hit_dates:
            # 预报已参与规划判定（无坏天气），证据照记：SOURCE_STALE 据此
            # 判「规划引用的天气数据是否已过期」
            return {"status": "success", "data": {"evidences": evidence_map},
                    "notes": [mild_note] if mild_note else []}

        new_itinerary, actions, extra_notes = plan_weather_swaps(
            itinerary, _state.get("candidates", []), hit_dates,
        )
        notes = list(extra_notes)
        if conditions and hit_dates:
            notes.append("已按条件天气约束，将命中雨天优先调整为室内安排")
        if not new_itinerary:
            if actions:  # 理论不达：有动作却没有产物，防御性兜底
                return {"status": "success", "data": {}, "notes": notes}
            notes.append(
                "出行期间预报有雨，但候选池中没有可替换的室内地点；"
                "行程维持原样，请备好雨具"
            )
            return {"status": "success", "data": {}, "notes": notes}

        logger.info("[TravelWeather] 坏天气日 %s，替换动作 %d 项",
                    hit_dates, len(actions))
        if mild_note:
            notes.append(mild_note)
        return {"status": "success",
                "data": {"itinerary": save_itinerary(new_itinerary),
                         "weather_actions": actions,
                         "evidences": evidence_map},
                "notes": notes}

    result = run_expert_safely("weather", _run, state)
    data = result.get("data") or {}

    history = list(state.get("expert_history", []))
    history.append({"expert": "weather", "status": result.get("status", "failed"),
                    "duration_ms": result.get("duration_ms", 0)})

    update: dict = {
        "last_expert_result": dict(result),
        "expert_history": history,
        "notes": list(state.get("notes", [])) + list(result.get("notes", [])),
    }
    evidences = data.get("evidences") or {}
    if evidences:
        # 合并写入（无 reducer 键是覆盖语义，直接写会冲掉 poi 节点证据）
        update["evidences"] = {**state.get("evidences", {}), **evidences}
    if data.get("itinerary"):
        update["itinerary"] = data["itinerary"]
    # 天气替换动作进 repair_log 展示通道：reporter 的「行程自动调整说明」
    # 会把 swapped 转成可读文案，用户能看到「为什么第 2 天变了」。
    weather_actions = data.get("weather_actions") or []
    if weather_actions:
        log = list(state.get("repair_log", []))
        for action in weather_actions:
            log.append({
                "code": "weather_swap",
                "day_index": action.get("day_index", 0),
                "dropped": [s["from"] for s in action.get("swaps", [])],
                "kept_required": [],
                "reason": action.get("reason", ""),
                "weather_swaps": action.get("swaps", []),
            })
        update["repair_log"] = log
    return update
