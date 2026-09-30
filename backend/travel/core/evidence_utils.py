"""travel/core/evidence_utils.py — Evidence 组装/序列化辅助（Phase 4）

Evidence 模型**唯一归属 core/contracts.py**（v4 §4 冻结契约，禁第二套证据
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
    """按冻结基线构造 Evidence（v4 §4 confidence 基线表的应用出口）。

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
