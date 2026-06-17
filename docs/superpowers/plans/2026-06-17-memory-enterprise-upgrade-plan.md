# Memory System Enterprise Upgrade — Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development. The controller auto-executes all tasks without pausing.

**Goal:** Upgrade 3-layer memory (Python List / SQLite / ChromaDB) to enterprise MemoryService on PostgreSQL + pgvector with async SQLAlchemy, worthiness classification, importance scoring, hybrid retrieval, and decay.

**Architecture:** 10 tasks. Build from bottom up: DDL → models → repository → core services → pipeline components → unified service → backward-compat layer. Each task is independently committable.

**Tech Stack:** Python 3.10+, SQLAlchemy 2.0 Async, asyncpg, pgvector, asyncio

## Global Constraints

- All DB access via SQLAlchemy AsyncSession (no sync psycopg2)
- Agent code never imports repository/ or models/ directly
- `MemoryService.store()` uses `asyncio.create_task()` — never blocks main flow
- `memory/__init__.py` preserves `MemoryManager` class and `memory_manager` singleton
- `memory/dedup.py` and `memory/pii_filter.py` kept as-is, only import paths updated
- ChromaDB dependency removed from `long_term.py`
- Importance threshold: 0.6 for L3 entry
- Decay: >90d ×0.95, >180d ×0.9, <0.2 → archive (is_active=false)
- Hybrid score: 0.5 × similarity + 0.3 × importance + 0.2 × recency
- New pip packages: `asyncpg`, `pgvector`

---

### Task 1: DDL + pip packages

**Files:**
- Create: `memory/migrations/001_init.sql`

**Interfaces:**
- Produces: `chat_sessions`, `chat_messages`, `memory_records` tables with all indexes

- [ ] **Step 1: Create migrations/001_init.sql**

```sql
-- memory/migrations/001_init.sql
-- Enterprise Memory System DDL
-- Run: psql -h localhost -U postgres -d demo -f memory/migrations/001_init.sql

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Session Memory
CREATE TABLE IF NOT EXISTS chat_sessions (
    id              SERIAL PRIMARY KEY,
    session_id      VARCHAR(128) NOT NULL UNIQUE,
    user_id         VARCHAR(64)  NOT NULL DEFAULT 'default',
    summary         TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_user ON chat_sessions (user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS chat_messages (
    id              SERIAL PRIMARY KEY,
    session_id      VARCHAR(128) NOT NULL REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
    role            VARCHAR(16)  NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content         TEXT         NOT NULL,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages (session_id, created_at);

-- LongTerm Memory
CREATE TABLE IF NOT EXISTS memory_records (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         VARCHAR(64)  NOT NULL DEFAULT 'default',
    session_id      VARCHAR(128) NOT NULL DEFAULT '',
    memory_type     VARCHAR(32)  NOT NULL CHECK (memory_type IN ('user_fact', 'preference', 'decision', 'knowledge')),
    content         TEXT         NOT NULL,
    embedding       vector(512),
    importance_score FLOAT       NOT NULL DEFAULT 0.5,
    confidence_score FLOAT       NOT NULL DEFAULT 1.0,
    access_count    INT          NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    last_access_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    expire_at       TIMESTAMPTZ,
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    superseded_by   UUID         REFERENCES memory_records(id)
);

CREATE INDEX IF NOT EXISTS idx_memory_embedding ON memory_records
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX IF NOT EXISTS idx_memory_user_active ON memory_records (user_id, is_active) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_memory_type ON memory_records (memory_type);
CREATE INDEX IF NOT EXISTS idx_memory_importance ON memory_records (importance_score DESC) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_memory_last_access ON memory_records (last_access_at DESC);
```

- [ ] **Step 2: Install packages**

```bash
pip install asyncpg pgvector sqlalchemy[asyncio]
```

- [ ] **Step 3: Execute DDL**

```bash
psql -h localhost -U postgres -d demo -f memory/migrations/001_init.sql
```

Expected: `CREATE EXTENSION` ×2, `CREATE TABLE` ×3, `CREATE INDEX` ×7

- [ ] **Step 4: Commit**

```bash
git add memory/migrations/ requirements.txt
git commit -m "feat(memory): add enterprise DDL, install asyncpg+pgvector"
```

---

### Task 2: SQLAlchemy ORM models

**Files:**
- Create: `memory/models/__init__.py`
- Create: `memory/models/session.py`
- Create: `memory/models/memory.py`

**Interfaces:**
- Produces: `ChatSession`, `ChatMessage`, `MemoryRecord` ORM classes; `Base` declarative base
- Consumes: nothing

- [ ] **Step 1: Create memory/models/__init__.py**

```python
from memory.models.session import ChatSession, ChatMessage
from memory.models.memory import MemoryRecord, Base

__all__ = ["ChatSession", "ChatMessage", "MemoryRecord", "Base"]
```

- [ ] **Step 2: Create memory/models/session.py**

```python
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
    summary = Column(Text, nullable=True)
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
```

