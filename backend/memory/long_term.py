"""L3 长期记忆 — pgvector only, async pipeline

provenance 契约（047 迁移，STOP B）：
  - 用户消息 = 唯一可形成用户长期记忆的证据（evidence）
  - assistant 回答 = 仅语境辅助（context only），禁止独立产生用户事实
  - origin 由代码层强制赋值（inferred/explicit），不信任模型输出
  - confidence_score 落库前服务端 clamp[0,1]，非法回退默认值
  - 提取候选必须携带「用户原话片段」且该片段能在用户消息中找到，
    否则拒绝写入（fail-closed）——prompt 不是安全边界，这是代码级防线
"""
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from backend.config import (
    MEMORY_EXPLICIT_DEFAULT_CONFIDENCE,
    MEMORY_HEDGED_CONFIDENCE_CAP,
    MEMORY_INFERRED_DEFAULT_CONFIDENCE,
    MEMORY_ORIGIN_EXPLICIT,
    MEMORY_ORIGIN_INFERRED,
    MEMORY_ORIGIN_LEGACY,
)
from backend.memory.keying import (
    StoreResult,
    StoreOutcome,
    normalize_memory_key,
    normalize_memory_value,
    normalize_tenant_id,
)
from backend.rag.embedding_singleton import get_embedding
from backend.config import L3_DEDUP_COSINE_THRESHOLD, L3_SUPERSEDE_THRESHOLD
from backend.memory.pii_filter import scan_and_sanitize
from backend.shared.logger import logger

_VALID_ORIGINS = frozenset({
    MEMORY_ORIGIN_EXPLICIT, MEMORY_ORIGIN_INFERRED, MEMORY_ORIGIN_LEGACY,
})

# 不确定性措辞：出现在用户原话片段中 → confidence 强制封顶。
# 防 assistant 复述把「可能看看 Go」强化成高置信「决定转 Go」（Case B4）。
# 纯规则启发式，词表可随 Golden 评测（STOP F）迭代。
_HEDGED_TERMS = (
    "可能", "或许", "也许", "大概", "说不定", "考虑", "看看", "倾向",
    "maybe", "might", "probably", "possibly", "consider",
)

# 提取候选拒绝原因（固定枚举，Prometheus label，禁止动态值）
REJECT_ASSISTANT_ONLY = "assistant_only"  # 证据片段缺失或不在用户消息中
REJECT_NOT_WORTHY = "not_worthy"
REJECT_LOW_IMPORTANCE = "low_importance"
REJECT_DUPLICATE = "duplicate"
REJECT_ERROR = "error"


@dataclass
class MemoryFact:
    fact_type: str  # user_fact | preference | decision | knowledge
    content: str
    importance_score: float = 0.5
    session_id: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    # provenance（STOP B）：origin 必须由写入通道的代码显式赋值；
    # dataclass 默认 legacy，防止旧调用方在无意识中伪装成 inferred/explicit
    origin: str = MEMORY_ORIGIN_LEGACY
    confidence_score: float = MEMORY_INFERRED_DEFAULT_CONFIDENCE
    source_message_id: int | None = None
    # 事实版本管理（STOP C）：key/value 由调用方给出候选，store_with_resolution
    # 入口统一 normalize+validate（非法 key/value 双双置 NULL 走 unkeyed 路径）
    memory_key: str | None = None
    structured_value: str | None = None
    # 检索结果携带稳定 ID，Agent 才能发起确定性本人删除。
    memory_id: str | None = None


def clamp_confidence(value, default: float = MEMORY_INFERRED_DEFAULT_CONFIDENCE) -> float:
    """confidence 服务端归一化：非法/缺失 → default，数值 → clamp[0,1]。

    LLM 可能返回 1.2 / -0.4 / None / "high"，一律不得污染数据库。
    """
    if value is None:
        return default
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(v) or math.isinf(v):
        return default
    return max(0.0, min(1.0, v))


