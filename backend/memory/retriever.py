"""HybridRetriever — L3 读取管线（STOP D）。

职责（§72）：semantic relevance gate → global preference 分流 → hybrid
rank → merge/dedup → max-K。SQL 层 hard eligibility（scope/active/not
expired）在 MemoryRepository.search_hybrid；safe context 与 mark_accessed
在 MemoryService。

Relevance 口径（§15）：cosine similarity = 1 - cosine_distance，范围约
[0,1]，越高越相关——gate 只看 semantic score（§14：importance/recency
不得参与 gate，只参与 gate 之后的排序）。

Global preference（§88-91）：memory_key 命中 MEMORY_GLOBAL_KEY_PREFIXES
白名单的 active 记忆（如 response.language）与问题主题无关也适用，免
semantic gate；上限 MEMORY_MAX_GLOBAL_PREFERENCES，与 semantic 命中共享
MEMORY_MAX_INJECTED 总上限。白名单为确定性配置，禁止 LLM 决定 global。
"""
from datetime import datetime, timezone
from typing import Any

from backend.config import (
    MEMORY_GLOBAL_KEY_PREFIXES,
    MEMORY_MAX_GLOBAL_PREFERENCES,
    MEMORY_MAX_INJECTED,
    MEMORY_MIN_RELEVANCE_SCORE,
    MEMORY_RETRIEVAL_CANDIDATES,
)
from backend.memory.keying import normalize_tenant_id, origin_priority


class RetrievedMemory:
    """检索产物：record + 双分数（gate 用 semantic，排序用 hybrid）。"""

    __slots__ = ("record", "semantic_score", "rank_score", "source")

    def __init__(self, record: Any, semantic_score: float,
                 rank_score: float, source: str):
        self.record = record
        self.semantic_score = semantic_score
        self.rank_score = rank_score
        self.source = source  # global | semantic


def _is_global_key(memory_key: str | None) -> bool:
    if not memory_key:
        return False
    key = memory_key.strip().lower()
    return any(key.startswith(p) for p in MEMORY_GLOBAL_KEY_PREFIXES if p)


def _metric_safe(fn, **labels) -> None:
    try:
        fn(**labels)
    except Exception:  # pragma: no cover - 观测面异常不外泄
        pass


class HybridRetriever:
    def __init__(self, repo):
        self._repo = repo
        # 最近一次 retrieve 的阶段计数（STOP G observability：供
        # memory.retrieve span 归因；只含 count/threshold，无任何内容）
        self.last_stage_counts: dict = {}

    async def retrieve(
        self, query: str, embedding: list[float], user_id: str,
        top_k: int | None = None, tenant_id: str = "",
        enforce_gate: bool = True, domain: str | None = None,
    ) -> list[RetrievedMemory]:
        """candidate（SQL eligibility）→ gate → rank → merge → max-K。

        enforce_gate=False 供 memory_search_tool 显式搜索路径（§70/§71：
        显式搜索语义宽松，但 SQL eligibility/scope 过滤同样生效）。
        返回 0 条完全合法（D-I8）；不足 max-K 不补无关项（D-I9）。
        """
        from backend.observability.metrics import (
            memory_retrieval_accepted_total,
            memory_retrieval_candidate_total,
            memory_retrieval_rejected_total,
        )

        tenant_id = normalize_tenant_id(tenant_id)
        max_k = max(1, int(top_k or MEMORY_MAX_INJECTED))
        threshold = min(max(MEMORY_MIN_RELEVANCE_SCORE, 0.0), 1.0)  # §65 归一
        candidate_k = max(MEMORY_RETRIEVAL_CANDIDATES, max_k)       # §66 invariant

        candidates = await self._repo.search_hybrid(
            embedding, user_id, top_k=candidate_k, tenant_id=tenant_id,
            domain=domain)
        _metric_safe(memory_retrieval_candidate_total.inc)
        now = datetime.now(timezone.utc)

        globals_: list[RetrievedMemory] = []
        semantic: list[RetrievedMemory] = []
        rejected = 0
        global_accepted = 0
        for record, sim in candidates:  # [(record, cosine_similarity)]
            if enforce_gate and not _is_global_key(record.memory_key) and sim < threshold:
                rejected += 1
                _metric_safe(memory_retrieval_rejected_total.labels(
                    reason="below_relevance").inc)
                continue
            if _is_global_key(record.memory_key):
                globals_.append(RetrievedMemory(record, sim, 0.0, "global"))
            else:
                semantic.append(RetrievedMemory(
                    record, sim, self._rank_score(sim, record, now), "semantic"))

        # rank：global 按 origin/confidence/recency（§92 Step 3A）；
        # semantic 沿用 0.5/0.3/0.2 hybrid（§18，不重调权）。
        # 稳定 tie-break（§19）：score → semantic → importance → 时间 → id。
        globals_.sort(key=lambda m: (
            -origin_priority(m.record.origin),
            -m.record.confidence_score,
            -(m.record.last_access_at.timestamp() if m.record.last_access_at else 0),
            str(m.record.id),
        ))
        semantic.sort(key=lambda m: (
            -m.rank_score,
            -m.semantic_score,
            -m.record.importance_score,
            -(m.record.created_at.timestamp() if m.record.created_at else 0),
            str(m.record.id),
        ))

        # merge/dedup（§96：global 同时命中 semantic 也只注入一次）＋
        # global 保留额（§91）＋ 总上限 max_k（§98：global+semantic ≤ K）。
        seen: set[str] = set()
        merged: list[RetrievedMemory] = []
        global_quota = min(len(globals_), MEMORY_MAX_GLOBAL_PREFERENCES, max_k)
        for m in globals_[:global_quota]:
            seen.add(str(m.record.id))
            merged.append(m)
        for m in semantic:
            if len(merged) >= max_k:
                break
            rid = str(m.record.id)
            if rid in seen:
                continue
            seen.add(rid)
            merged.append(m)

        for m in merged:
            _metric_safe(memory_retrieval_accepted_total.labels(source=m.source).inc)
        # 阶段计数快照（STOP G：memory.retrieve span 归因）——accepted 为
        # gate 后、merge 前的口径；final_injected 为 merge/max-K 后终值。
        # 只含 count/threshold 枚举，无任何记忆内容/PII。
        self.last_stage_counts = {
            "candidate_count": len(candidates),
            "semantic_accepted": len(semantic),
            "semantic_rejected": rejected,
            "global_accepted": len(globals_),
            "final_injected": len(merged),
            "threshold": float(threshold) if enforce_gate else 0.0,
        }
        return merged

    @staticmethod
    def _rank_score(sim: float, record, now: datetime) -> float:
        """post-gate hybrid rank：0.5 semantic + 0.3 importance + 0.2 recency。"""
        try:
            delta_days = (now - record.last_access_at).days
        except (TypeError, ValueError):
            delta_days = 365
        recency = max(0.0, 1.0 - max(0, delta_days) / 365.0)
        return 0.5 * sim + 0.3 * record.importance_score + 0.2 * recency
