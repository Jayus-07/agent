"""config/redis.py — Redis 配置。"""
import os

from dotenv import load_dotenv

load_dotenv()

# 是否启用 Redis（默认关闭，开发环境无需安装 Redis）
REDIS_ENABLED = os.getenv("REDIS_ENABLED", "false").strip().lower() in ("1", "true", "yes")

# Redis 连接 URL
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# 认证控制面 Redis（JWT 黑名单与会话闸）。默认回退到业务 Redis，兼容未
# 配置独立实例的开发环境；生产 compose 显式注入 auth-redis，避免缓存实例
# 故障导致网关鉴权链路整体不可用。
AUTH_REDIS_ENABLED = os.getenv(
    "AUTH_REDIS_ENABLED", "true" if REDIS_ENABLED else "false"
).strip().lower() in ("1", "true", "yes", "on")
AUTH_REDIS_URL = os.getenv("AUTH_REDIS_URL", REDIS_URL)

# Key 前缀（多实例/多项目共用同一 Redis 时隔离）
REDIS_KEY_PREFIX = os.getenv("REDIS_KEY_PREFIX", "agent:")

# 连接池大小
REDIS_MAX_CONNECTIONS = int(os.getenv("REDIS_MAX_CONNECTIONS", "20"))

# Socket 超时（秒）
REDIS_SOCKET_TIMEOUT = int(os.getenv("REDIS_SOCKET_TIMEOUT", "5"))

# Prompt Runtime 热更新（Phase 2）。Redis 只做跨进程传播信号，DB epoch 是事实源。
PROMPT_HOT_RELOAD_ENABLED = os.getenv(
    "PROMPT_HOT_RELOAD_ENABLED", "true"
).strip().lower() in ("1", "true", "yes", "on")
PROMPT_EPOCH_CHECK_TTL = float(os.getenv("PROMPT_EPOCH_CHECK_TTL", "10"))
PROMPT_PUBSUB_CHANNEL = os.getenv(
    "PROMPT_PUBSUB_CHANNEL", f"{REDIS_KEY_PREFIX}prompt:changed"
)
PROMPT_RUNTIME_HEARTBEAT_TTL = int(
    os.getenv("PROMPT_RUNTIME_HEARTBEAT_TTL", "30")
)
PROMPT_RUNTIME_HEARTBEAT_INTERVAL = float(
    os.getenv("PROMPT_RUNTIME_HEARTBEAT_INTERVAL", "10")
)

# ── 熔断状态跨进程共享（审查 #13 / docs/architecture/domain-service-map.md#下游熔断）──
# 开 = fail 计数与 OPEN 广播走 Redis（多 worker/多副本下阈值不再放大 N 倍）；
# Redis 不可用时自动退回进程内状态（方向安全：等于现状行为）。
# 走缓存实例（REDIS_URL，allkeys-lru）：键被驱逐 = 短暂退回本地计数，可接受。
CIRCUIT_BREAKER_SHARED_ENABLED = (
    os.getenv("CIRCUIT_BREAKER_SHARED_ENABLED", "false").strip().lower()
    in ("1", "true", "yes", "on")
)
# 其它实例 OPEN 广播的轮询间隔（秒）；热路径不查，仅此周期一次 GET
CIRCUIT_BREAKER_SHARED_POLL_SECONDS = float(
    os.getenv("CIRCUIT_BREAKER_SHARED_POLL_SECONDS", "5")
)