- [ ] **Step 3: Create memory/models/memory.py**

```python
"""SQLAlchemy ORM for memory_records"""
from uuid import uuid4
from datetime import datetime, timezone
from sqlalchemy import Column, String, Text, Float, Integer, Boolean, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base
from pgvector.sqlalchemy import Vector

Base = declarative_base()

class MemoryRecord(Base):
    __tablename__ = "memory_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = Column(String(64), nullable=False, default="default")
    session_id = Column(String(128), nullable=False, default="")
    memory_type = Column(String(32), nullable=False)
    content = Column(Text, nullable=False)
    embedding = Column(Vector(512))
    importance_score = Column(Float, nullable=False, default=0.5)
    confidence_score = Column(Float, nullable=False, default=1.0)
    access_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_access_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expire_at = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    superseded_by = Column(UUID(as_uuid=True), ForeignKey("memory_records.id"), nullable=True)
```

- [ ] **Step 4: Verify imports**

```bash
cd D:/Program Files/workplace/agent && python -c "
from memory.models.session import ChatSession, ChatMessage
from memory.models.memory import MemoryRecord
print('All models OK')
"
```

- [ ] **Step 5: Commit**

```bash
git add memory/models/
git commit -m "feat(memory): add SQLAlchemy ORM models"
```

---

### Task 3: Repository layer

**Files:**
- Create: `memory/repository/__init__.py`
- Create: `memory/repository/session_repo.py`
- Create: `memory/repository/memory_repo.py`

**Interfaces:**
- Consumes: `ChatSession`, `ChatMessage`, `MemoryRecord` from Task 2
- Produces: `SessionRepository`, `MemoryRepository` with async methods

- [ ] **Step 1: Create memory/repository/__init__.py**

```python
from memory.repository.session_repo import SessionRepository
from memory.repository.memory_repo import MemoryRepository

__all__ = ["SessionRepository", "MemoryRepository"]
```

- [ ] **Step 2: Create memory/repository/session_repo.py**

```python
"""SessionRepository — async CRUD for chat_sessions + chat_messages"""
from sqlalchemy import select, insert, update, func
from sqlalchemy.ext.asyncio import AsyncSession
from memory.models.session import ChatSession, ChatMessage
from datetime import datetime, timezone


class SessionRepository:
    def __init__(self, session: AsyncSession):
        self._s = session

    async def get_or_create(self, session_id: str, user_id: str = "default") -> ChatSession:
        result = await self._s.execute(
            select(ChatSession).where(ChatSession.session_id == session_id)
        )
        row = result.scalar_one_or_none()
        if row:
            return row
        obj = ChatSession(session_id=session_id, user_id=user_id)
        self._s.add(obj)
        await self._s.flush()
        return obj

    async def load_messages(self, session_id: str, limit: int | None = None) -> list[ChatMessage]:
        q = select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.created_at)
        if limit:
            q = q.limit(limit)
        result = await self._s.execute(q)
        return list(result.scalars().all())

    async def save_message(self, session_id: str, role: str, content: str) -> ChatMessage:
        msg = ChatMessage(session_id=session_id, role=role, content=content)
        self._s.add(msg)
        # touch session updated_at
        await self._s.execute(
            update(ChatSession).where(ChatSession.session_id == session_id).values(updated_at=datetime.now(timezone.utc))
        )
        await self._s.flush()
        return msg

    async def save_turn(self, session_id: str, question: str, answer: str) -> tuple[ChatMessage, ChatMessage]:
        q = await self.save_message(session_id, "user", question)
        a = await self.save_message(session_id, "assistant", answer)
        return q, a

    async def message_count(self, session_id: str) -> int:
        result = await self._s.execute(
            select(func.count()).where(ChatMessage.session_id == session_id)
        )
        return result.scalar() or 0

    async def needs_summarization(self, session_id: str, max_messages: int = 50) -> bool:
        count = await self.message_count(session_id)
        return count >= max_messages
```

- [ ] **Step 3: Create memory/repository/memory_repo.py**

