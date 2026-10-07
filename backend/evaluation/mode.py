"""评测方式的单一口径。

评测中心、Prompt 发布门禁和 GitHub 外部评测都使用同一组模式，避免
前端显示的“离线 / RAGAS”和实际 ``EvalConfig`` 参数不一致。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvaluationModeConfig:
    """一个用户可选评测方式对应的运行参数。"""

    mode: str
    live: bool
    ragas: bool
    no_ragas: bool


_MODES: dict[str, EvaluationModeConfig] = {
    # 离线只跑确定性自研指标，不调用在线生成或 RAGAS Judge。
    "offline": EvaluationModeConfig("offline", live=False, ragas=False, no_ragas=True),
    # 在线回放，只跑自研指标。
    "semantic": EvaluationModeConfig("semantic", live=True, ragas=False, no_ragas=True),
    # 在线回放，只跑 RAGAS 指标。
    "ragas": EvaluationModeConfig("ragas", live=True, ragas=True, no_ragas=False),
    # 在线回放，同时保留自研指标和 RAGAS 指标。
    "self+ragas": EvaluationModeConfig(
        "self+ragas", live=True, ragas=False, no_ragas=False
    ),
}

DEFAULT_EVALUATION_MODE = "self+ragas"


def resolve_evaluation_mode(value: str | None) -> EvaluationModeConfig:
    """解析用户输入；空值使用发布链路的双轨默认值。"""

    mode = str(value or DEFAULT_EVALUATION_MODE).strip().lower()
    try:
        return _MODES[mode]
    except KeyError as exc:
        supported = ", ".join(sorted(_MODES))
        raise ValueError(f"不支持的评测方式：{mode}（支持：{supported}）") from exc


def evaluation_mode_labels() -> dict[str, str]:
    """返回给 API/UI 的稳定显示文案。"""

    return {
        "offline": "离线自研指标",
        "semantic": "在线自研指标",
        "ragas": "RAGAS",
        "self+ragas": "在线自研 + RAGAS",
    }


__all__ = [
    "DEFAULT_EVALUATION_MODE",
    "EvaluationModeConfig",
    "evaluation_mode_labels",
    "resolve_evaluation_mode",
]
