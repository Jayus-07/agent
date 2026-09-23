"""config/tasks.py — 异步任务编排（Celery + Agent Worker）配置。

全部来自环境变量（.env / docker-compose / K8s ConfigMap），无硬编码默认业务值。
"""
import os

from dotenv import load_dotenv

load_dotenv()

# ── Celery 基础 ──────────────────────────────────────────────
# broker / backend 分库：/1 队列（broker），/2 结果（result backend），
# 均与业务缓存 (/0) 隔离，避免键冲突与监控口径混淆。
# 容器化部署由 docker-compose 显式注入 redis://redis:6379/{1,2}（优先级更高）。
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/1")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/2")

# 任务超时（秒）：soft limit 触发 SoftTimeLimitExceeded → 任务 FAILED；
# hard limit = soft + 30s 兜底杀进程
CELERY_TASK_TIMEOUT = int(os.getenv("CELERY_TASK_TIMEOUT", "1800"))
CELERY_HARD_TASK_TIMEOUT = CELERY_TASK_TIMEOUT + 30

# 失败自动重试次数（任务级，从最近 checkpoint 续跑）
CELERY_MAX_RETRIES = int(os.getenv("CELERY_MAX_RETRIES", "3"))

# 重试退避（秒）
CELERY_RETRY_BACKOFF = int(os.getenv("CELERY_RETRY_BACKOFF", "5"))
CELERY_RETRY_BACKOFF_MAX = int(os.getenv("CELERY_RETRY_BACKOFF_MAX", "120"))

# ── 集中 RetryPolicy（Phase2 Step2）────────────────────────
# Retry 只负责"进程活着 + 已知临时错误"；Worker 死亡走 Recovery（Step1），
# 重试耗尽/永久错误走 Failure。参数沿用既有 env 源（CELERY_MAX_RETRIES /
# CELERY_RETRY_BACKOFF / CELERY_RETRY_BACKOFF_MAX），per-workflow 只允许
# 覆盖延迟形态，max_retries 统一，避免散落 decorator。
TASK_RETRY_INITIAL_DELAY = int(os.getenv("TASK_RETRY_INITIAL_DELAY",
                                         str(CELERY_RETRY_BACKOFF)))
TASK_RETRY_MAX_DELAY = int(os.getenv("TASK_RETRY_MAX_DELAY",
                                     str(CELERY_RETRY_BACKOFF_MAX)))
TASK_RETRY_JITTER = os.getenv("TASK_RETRY_JITTER", "true").lower() != "false"

# 单 Worker 并发槽（prefetch=1 + 该并发 = 公平排队）
# Phase2 Step5：per-workload 并发独立配置（compose command 消费）。
# CELERY_WORKER_CONCURRENCY 保留为 agent-worker 的兼容回退源。
CELERY_WORKER_CONCURRENCY = int(os.getenv("CELERY_WORKER_CONCURRENCY", "4"))

# per-workload 并发（依据见 Step5 验收报告 §参数矩阵）：
# - agent：IO-bound（LLM API/RAG/SQL），latency 优先
# - rag_index：CPU/RAM 重（文档解析 + 本地 embedding/rerank 懒加载，每
#   prefork 子进程各持一份模型内存），保守 2 防 RAM/VRAM 翻倍
# - metadata_shadow：沿用既有默认 2
# - maintenance：原子幂等扫描（sweep/zombie CAS），并行无益且 1 并发保证
#   不重叠执行；准时性由专属 worker 保证
# - report：低频长任务（每日 qa_daily_report），隔离即可无需吞吐
AGENT_WORKER_CONCURRENCY = int(
    os.getenv("AGENT_WORKER_CONCURRENCY", str(CELERY_WORKER_CONCURRENCY)))
RAG_INDEX_WORKER_CONCURRENCY = int(os.getenv("RAG_INDEX_WORKER_CONCURRENCY", "2"))
METADATA_SHADOW_WORKER_CONCURRENCY = int(
    os.getenv("METADATA_SHADOW_WORKER_CONCURRENCY", "2"))
MAINTENANCE_WORKER_CONCURRENCY = int(os.getenv("MAINTENANCE_WORKER_CONCURRENCY", "1"))
REPORT_WORKER_CONCURRENCY = int(os.getenv("REPORT_WORKER_CONCURRENCY", "1"))