```python
"""MemoryRepository — async CRUD + pgvector hybrid search for memory_records"""
from uuid import uuid4
from datetime import datetime, timezone
from sqlalchemy import select, update, text, func
from sqlalchemy.ext.asyncio import AsyncSession
from memory.models.memory import MemoryRecord


class MemoryRepository:
    def __init__(self, session: AsyncSession):
        self._s = session

    async def insert(self, record: MemoryRecord) -> MemoryRecord:
        if record.id is None:
            record.id = uuid4()
        self._s.add(record)
        await self._s.flush()
        return record

    async def search_hybrid(
        self, embedding: list[float], user_id: str, top_k: int = 20,
        memory_type: str | None = None,
    ) -> list[MemoryRecord]:
        """pgvector cosine similarity + importance + recency joint scoring"""
        query = select(
            MemoryRecord,
            (1.0 - (MemoryRecord.embedding.cosine_distance(embedding))).label("similarity"),
        ).where(
            MemoryRecord.is_active == True,
            MemoryRecord.user_id == user_id,
        )
        if memory_type:
            query = query.where(MemoryRecord.memory_type == memory_type)
        query = query.order_by(text("similarity DESC")).limit(top_k)

        result = await self._s.execute(query)
        return [row[0] for row in result.all()]

    async def find_similar(
        self, embedding: list[float], user_id: str, threshold: float = 0.85,
    ) -> MemoryRecord | None:
        """Find most similar active record above threshold"""
        result = await self._s.execute(
            select(MemoryRecord, (1.0 - MemoryRecord.embedding.cosine_distance(embedding)).label("sim"))
            .where(MemoryRecord.is_active == True, MemoryRecord.user_id == user_id)
            .having(text(f"1.0 - (embedding <=> :emb) >= {threshold}"))
            .order_by(text("sim DESC")).limit(1),
            {"emb": embedding},
        )
        row = result.first()
        return row[0] if row else None

    async def supersede(self, old_id: str, new_id: str) -> bool:
        result = await self._s.execute(
            update(MemoryRecord).where(MemoryRecord.id == old_id, MemoryRecord.is_active == True)
            .values(is_active=False, superseded_by=new_id)
        )
        return result.rowcount > 0

    async def mark_accessed(self, ids: list[str]) -> None:
        await self._s.execute(
            update(MemoryRecord).where(MemoryRecord.id.in_(ids))
            .values(access_count=MemoryRecord.access_count + 1, last_access_at=datetime.now(timezone.utc))
        )

    async def find_by_id(self, record_id: str) -> MemoryRecord | None:
        result = await self._s.execute(select(MemoryRecord).where(MemoryRecord.id == record_id))
        return result.scalar_one_or_none()

    async def update_fields(self, record_id: str, **fields) -> bool:
        result = await self._s.execute(
            update(MemoryRecord).where(MemoryRecord.id == record_id).values(**fields)
        )
        return result.rowcount > 0

    async def apply_decay(self, days: int, factor: float) -> int:
        threshold = datetime.now(timezone.utc).isoformat()
        result = await self._s.execute(
            text("""
                UPDATE memory_records
                SET importance_score = importance_score * :factor
                WHERE is_active = TRUE
                  AND last_access_at < NOW() - (:days || ' days')::INTERVAL
            """),
            {"factor": factor, "days": str(days)},
        )
        return result.rowcount

    async def archive_stale(self, min_importance: float = 0.2) -> int:
        result = await self._s.execute(
            update(MemoryRecord)
            .where(MemoryRecord.is_active == True, MemoryRecord.importance_score < min_importance)
            .values(is_active=False)
        )
        return result.rowcount

    async def count_active(self) -> int:
        result = await self._s.execute(select(func.count()).where(MemoryRecord.is_active == True))
        return result.scalar() or 0
```

- [ ] **Step 4: Verify imports**

```bash
python -c "from memory.repository.session_repo import SessionRepository; from memory.repository.memory_repo import MemoryRepository; print('OK')"
```

- [ ] **Step 5: Commit**

```bash
git add memory/repository/
git commit -m "feat(memory): add async repository layer"
```

---

### Task 4: Database engine + session factory

**Files:**
- Create: `memory/database.py`

**Interfaces:**
- Consumes: `DB_CONFIG` from `config.py`
- Produces: `async_engine`, `AsyncSessionLocal`, `get_session()` async generator, `init_db()` for schema creation

- [ ] **Step 1: Create memory/database.py**

```python
"""Async database engine + session factory"""
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from config import DB_CONFIG
from utils.logger import logger

DATABASE_URL = (
    f"postgresql+asyncpg://{DB_CONFIG['user']}:{DB_CONFIG['password']}"
    f"@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['dbname']}"
)

async_engine = create_async_engine(
    DATABASE_URL,
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,
    echo=False,
)

AsyncSessionLocal = async_sessionmaker(
    async_engine, class_=AsyncSession, expire_on_commit=False,
)

async def get_session():
    """Async context manager — yields AsyncSession"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

async def init_db():
    """Create all tables (idempotent). Call once at startup."""
    from memory.models.session import Base as SessionBase
    from memory.models.memory import Base as MemoryBase
    async with async_engine.begin() as conn:
        await conn.run_sync(SessionBase.metadata.create_all)
        await conn.run_sync(MemoryBase.metadata.create_all)
    logger.info("[Database] Schema verified")
```

- [ ] **Step 2: Verify connection**

```bash
python -c "
import asyncio
from config import DB_CONFIG
async def test():
    from memory.database import async_engine, init_db
    await init_db()
    print('DB OK')
asyncio.run(test())
"
```

- [ ] **Step 3: Commit**

```bash
git add memory/database.py
git commit -m "feat(memory): add async engine + session factory"
```

---

### Task 5: Refactor L2 SessionMemory

**Files:**
- Modify: `memory/session.py` — replace SQLChatMessageHistory with SessionRepository

