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
CELERY_WORKER_CONCURRENCY = int(os.getenv("CELERY_WORKER_CONCURRENCY", "4"))

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
# agent 主队列：interactive_agent workload 的物理队列（beat maintenance/
# report 暂共享，Step5 拆分时只改 queue_router 的 logical→physical 映射）。
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