# 长任务 worker 的 prefetch 基线：1（防止单 worker 进程囤积消息造成
# head-of-line blocking；全局显式配置于 celery_app.worker_prefetch_multiplier）

# ── 任务运行时 ──────────────────────────────────────────────
# Redis 控制标志/事件通道前缀（与 infra.redis REDIS_KEY_PREFIX 同源语义）
TASK_KEY_PREFIX = os.getenv("TASK_KEY_PREFIX", "agent:task:")

# 取消/暂停标志 TTL（秒）：超时自动过期，防止僵尸标志永久阻塞新任务
TASK_FLAG_TTL = int(os.getenv("TASK_FLAG_TTL", "3600"))

# SSE 事件通道前缀（pub/sub）：TASK_KEY_PREFIX + "events:" + task_id
TASK_EVENT_CHANNEL = TASK_KEY_PREFIX + "events:"

# ── 僵尸任务收尸（2026-09-21 高并发审查 B5；Phase2 Step1 起为最终兜底）──
# 判定口径：RUNNING 且 updated_at 停更超过阈值 = Worker 已死（崩溃/消息丢失）。
# 默认 = Celery 硬超时 + 60s 余量——Phase2 起心跳线程周期续租会同步刷新
# updated_at，活任务不会被误杀；主恢复路径是 stale recovery sweep（租约过期
# ~3min 重投），本收尸只在 sweep 链路失效时兜底。
TASK_ZOMBIE_THRESHOLD_SECONDS = int(
    os.getenv("TASK_ZOMBIE_THRESHOLD_SECONDS", str(CELERY_HARD_TASK_TIMEOUT + 60))
)
# reconcile 扫描间隔（秒），beat 调度
TASK_ZOMBIE_RECONCILE_INTERVAL = int(
    os.getenv("TASK_ZOMBIE_RECONCILE_INTERVAL", "300")
)

# 任务列表默认分页
TASKS_LIST_DEFAULT_LIMIT = int(os.getenv("TASKS_LIST_DEFAULT_LIMIT", "20"))

# ── 执行租约与 Stale Recovery（Phase2 Step1）────────────────
# 设计：lease_expires_at 是 stale 判定唯一权威；Worker 侧心跳线程周期续租。
# 推导依据（不机械取值）：
# - heartbeat interval(15s) << TTL(120s)：容忍连续 8 次丢跳（GIL 长占/DB 抖动），
#   实测单次续租 DB 往返 ~ms 级，15s 间隔开销可忽略
# - TTL 必须 > rag_index 长原子节点内最坏 GIL 停顿（嵌入为 torch C 段会放 GIL，
#   纯 Python 段秒级），120s 足够安全；恢复延迟 ≈ TTL + grace + sweep 间隔 ≈ 2.5~3min
TASK_LEASE_TTL_SECONDS = int(os.getenv("TASK_LEASE_TTL_SECONDS", "120"))
TASK_LEASE_HEARTBEAT_INTERVAL = int(os.getenv("TASK_LEASE_HEARTBEAT_INTERVAL", "15"))
# sweep 比租约过期多等一个 grace，避免与一次在途续租竞态
TASK_RECOVERY_SWEEP_INTERVAL = int(os.getenv("TASK_RECOVERY_SWEEP_INTERVAL", "30"))
TASK_RECOVERY_GRACE_SECONDS = int(os.getenv("TASK_RECOVERY_GRACE_SECONDS", "15"))
# 同一任务最多自动恢复次数；超过后由 sweep 直接终态 FAILED(ZOMBIE_RECONCILED)
TASK_MAX_LEASE_RECOVERIES = int(os.getenv("TASK_MAX_LEASE_RECOVERIES", "3"))
# 自动恢复时效上限：stale 行 updated_at 停更超过该窗口（部署过渡期的远古
# RUNNING 垃圾行、sweep 长期失效残留）不做自动重投——避免"复活"几天前的
# 旧任务；这些行由 zombie reconcile（最终兜底）收尸为 FAILED 可重试。
TASK_RECOVERY_MAX_AGE_SECONDS = int(os.getenv("TASK_RECOVERY_MAX_AGE_SECONDS", "86400"))

