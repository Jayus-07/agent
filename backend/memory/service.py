"""MemoryService — 统一记忆服务入口"""
from backend.memory.database import get_session, AsyncSessionLocal
from backend.memory.repository.session_repo import SessionRepository
from backend.memory.repository.memory_repo import MemoryRepository
from backend.memory.session import SessionMemory
from backend.memory.long_term import LongTermMemory, MemoryFact
from backend.memory.short_term import ShortTermBuffer
from backend.memory.trigger import MemoryWorthinessClassifier
from backend.memory.importance import ImportanceScorer
from backend.memory.retriever import HybridRetriever
from backend.memory.decay import MemoryDecayService
from backend.memory.pii_filter import scan_and_sanitize
from langchain_core.messages import SystemMessage
from backend.shared.logger import logger


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

                # Ensure chat_sessions row exists (FK target for chat_messages)
                await srepo.get_or_create(session_id, user_id)

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

                # Ensure chat_sessions row exists (may not if start_session was never called)
                await srepo.get_or_create(session_id, user_id)

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

        # L3: background write — caller's loop must keep running (Manager handles this)
        asyncio.ensure_future(self.store(question, answer, session_id, user_id))

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
                    if self._trigger.classify(fact.content, fact.fact_type) == "IGNORE":
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
