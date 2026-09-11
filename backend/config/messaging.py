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

# 写权切换（cutover 阶段 C）：local=Python 直写 customer_service（现状），
# java=状态转换/消息落库改调 business-service /internal/*，Python 停止直写。
# java 模式下 business-service 不可用时返回失败，不静默回落本地写（保证写权唯一）。
CS_WRITE_SOURCE = os.getenv("CS_WRITE_SOURCE", "local").strip().lower()

# Java→Python 工具网关（/internal/ai/*）鉴权令牌；为空=本地开发跳过校验
# （与 business-service InternalTokenFilter 行为一致）
AI_INTERNAL_TOKEN = os.getenv("AI_INTERNAL_TOKEN", os.getenv("INTERNAL_API_TOKEN", ""))

# 是否开放 Java→Python 工具网关（默认关闭，部署后再开）
AI_TOOLS_ENABLED = os.getenv("AI_TOOLS_ENABLED", "false").strip().lower() in ("1", "true", "yes")

# 工具白名单：Java 可调用的 tool 名，"*" 表示全部。
# manager.route() 按 tool_name 路由，白名单在网关层先过滤，防止误调危险工具。
AI_TOOLS_ALLOWED = os.getenv("AI_TOOLS_ALLOWED", "sql_query,list_tables,search_knowledge,list_documents")

# WhatsApp 闭环：Python 消费 channel.whatsapp.inbound 的独立开关
KAFKA_CONSUMER_ENABLED = os.getenv("KAFKA_CONSUMER_ENABLED", "false").strip().lower() in ("1", "true", "yes")

# 消费者组 ID（WhatsApp 入站消费）
KAFKA_CONSUMER_GROUP = os.getenv("KAFKA_CONSUMER_GROUP", "ai-service")
