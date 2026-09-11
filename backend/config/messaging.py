"""config/messaging.py — Kafka 消息队列配置。

微服务拆分后的事件总线：
  - Python AI 系统发布：会话状态变更、消息落库等事件
  - Java 业务系统发布：conversation/handoff/action 事件
  - 双方通过 topic 契约协作，见 docs/architecture-overview.md
"""
import os

from dotenv import load_dotenv

load_dotenv()

# 是否启用 Kafka 事件发布（默认关闭，保证向后兼容：未部署 Kafka 时一切照旧）
KAFKA_ENABLED = os.getenv("KAFKA_ENABLED", "false").strip().lower() in ("1", "true", "yes")

# Bootstrap servers（容器网络内 kafka:9092；宿主机开发 127.0.0.1:9094）
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:9094")

# 生产者客户端 ID
KAFKA_CLIENT_ID = os.getenv("KAFKA_CLIENT_ID", "ai-service")

# Topic 契约（与 business-service KafkaTopicsConfig 对齐）
TOPIC_CONVERSATION_EVENTS = "cs.conversation.events"
TOPIC_MESSAGE_EVENTS = "cs.message.events"
TOPIC_HANDOFF_EVENTS = "cs.handoff.events"
TOPIC_ACTION_EVENTS = "cs.action.events"
TOPIC_AI_REPLY_EVENTS = "ai.reply.events"
TOPIC_WHATSAPP_INBOUND = "channel.whatsapp.inbound"

# ── 业务系统（Java business-service）直连配置 ──

BUSINESS_SERVICE_URL = os.getenv("BUSINESS_SERVICE_URL", "http://127.0.0.1:8081")

# 内部调用令牌（与 business-service INTERNAL_API_TOKEN 一致；为空则不携带）
INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "")

# cs_admin 读源切换：local=读本地 PG（现状），java=代理到 business-service
# Java 验证通过后置 java 并下线本地查询路径
CS_ADMIN_SOURCE = os.getenv("CS_ADMIN_SOURCE", "local").strip().lower()
