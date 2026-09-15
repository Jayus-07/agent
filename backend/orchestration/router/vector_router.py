"""vector_router.py — Embedding Router（2026-08-11）

复用 Chroma 路由索引：
- 启动时：把每条 capability 的 example queries embedding 存进 Chroma
- 查询时：top-K 相似度匹配
- 解决关键词覆盖不了的问题（"补货" → inventory_alert）

路由索引:
  backend/data/router_index/  (Chroma persist)
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import List

from backend.orchestration.router.types import (
    CapabilityScore,
    ExecutionMode,
    RouteDecision,
    ALL_CAPABILITIES,
    WORKFLOW_NAMES,
)


# ── 路由 example queries：由 capabilities.yaml 派生（唯一事实源）──
# 手写字典已成历史（曾与 skills 注册表漂移）。增删 examples 改 YAML；
# VectorRouter 启动时按 manifest 对账，数量不符自动重建索引。
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
    """Embedding Router：复用 Chroma 做语义路由。

    索引结构:
      collection: router_v1
      docs: [{text: example_query, metadata: {capability: name}}, ...]

    注意：embedding 模型更换（维度变化）后必须删除索引目录重建——
    Chroma 不会自动迁移维度，旧索引会导致检索报错并静默降级 LLM Router。
    """

    # 绝对路径（相对 cwd 的 "backend/data/router_index" 在 cwd=backend 时
    # 会解析成 backend/backend/data/... 双重嵌套）
    _DEFAULT_PERSIST_DIR = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "data", "router_index",
    )

    def __init__(self, persist_dir: str | None = None, collection_name: str = "router_v1"):
        self.persist_dir = persist_dir or self._DEFAULT_PERSIST_DIR
        self.collection_name = collection_name
        self._collection = None
        self._ensure_index()

    def _ensure_index(self) -> None:
        """启动时建索引（idempotent）。"""
        try:
            from langchain_chroma import Chroma
            from backend.rag.embedding_singleton import get_embedding

            Path(self.persist_dir).mkdir(parents=True, exist_ok=True)

            embedding = get_embedding()
            self._collection = Chroma(
                collection_name=self.collection_name,
                embedding_function=embedding,
                persist_directory=self.persist_dir,
            )

            # 空索引 → 初始化；非空但数量与 manifest 对不上 → manifest 改过
            # 而索引没重建（如新增 capability），自动重建自愈。旧逻辑只在
            # count()==0 时建，曾导致新增 capability 的 examples 永远进不了
            # 索引（静默漏路由）。
            count = self._collection._collection.count()
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
            self._collection = None

    def _init_examples(self) -> None:
        """把所有 capability 的 example queries 写入索引。"""
        from backend.shared.logger import logger
        if self._collection is None:
            return

        all_examples = []
        for cap, queries in {**ROUTE_EXAMPLES, **WORKFLOW_EXAMPLES}.items():
            for q in queries:
                all_examples.append({"text": q, "metadata": {"capability": cap}})

        if all_examples:
            self._collection.add_texts(
                texts=[e["text"] for e in all_examples],
                metadatas=[e["metadata"] for e in all_examples],
            )
            logger.info(f"[VectorRouter] 已建路由索引: {len(all_examples)} 条 example")

    def _rebuild_index(self) -> None:
        """删除旧 collection 并按当前 embedding 维度重建（embedding 模型换型自愈）。

        不删目录：chromadb 的 PersistentClient 对同一路径有进程级缓存，
        Windows 下 rmtree 会因文件锁静默失败、旧 schema 残留。改用
        delete_collection 清掉旧 schema，同一 client 内重建新维度 collection。
        """
        from backend.shared.logger import logger
        try:
            if self._collection is not None:
                self._collection._client.delete_collection(self.collection_name)
                logger.info(f"[VectorRouter] 已删除旧 collection: {self.collection_name}")
        except Exception:
            logger.debug("[VectorRouter] 删除旧 collection 失败", exc_info=True)
        self._collection = None
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

        if self._collection is None or self._collection._collection.count() == 0:
            return RouteDecision(
                execution_mode=ExecutionMode.PLAN,
                candidates=[],
                confidence=0.0,
                reason="向量索引未初始化，交给 LLM Router",
            )

        try:
            results = self._collection.similarity_search_with_score(query, k=top_k)
        except Exception as e:
            # embedding 模型更换（维度变化）→ 旧索引作废：自动重建一次再试，
            # 避免 vector 层静默失效导致所有请求落到 LLM Router（数秒级延迟）
            if "dimension" in str(e).lower():
                logger.warning(f"[VectorRouter] embedding 维度不匹配，自动重建路由索引: {e}")
                self._rebuild_index()
                try:
                    results = self._collection.similarity_search_with_score(query, k=top_k)
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

        # 归一化距离 → 相似度（chroma 默认 L2，越小越相似）
        # 实际不同 collection 距离分布不同，这里简化：直接用倒数
        candidates: list[CapabilityScore] = []
        for doc, distance in results:
            cap = doc.metadata.get("capability", "")
            # 简单归一化：1 / (1 + distance)
            score = 1.0 / (1.0 + distance)
            candidates.append(CapabilityScore(name=cap, score=round(score, 3)))

        # 确定性排序（2026-09-15）：同分候选按名字稳定排序，不再依赖
        # Chroma 返回顺序——索引重建后顺序漂移是路由波动来源之一。
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