**Interfaces:**
- Consumes: `SessionRepository` from Task 3, `get_session` from Task 4
- Produces: `SessionMemory` class (same public API: `load_messages()`, `save_turn()`, `needs_summarization`, `summarize()`, `message_count`)

- [ ] **Step 1: Rewrite memory/session.py**

```python
"""L2 会话记忆 — PostgreSQL async backend"""
from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
from llm.llm_factory import llm
from config import SESSION_MAX_MESSAGES
from utils.logger import logger

_SUMMARY_PROMPT = """请用 2-3 句话总结以下对话的核心内容，保留关键实体、数字、决策和结论:

{conversation}

摘要:"""


class SessionMemory:
    """单个会话的持久化记忆 — backed by PostgreSQL"""

    def __init__(self, session_id: str, user_id: str = "default"):
        self.session_id = session_id
        self.user_id = user_id
        self._summary: str | None = None
        self._repo = None  # set during async init
        self._message_count: int = 0

    @classmethod
    async def create(cls, session_id: str, repo, user_id: str = "default") -> "SessionMemory":
        inst = cls(session_id, user_id)
        inst._repo = repo
        inst._message_count = await repo.message_count(session_id)
        return inst

    async def load_messages(self, limit: int | None = None) -> list[BaseMessage]:
        rows = await self._repo.load_messages(self.session_id, limit=limit)
        return [
            HumanMessage(content=r.content) if r.role == "user"
            else AIMessage(content=r.content)
            for r in rows
        ]

    async def save_turn(self, question: str, answer: str) -> None:
        await self._repo.save_turn(self.session_id, question, answer)
        self._message_count += 2

    @property
    def needs_summarization(self) -> bool:
        return self._message_count >= SESSION_MAX_MESSAGES

    async def summarize(self) -> str:
        rows = await self._repo.load_messages(self.session_id, limit=SESSION_MAX_MESSAGES)
        conversation = "\n".join(
            f"{'用户' if r.role == 'user' else '助手'}: {r.content}"
            for r in rows[-SESSION_MAX_MESSAGES:]
        )
        try:
            resp = llm.invoke(_SUMMARY_PROMPT.format(conversation=conversation))
            self._summary = resp.content if hasattr(resp, "content") else str(resp)
        except Exception as e:
            logger.warning(f"[SessionMemory:{self.session_id}] 摘要失败: {e}")
            self._summary = conversation[:500]
        return self._summary

    @property
    def message_count(self) -> int:
        return self._message_count
```

- [ ] **Step 2: Verify import**

```bash
python -c "from memory.session import SessionMemory; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add memory/session.py
git commit -m "refactor(memory): migrate L2 to async PostgreSQL via SessionRepository"
```

---

### Task 6: Refactor L3 LongTermMemory — pgvector only

**Files:**
- Modify: `memory/long_term.py` — replace ChromaDB/pgvector dual backend with pgvector-only via MemoryRepository
- Delete: `memory/store.py` — absorbed into MemoryRepository

**Interfaces:**
- Consumes: `MemoryRepository` from Task 3
- Produces: `LongTermMemory` class (retain `extract_facts`, `retrieve`, `store_facts`, `format_for_prompt`; new async pipeline)

- [ ] **Step 1: Rewrite memory/long_term.py**

