"""LLM 结构化输出解析失败指标（2026-10-06 提示词治理批次 C）。

为什么独立成模块：observability/metrics.py 当时混有并行会话未提交改动，
为避免混线，解析失败计数单独落文件；后续如需并入 metrics.py 请连同
消费方（json_extractor 及 4 个 source 接入点）一起迁移。

口径：source = prompt key（如 planner.system）或模块名——解析失败率突然
上升 = 对应提示词/模型劣化的最早信号。只在策略链**全失败**时计 1 次，
任一层成功不计。
"""
from prometheus_client import Counter

llm_json_parse_fail_total = Counter(
    "llm_json_parse_fail_total",
    "LLM structured output JSON parse failures (all strategy layers failed)",
    labelnames=("source",),
)


def record_json_parse_fail(source: str) -> None:
    """解析失败计数（软失败：指标异常不得影响解析语义）。"""
    try:
        llm_json_parse_fail_total.labels(source or "unknown").inc()
    except Exception:  # noqa: BLE001 — 指标旁路绝不抛
        pass
