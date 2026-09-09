"""评测配置 — EvalConfig 模型。"""
from __future__ import annotations

from pydantic import BaseModel


class EvalConfig(BaseModel):
    """单次评估运行的完整配置。"""

    module: str = "all"
    live: bool = False
    smoke: bool = False
    judge: bool = False
    tier: str = "all"
    ragas: bool = False
    no_ragas: bool = False
    ragas_level: str = "standard"
    dataset: str | None = None
    selection: str | None = None
    semantic_thresholds: dict[str, float] | None = None
    regression: bool = False
    promote_baseline: bool = False