def _normalize_for_match(text: str) -> str:
    """证据匹配归一化：去空白 + 小写（中英文混合口径一致）。"""
    return re.sub(r"\s+", "", text or "").lower()


def _evidence_from_user(evidence: str, user_evidence: str) -> bool:
    """代码级硬防线：证据片段必须是用户消息的连续子串（归一化后）。

    assistant 复述/改写/补充的内容不可能通过该校验——即使 prompt 漂移，
    assistant-only 事实也无法落库。
    """
    ev = _normalize_for_match(evidence)
    src = _normalize_for_match(user_evidence)
    return bool(ev) and bool(src) and ev in src


def _is_hedged(evidence: str) -> bool:
    lowered = (evidence or "").lower()
    return any(term in lowered for term in _HEDGED_TERMS)


class LongTermMemory:
    """跨会话长期记忆 — pgvector backend"""

    def __init__(self, repo):
        self._repo = repo
        self._embedding_model = None

    @property
    def embedding(self):
        if self._embedding_model is None:
            self._embedding_model = get_embedding()  # 全局单例，避免重复加载
        return self._embedding_model

    # ── Fact extraction (LLM) ──
    def extract_facts(self, question: str, answer: str) -> tuple[list["MemoryFact"], list[str]]:
        """从本轮对话提取记忆候选。

        输入分块：question 是唯一证据源（<user_evidence>），answer 只是
        语境辅助（<assistant_context>）——权限差异由 prompt 结构显式表达。
        返回 (通过校验的 facts, 拒绝原因列表)。
        """
        conversation = (
            f"<user_evidence>\n{question}\n</user_evidence>\n\n"
            f"<assistant_context>\n{answer}\n</assistant_context>"
        )
        try:
            from backend.infra.llm import llm
            from backend.prompts.service import prompt_service
            r = prompt_service.render_sync("memory.long_term.fact_extraction", conversation=conversation)
            resp = llm.invoke(r.text)
            text = resp.content if hasattr(resp, "content") else str(resp)
        except Exception as e:
            logger.warning(f"[LongTermMemory] 事实提取失败: {e}")
            return [], [REJECT_ERROR]
        return self._parse_facts(text, user_evidence=question)

    @staticmethod
    def _parse_facts(text: str, user_evidence: str = "") -> tuple[list["MemoryFact"], list[str]]:
        """解析提取输出，协议：`类型|内容|置信度|用户原话片段[|memory_key[|structured_value]]`。

        前 4 段必需；第 5/6 段（key/value）可选（STOP C 扩展，不增加 LLM 调用）。
        key/value 代码层 normalize+validate，非法 → 双双 NULL（unkeyed 路径）。

        fail-closed：证据片段缺失 / 不在用户消息中 / 段数不足 → 拒绝。
        旧两段格式（类型|内容）无法证明证据来自用户，一律拒绝——
        宁可少写，不可把来源不明的内容固化为用户长期事实。
        """
        text = text.strip()
        facts: list[MemoryFact] = []
        rejections: list[str] = []
        if not text or text.upper().startswith("NONE"):
            return facts, rejections
        valid_types = {"user_fact", "preference", "decision", "knowledge"}

        for line in text.splitlines():
            line = line.strip()
            if not line or line.upper().startswith("NONE") or "|" not in line:
                continue
            parts = line.split("|", 5)
            if len(parts) < 4:
                # 旧协议/残缺协议：无证据片段，无法追溯来源
                rejections.append(REJECT_ASSISTANT_ONLY)
                continue
            ft = parts[0].strip()
            content = parts[1].strip()
            confidence_raw = parts[2].strip()
            evidence = parts[3].strip()
            key_raw = parts[4].strip() if len(parts) > 4 else ""
            value_raw = parts[5].strip() if len(parts) > 5 else ""
            if ft not in valid_types or not content:
                continue
            confidence = clamp_confidence(confidence_raw, MEMORY_INFERRED_DEFAULT_CONFIDENCE)
            if not _evidence_from_user(evidence, user_evidence):
                rejections.append(REJECT_ASSISTANT_ONLY)
                continue
            if _is_hedged(evidence):
                confidence = min(confidence, MEMORY_HEDGED_CONFIDENCE_CAP)
            # key/value 成对约束：任一非法 → 双双 NULL（不 reject 事实本身）
            memory_key = normalize_memory_key(key_raw) if key_raw else None
            structured_value = None
            if memory_key is not None:
                structured_value = normalize_memory_value(value_raw) if value_raw else None
                if structured_value is None:
                    memory_key = None
            scan = scan_and_sanitize(content)
            facts.append(MemoryFact(
                fact_type=ft,
                content=scan.sanitized,
                confidence_score=confidence,
                origin=MEMORY_ORIGIN_INFERRED,
                memory_key=memory_key,
                structured_value=structured_value,
            ))
        return facts, rejections

    # ── Retrieval ──
    async def retrieve(self, query: str, user_id: str = "default", k: int = 20,
                       tenant_id: str = "", domain: str | None = None) -> list[MemoryFact]:
        emb = self.embedding.embed_query(query)
        rows = await self._repo.search_hybrid(emb, user_id, top_k=k,
                                              tenant_id=normalize_tenant_id(tenant_id),
                                              domain=domain)
        return [MemoryFact(
            fact_type=r.memory_type,
            content=r.content,
            session_id=r.session_id,
            created_at=str(r.created_at),
            memory_key=r.memory_key,
            structured_value=r.structured_value,
            memory_id=str(r.id),
        ) for r, _sim in rows]

    async def store_with_resolution(self, fact: MemoryFact, user_id: str,
                                    session_id: str, tenant_id: str = "") -> StoreResult:
        """事实写入唯一入口（STOP C）：normalize → 裁决 → 原子落库。

        防线纵深：confidence 再 clamp、origin 非法值回退 legacy、key/value
        再归一（非法 → 双双 NULL 走 unkeyed）——工具路径不经过 parser，
        此处是最后一道代码级防线。
        裁决与原子 supersede 见 memory/conflict.py（集中实现）。
        """
        tenant_id = normalize_tenant_id(tenant_id)
        fact.confidence_score = clamp_confidence(
            fact.confidence_score, MEMORY_INFERRED_DEFAULT_CONFIDENCE)
        if fact.origin not in _VALID_ORIGINS:
            fact.origin = MEMORY_ORIGIN_LEGACY
        fact.memory_key = normalize_memory_key(fact.memory_key)
        if fact.memory_key is not None:
            # structured_value 与 content 同受 PII 防线（§75：key/value 不得
            # 成为绕过通道）；脱敏后再 normalize（"[邮箱]" 等占位符合法保留）
            raw_value = fact.structured_value
            if raw_value:
                sanitized_value = scan_and_sanitize(str(raw_value)).sanitized
            else:
                sanitized_value = None
            fact.structured_value = normalize_memory_value(sanitized_value)
            if fact.structured_value is None:
                fact.memory_key = None  # key/value 成对约束

        emb = self.embedding.embed_query(fact.content)
        from backend.memory import conflict
        if fact.memory_key is not None:
            return await conflict.resolve_keyed(
                self._repo, fact, user_id, session_id, tenant_id, emb)
        return await conflict.resolve_unkeyed(
            self._repo, fact, user_id, session_id, tenant_id, emb)

    async def store_single(self, fact: MemoryFact, user_id: str, session_id: str,
                           tenant_id: str = "") -> bool:
        """兼容 wrapper（§47）：旧调用方拿到 bool；核心路径用 store_with_resolution。"""
        result = await self.store_with_resolution(fact, user_id, session_id, tenant_id)
        return result.stored

