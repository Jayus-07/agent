"""SQLAlchemy ORM for chat_sessions + chat_messages"""
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship, declarative_base
from datetime import datetime, timezone

Base = declarative_base()

class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id = Column(Integer, primary_key=True)
    session_id = Column(String(128), unique=True, nullable=False, index=True)
    user_id = Column(String(64), nullable=False, default="default")
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
    role = Column(String(16), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    session = relationship("ChatSession", back_populates="messages")
