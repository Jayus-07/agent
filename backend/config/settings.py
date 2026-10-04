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
# worker 数 = SSE 并发上限；第 N+1 路排队等待空闲 worker；0 = 按 CPU 自适应。
# sizing 口径（P1-3，2026-09-30）：workers ≈ 目标 SSE 并发数（每路流全程
# 占用 1 个 worker 线程直到终帧，不是短任务）；排队即背压信号——
# chat_sse_executor_active 持平 max_workers 且 chat_sse_executor_wait_seconds
# 分布右移 = 容量不足，应上调本值或前端限流，而不是让它表现为首字延迟升高。
CHAT_SSE_MAX_WORKERS = int(os.getenv("CHAT_SSE_MAX_WORKERS", "0")) or max(
    4, (os.cpu_count() or 4) * 2
)
# 队列容量 1024 → 100Hz 输出下可撑 ~10s；超出走 backpressure（记 metric + set stop）
CHAT_SSE_QUEUE_MAXSIZE = int(os.getenv("CHAT_SSE_QUEUE_MAXSIZE", "1024"))
# consumer 阻塞拉取超时（秒）→ CPU 占用从 100Hz 轮询降到 ~0.5Hz
CHAT_SSE_GET_TIMEOUT = float(os.getenv("CHAT_SSE_GET_TIMEOUT", "0.5"))

# ── SSE 断线恢复（F2 Resume Protocol，2026-09-25）──
# 客户端断开只脱离订阅，服务端跑完并缓冲事件供 resume 重放（有界）。
# 缓冲超限丢最老并置 gap；resume 命中 gap / 进程重启 → STREAM_NOT_RESUMABLE。
SSE_RESUME_BUFFER_MAX_EVENTS = int(os.getenv("SSE_RESUME_BUFFER_MAX_EVENTS", "4096"))
SSE_RESUME_FINISHED_TTL_SECONDS = float(
    os.getenv("SSE_RESUME_FINISHED_TTL_SECONDS", "600"))
# 客户端最多重连尝试次数（前端同口径，双端常量一致）
SSE_RESUME_MAX_ATTEMPTS = int(os.getenv("SSE_RESUME_MAX_ATTEMPTS", "3"))

# ── Tool 失败治理（core/tool_runtime，2026-09-22）──
# 请求级 Deadline（在线交互请求专用；Celery 后台任务不走图链路，不受此约束）
# business_deadline 30s = 请求端到端上限；workflow 25s = 图执行可用预算；
# reporter 5s = 给 Reporter 生成回答保留的尾款
BUSINESS_DEADLINE_S = float(os.getenv("BUSINESS_DEADLINE_S", "30"))
WORKFLOW_DEADLINE_S = float(os.getenv("WORKFLOW_DEADLINE_S", "25"))
REPORTER_RESERVED_S = float(os.getenv("REPORTER_RESERVED_S", "5"))
# ── 预算分段（P1 阶段 2，2026-09-22）：selector / tool execution / reserve ──
# selector（tool_selector FC 等 LLM 选择动作）最多消耗总预算的该比例，
# 超过即中止/降级，不得侵占工具执行保底预算。
TOOL_SELECTOR_BUDGET_RATIO = float(os.getenv("TOOL_SELECTOR_BUDGET_RATIO", "0.35"))
# 工具执行保底：进入 tool execution 前剩余预算必须 ≥ 该比例 × 总预算，
# 否则按策略降级（防止 selector/规划把工具执行饿死）。
MIN_TOOL_EXECUTION_RATIO = float(os.getenv("MIN_TOOL_EXECUTION_RATIO", "0.30"))
# 收尾预留：结果整合 / SSE 收尾 / 最终响应至少保留该比例 × 总预算。
RESERVE_BUDGET_RATIO = float(os.getenv("RESERVE_BUDGET_RATIO", "0.10"))
# Tool 治理总开关（false 时 BaseSkill 回退旧执行循环，仅作紧急回滚用）
TOOL_RUNTIME_ENABLED = os.getenv("TOOL_RUNTIME_ENABLED", "true").strip().lower() in ("1", "true", "yes")
# 熔断器全局默认（可被 TOOL_POLICY_JSON 按 tool 覆盖）
TOOL_CB_FAILURE_THRESHOLD = int(os.getenv("TOOL_CB_FAILURE_THRESHOLD", "5"))
TOOL_CB_RECOVERY_SECONDS = float(os.getenv("TOOL_CB_RECOVERY_SECONDS", "30"))
# 隔离舱拿不到槽位的最长等待（毫秒），超过快速失败 TOOL_BUSY
TOOL_BULKHEAD_WAIT_MS = float(os.getenv("TOOL_BULKHEAD_WAIT_MS", "300"))
# 按工具覆盖策略：JSON dict，如 {"rag.search": {"timeout_ms": 15000}}（见 core/tool_runtime/policy.py）
TOOL_POLICY_JSON = os.getenv("TOOL_POLICY_JSON", "")

# ── GitHub Prompt 评测轮询（发布门禁）────────────────────────
# GitHub Actions 不回调业务后端；Celery worker 按此间隔拉取 Run 和 Artifact。
PROMPT_EVAL_EXECUTOR = os.getenv("PROMPT_EVAL_EXECUTOR", "github").strip().lower()
PROMPT_EVAL_POLL_INTERVAL_SECONDS = int(
    os.getenv("PROMPT_EVAL_POLL_INTERVAL_SECONDS", "15")
)
PROMPT_EVAL_POLL_MAX_ATTEMPTS = int(
    os.getenv("PROMPT_EVAL_POLL_MAX_ATTEMPTS", "120")
)

# ── Prompt 发布双门禁灰度开关（GATE-03/11，2026-10-04）────────
# 取值 off / audit / enforce：audit=计算并落痕不拦截（上线初期跑两周），
# enforce=门禁失败拒绝发布（fail-closed 409），off=完全跳过。
# baseline_unavailable（无基线）在两种模式下都只记原因不拦截（REG-10：
# 显式口径，不伪造 delta=0，也不把首版发布堵死）。
PROMPT_RELEASE_REGRESSION_GATE_ENABLED = (
    os.getenv("PROMPT_RELEASE_REGRESSION_GATE_ENABLED", "audit").strip().lower()
)
PROMPT_RELEASE_RAGAS_GATE_ENABLED = (
    os.getenv("PROMPT_RELEASE_RAGAS_GATE_ENABLED", "audit").strip().lower()
)