```python
"""L3 长期记忆 — pgvector only, async pipeline"""
from datetime import datetime, timezone
from langchain_huggingface import HuggingFaceEmbeddings
from llm.llm_factory import llm
from config import EMBEDDING_MODEL_PATH, L3_DEDUP_COSINE_THRESHOLD, L3_SUPERSEDE_THRESHOLD
from memory.pii_filter import scan_and_sanitize
from memory.dedup import DedupDecision  # kept for type hint
from utils.logger import logger
from dataclasses import dataclass, field

_FACT_EXTRACTION_PROMPT = """提取对话中的关键信息。每条信息一行，格式: 类型|内容

类型只能是: user_fact(用户信息), preference(偏好), decision(决策), knowledge(知识)
没有重要信息则输出: NONE

对话:
{conversation}

输出:"""


@dataclass
class MemoryFact:
    fact_type: str  # user_fact | preference | decision | knowledge
    content: str
    importance_score: float = 0.5
    session_id: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class LongTermMemory:
    """跨会话长期记忆 — pgvector backend"""

    def __init__(self, repo):
        self._repo = repo
        self._embedding_model = None

    @property
    def embedding(self):
        if self._embedding_model is None:
            self._embedding_model = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL_PATH)
        return self._embedding_model

    # ── Fact extraction (LLM) ──
    def extract_facts(self, question: str, answer: str) -> list[MemoryFact]:
        conversation = f"用户: {question}\n助手: {answer}"
        try:
            resp = llm.invoke(_FACT_EXTRACTION_PROMPT.format(conversation=conversation))
            text = resp.content if hasattr(resp, "content") else str(resp)
        except Exception as e:
            logger.warning(f"[LongTermMemory] 事实提取失败: {e}")
            return []
        return self._parse_facts(text)

    @staticmethod
    def _parse_facts(text: str) -> list[MemoryFact]:
        text = text.strip()
        if not text or text.upper().startswith("NONE"):
            return []
        facts = []
        valid_types = {"user_fact", "preference", "decision", "knowledge"}
        for line in text.splitlines():
            line = line.strip()
            if not line or "|" not in line:
                continue
            parts = line.split("|", 1)
            ft = parts[0].strip()
            content = parts[1].strip() if len(parts) > 1 else ""
            if ft in valid_types and content:
                scan = scan_and_sanitize(content)
                facts.append(MemoryFact(fact_type=ft, content=scan.sanitized))
        return facts

    # ── Retrieval ──
    async def retrieve(self, query: str, user_id: str = "default", k: int = 20) -> list[MemoryFact]:
        emb = self.embedding.embed_query(query)
        rows = await self._repo.search_hybrid(emb, user_id, top_k=k)
        return [MemoryFact(fact_type=r.memory_type, content=r.content, session_id=r.session_id, created_at=str(r.created_at)) for r in rows]

    async def store_single(self, fact: MemoryFact, user_id: str, session_id: str) -> bool:
        """Store one fact with dedup check"""
        from memory.models.memory import MemoryRecord
        emb = self.embedding.embed_query(fact.content)

        existing = await self._repo.find_similar(emb, user_id, threshold=L3_DEDUP_COSINE_THRESHOLD)
        if existing:
            # Check supersede
            from numpy import dot
            from numpy.linalg import norm
            sim = dot(emb, existing.embedding) / (norm(emb) * norm(existing.embedding))
            if sim >= L3_SUPERSEDE_THRESHOLD and existing.memory_type == fact.fact_type:
                record = MemoryRecord(
                    user_id=user_id, session_id=session_id, memory_type=fact.fact_type,
                    content=fact.content, embedding=emb, importance_score=fact.importance_score,
                )
                await self._repo.insert(record)
                await self._repo.supersede(str(existing.id), str(record.id))
                return True
            return False  # skip duplicate

        record = MemoryRecord(
            user_id=user_id, session_id=session_id, memory_type=fact.fact_type,
            content=fact.content, embedding=emb, importance_score=fact.importance_score,
        )
        await self._repo.insert(record)
        return True

    @staticmethod
    def format_for_prompt(facts: list[MemoryFact]) -> str:
        if not facts:
            return ""
        lines = ["[已知背景信息]"]
        type_label = {"user_fact": "信息", "preference": "偏好", "decision": "决策", "knowledge": "知识"}
        for f in facts:
            lines.append(f"- [{type_label.get(f.fact_type, '其他')}] {f.content}")
        return "\n".join(lines)
```

- [ ] **Step 2: Verify import**

```bash
python -c "from memory.long_term import LongTermMemory, MemoryFact; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add memory/long_term.py && git rm memory/store.py && git commit -m "refactor(memory): migrate L3 to pgvector-only, remove ChromaDB store"
```

---

### Task 7: Pipeline components — trigger + importance + retriever + decay

**Files:**
- Create: `memory/trigger.py`
- Create: `memory/importance.py`
- Create: `memory/retriever.py`
- Create: `memory/decay.py`

- [ ] **Step 1: Create memory/trigger.py**

```python
"""MemoryWorthinessClassifier — rule-first, LLM fallback"""
import re
from llm.llm_factory import llm
from utils.logger import logger

_STORE_SIGNALS = [
    r"我是", r"我在", r"我喜欢", r"我习惯", r"我常用", r"我偏好",
    r"项目", r"系统", r"架构", r"技术栈", r"负责", r"管理",
    r"开发", r"部署", r"配置", r"数据库", r"方案", r"决策",
]
_IGNORE_SIGNALS = [
    r"天气", r"你好", r"谢谢", r"好的", r"收到", r"明白",
    r"今天.*吃", r"周末.*去", r"哈哈", r"嗯", r"哦",
]

_TRIGGER_PROMPT = """判断这条信息是否值得存入长期记忆。只需回答 STORE 或 IGNORE。

信息: "{content}"

规则:
- 关于用户身份/角色/技能/偏好的事实 → STORE
- 关于项目/工作/技术决策的信息 → STORE
- 问候/闲聊/确认/情绪表达 → IGNORE

回答:"""


class MemoryWorthinessClassifier:
    def classify(self, content: str) -> str:
        # Rule layer
        for pat in _STORE_SIGNALS:
            if re.search(pat, content):
                return "STORE"
        for pat in _IGNORE_SIGNALS:
            if re.search(pat, content):
                return "IGNORE"
        # LLM fallback
        return self._llm_classify(content)

    def _llm_classify(self, content: str) -> str:
        try:
            resp = llm.invoke(_TRIGGER_PROMPT.format(content=content))
            text = resp.content if hasattr(resp, "content") else str(resp)
            return "STORE" if "STORE" in text.upper() else "IGNORE"
        except Exception as e:
            logger.warning(f"[Trigger] LLM 分类失败: {e}")
            return "IGNORE"
```

