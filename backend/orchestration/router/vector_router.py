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


# ── 路由 example queries（启动时建索引）──
# 每条 capability 5-10 条 example
ROUTE_EXAMPLES: dict[str, list[str]] = {
    "sql.query": [
        "最近 30 天销售金额",
        "本月销量前 10 的商品",
        "查询库存不足的商品",
        "上个月退款金额",
        "各品类商品数量统计",
        "排名前五的客户",
    ],
    "rag.search": [
        "公司制度是什么",
        "请解释退款流程",
        "员工福利有哪些规定",
        "广告政策说明",
        "商品上架要求",
        "差评处理 SOP",
    ],
    "business.analyze": [
        "分析库存不足的原因",
        "毛利率下降趋势分析",
        "找出销量下降的关键商品",
        "评估供应商绩效",
    ],
    "report.generate": [
        "生成月度销售报告",
        "生成库存分析报告",
        "生成竞品对比报告",
    ],
    "email.send": [
        "发送邮件给运营",
        "把日报发给 CEO",
        "邮件通知采购",
    ],
    "data.export": [
        "导出销售数据到 Excel",
        "把客户名单导出",
    ],
    "web.search": [
        "搜索 Amazon 政策更新",
        "查 Google 趋势",
    ],
    "web.crawl": [
        "抓取竞品网站",
        "爬取新闻内容",
    ],
    "data.collect": [
        "采集 90 天销售数据",
        "收集各平台数据",
    ],
    # 仅覆盖「候选地点检索」这一无状态能力；完整行程规划走旅游域图，
    # 不经主 Router（见 orchestration/graph/travel_prefilter.py）
    "travel.poi_search": [
        "福州有哪些值得去的景点",
        "厦门必去的打卡点有哪些",
        "帮我找杭州适合亲子的地方",
        "查一下鼓浪屿附近的景点",
    ],
}


# 特殊 workflow 不在 ALL_CAPABILITIES 里
WORKFLOW_EXAMPLES: dict[str, list[str]] = {
    "daily_report": [
        "每天跑日报",
        "自动生成日报",
        "今天的日报",
        "把日报发邮件",
    ],
    "inventory_alert": [
        "检查库存风险",
        "自动提醒采购",
        "库存预警扫描",
    ],
}


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

            # 如果索引为空，初始化
            if self._collection._collection.count() == 0:
                self._init_examples()
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
