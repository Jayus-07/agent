"""tests/selection_funnel/conftest.py — 漏斗域测试工厂

纪律：工厂函数造数据（不共享可变常量）；DB 属外部依赖，单测注入内存替身
（2026-09-18 SQLite 轨退场后，替身是唯一单测隔离手段——PG 真实行为由
test_import_pool_pg.py 集成测试锁定，替身只保语义对齐：id 单调、
list_candidates 旧行在前、history_by_keys 新→旧）；漏斗域无 checkpointer，
invoke 不需要 thread_id。
"""
from __future__ import annotations

import uuid
from datetime import datetime

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


class FakeImportPoolStore:
    """导入候选池内存替身（语义对齐 PostgresImportPoolStore）。"""

    def __init__(self):
        self._rows: list[dict] = []
        self._next_id = 1

    @staticmethod
    def _key(r: dict) -> str:
        from backend.selection_funnel.import_pool import dedup_key
        return dedup_key(r.get("url") or "", r.get("title") or "",
                         r.get("platform") or "")

    def add_batch(self, rows: list[dict], category: str = "",
                  platform: str = "") -> tuple[str, int]:
        batch_id = f"imp-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
        now = datetime.now().isoformat(timespec="seconds")
        for r in rows:
            self._rows.append({
                "id": self._next_id, "batch_id": batch_id,
                "title": r.get("title") or "",
                "platform": r.get("platform") or platform,
                "price": r.get("price"), "original_price": r.get("original_price"),
                "rating": r.get("rating"),
                "review_count": int(r["review_count"]) if r.get("review_count") is not None else None,
                "sales": int(r["sales"]) if r.get("sales") is not None else None,
                "unit_cost": r.get("unit_cost"),
                "category": r.get("category") or category,
                "url": r.get("url") or "",
                "promo_text": r.get("promo_text") or "",
                "highlights": r.get("highlights") or "",
                "extra": dict(r.get("extra") or {}),
                "imported_at": now,
            })
            self._next_id += 1
        return batch_id, len(rows)

    def list_candidates(self, category: str = "", platform: str = "") -> list[dict]:
        out = []
        for r in self._rows:
            if category and category not in (r.get("category") or ""):
                continue
            if platform and (r.get("platform") or "") != platform:
                continue
            d = dict(r)
            key = self._key(r)
            d["history_batches"] = len({x["batch_id"] for x in self._rows
                                        if self._key(x) == key})
            out.append(d)
        return out   # 插入序即 id 序（旧行在前，对齐 ORDER BY id ASC）

    def clear_batch(self, batch_id: str) -> int:
        before = len(self._rows)
        self._rows = [r for r in self._rows if r["batch_id"] != batch_id]
        return before - len(self._rows)

    def count(self) -> int:
        return len(self._rows)

    def history_by_keys(self, keys: list[tuple[str, str, str]],
                        limit: int = 50) -> dict[str, list[dict]]:
        from backend.selection_funnel.import_pool import dedup_key
        wanted = {dedup_key(u, t, p) for u, t, p in keys}
        grouped: dict[str, list[dict]] = {}
        for r in sorted(self._rows, key=lambda x: -x["id"]):   # 新→旧
            key = self._key(r)
            if key not in wanted:
                continue
            grouped.setdefault(key, []).append({
                "title": r["title"], "url": r["url"], "platform": r["platform"],
                "price": r["price"], "rating": r["rating"],
                "review_count": r["review_count"], "sales": r["sales"],
                "imported_at": r["imported_at"], "crawled_at": r["imported_at"],
            })
        return {k: v[:limit] for k, v in grouped.items()}


class FakeMarketStore:
    """赛道数据内存替身（语义对齐 PostgresMarketStore）。"""

    def __init__(self):
        self._kw: list[dict] = []
        self._rv: list[dict] = []

    @staticmethod
    def _batch(prefix: str) -> tuple[str, str]:
        return (f"{prefix}-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}",
                datetime.now().isoformat(timespec="seconds"))

    def add_keywords(self, rows: list[dict], category: str) -> tuple[str, int]:
        batch_id, now = self._batch("kw")
        self._kw.extend({"batch_id": batch_id, "category": category,
                         "keyword": r.get("keyword") or "",
                         "search_pop": r.get("search_pop"),
                         "click_rate": r.get("click_rate"),
                         "pay_rate": r.get("pay_rate"),
                         "competition": r.get("competition"),
                         "imported_at": now} for r in rows)
        return batch_id, len(rows)

    def add_reviews(self, rows: list[dict], category: str) -> tuple[str, int]:
        batch_id, now = self._batch("rv")
        self._rv.extend({"batch_id": batch_id, "category": category,
                         "product_title": r.get("product_title") or "",
                         "content": r.get("content") or "",
                         "star": r.get("star"), "imported_at": now}
                        for r in rows)
        return batch_id, len(rows)

    def keywords(self, category: str = "") -> list[dict]:
        rows = [dict(r) for r in self._kw
                if not category or category in (r.get("category") or "")]
        rows.sort(key=lambda r: -(r["search_pop"] or 0))
        return rows

    def reviews(self, category: str = "") -> list[dict]:
        return [dict(r) for r in self._rv
                if not category or category in (r.get("category") or "")]

    def clear_batch(self, batch_id: str) -> int:
        removed = sum(1 for r in self._kw if r["batch_id"] == batch_id)
        removed += sum(1 for r in self._rv if r["batch_id"] == batch_id)
        self._kw = [r for r in self._kw if r["batch_id"] != batch_id]
        self._rv = [r for r in self._rv if r["batch_id"] != batch_id]
        return removed


@pytest.fixture(autouse=True)
def isolated_rag(monkeypatch):
    """知识层 RAG 桥默认隔离——测试绝不拉起真实 RAG pipeline
    （torch/embedding 初始化分钟级拖慢 + 碰真实知识库）。测 RAG 注入的用例自行覆盖。"""
    import backend.selection_funnel.knowledge as k
    monkeypatch.setattr(k, "rag_enhance", lambda c, p, top_k=3: ([], ""))


@pytest.fixture(autouse=True)
def isolated_import_store(monkeypatch):
    """导入候选池替换身——单测绝不打真实 PG（agent_business）。"""
    from backend.selection_funnel import import_pool
    store = FakeImportPoolStore()
    monkeypatch.setattr(import_pool, "get_import_store", lambda: store)
    return store


@pytest.fixture(autouse=True)
def isolated_market_store(monkeypatch):
    """赛道数据（关键词榜/差评）store 替身。"""
    import backend.selection_funnel.market_data as md
    store = FakeMarketStore()
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
