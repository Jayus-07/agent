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

# 单 Worker 并发槽（prefetch=1 + 该并发 = 公平排队）
CELERY_WORKER_CONCURRENCY = int(os.getenv("CELERY_WORKER_CONCURRENCY", "4"))

# ── 任务运行时 ──────────────────────────────────────────────
# Redis 控制标志/事件通道前缀（与 infra.redis REDIS_KEY_PREFIX 同源语义）
TASK_KEY_PREFIX = os.getenv("TASK_KEY_PREFIX", "agent:task:")

# 取消/暂停标志 TTL（秒）：超时自动过期，防止僵尸标志永久阻塞新任务
TASK_FLAG_TTL = int(os.getenv("TASK_FLAG_TTL", "3600"))

# SSE 事件通道前缀（pub/sub）：TASK_KEY_PREFIX + "events:" + task_id
TASK_EVENT_CHANNEL = TASK_KEY_PREFIX + "events:"

# 任务列表默认分页
TASKS_LIST_DEFAULT_LIMIT = int(os.getenv("TASKS_LIST_DEFAULT_LIMIT", "20"))

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
CELERY_METADATA_SHADOW_TASK_TIMEOUT = int(
    os.getenv("CELERY_METADATA_SHADOW_TASK_TIMEOUT", "120")
)
CELERY_METADATA_SHADOW_MAX_RETRIES = int(
    os.getenv("CELERY_METADATA_SHADOW_MAX_RETRIES", str(CELERY_MAX_RETRIES))
)
