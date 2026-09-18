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

# ── 网关访问审计日志（APISIX gateway-access-log.lua → Redis Streams → PG）──
GATEWAY_LOG_INGEST_ENABLED = (
    os.getenv("GATEWAY_LOG_INGEST_ENABLED", "true").strip().lower()
    in ("1", "true", "yes")
)
GATEWAY_LOG_RETENTION_DAYS = int(os.getenv("GATEWAY_LOG_RETENTION_DAYS", "14"))

# ── trace 留存期限（2026-09-19 合规确认结论，申请文档 §5.5）──
# 默认 14 天；敏感 doc_type（财务/客户数据/法律，抽取结果含实体与业务敏感内容）180 天
TRACE_RETENTION_DAYS_DEFAULT = int(os.getenv("TRACE_RETENTION_DAYS_DEFAULT", "14"))
TRACE_RETENTION_DAYS_SENSITIVE = int(os.getenv("TRACE_RETENTION_DAYS_SENSITIVE", "180"))
TRACE_SENSITIVE_DOC_TYPES = tuple(
    t.strip() for t in os.getenv(
        "TRACE_SENSITIVE_DOC_TYPES", "financial,customer_data,legal").split(",")
    if t.strip()
)
# 清理周期（小时）与首次延迟（分钟，避开启动期增量索引）；单批上限防长事务
TRACE_RETENTION_SWEEP_INTERVAL_HOURS = int(os.getenv("TRACE_RETENTION_SWEEP_INTERVAL_HOURS", "24"))
TRACE_RETENTION_SWEEP_FIRST_DELAY_MIN = int(os.getenv("TRACE_RETENTION_SWEEP_FIRST_DELAY_MIN", "10"))
TRACE_RETENTION_BATCH_SIZE = int(os.getenv("TRACE_RETENTION_BATCH_SIZE", "5000"))
