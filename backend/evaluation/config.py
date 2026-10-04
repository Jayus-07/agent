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
    kb_id: str | None = None             # RAG suite 解析出的实际 KB
    fixture_set: str | None = None       # RAG 运行时语料范围
    dataset_version: str | None = None   # suite/canonical 版本快照
    run_id: str | None = None            # 断点续跑标识；启动时生成或由 CLI 指定
    # RUN-02：对已有终态的 run_id 再次启动时的显式覆盖（全量重跑，忽略 checkpoint）
    force_rerun: bool = False
    # C9-2/P0-03：严格字段校验（缺 question/expected_answer/ground_truth 阻止
    # 启动并列出缺失清单）。默认关保持现降级语义；发布评测强制 True。
    strict_fields: bool = False
    semantic_thresholds: dict[str, float] | None = None
    regression: bool = False
    promote_baseline: bool = False
    workers: int = 1              # case 级并发线程数（1=串行）
    ragas_workers: int = 4        # RAGAS 批量评估线程数
    resume: bool = True           # 断点续跑（按 run 目录 checkpoint 跳过已完成用例）
    multiquery: bool = False      # 评测检索链套生产 MultiQuery 层（口径对齐线上）
    full_trace: bool = False      # per_case 保留完整 page_content/span（默认瘦身）
    prompt_versions: dict[str, int | str | None] = {}  # 候选 Prompt 版本快照
    release_id: str | None = None  # 关联的 Prompt 发布记录
