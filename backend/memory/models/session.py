"""SQLAlchemy ORM for chat_sessions + chat_messages"""
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Index, JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship, declarative_base
from datetime import datetime, timezone

Base = declarative_base()

class ChatSession(Base):
    __tablename__ = "chat_sessions"
    __table_args__ = (
        # 产品维度唯一（143，ADR-0012 第 1 期）：跨产品同 session_id 各建各
        # 的行；同产品内越权由运行时属主校验（SessionOwnerMismatch）承担。
        Index("uq_chat_sessions_product_session", "product", "session_id", unique=True),
        Index("idx_chat_sessions_tenant_user", "tenant_id", "product", "user_id"),
    )

    id = Column(Integer, primary_key=True)
    session_id = Column(String(128), nullable=False, index=True)
    user_id = Column(String(64), nullable=False, default="default")
    # 产品命名空间（143，ADR-0012 第 1 期）：user_id 拆裸后由本列承载产品。
    product = Column(String(32), nullable=False, default="agent")
    # 运行时空租户归一为 default；quarantine 仅用于无法权威映射的旧会话。
    tenant_id = Column(
        String(64), nullable=False, default="default", server_default="quarantine",
    )
    # 会话标题：首轮用户问题截断（save_turn 自动填）或用户重命名；
    # 与 L2 自动摘要 summary 分离 —— 摘要只喂 prompt，标题只管展示
    title = Column(String(128), nullable=True, comment="会话标题（与L2摘要summary分离）")
    summary = Column(Text, nullable=True)
    # L5 AutoCompact 增量摘要水位线（2026-09-22 Phase 3，migration 040）：
    # summary 已覆盖到的最新 chat_messages.id；NULL = 尚无增量摘要。
    # 红线：只推进水位线，绝不删除/改写 chat_messages 原始行。
    summary_through_message_id = Column(Integer, nullable=True)
    summary_token_count = Column(Integer, nullable=True, comment="L5摘要自身token数")
    summary_updated_at = Column(DateTime(timezone=True), nullable=True, comment="L5摘要最后更新时间")
    context_summary = Column(Text, nullable=True, comment="Agent工作上下文: SQL结果/RAG文档/报告摘要聚合JSON")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    messages = relationship("ChatMessage", back_populates="session", cascade="all, delete-orphan")

class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True)
    session_id = Column(String(128), ForeignKey("chat_sessions.session_id", ondelete="CASCADE"), nullable=False)
    # 产品命名空间（143，ADR-0012 第 1 期）：跨产品同 session_id 时区分消息行。
    product = Column(String(32), nullable=False, default="agent")
    role = Column(String(16), nullable=False)
    content = Column(Text, nullable=False)
    # 旧行保持 NULL；新 LangChain 行保存 human/ai/tool 类型及非正文元数据。
    message_type = Column(String(32), nullable=True)
    message_metadata = Column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=True,
    )
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    session = relationship("ChatSession", back_populates="messages")