# Redis broker visibility_timeout（Phase2 Step1 显式化，原为 kombu 默认 3600s）：
# 必须 > 单条消息"取出到 ack"的最长未 ack 时长上界 = hard limit 1830s
#（soft 1800s 超时处理器仍需落库+广播后才 ack，hard 1830s 杀进程后消息本就该重投）
# + 安全余量 120s（超时处理器写库/广播 + 心跳 DB 往返的余量）= 1950s。
# 不得为"恢复快"压到活任务时长以内——Runtime 恢复靠 stale sweeper（~3min），
# broker 重投只是第二层兜底（sweeper 不可用时的保险），晚到消息由显式状态短路 NO-OP。
CELERY_BROKER_VISIBILITY_TIMEOUT = int(
    os.getenv("CELERY_BROKER_VISIBILITY_TIMEOUT", "1950"))

# ── 队列名（Phase2 Step3：物理队列名唯一在此与 queue_router 消费）────
# agent 主队列：interactive_agent workload 的物理队列（agent-worker 独占消费）。
CELERY_AGENT_QUEUE = os.getenv("CELERY_AGENT_QUEUE", "agent")

# ── RAG 上传索引队列化（固定启用，无开关）──────────────────
# 上传后索引任务固定投递 Celery（rag_index 队列）由 Worker 执行，
# SSE 经 Redis 进度镜像跨进程轮询消费；broker 不可达入队失败时
# 自动回退进程内索引，上传可用性不受影响。
CELERY_RAG_INDEX_QUEUE = os.getenv("CELERY_RAG_INDEX_QUEUE", "rag_index")

# 元数据影子评估单独队列：只承载 job_id，允许独立扩缩容；影子失败不得
# 占满 rag_index worker。默认沿用索引任务的可靠性边界，但单次更短。
CELERY_METADATA_SHADOW_QUEUE = os.getenv(
    "CELERY_METADATA_SHADOW_QUEUE", "rag_metadata_shadow"
)

# Phase2 Step5：maintenance / report 独立物理队列（各自专属 worker 消费），
# 不再与 interactive_agent 共享 agent 队列——beat 周期扫描不再排在用户
# 任务 backlog 之后，报表长任务不再占用 agent 并发槽。
CELERY_MAINTENANCE_QUEUE = os.getenv("CELERY_MAINTENANCE_QUEUE", "maintenance")
CELERY_REPORT_QUEUE = os.getenv("CELERY_REPORT_QUEUE", "report")

# 未登记 workflow 的路由降级队列（Phase2 Step3 queue_router 消费）。
# 默认空 = fail-closed（未登记直接拒绝入队）；显式设为物理队列名时
# 未登记路由降级 legacy_fallback 并必打 warning（可审计的逃生门）。
QUEUE_ROUTING_UNKNOWN_FALLBACK = os.getenv("QUEUE_ROUTING_UNKNOWN_FALLBACK", "")
CELERY_METADATA_SHADOW_TASK_TIMEOUT = int(
    os.getenv("CELERY_METADATA_SHADOW_TASK_TIMEOUT", "120")
)
CELERY_METADATA_SHADOW_MAX_RETRIES = int(
    os.getenv("CELERY_METADATA_SHADOW_MAX_RETRIES", str(CELERY_MAX_RETRIES))
)

# ── Admission Control（Phase2 Step4：并发准入）────────────────
# 语义：限制"同时执行的 RUNNING 任务数"（acquire 在 Worker 执行起点、
# lease 认领之后；排队/倒计时等待不占 admission 槽位，无容量虚占）。
# token = 本次 execution 占用一个 admission slot 的证明，与执行租约
# （execution lease，写权 fencing）概念分离；Token TTL 对齐租约 TTL，
# 由 lease heartbeat 顺带续期，Worker crash 后随 TTL 自动回收容量。
TASK_ADMISSION_ENABLED = os.getenv("TASK_ADMISSION_ENABLED", "true").strip().lower() in (
    "1", "true", "yes")

# Admission store（Redis）自身不可用时的行为：
#   closed = 拒绝准入（fail-closed，走 defer 重投，容量保护不被绕过）
#   open   = 放行 + 高优告警（break-glass，须显式配置）
TASK_ADMISSION_FAIL_MODE = os.getenv("TASK_ADMISSION_FAIL_MODE", "closed").strip().lower()
if TASK_ADMISSION_FAIL_MODE not in ("closed", "open"):
    raise ValueError(f"TASK_ADMISSION_FAIL_MODE 非法: {TASK_ADMISSION_FAIL_MODE!r}"
                     "（仅支持 closed/open）")


