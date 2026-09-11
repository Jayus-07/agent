"""MemoryService — 统一记忆服务入口"""
import asyncio

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
        self._trigger = None  # lazy init with LLM model
        self._importance = None  # lazy init
        self._decay_service = None  # lazy init with repo

    def _get_trigger(self):
        if self._trigger is None:
            self._trigger = MemoryWorthinessClassifier()
        return self._trigger

    def _get_importance(self):
        if self._importance is None:
            self._importance = ImportanceScorer()
        return self._importance

    # ============================================================
    # Session lifecycle
    # ============================================================

    async def start_session(self, session_id: str, user_id: str = "default") -> ShortTermBuffer:
        async with AsyncSessionLocal() as db_session:
            try:
                srepo = SessionRepository(db_session)
                mrepo = MemoryRepository(db_session)

                # Ensure chat_sessions row exists (FK target for chat_messages)
                srow = await srepo.get_or_create(session_id, user_id)

                # L2 → L1（只取最近 SHORT_TERM_MAX_MESSAGES*2 条：
                #  ① 查询层限量，避免长会话全量拉取；② 多取一倍给去重留余量）
                from backend.config import SHORT_TERM_MAX_MESSAGES
                l2 = await SessionMemory.create(session_id, srepo, user_id)
                self._sessions[session_id] = l2

                l1 = ShortTermBuffer()
                history = await l2.load_messages(limit=SHORT_TERM_MAX_MESSAGES * 2)
                # 历史双写修复：旧数据曾因前端+后端各持久化一次，产生连续重复消息。
                # 注入上下文前按 (role, content) 连续去重，避免重复历史挤占窗口、误导模型
                prev = None
                for msg in history:
                    if prev is not None and type(msg) is type(prev) and msg.content == prev.content:
                        continue
                    l1.add(msg)
                    prev = msg

                # L2 摘要注入：超过短消息窗口的更早上下文由会话摘要补足 ——
                # 否则摘要落库后从未参与 prompt，长会话的早期信息完全丢失。
                # 002 迁移后 title/summary 已分字段，summary 即纯 L2 摘要；
                # 保留短文本守卫仅为兼容未回填的旧库（summary 里可能残留旧重命名标题）
                if srow.summary and len(srow.summary) >= 30:
                    l1._messages.insert(0, SystemMessage(
                        content=f"以下是本会话早期对话的摘要，可结合它理解用户当前问题：\n{srow.summary}"
                    ))
                    logger.info(f"[MemoryService] 注入 L2 会话摘要 (session={session_id}, {len(srow.summary)} 字)")

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
                        summary = await l2.summarize()
                        # 摘要必须落库，否则下次会话列表/历史读取时 summary 永远为空
                        await srepo.update_summary(session_id, summary)

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
        """后台管线: extract → PII → classify → score → dedup → write

        注意：本协程运行在 MemoryManager 的后台 event loop 上，
        LLM 同步调用（提取/分类）必须放到线程池，否则会阻塞整个 loop，
        导致同期其他记忆操作（会话持久化等）超时降级。
        """
        async with AsyncSessionLocal() as db_session:
            try:
                mrepo = MemoryRepository(db_session)
                l3 = LongTermMemory(mrepo)

                # 1. Extract（LLM 同步调用 → 线程池）
                facts = await asyncio.to_thread(l3.extract_facts, question, answer)
                if not facts:
                    return

                stored = 0
                for fact in facts:
                    # 2. PII already applied in extract_facts
                    # 3. Classify（可能触发 LLM 同步调用 → 线程池）
                    verdict = await asyncio.to_thread(
                        self._get_trigger().classify, fact.content, fact.fact_type
                    )
                    if verdict == "IGNORE":
                        continue
                    # 4. Score
                    fact.importance_score = self._get_importance().score(fact.fact_type, fact.content)
                    if not self._get_importance().should_store(fact.importance_score):
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

    async def save_messages(self, session_id: str, messages: list[dict], user_id: str = "default") -> dict:
        """批量保存会话消息（供 chat API 调用）。"""
        if not session_id or not messages:
            return {"saved": 0}
        saved = 0
        try:
            async with AsyncSessionLocal() as db:
                repo = SessionRepository(db)
                await repo.get_or_create(session_id, user_id)
                for m in messages:
                    await repo.save_message(session_id, m.get("role", "user"), m.get("content", ""))
                    saved += 1
                await db.commit()
        except Exception as e:
            logger.error(f"[MemoryService] save_messages 失败: {e}")
        return {"saved": saved}

    async def run_decay(self) -> dict:
        async with AsyncSessionLocal() as db_session:
            mrepo = MemoryRepository(db_session)
            decay = MemoryDecayService(mrepo)
            result = await decay.run()
            await db_session.commit()
            return result

    # ============================================================
    # Session CRUD（供 API 路由使用）
    # ============================================================

    async def list_sessions(self, user_id: str = "default",
                            limit: int = 50, before: str | None = None) -> dict:
        """列出用户所有会话（支持分页）。

        Args:
            limit: 单次返回上限（50 / 100 / 200 等）
            before: 游标分页 — 仅返回 updated_at < before 的会话
        """
        async with AsyncSessionLocal() as db_session:
            try:
                repo = SessionRepository(db_session)
                sessions = await repo.list_all(user_id=user_id, limit=limit, before=before)
                # 第一页（无 cursor）时返回 total；分页时 total 不变（cost）
                # 前端可据此判断 hasMore = (已显示 < total)
                total: int | None = None
                if before is None:
                    total = await repo.count_sessions(user_id=user_id)
                await db_session.commit()
                return {
                    "sessions": sessions,
                    "total": total if total is not None else len(sessions),
                    "has_more": total is not None and len(sessions) < (total or 0),
                }
            except Exception as e:
                await db_session.rollback()
                logger.error(f"[MemoryService] list_sessions 失败: {e}")
                return {"sessions": [], "total": 0, "error": str(e)}

    async def get_session_messages(self, session_id: str) -> dict:
        """获取会话消息列表。"""
        async with AsyncSessionLocal() as db_session:
            try:
                repo = SessionRepository(db_session)
                msgs = await repo.load_messages(session_id)
                await db_session.commit()
                return {
                    "session_id": session_id,
                    "messages": [
                        {"role": m.role, "content": m.content, "created_at": m.created_at.isoformat()}
                        for m in msgs
                    ],
                }
            except Exception as e:
                await db_session.rollback()
                logger.error(f"[MemoryService] get_session_messages 失败: {e}")
                return {"session_id": session_id, "messages": [], "error": str(e)}

    async def get_session_context(self, session_id: str) -> dict:
        """获取会话 Agent 工作上下文。"""
        async with AsyncSessionLocal() as db_session:
            try:
                repo = SessionRepository(db_session)
                ctx = await repo.get_context(session_id)
                await db_session.commit()
                if ctx:
                    import json as _json
                    try:
                        return {"session_id": session_id, "context": _json.loads(ctx)}
                    except (_json.JSONDecodeError, TypeError, ValueError) as e:
                        logger.debug("会话上下文 JSON 解析失败，回退为 raw: %s", e)
                        return {"session_id": session_id, "context": {"raw": ctx}}
                return {"session_id": session_id, "context": None}
            except Exception as e:
                await db_session.rollback()
                return {"session_id": session_id, "context": None, "error": str(e)}

    async def delete_session(self, session_id: str) -> dict:
        """删除会话及消息。"""
        async with AsyncSessionLocal() as db_session:
            try:
                repo = SessionRepository(db_session)
                ok = await repo.delete(session_id)
                await db_session.commit()
                if ok:
                    logger.info(f"[MemoryService] 已删除会话: {session_id}")
                    return {"ok": True, "session_id": session_id}
                return {"ok": False, "error": "会话不存在"}
            except Exception as e:
                await db_session.rollback()
                logger.error(f"[MemoryService] delete_session 失败: {e}")
                return {"ok": False, "error": str(e)}

    async def rename_session(self, session_id: str, title: str) -> dict:
        """重命名会话。"""
        async with AsyncSessionLocal() as db_session:
            try:
                repo = SessionRepository(db_session)
                ok = await repo.rename(session_id, title)
                await db_session.commit()
                if ok:
                    logger.info(f"[MemoryService] 已重命名: {session_id} → {title}")
                    return {"ok": True, "session_id": session_id, "title": title}
                return {"ok": False, "error": "会话不存在"}
            except Exception as e:
                await db_session.rollback()
                logger.error(f"[MemoryService] rename_session 失败: {e}")
                return {"ok": False, "error": str(e)}