- [ ] **Step 2: Create memory/importance.py**

```python
"""ImportanceScorer — 5-dimension scoring 0.0-1.0"""
import re

_DIMENSIONS = [
    (r"我是|我叫|我的职位|我的角色|负责", 1.0, "user_long_term_fact"),
    (r"我喜欢|我习惯|我偏好|我常用|我讨厌", 0.8, "user_preference"),
    (r"项目|架构|技术栈|系统|方案", 0.7, "project_context"),
    (r"开发|部署|配置|测试|上线|运维", 0.5, "work_background"),
    (r".*", 0.2, "casual_chat"),  # default
]

class ImportanceScorer:
    THRESHOLD = 0.6

    def score(self, memory_type: str, content: str) -> float:
        for pattern, weight, _dim in _DIMENSIONS:
            if re.search(pattern, content):
                # Apply type bonus
                type_bonus = {
                    "user_fact": 0.1,
                    "preference": 0.05,
                    "decision": 0.08,
                    "knowledge": 0.0,
                }.get(memory_type, 0.0)
                return min(weight + type_bonus, 1.0)
        return 0.2

    def should_store(self, score: float) -> bool:
        return score >= self.THRESHOLD
```

- [ ] **Step 3: Create memory/retriever.py**

```python
"""HybridRetriever — embedding + importance + recency → rerank → top_k"""
from numpy import dot
from numpy.linalg import norm
from datetime import datetime, timezone


class HybridRetriever:
    def __init__(self, repo):
        self._repo = repo

    async def retrieve(
        self, query: str, embedding: list[float], user_id: str, top_k: int = 5,
    ) -> list:
        # 1. Recall top_20 from pgvector
        candidates = await self._repo.search_hybrid(embedding, user_id, top_k=20)

        # 2. Compute final score: 0.5×sim + 0.3×importance + 0.2×recency
        scored = []
        now = datetime.now(timezone.utc)
        for record in candidates:
            # cosine similarity
            try:
                emb = record.embedding
                sim = dot(embedding, emb) / (norm(embedding) * norm(emb))
            except (ValueError, ZeroDivisionError):
                sim = 0.5

            # recency: days since last access, capped at 365
            delta_days = (now - record.last_access_at).days
            recency = max(0.0, 1.0 - delta_days / 365.0)

            final = 0.5 * sim + 0.3 * record.importance_score + 0.2 * recency
            scored.append((record, final))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [r for r, _ in scored[:top_k]]
```

- [ ] **Step 4: Create memory/decay.py**

```python
"""MemoryDecayService — daily scheduled importance decay + archival"""
from utils.logger import logger


class MemoryDecayService:
    def __init__(self, repo):
        self._repo = repo

    async def run(self) -> dict:
        """Execute one decay cycle. Returns stats."""
        # >180 days → importance × 0.9
        n_180 = await self._repo.apply_decay(180, 0.9)
        # >90 days → importance × 0.95
        n_90 = await self._repo.apply_decay(90, 0.95)
        # < 0.2 → archive
        n_archived = await self._repo.archive_stale(0.2)

        total = n_180 + n_90 + n_archived
        if total:
            logger.info(f"[Decay] 衰减 {n_180 + n_90} 条, 归档 {n_archived} 条")
        return {"decayed": n_180 + n_90, "archived": n_archived}
```

- [ ] **Step 5: Verify all imports**

```bash
cd D:/Program Files/workplace/agent && python -c "
from memory.trigger import MemoryWorthinessClassifier
from memory.importance import ImportanceScorer
from memory.retriever import HybridRetriever
from memory.decay import MemoryDecayService
print('All pipeline components OK')
"
```

- [ ] **Step 6: Commit**

```bash
git add memory/trigger.py memory/importance.py memory/retriever.py memory/decay.py
git commit -m "feat(memory): add pipeline components — trigger, importance, retriever, decay"
```

---

### Task 8: MemoryService — unified entry point

**Files:**
- Create: `memory/service.py`

**Interfaces:**
- Consumes: all components from Tasks 1-7
- Produces: `MemoryService` class with `search`, `store`, `start_session`, `end_turn`, `update`, `archive`, `run_decay`

- [ ] **Step 1: Create memory/service.py**

