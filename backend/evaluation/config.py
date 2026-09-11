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
    workers: int = 1              # case 级并发线程数（1=串行）
    ragas_workers: int = 4        # RAGAS 批量评估线程数
    resume: bool = True           # 断点续跑（按 run 目录 checkpoint 跳过已完成用例）
