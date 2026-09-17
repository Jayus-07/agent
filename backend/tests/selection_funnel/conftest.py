"""tests/selection_funnel/conftest.py — 漏斗域测试工厂

纪律：工厂函数造数据（不共享可变常量）；monkeypatch 两个 _default_store
隔离真实竞品库；漏斗域无 checkpointer，invoke 不需要 thread_id。
"""
from __future__ import annotations

import pytest

SNAP_FIELDS = ("url", "title", "platform", "price", "original_price", "currency",
               "rating", "review_count", "highlights", "promo_text", "in_stock",
               "category", "crawled_at", "snapshot_id")


def make_snap(**overrides) -> dict:
    """快照工厂：合理默认值 + 显式覆盖。"""
    snap = {
        "url": "https://example.com/item",
        "title": "宠物零食冻干鸡肉 500g",
        "platform": "淘宝",
        "price": 59.0,
        "original_price": 79.0,
        "currency": "CNY",
        "rating": 4.6,
        "review_count": 200,
        "highlights": "大容量,便携",
        "promo_text": "",
        "in_stock": True,
        "category": "宠物零食",
        "crawled_at": "2026-09-01T10:00:00",
        "snapshot_id": 1,
    }
    snap.update(overrides)
    return snap


class FakeCompetitorStore:
    """competitor.store 同接口假实现：list_watch / latest_snapshot / history"""

    def __init__(self, snaps: list[dict]):
        self._by_url: dict[str, list[dict]] = {}
        self._order: list[dict] = []
        for s in snaps:
            url = s["url"]
            self._by_url.setdefault(url, []).append(s)
            self._order.append({"url": url, "name": s.get("title", ""), "enabled": True})

    def list_watch(self, enabled_only: bool = False) -> list[dict]:
        return [dict(w) for w in self._order]

    def latest_snapshot(self, url: str) -> dict | None:
        snaps = self._by_url.get(url) or []
        return snaps[0] if snaps else None

    def history(self, url: str, limit: int = 50) -> list[dict]:
        return list(self._by_url.get(url) or [])[:limit]


@pytest.fixture(autouse=True)
def isolated_rag(monkeypatch):
    """知识层 RAG 桥默认隔离——测试绝不拉起真实 RAG pipeline
    （torch/embedding 初始化分钟级拖慢 + 碰真实知识库）。测 RAG 注入的用例自行覆盖。"""
    import backend.selection_funnel.knowledge as k
    monkeypatch.setattr(k, "rag_enhance", lambda c, p, top_k=3: ([], ""))


@pytest.fixture(autouse=True)
def isolated_import_store(tmp_path, monkeypatch):
    """导入候选池隔离到临时库——测试绝不碰真实 data/selection_import.db。

    pool_builder 在函数内动态 import get_import_store，monkeypatch 模块属性即生效。
    """
    from backend.selection_funnel import import_pool
    store = import_pool.ImportPoolStore(str(tmp_path / "import_pool_test.db"))
    monkeypatch.setattr(import_pool, "get_import_store", lambda: store)
    return store


@pytest.fixture(autouse=True)
def isolated_market_store(isolated_import_store, monkeypatch):
    """赛道数据（关键词榜/差评）store 隔离——与导入池同 tmp 库文件。"""
    import backend.selection_funnel.market_data as md
    tmp_db = str(isolated_import_store._db_path)
    store = md.MarketStore(tmp_db)
    monkeypatch.setattr(md, "get_market_store", lambda: store)
    return store


@pytest.fixture
def patch_stores(monkeypatch):
    """把漏斗域两个数据源入口都替换为 FakeCompetitorStore 工厂。"""
    def _install(snaps: list[dict]) -> FakeCompetitorStore:
        store = FakeCompetitorStore(snaps)
        from backend.selection_funnel.stages import pool_builder, verifier
        monkeypatch.setattr(pool_builder, "_default_store", lambda: store)
        monkeypatch.setattr(verifier, "_default_store", lambda: store)
        return store
    return _install


@pytest.fixture
def funnel_graph():
    from backend.selection_funnel.graph_builder import get_selection_funnel_graph
    return get_selection_funnel_graph()
