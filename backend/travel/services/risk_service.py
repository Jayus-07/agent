"""travel/services/risk_service.py — 风险与数据可信度服务（Phase 3 Commit A）

真身自 experts/risk.py 逐字迁入。两个职责：
  - assess_risks：行程溯源与能力边界披露（纯函数）——seed 来源警告 +
    三条「明确未接入」的免责声明（措辞具体到用户能动作的地步）
  - retrieve_knowledge：知识库摘录封装（内部 RAG Tool，非外部 Provider；
    软失败——检索层已兜底，这里再加一层防意外，失败返回空不抛出）
"""
from __future__ import annotations

from datetime import datetime

from backend.shared.logger import logger
from backend.travel.models.poi import is_seed_source

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

    if any(is_seed_source(s) for s in sources):
        warnings.append(
            "本行程使用的坐标、营业时段与票价为本地示例数据，"
            "尚未接入实时数据源；出行前请以景区/官方渠道公布的时刻与票价为准"
        )

    warnings.extend(_UNAVAILABLE_DISCLAIMERS)
    return warnings, sources


def retrieve_knowledge(
    destination: str, preferences: list[str],
) -> tuple[list[str], str]:
    """知识库摘录（P0-1，软失败）。

    Returns:
        (摘录 chunks, 来源标识 tag)；未提供目的地 / 检索为空 / 失败一律
        返回 ([], "")——调用方据此自动退回纯免责声明，不报错不阻塞。
    """
    if not destination:
        return [], ""
    try:
        from backend.tools.travel.knowledge import (
            build_knowledge_query,
            retrieve_travel_knowledge,
        )

        chunks, source_tag = retrieve_travel_knowledge(
            build_knowledge_query(destination, preferences))
        return (chunks or [], source_tag or "")
    except Exception:  # noqa: BLE001 — 双保险：检索层已兜底，这里防意外
        logger.debug("[TravelRiskService] 知识库检索失败（已跳过）", exc_info=True)
        return [], ""


def build_knowledge_evidence(
    destination: str, chunks: list[str], source_tag: str,
) -> dict[str, dict]:
    """知识摘录证据表（travel-domain-design-v5.md §13，Phase 4；纯函数，检索逻辑零改动）。

    source_type=RAG（基线 0.7）；verified_at=检索时点——检索即核实事件，
    来源文档经 source_tag 锚定进 source。value 只存摘录索引不存全文
    （全文在 state.knowledge_refs，控 checkpoint 体积）；expire_at=None
    （知识摘录无时效边界，信任档位由 RAG 基线表达）。"""
    if not chunks:
        return {}
    from backend.travel.core.contracts import SourceType
    from backend.travel.core.evidence_utils import evidence_to_dict, make_evidence

    verified_at = datetime.now().astimezone()
    evidences: dict[str, dict] = {}
    for i, chunk in enumerate(chunks):
        ev = make_evidence(
            f"kb:{destination}:{i}",
            value={"chunk_index": i, "chars": len(chunk)},
            source=f"rag:{source_tag}" if source_tag else "rag:unknown",
            source_type=SourceType.RAG,
            verified_at=verified_at,
        )
        evidences[ev.fact_id] = evidence_to_dict(ev)
    return evidences
