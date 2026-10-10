"""travel/core/evidence_utils.py — Evidence 组装/序列化辅助（Phase 4）

Evidence 模型**唯一归属 core/contracts.py**（travel-domain-design-v5.md §13 冻结契约，禁第二套证据
模型）；本模块只是三个组装点（poi/weather/risk_service）与消费方
（validator SOURCE_STALE）共用的转换薄层：

- make_evidence：按冻结基线表定 confidence（缺 verified_at 自动压到上限）；
- evidence_to_dict：enum/datetime 感知序列化（state 只放可 JSON 序列化 dict）；
- is_stale：从 dict 判过期（validator 零 IO 消费——stale-if-error 降级服务
  的旧数据 observed_at 早、expire_at 已过，同样被本判定覆盖）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.travel.core.contracts import (
    CONFIDENCE_BASELINE,
    UNVERIFIED_CONFIDENCE_CAP,
    Evidence,
    SourceType,
)


def parse_iso(value: str | None) -> datetime | None:
    """ISO 字符串 → datetime；None/空/非法一律 None（不抛，软语义）。"""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.astimezone()


def make_evidence(
    fact_id: str,
    *,
    value: Any,
    source: str,
    source_type: SourceType,
    verified_at: datetime | None = None,
    expire_at: datetime | None = None,
    confidence: float | None = None,
) -> Evidence:
    """按冻结基线构造 Evidence（travel-domain-design-v5.md §13 confidence 基线表的应用出口）。

    confidence 缺省取 source_type 基线；缺 verified_at 时压到
    UNVERIFIED_CONFIDENCE_CAP——基线再高，无核实时点也不允许高置信
    （contracts 构造期不变量的诚实前置，此处做的是"给出合法值"，
    越界值仍由 Evidence.__post_init__ fail-fast）。
    """
    if confidence is None:
        confidence = CONFIDENCE_BASELINE[source_type]
    if verified_at is None:
        confidence = min(confidence, UNVERIFIED_CONFIDENCE_CAP)
    return Evidence(
        fact_id=fact_id, value=value, source=source,
        source_type=source_type, confidence=confidence,
        verified_at=verified_at, expire_at=expire_at,
    )


def evidence_to_dict(evidence: Evidence) -> dict:
    """Evidence → 可 JSON 序列化 dict（source_type 取 .value，
    datetime 取 isoformat；None 保持 None）。"""
    return {
        "fact_id": evidence.fact_id,
        "value": evidence.value,
        "source": evidence.source,
        "source_type": evidence.source_type.value,
        "confidence": evidence.confidence,
        "verified_at": (evidence.verified_at.isoformat()
                        if evidence.verified_at else None),
        "expire_at": (evidence.expire_at.isoformat()
                      if evidence.expire_at else None),
    }


def is_stale(evidence: dict, *, now: datetime | None = None) -> bool:
    """Evidence dict 是否已过期（validator SOURCE_STALE 消费，零 IO）。

    无 expire_at 的事实不判 stale：SEED/ESTIMATE 的诚实语义是「不冒充
    时效」，不是「会过期」。"""
    raw = (evidence or {}).get("expire_at")
    expire_at = parse_iso(raw)
    if expire_at is None:
        return False
    return expire_at < (now or datetime.now().astimezone())


# 来源优先级（验收 #90）：冲突裁决顺序 —— Provider 实时数据 > RAG 引文 >
# 本地种子 > 估算。高值可信，同值不冲突。
SOURCE_PRIORITY: dict[str, int] = {
    "provider": 3,
    "live": 3,
    "rag": 2,
    "knowledge": 2,
    "seed": 1,
    "estimate": 0,
}


def detect_conflicts(evidences: dict[str, dict]) -> list[dict]:
    """跨来源事实冲突检测（纯函数，可单测）。

    同一 POI（evidence key 归一到 poi_id）在多个来源下对同一字段给出
    不同值时产出 conflict 记录：字段名、各来源值、按 SOURCE_PRIORITY
    裁决的胜出方。当前候选池为单源单值（合并期已裁决），该框架供
    知识路径（RAG 引文 vs Provider）接入时消费——接入前先以单测固化
    裁决语义，防止接入时口径漂移。

    evidence dict 形态见 make_evidence/evidence_to_dict：{value, source,
    source_type, ...}，value 是字段→值的 dict 或标量。
    """
    by_poi: dict[str, list[tuple[str, dict]]] = {}
    for key, ev in evidences.items():
        if not isinstance(ev, dict):
            continue
        poi_id = str(ev.get("poi_id") or key).split("#")[0]
        by_poi.setdefault(poi_id, []).append((key, ev))

    conflicts: list[dict] = []
    for poi_id, entries in by_poi.items():
        if len(entries) < 2:
            continue
        field_values: dict[str, list[tuple[str, dict]]] = {}
        for key, ev in entries:
            source = str(ev.get("source") or ev.get("source_type") or "")
            value = ev.get("value")
            fields = value if isinstance(value, dict) else {"value": value}
            for field, v in fields.items():
                field_values.setdefault(field, []).append((source, ev))
        for field, pairs in field_values.items():
            distinct = {}
            for source, ev in pairs:
                v = (ev.get("value") or {}).get(field) if isinstance(
                    ev.get("value"), dict) else ev.get("value")
                distinct.setdefault(str(v), []).append(source)
            if len(distinct) > 1:
                ranked = sorted(
                    ((v, srcs) for v, srcs in distinct.items()),
                    key=lambda item: max(
                        (SOURCE_PRIORITY.get(s.lower(), 0) for s in item[1]),
                        default=0),
                    reverse=True,
                )
                conflicts.append({
                    "poi_id": poi_id,
                    "field": field,
                    "values": [
                        {"value": _json_safe(v), "sources": srcs}
                        for v, srcs in ranked
                    ],
                    "winner": _json_safe(ranked[0][0]),
                    "rule": "source_priority",
                })
    return conflicts


def _json_safe(v):
    return v if isinstance(v, (str, int, float, bool, type(None))) else str(v)
