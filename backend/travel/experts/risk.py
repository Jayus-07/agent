"""travel/experts/risk.py — 风险与数据可信度专家

这是四个专家中唯一**不产出规划结果**、只产出「免责与溯源」的节点。

它存在的理由：行程单必须能回答「你凭什么这么说」。营业时间错了、票价
过期了、某个馆临时闭馆，用户按这份行程真的会白跑一趟。P0 时代未接入
实时数据源时本节点只做如实披露；2026-09-22（P0-1）起接入既有 RAG
知识库（pgvector），在免责声明之外**补一层知识库摘录**：

  - 检索到原文 → 摘录进行程单「知识库参考」段（knowledge_refs），
    来源标识登记进 sources，reporter 渲染时说明「未核实时效」
  - 检索为空 / 失败 → 自动退回纯免责声明，不报错不阻塞

P1 接入 MCP 数据源后，本节点应扩充为真正的外部风险核查（台风季、
景区限流、口岸政策），但「先声明能力边界」这一层不要去掉。
"""
from __future__ import annotations

from backend.shared.logger import logger
from backend.travel.experts.base import run_expert_safely
from backend.travel.graph_state import load_brief, load_itinerary, save_itinerary

# 明确未接入的数据能力 —— 措辞要具体到用户能动作的地步
# （2026-09-22 起天气预报已接入域图 weather 专家，天气条目改为时效提示）
_UNAVAILABLE_DISCLAIMERS: tuple[str, ...] = (
    "行程使用的天气预报为规划时点的预报，临近出发可能变化，出行前请再次确认",
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
    """风险专家节点：填充行程的 warnings / sources，并检索知识库补充摘录。"""
    def _run(_state: dict) -> dict:
        itinerary = load_itinerary(state)
        if itinerary is None:
            return {"status": "failed", "data": {}, "notes": [],
                    "error": "行程尚未生成，无法评估数据风险"}

        warnings, sources = assess_risks(itinerary)
        itinerary.warnings = warnings
        itinerary.sources = sources

        # P0-1：知识库检索（软失败）。摘录进 state.knowledge_refs，
        # 由 reporter 渲染为独立「知识库参考」段；来源标识补进 sources。
        knowledge_refs: list[str] = []
        try:
            from backend.tools.travel.knowledge import (
                build_knowledge_query,
                retrieve_travel_knowledge,
            )

            brief = load_brief(_state)
            if brief.destination:
                chunks, source_tag = retrieve_travel_knowledge(
                    build_knowledge_query(brief.destination, brief.preferences))
                if chunks:
                    knowledge_refs = chunks
                    if source_tag and source_tag not in itinerary.sources:
                        itinerary.sources.append(source_tag)
        except Exception:  # noqa: BLE001 — 双保险：检索层已兜底，这里防意外
            logger.debug("[TravelRisk] 知识库检索意外失败（已跳过）", exc_info=True)

        logger.info("[TravelRisk] sources=%s warnings=%d knowledge_refs=%d",
                    itinerary.sources, len(warnings), len(knowledge_refs))
        return {"status": "success",
                "data": {"itinerary": save_itinerary(itinerary),
                         "knowledge_refs": knowledge_refs},
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
    if data.get("knowledge_refs"):
        update["knowledge_refs"] = list(data["knowledge_refs"])
    return update
