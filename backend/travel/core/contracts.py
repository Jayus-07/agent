"""travel/core/contracts.py — 跨 Agent 统一契约（旅游域统一契约 冻结）

Phase 1 只立契约不接线：现有 Agent/专家代码不经此文件，Phase 3 起逐个
接入。字段口径以 docs/travel-domain-design-v5.md 为准，
冻结后只向后兼容演进（新增可选字段），破坏性变更走版本化。
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime
from typing import Any


class SourceType(str, enum.Enum):
    """事实来源类型（Evidence.source_type，travel-domain-design-v5.md §13 冻结口径）。

    SEED 是对四类基线（LIVE/RAG/CACHE/ESTIMATE）的诚实扩展：种子库的
    坐标/营业时间/票价是自声明示例值，既非实时数据也非估算——冒充任何
    一类都会虚高信任分（数据真实性 > 内容丰富）。
    """

    LIVE = "live"
    RAG = "rag"
    CACHE = "cache"
    SEED = "seed"
    ESTIMATE = "estimate"


# source_type → confidence 基线（travel-domain-design-v5.md §13 冻结表）。
CONFIDENCE_BASELINE: dict[SourceType, float] = {
    SourceType.LIVE: 0.95,
    SourceType.CACHE: 0.85,
    SourceType.RAG: 0.7,
    SourceType.SEED: 0.5,
    SourceType.ESTIMATE: 0.5,
}

# 缺 verified_at 的事实 confidence 上限：无核实时点就不允许高置信。
UNVERIFIED_CONFIDENCE_CAP = 0.5


@dataclass(frozen=True)
class TravelContext:
    """工具统一信封的追踪上下文（travel-domain-design-v5.md §9）。

    一次构造全链透传；request_id 对齐平台 request_context，trace_id
    对齐 SSE/trace 链路。frozen 防止中途篡改血缘。
    """

    request_id: str = ""
    tenant_id: str = ""
    user_id: str = ""
    trace_id: str = ""


@dataclass(frozen=True)
class Evidence:
    """统一事实可信模型（travel-domain-design-v5.md §13 冻结）。

    所有进入 state 的旅游事实（POI/天气/知识/预算分项）经此包装，
    reporter 与 assistant 按 evidence_level() 唯一口径渲染；validator 的
    SOURCE_STALE 检查消费本结构字段（保持零 IO）。provider 层零改动，
    Evidence 在域层由 status × Freshness 组装。

    fail-fast 两条不变量（构造期校验）：
      1. confidence 不超过 source_type 基线；
      2. 缺 verified_at 时 confidence 不超过 UNVERIFIED_CONFIDENCE_CAP。
    """

    fact_id: str
    value: Any
    source: str
    source_type: SourceType
    confidence: float
    verified_at: datetime | None = None
    expire_at: datetime | None = None

    def __post_init__(self) -> None:
        baseline = CONFIDENCE_BASELINE[self.source_type]
        if not 0.0 <= self.confidence <= baseline:
            raise ValueError(
                f"confidence={self.confidence} 超出 source_type="
                f"{self.source_type.value} 基线 {baseline}"
            )
        if self.verified_at is None and self.confidence > UNVERIFIED_CONFIDENCE_CAP:
            raise ValueError(
                f"缺 verified_at 的事实 confidence 不得超过 "
                f"{UNVERIFIED_CONFIDENCE_CAP}（got {self.confidence}）"
            )

    def evidence_level(self) -> str:
        """三档消费口径（travel-domain-design-v5.md §13 冻结，reporter/assistant 唯一渲染依据）。

        trusted = LIVE 且未过期；may_change = CACHE/已过期/stale；
        needs_confirmation = SEED/ESTIMATE/低置信。
        """
        now = datetime.now().astimezone()
        expired = self.expire_at is not None and self.expire_at < now
        if self.source_type is SourceType.LIVE and not expired:
            return "trusted"
        if self.source_type in (SourceType.SEED, SourceType.ESTIMATE):
            return "needs_confirmation"
        if self.confidence < 0.6:
            return "needs_confirmation"
        return "may_change"
