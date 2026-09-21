"""config/settings.py — 通用配置

跨模块共用的运行时配置（超时、日志等级等）。
"""
import os

from dotenv import load_dotenv

load_dotenv()

# 日志配置（基础级别）
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE = os.getenv("LOG_FILE", "rag_system.log")

# 通用超时（秒）
# retrieval/pipeline.py 用作软超时告警阈值（elapsed > 0.8 * OVERALL_REQUEST_TIMEOUT 触发告警）
OVERALL_REQUEST_TIMEOUT = int(os.getenv("OVERALL_REQUEST_TIMEOUT", "60"))

# ── SSE 流式对话运行时配置（P1-14：自 chat.py 收敛到 config）──
# worker 数 = SSE 并发上限；第 N+1 路排队等待空闲 worker；0 = 按 CPU 自适应
CHAT_SSE_MAX_WORKERS = int(os.getenv("CHAT_SSE_MAX_WORKERS", "0")) or max(
    4, (os.cpu_count() or 4) * 2
)
# 队列容量 1024 → 100Hz 输出下可撑 ~10s；超出走 backpressure（记 metric + set stop）
CHAT_SSE_QUEUE_MAXSIZE = int(os.getenv("CHAT_SSE_QUEUE_MAXSIZE", "1024"))
# consumer 阻塞拉取超时（秒）→ CPU 占用从 100Hz 轮询降到 ~0.5Hz
CHAT_SSE_GET_TIMEOUT = float(os.getenv("CHAT_SSE_GET_TIMEOUT", "0.5"))

# ── Tool 失败治理（core/tool_runtime，2026-09-22）──
# 请求级 Deadline（在线交互请求专用；Celery 后台任务不走图链路，不受此约束）
# business_deadline 30s = 请求端到端上限；workflow 25s = 图执行可用预算；
# reporter 5s = 给 Reporter 生成回答保留的尾款
BUSINESS_DEADLINE_S = float(os.getenv("BUSINESS_DEADLINE_S", "30"))
WORKFLOW_DEADLINE_S = float(os.getenv("WORKFLOW_DEADLINE_S", "25"))
REPORTER_RESERVED_S = float(os.getenv("REPORTER_RESERVED_S", "5"))
# Tool 治理总开关（false 时 BaseSkill 回退旧执行循环，仅作紧急回滚用）
TOOL_RUNTIME_ENABLED = os.getenv("TOOL_RUNTIME_ENABLED", "true").strip().lower() in ("1", "true", "yes")
# 熔断器全局默认（可被 TOOL_POLICY_JSON 按 tool 覆盖）
TOOL_CB_FAILURE_THRESHOLD = int(os.getenv("TOOL_CB_FAILURE_THRESHOLD", "5"))
TOOL_CB_RECOVERY_SECONDS = float(os.getenv("TOOL_CB_RECOVERY_SECONDS", "30"))
# 隔离舱拿不到槽位的最长等待（毫秒），超过快速失败 TOOL_BUSY
TOOL_BULKHEAD_WAIT_MS = float(os.getenv("TOOL_BULKHEAD_WAIT_MS", "300"))
# 按工具覆盖策略：JSON dict，如 {"rag.search": {"timeout_ms": 15000}}（见 core/tool_runtime/policy.py）
TOOL_POLICY_JSON = os.getenv("TOOL_POLICY_JSON", "")