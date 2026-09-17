"""vector_router.py — Embedding Router（2026-08-11 / 2026-09-17 切 pgvector）

复用 pgvector 路由索引（rag_vectors 表 collection="router_index"）：
- 启动时：把每条 capability 的 example queries embedding 写入 pgvector
- 查询时：top-K 相似度匹配
- 解决关键词覆盖不了的问题（"补货" → inventory_alert）

历史：曾用 Chroma 本地目录 backend/data/router_index/（已随全站存储
收口 pgvector 删除，examples 由 manifest 重新嵌入，无需迁移旧数据）。

路由 examples 由 capabilities.yaml 派生（唯一事实源）。增删 examples 改
YAML；VectorRouter 启动时按 manifest 对账，数量不符自动重建索引。
"""
from __future__ import annotations

import os
from typing import List

from backend.orchestration.router.types import (
    CapabilityScore,
    ExecutionMode,
    RouteDecision,
    ALL_CAPABILITIES,
    WORKFLOW_NAMES,
)


# ── 路由 example queries：由 capabilities.yaml 派生（唯一事实源）──
# 手写字典已成历史（曾与 skills 注册表漂移）。
from backend.orchestration.router.manifest import load_manifest

_manifest = load_manifest()
ROUTE_EXAMPLES: dict[str, list[str]] = {
    c.name: list(c.examples) for c in _manifest.routed_capabilities
}
WORKFLOW_EXAMPLES: dict[str, list[str]] = {
    w.name: list(w.examples) for w in _manifest.workflows
}
_EXPECTED_EXAMPLE_COUNT = _manifest.total_example_count


