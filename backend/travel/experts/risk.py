"""travel/experts/risk.py — 风险与数据可信度专家节点（Phase 3 改造：节点编排 + 兼容入口）

真身已迁 services/risk_service.py（assess_risks 纯函数 / retrieve_knowledge
软失败封装，逐字搬运零改动）。本文件保留 LangGraph 节点 `risk_expert_node`
（state 读写 / run_expert_safely / sources 合并 / expert_history——Node
边界冻结）并 re-export 历史公开符号（experts/__init__ 等）。

这是五专家中唯一**不产出规划结果**、只产出「免责与溯源」的节点：
行程单必须能回答「你凭什么这么说」。检索到知识库原文 → 摘录进
knowledge_refs；检索为空 / 失败 → 自动退回纯免责声明，不报错不阻塞。
"""
from __future__ import annotations

from backend.shared.logger import logger
from backend.travel.experts.base import run_expert_safely
from backend.travel.graph_state import load_brief, load_itinerary, save_itinerary

# 兼容面 + 节点调用面（本模块命名空间 = 补丁缝）：真身在 services/risk_service.py
from backend.travel.services.risk_service import (  # noqa: F401
    assess_risks,
    retrieve_knowledge,
)


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

        # P0-1：知识库检索（软失败，封装在 risk_service）。摘录进
        # state.knowledge_refs，由 reporter 渲染为独立「知识库参考」段；
        # 来源标识补进 sources。
        knowledge_refs: list[str] = []
        try:
            brief = load_brief(_state)
            chunks, source_tag = retrieve_knowledge(
                brief.destination, brief.preferences)
            if chunks:
                knowledge_refs = chunks
                if source_tag and source_tag not in itinerary.sources:
                    itinerary.sources.append(source_tag)
        except Exception:  # noqa: BLE001 — 双保险：service 已兜底，这里防意外
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
