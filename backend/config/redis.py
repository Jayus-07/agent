"""config/redis.py — Redis 配置。"""
import os

from dotenv import load_dotenv

load_dotenv()

# 是否启用 Redis（默认关闭，开发环境无需安装 Redis）
REDIS_ENABLED = os.getenv("REDIS_ENABLED", "false").strip().lower() in ("1", "true", "yes")

# Redis 连接 URL
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Key 前缀（多实例/多项目共用同一 Redis 时隔离）
REDIS_KEY_PREFIX = os.getenv("REDIS_KEY_PREFIX", "agent:")

# 连接池大小
REDIS_MAX_CONNECTIONS = int(os.getenv("REDIS_MAX_CONNECTIONS", "20"))

# Socket 超时（秒）
REDIS_SOCKET_TIMEOUT = int(os.getenv("REDIS_SOCKET_TIMEOUT", "5"))
