"""selection/market_index.py — 竞品市场语义索引（spec §3.2 / §4.2）

独立 Chroma collection `competitor_market`（persist: data/chroma_market），
不共用主知识库 persist 目录（ChromaKnowledgeStore 未指定 collection_name）。
embedding 复用 backend.rag.embedding_singleton 全局 BGE 实例，保证向量空间一致。

每条快照 → 一条文档（id = snap-{snapshot_id}，保留时序）。
"""
import os
from typing import Any, Optional

from backend.shared.logger import logger

_COLLECTION = "competitor_market"

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PROJECT_ROOT = os.path.dirname(_BACKEND_DIR)
MARKET_PERSIST_DIR = os.getenv(
    "MARKET_PERSIST_DIR", os.path.join(_PROJECT_ROOT, "data", "chroma_market")
)


def build_doc(snap: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """快照 → (doc_id, text, metadata)"""
    price = snap.get("price")
    band = ""
    if price is not None:
        band = "low" if price < 100 else ("mid" if price < 500 else "high")
    price_str = f"{price}{snap.get('currency') or 'CNY'}" if price is not None else "未知"
    text = (
        f"{snap.get('title') or ''}｜平台:{snap.get('platform') or 'generic'}"
        f"｜价格:{price_str}"
        f"｜卖点:{snap.get('highlights') or ''}"
        f"｜促销:{snap.get('promo_text') or ''}"
    )
    meta = {
        "url": snap.get("url") or "",
        "platform": snap.get("platform") or "generic",
        "category": snap.get("category") or "",
        "price_band": band,
        "crawled_at": snap.get("crawled_at") or "",
        "snapshot_id": snap.get("id") or 0,
    }
    return f"snap-{snap.get('id')}", text, meta


class MarketIndex:
    """竞品市场语义索引（懒加载 embedding，构造不触发 400MB 模型加载）"""

    def __init__(self, persist_directory: str = MARKET_PERSIST_DIR):
        self._persist_directory = persist_directory
        self._chroma = None

    def _ensure(self):
        if self._chroma is None:
            from langchain_chroma import Chroma
            from backend.rag.embedding_singleton import get_embedding
            os.makedirs(self._persist_directory, exist_ok=True)
            self._chroma = Chroma(
                collection_name=_COLLECTION,
                persist_directory=self._persist_directory,
                embedding_function=get_embedding(),
            )
            logger.info(f"[MarketIndex] 就绪: {self._persist_directory}")
        return self._chroma

    def index_snapshot(self, snap: dict[str, Any]) -> str:
        """索引一条快照，返回 doc id（无 id 时跳过返回空串）"""
        if not snap.get("id"):
            return ""
        doc_id, text, meta = build_doc(snap)
        self._ensure()._collection.upsert(ids=[doc_id], documents=[text], metadatas=[meta])
        return doc_id

    def search_trends(self, query: str, k: int = 10,
                      metadata_filter: Optional[dict] = None) -> list[dict[str, Any]]:
        """语义趋势检索（独立于主 RAG 管线）"""
        docs = self._ensure().similarity_search_with_score(query, k=k, filter=metadata_filter)
        return [
            {"text": d.page_content, "metadata": d.metadata, "score": float(s)}
            for d, s in docs
        ]

    def count(self) -> int:
        """collection 文档总数"""
        return self._ensure()._collection.count()


_index: Optional[MarketIndex] = None


def get_market_index() -> MarketIndex:
    """全局单例"""
    global _index
    if _index is None:
        _index = MarketIndex()
    return _index


def reset_market_index() -> None:
    """重置全局单例（测试隔离）"""
    global _index
    _index = None


def index_snapshot_safe(snap: dict[str, Any]) -> None:
    """供 competitor pipeline 调用的安全钩子：失败仅记日志，不影响采集主流程"""
    try:
        get_market_index().index_snapshot(snap)
    except Exception as e:
        logger.warning(f"[MarketIndex] 快照索引失败（忽略）: {e}")