```python
"""MemoryService — 企业级统一记忆服务入口"""
import asyncio
from memory.database import get_session, AsyncSessionLocal
from memory.repository.session_repo import SessionRepository
from memory.repository.memory_repo import MemoryRepository
from memory.session import SessionMemory
from memory.long_term import LongTermMemory, MemoryFact
from memory.short_term import ShortTermBuffer
from memory.trigger import MemoryWorthinessClassifier
from memory.importance import ImportanceScorer
from memory.retriever import HybridRetriever
from memory.decay import MemoryDecayService
from memory.pii_filter import scan_and_sanitize
from langchain_core.messages import SystemMessage
from utils.logger import logger


class MemoryService:
    """统一记忆服务。Agent 只能通过此接口访问记忆。"""

    def __init__(self):
        self._sessions: dict[str, SessionMemory] = {}
        self._trigger = MemoryWorthinessClassifier()
        self._importance = ImportanceScorer()
        self._decay_service = None  # lazy init with repo

    # ============================================================
    # Session lifecycle
    # ============================================================

    async def start_session(self, session_id: str, user_id: str = "default") -> ShortTermBuffer:
        async with AsyncSessionLocal() as db_session:
            try:
                srepo = SessionRepository(db_session)
                mrepo = MemoryRepository(db_session)

                # L2 → L1
                l2 = await SessionMemory.create(session_id, srepo, user_id)
                self._sessions[session_id] = l2

                l1 = ShortTermBuffer()
                history = await l2.load_messages()
                for msg in history:
                    l1.add(msg)

                # L3 → L1
                retriever = HybridRetriever(mrepo)
                l3 = LongTermMemory(mrepo)
                # Use a dummy query to get user context
                emb = l3.embedding.embed_query(session_id)
                records = await retriever.retrieve(session_id, emb, user_id, top_k=5)
                if records:
                    facts = [MemoryFact(fact_type=r.memory_type, content=r.content, session_id=r.session_id) for r in records]
                    prompt_text = LongTermMemory.format_for_prompt(facts)
                    l1._messages.insert(0, SystemMessage(content=prompt_text))
                    logger.info(f"[MemoryService] 注入 {len(records)} 条长期记忆 (session={session_id})")

                await db_session.commit()
                return l1
            except Exception as e:
                await db_session.rollback()
                logger.error(f"[MemoryService] start_session 失败: {e}")
                raise

    async def end_turn(self, session_id: str, question: str, answer: str, user_id: str = "default") -> None:
        async with AsyncSessionLocal() as db_session:
            try:
                srepo = SessionRepository(db_session)

                # L2 persistence
                await srepo.save_turn(session_id, question, answer)

                # Check summarization
                if await srepo.needs_summarization(session_id):
                    l2 = self._sessions.get(session_id)
                    if l2:
                        l2._repo = srepo
                        await l2.summarize()

                await db_session.commit()
            except Exception as e:
                await db_session.rollback()
                logger.error(f"[MemoryService] end_turn 失败: {e}")

        # L3: async background write
        asyncio.create_task(self.store(question, answer, session_id, user_id))

    # ============================================================
    # Retrieval
    # ============================================================

    async def search(self, query: str, session_id: str, user_id: str = "default", top_k: int = 5) -> list[MemoryFact]:
        async with AsyncSessionLocal() as db_session:
            try:
                mrepo = MemoryRepository(db_session)
                l3 = LongTermMemory(mrepo)
                retriever = HybridRetriever(mrepo)
                emb = l3.embedding.embed_query(query)
                records = await retriever.retrieve(query, emb, user_id, top_k=top_k)

                if records:
                    await mrepo.mark_accessed([str(r.id) for r in records])
                    await db_session.commit()

                return [
                    MemoryFact(fact_type=r.memory_type, content=r.content, session_id=r.session_id,
                               created_at=str(r.created_at), importance_score=r.importance_score)
                    for r in records
                ]
            except Exception as e:
                await db_session.rollback()
                logger.error(f"[MemoryService] search 失败: {e}")
                return []

    # ============================================================
    # Async store pipeline (background)
    # ============================================================

    async def store(self, question: str, answer: str, session_id: str, user_id: str = "default") -> None:
        """后台管线: extract → PII → classify → score → dedup → write"""
        async with AsyncSessionLocal() as db_session:
            try:
                mrepo = MemoryRepository(db_session)
                l3 = LongTermMemory(mrepo)

                # 1. Extract
                facts = l3.extract_facts(question, answer)
                if not facts:
                    return

                stored = 0
                for fact in facts:
                    # 2. PII already applied in extract_facts
                    # 3. Classify
                    if self._trigger.classify(fact.content) == "IGNORE":
                        continue
                    # 4. Score
                    fact.importance_score = self._importance.score(fact.fact_type, fact.content)
                    if not self._importance.should_store(fact.importance_score):
                        continue
                    # 5. Dedup + Write
                    ok = await l3.store_single(fact, user_id, session_id)
                    if ok:
                        stored += 1

                if stored:
                    await db_session.commit()
                    logger.info(f"[MemoryService] 后台写入 {stored}/{len(facts)} 条记忆")
            except Exception as e:
                await db_session.rollback()
                logger.error(f"[MemoryService] store 失败: {e}")

    # ============================================================
    # Maintenance
    # ============================================================

    async def update(self, record_id: str, **fields) -> bool:
        async with AsyncSessionLocal() as db_session:
            try:
                mrepo = MemoryRepository(db_session)
                result = await mrepo.update_fields(record_id, **fields)
                await db_session.commit()
                return result
            except Exception as e:
                await db_session.rollback()
                logger.error(f"[MemoryService] update 失败: {e}")
                return False

    async def archive(self, record_id: str) -> bool:
        return await self.update(record_id, is_active=False)

    async def run_decay(self) -> dict:
        async with AsyncSessionLocal() as db_session:
            mrepo = MemoryRepository(db_session)
            decay = MemoryDecayService(mrepo)
            result = await decay.run()
            await db_session.commit()
            return result
```