def _int_or_none(name: str, default: str) -> int | None:
    """admission 限额解析：未设/空 = None（该层不启用限制）；负数 = 配置错误 fail-fast。

    显式 0 = 全拒（极端闸刀，立即拒绝所有任务）——与 None 的"不限制"语义
    严格区分，不允许 silently unlimited。
    """
    raw = os.getenv(name, default).strip()
    if not raw:
        return None
    value = int(raw)  # 非整数直接抛 ValueError（fail-fast）
    if value < 0:
        raise ValueError(f"{name} 非法: {value}（负数不合法；0=全拒，未设=不限）")
    return value


TASK_ADMISSION_GLOBAL_LIMIT = _int_or_none("TASK_ADMISSION_GLOBAL_LIMIT", "100")
TASK_ADMISSION_TENANT_LIMIT = _int_or_none("TASK_ADMISSION_TENANT_LIMIT", "50")
TASK_ADMISSION_USER_LIMIT = _int_or_none("TASK_ADMISSION_USER_LIMIT", "10")
# per-workflow 限额（workflow=graph_name 口径，非物理队列名；QueueRouter
# 负责 workflow→队列，admission 只消费 workflow 字符串本身）
TASK_ADMISSION_WORKFLOW_LIMITS_RAW = os.getenv(
    "TASK_ADMISSION_WORKFLOW_LIMITS", "main=50,rag_index=20")


def _parse_workflow_limits(raw: str) -> dict[str, int | None]:
    """main=50,rag_index=20 形态解析；条目值为空 = 该 workflow 不限。"""
    limits: dict[str, int | None] = {}
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            raise ValueError(
                f"TASK_ADMISSION_WORKFLOW_LIMITS 条目非法: {item!r}（应为 workflow=limit）")
        wf, _, val = item.partition("=")
        wf = wf.strip()
        val = val.strip()
        if not wf:
            raise ValueError("TASK_ADMISSION_WORKFLOW_LIMITS workflow 名为空")
        if not val:
            limits[wf] = None
            continue
        limit = int(val)  # 非整数 fail-fast
        if limit < 0:
            raise ValueError(f"TASK_ADMISSION_WORKFLOW_LIMITS {wf} 非法: {limit}")
        limits[wf] = limit
    return limits


TASK_ADMISSION_WORKFLOW_LIMITS = _parse_workflow_limits(
    TASK_ADMISSION_WORKFLOW_LIMITS_RAW)

# Token TTL（秒）：对齐执行租约 TTL——lease heartbeat 每 15s 续租时顺带续
# token；Worker crash 后 token 随 TTL 过期自动释放容量（无需 finally）。
TASK_ADMISSION_TOKEN_TTL_SECONDS = int(
    os.getenv("TASK_ADMISSION_TOKEN_TTL_SECONDS", str(TASK_LEASE_TTL_SECONDS)))

# 满载 defer（延迟准入）退避：拒绝后任务保持 PENDING，消息按 countdown
# 重投轮询；bounded + jitter，budget 耗尽落 FAILED(admission_rejected) 可重试。
TASK_ADMISSION_DEFER_INITIAL_DELAY = int(
    os.getenv("TASK_ADMISSION_DEFER_INITIAL_DELAY", "10"))
TASK_ADMISSION_DEFER_MAX_DELAY = int(
    os.getenv("TASK_ADMISSION_DEFER_MAX_DELAY", "60"))
TASK_ADMISSION_DEFER_MAX_COUNT = int(
    os.getenv("TASK_ADMISSION_DEFER_MAX_COUNT", "60"))
TASK_ADMISSION_DEFER_JITTER = os.getenv(
    "TASK_ADMISSION_DEFER_JITTER", "true").strip().lower() in ("1", "true", "yes")

# admission key 前缀（挂在任务控制面 TASK_KEY_PREFIX 之下，与 cancel/pause
# 标志、事件通道同域；计数/令牌/defer 计数均在此命名空间内）
TASK_ADMISSION_KEY_PREFIX = TASK_KEY_PREFIX + "admission:"
