"""SQLAlchemy ORM for memory_records"""
from uuid import uuid4
from datetime import datetime, timezone
from sqlalchemy import Column, String, Text, Float, Integer, Boolean, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base
from pgvector.sqlalchemy import Vector

# 复用 RAG 向量库的统一维度配置（text-embedding-v3 = 1024，env VECTOR_PG_DIM 可覆盖）。
# 历史 bug：此处曾声明 Vector(512)，与 provider 维度不符（台账外修复，2026-09-21）。
from backend.rag.vectorstore.pgvector_store import EMBEDDING_DIM

Base = declarative_base()

class MemoryRecord(Base):
    __tablename__ = "memory_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    # scope（048，STOP C）：tenant_id 经 normalize_tenant_id 归一，
    # quarantine=迁移保留哨兵（无身份表映射的 legacy 行，运行时永不产出）
    tenant_id = Column(String(64), nullable=False, default="default")
    user_id = Column(String(64), nullable=False, default="default")
    session_id = Column(String(128), nullable=False, default="")
    memory_type = Column(String(32), nullable=False)
    content = Column(Text, nullable=False)
    embedding = Column(Vector(EMBEDDING_DIM))
    importance_score = Column(Float, nullable=False, default=0.5)
    confidence_score = Column(Float, nullable=False, default=1.0)
    # provenance（047，STOP B）：写入通道 + 证据消息追溯。
    # origin 由代码层强制赋值（不信任模型输出）；source_message_id 指向
    # role=user 的 chat_messages.id，仅逻辑外键（会话级联删除不得波及长期记忆）
    origin = Column(String(16), nullable=False, default="legacy")
    source_message_id = Column(Integer, nullable=True)
    # 事实版本管理（048，STOP C）：key=属性身份，value=规范化属性值，成对出现；
    # 同 (tenant,user,key) 最多一个 active（partial unique index uq_memory_active_key）
    memory_key = Column(String(128), nullable=True)
    structured_value = Column(String(256), nullable=True)
    access_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_access_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expire_at = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    superseded_by = Column(UUID(as_uuid=True), ForeignKey("memory_records.id"), nullable=True)