- [ ] **Step 2: Verify import**

```bash
python -c "from memory.service import MemoryService; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add memory/service.py
git commit -m "feat(memory): add MemoryService unified entry point"
```

---

### Task 9: Update manager.py + __init__.py backward-compat

**Files:**
- Modify: `memory/manager.py` (rename from current `__init__.py` logic)
- Modify: `memory/__init__.py` — backward-compat wrapper

- [ ] **Step 1: Create memory/manager.py**

```python
"""MemoryManager — L1+L2+L3 lifecycle orchestrator, backward-compat wrapper"""
import asyncio
from memory.service import MemoryService
from memory.short_term import ShortTermBuffer
from utils.logger import logger


class MemoryManager:
    def __init__(self):
        self._service = MemoryService()
        self._loop = None

    def _run(self, coro):
        """Sync wrapper for async calls"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result()
        return asyncio.run(coro)

    def start_session(self, session_id: str, question: str) -> ShortTermBuffer:
        return self._run(self._service.start_session(session_id))

    def end_turn(self, session_id: str, question: str, answer: str) -> None:
        return self._run(self._service.end_turn(session_id, question, answer))

    def end_session(self, session_id: str) -> None:
        if session_id in self._service._sessions:
            del self._service._sessions[session_id]


memory_manager = MemoryManager()
```

- [ ] **Step 2: Update memory/__init__.py**

```python
"""memory — 企业级三层记忆系统"""
from memory.manager import MemoryManager, memory_manager
from memory.short_term import ShortTermBuffer
from memory.service import MemoryService

__all__ = ["MemoryManager", "memory_manager", "ShortTermBuffer", "MemoryService"]
```

- [ ] **Step 3: Verify backward compat**

```bash
python -c "
from memory import MemoryManager, memory_manager, ShortTermBuffer
print('Backward compat OK')
print('MemoryManager:', MemoryManager)
print('Singleton:', memory_manager)
"
```

- [ ] **Step 4: Commit**

```bash
git add memory/manager.py memory/__init__.py
git commit -m "refactor(memory): add MemoryManager compat layer + update public API"
```

---

### Task 10: Update config.py + Agent callers

**Files:**
- Modify: `config.py` — remove ChromaDB configs, add async PG configs
- Modify: `multi_agent/graph.py` — switch to MemoryService
- Modify: `api/deps.py` — switch to MemoryService

- [ ] **Step 1: Update config.py**

Remove: `L3_STORE_BACKEND`, `CHAT_HISTORY_DB` (SQLite), `CHROMA_PATH`, `DOC_DB_PATH`, `LONG_TERM_MEMORY_PATH` ChromaDB config.

Add:
```python
# Memory — Enterprise PostgreSQL
MEMORY_ASYNC_POOL_SIZE = int(os.getenv("MEMORY_ASYNC_POOL_SIZE", "20"))
MEMORY_ASYNC_MAX_OVERFLOW = int(os.getenv("MEMORY_ASYNC_MAX_OVERFLOW", "10"))
```

- [ ] **Step 2: Update caller code**

Search for all `from memory import` or `MemoryManager()` calls. Update to use `memory_manager` from `memory/__init__.py`. The `memory_manager` singleton already wraps `MemoryService` internally.

- [ ] **Step 3: Full integration test**

```bash
python -c "
import asyncio
async def test():
    from memory.service import MemoryService
    svc = MemoryService()
    l1 = await svc.start_session('test_integration', 'test_user')
    print(f'L1 messages: {len(l1)}')
    await svc.end_turn('test_integration', '测试问题', '测试答案', 'test_user')
    print('end_turn OK')
    await asyncio.sleep(0.5)
    results = await svc.search('测试', 'test_integration')
    print(f'Search results: {len(results)}')
    print('All integration OK')
asyncio.run(test())
"
```

- [ ] **Step 4: Commit**

```bash
git add config.py multi_agent/ api/ memory/
git commit -m "refactor(memory): update config + agent callers for enterprise memory"
```

---

## Verification Checklist

- [ ] All 10 tasks committed
- [ ] `python -c "from memory import MemoryManager, memory_manager"` works
- [ ] `MemoryService.start_session()` returns populated ShortTermBuffer
- [ ] `MemoryService.store()` pipeline runs without errors
- [ ] `MemoryService.search()` returns results from pgvector
- [ ] `MemoryService.run_decay()` executes decay + archival
- [ ] Old `MemoryManager` API still works (backward compat)
- [ ] ChromaDB references fully removed from config and code
