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
