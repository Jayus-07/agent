"""travel/experts/risk.py — 风险与数据可信度专家

这是四个专家中唯一**不产出规划结果**、只产出「免责与溯源」的节点。

它存在的理由：行程单必须能回答「你凭什么这么说」。营业时间错了、票价
过期了、某个馆临时闭馆，用户按这份行程真的会白跑一趟。P0 尚未接入
实时数据源，所以本节点做的是**如实披露**而不是假装掌握：

  - 数据来源逐条汇总进 sources（当前是本地示例数据，就写示例数据）
  - 明确列出「未接入、需用户自行确认」的信息类别（天气/预约/签证）
  - 汇总上游专家产生的结构性提示（候选偏少、必去未匹配等由 supervisor
    在 state.notes 里累积，本节点不重复生成，只做兜底补充）

P1 接入 MCP 数据源后，本节点应扩充为真正的外部风险核查（台风季、
景区限流、口岸政策），但「先声明能力边界」这一层不要去掉。
"""
from __future__ import annotations

from backend.shared.logger import logger
from backend.travel.experts.base import run_expert_safely
from backend.travel.graph_state import load_itinerary, save_itinerary

# 明确未接入的数据能力 —— 措辞要具体到用户能动作的地步
_UNAVAILABLE_DISCLAIMERS: tuple[str, ...] = (
    "景区实时天气与预警未接入数据源，出行前请自行确认",
    "门票预约余量、索道/演出等需预约项目未接入，热门点位请提前预约",
    "签证、入境与当地政策未接入数据源，跨境行程请以官方公布为准",
)


def assess_risks(itinerary) -> tuple[list[str], list[str]]:
    """产出 (warnings, sources)（纯函数，可单测）。"""
    warnings: list[str] = []
    sources: list[str] = []

    for poi in itinerary.all_pois():
        if poi.source and poi.source not in sources:
            sources.append(poi.source)

    if any(s.startswith("seed") for s in sources):
        warnings.append(
            "本行程使用的坐标、营业时段与票价为本地示例数据，"
            "尚未接入实时数据源；出行前请以景区/官方渠道公布的时刻与票价为准"
        )

    warnings.extend(_UNAVAILABLE_DISCLAIMERS)
    return warnings, sources


def risk_expert_node(state: dict) -> dict:
    """风险专家节点：填充行程的 warnings / sources。"""
    def _run(_state: dict) -> dict:
        itinerary = load_itinerary(state)
        if itinerary is None:
            return {"status": "failed", "data": {}, "notes": [],
                    "error": "行程尚未生成，无法评估数据风险"}

        warnings, sources = assess_risks(itinerary)
        itinerary.warnings = warnings
        itinerary.sources = sources

        logger.info("[TravelRisk] sources=%s warnings=%d",
                    sources, len(warnings))
        return {"status": "success",
                "data": {"itinerary": save_itinerary(itinerary)},
                "notes": []}

    result = run_expert_safely("risk", _run, state)
    data = result.get("data") or {}

    history = list(state.get("expert_history", []))
    history.append({"expert": "risk", "status": result.get("status", "failed"),
                    "duration_ms": result.get("duration_ms", 0)})

    update: dict = {
        "last_expert_result": dict(result),
        "expert_history": history,
        "notes": list(state.get("notes", [])) + list(result.get("notes", [])),
    }
    if data.get("itinerary"):
        update["itinerary"] = data["itinerary"]
    return update
