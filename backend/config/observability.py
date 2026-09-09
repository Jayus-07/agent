"""可观测性配置 — Trace 脱敏 / 采样 / PG Mirror。"""
import os

TRACE_PII_MASKING_ENABLED = (
    os.getenv("TRACE_PII_MASKING_ENABLED", "true").strip().lower()
    in ("1", "true", "yes")
)

TRACE_DETAIL_LEVEL = os.getenv("TRACE_DETAIL_LEVEL", "full").strip().lower()
if TRACE_DETAIL_LEVEL not in ("full", "masked", "summary"):
    TRACE_DETAIL_LEVEL = "full"

TRACE_SAMPLING_RATE = float(os.getenv("TRACE_SAMPLING_RATE", "1.0"))
TRACE_SAMPLING_RATE = max(0.0, min(1.0, TRACE_SAMPLING_RATE))

TRACE_PG_MIRROR_ENABLED = (
    os.getenv("TRACE_PG_MIRROR_ENABLED", "false").strip().lower()
    in ("1", "true", "yes")
)
