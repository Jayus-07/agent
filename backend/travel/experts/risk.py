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
from backend.travel.core.events import run_travel_tool
from backend.travel.experts.base import run_expert_safely
from backend.travel.graph_state import load_brief, load_itinerary, save_itinerary

# 兼容面 + 节点调用面（本模块命名空间 = 补丁缝）：委托 ResearchAgent
from backend.travel.agents.research_agent import ResearchAgent

_research = ResearchAgent()


def assess_risks(itinerary):
    """溯源与免责（Research 能力）：委托 ResearchAgent。"""
    return _research.assess_risks(itinerary)


def retrieve_knowledge(destination, preferences):
    """知识库摘录（Research 能力）：委托 ResearchAgent。"""
    return _research.retrieve_knowledge(destination, preferences)


def build_knowledge_evidence(destination, chunks, source_tag):
    """知识摘录证据表（Research 能力，Phase 4）：委托 ResearchAgent。"""
    return _research.build_knowledge_evidence(destination, chunks, source_tag)


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

        # P0-1：知识库检索。摘录进 state.knowledge_refs，由 reporter
        # 渲染为独立「知识库参考」段；来源标识补进 sources。Tool 异常
        # 必须交给 run_expert_safely 收口为 failed，不能继续伪装成成功。
        knowledge_refs: list[str] = []
        evidences: dict = {}
        brief = load_brief(_state)
        chunks, source_tag = run_travel_tool(
            "travel.retrieve_knowledge",
            "research",
            lambda: retrieve_knowledge(
                brief.destination, brief.preferences),
            result_summary=lambda value: {
                "result_count": len(value[0]),
                "data_status": "available" if value[0] else "empty",
                # M2 验收反馈：知识库摘录原文（≤4 条，每条截断，出处 tag 随行）
                "category": "knowledge",
                "preview": [
                    {"text": chunk[:120], "source": value[1] or ""}
                    for chunk in value[0][:4]
                    if len(chunk.strip()) > 4  # 滤掉空摘录/省略号占位
                ],
            },
        )
        if chunks:
            knowledge_refs = chunks
            if source_tag and source_tag not in itinerary.sources:
                itinerary.sources.append(source_tag)
            # 摘录证据表（Phase 4，travel-domain-design-v5.md §13）：RAG/0.7，检索时点即核实事件
            evidences = build_knowledge_evidence(
                brief.destination, chunks, source_tag)

        logger.info("[TravelRisk] sources=%s warnings=%d knowledge_refs=%d",
                    itinerary.sources, len(warnings), len(knowledge_refs))
        return {"status": "success",
                "data": {"itinerary": save_itinerary(itinerary),
                         "knowledge_refs": knowledge_refs,
                         "evidences": evidences},
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
    evidences = data.get("evidences") or {}
    if evidences:
        # 合并写入（无 reducer 键是覆盖语义，直接写会冲掉前序节点证据）
        update["evidences"] = {**state.get("evidences", {}), **evidences}
    return update