class VectorRouter:
    """Embedding Router：pgvector 语义路由。

    索引结构:
      collection: router_index（rag_vectors 表 collection 列）
      docs: [{text: example_query, metadata: {capability: name}}, ...]

    注意：embedding 模型更换（维度变化）后 rag_vectors 表列维度必须
    重建（运维操作）；维度不符时写入/查询会报错并静默降级 LLM Router。
    """

    # collection 名（pgvector rag_vectors.collection）。保留历史路径常量
    # 语义：旧 Chroma 目录名 basename 即 "router_index"。
    _DEFAULT_COLLECTION = os.environ.get("ROUTER_INDEX_COLLECTION", "router_index")

    def __init__(self, persist_dir: str | None = None, collection_name: str | None = None):
        # persist_dir 参数保留兼容旧签名，pgvector 下仅作 collection 名来源
        self.collection_name = collection_name or persist_dir or self._DEFAULT_COLLECTION
        self._store = None
        self._ensure_index()

    def _ensure_index(self) -> None:
        """启动时建索引（idempotent）。"""
        try:
            from backend.rag.embedding_singleton import get_embedding
            from backend.rag.vectorstore.pgvector_store import PgVectorKnowledgeStore

            self._store = PgVectorKnowledgeStore(
                persist_directory=self.collection_name,
                embedding_function=get_embedding(),
            )

            # 空索引 → 初始化；非空但数量与 manifest 对不上 → manifest 改过
            # 而索引没重建（如新增 capability），自动重建自愈。旧逻辑只在
            # count()==0 时建，曾导致新增 capability 的 examples 永远进不了
            # 索引（静默漏路由）。
            count = self._store.count()
            if count == 0:
                self._init_examples()
            elif count != _EXPECTED_EXAMPLE_COUNT:
                from backend.shared.logger import logger
                logger.warning(
                    f"[VectorRouter] 路由索引条数({count})与 manifest({_EXPECTED_EXAMPLE_COUNT})"
                    "不符，自动重建索引"
                )
                self._rebuild_index()
        except Exception as e:
            # 启动期失败不阻塞（让 LLM Router 兜底）
            from backend.shared.logger import logger
            logger.warning(f"[VectorRouter] 索引初始化失败（将走 LLM Router）: {e}")
            self._store = None

    def _init_examples(self) -> None:
        """把所有 capability 的 example queries 写入索引。"""
        from backend.shared.logger import logger
        if self._store is None:
            return

        all_examples = []
        for cap, queries in {**ROUTE_EXAMPLES, **WORKFLOW_EXAMPLES}.items():
            for q in queries:
                all_examples.append({"text": q, "metadata": {"capability": cap}})

        if all_examples:
            self._store.add_texts(
                texts=[e["text"] for e in all_examples],
                metadatas=[e["metadata"] for e in all_examples],
            )
            logger.info(f"[VectorRouter] 已建路由索引: {len(all_examples)} 条 example")

    def _rebuild_index(self) -> None:
        """清空当前 collection 并按当前 manifest 重建（自愈）。

        pgvector 下索引是表行而非目录文件：先按全量 id 删除再重插，
        幂等且不留残留（capability 删除后旧行也一并清掉）。
        """
        from backend.shared.logger import logger
        try:
            if self._store is not None:
                existing = self._store.get()
                ids = existing.get("ids") or []
                if ids:
                    self._store.delete(ids=ids)
                    logger.info(
                        f"[VectorRouter] 已清空旧路由索引: {len(ids)} 行"
                    )
        except Exception:
            logger.debug("[VectorRouter] 清空旧路由索引失败", exc_info=True)
        self._store = None
        self._ensure_index()

    def route(self, query: str, top_k: int = 3, confidence_threshold: float = 0.85) -> RouteDecision:
        """Embedding 相似度匹配，返回 candidates + 分数。

        Args:
            query: 用户问题
            top_k: 取前 K 个最相似 capability
            confidence_threshold: 高于此值视为强信号（但 Router 不绑定 mode）

        Returns:
            RouteDecision: candidates 是按相似度排序的列表
        """
        from backend.shared.logger import logger

        if self._store is None or self._store.count() == 0:
            return RouteDecision(
                execution_mode=ExecutionMode.PLAN,
                candidates=[],
                confidence=0.0,
                reason="向量索引未初始化，交给 LLM Router",
            )

        try:
            results = self._store.similarity_search_with_score(query, k=top_k)
        except Exception as e:
            # pgvector 侧检索异常（连接/维度等）→ 自动重建一次再试；
            # 仍失败则降级 LLM Router（数秒级延迟）
            if "dimension" in str(e).lower():
                logger.warning(f"[VectorRouter] embedding 维度不匹配，自动重建路由索引: {e}")
                self._rebuild_index()
                try:
                    results = self._store.similarity_search_with_score(query, k=top_k)
                except Exception as e2:
                    logger.warning(f"[VectorRouter] 重建后检索仍失败: {e2}")
                    return RouteDecision(
                        execution_mode=ExecutionMode.PLAN,
                        candidates=[],
                        confidence=0.0,
                        reason="检索异常，交给 LLM Router",
                    )
            else:
                logger.warning(f"[VectorRouter] 检索失败: {e}")
                return RouteDecision(
                    execution_mode=ExecutionMode.PLAN,
                    candidates=[],
                    confidence=0.0,
                    reason="检索异常，交给 LLM Router",
                )

        # 归一化距离 → 相似度。pgvector cosine distance 已按 Chroma 量纲
        # （2−2·cos_sim）×2 对齐，分数公式与迁移前一致：1 / (1 + distance)
        candidates: list[CapabilityScore] = []
        for doc, distance in results:
            cap = doc.metadata.get("capability", "")
            score = 1.0 / (1.0 + distance)
            candidates.append(CapabilityScore(name=cap, score=round(score, 3)))

        # 确定性排序（2026-09-15）：同分候选按名字稳定排序，不再依赖
        # 检索返回顺序——索引重建后顺序漂移是路由波动来源之一。
        candidates.sort(key=lambda c: (-c.score, c.name))

        # 整体置信度 = top1 分数
        top1 = candidates[0].score if candidates else 0.0
        reason = f"embedding top1={candidates[0].name} score={top1:.2f}" if candidates else "no match"

        # 决定 mode：根据 top1 capability 类型
        top1_cap = candidates[0].name if candidates else None
        if top1_cap in WORKFLOW_NAMES:
            execution_mode = ExecutionMode.WORKFLOW
        else:
            execution_mode = ExecutionMode.DIRECT

        return RouteDecision(
            execution_mode=execution_mode,
            candidates=candidates,
            confidence=top1,
            reason=reason,
            workflow_name=top1_cap if execution_mode == ExecutionMode.WORKFLOW else None,
        )
