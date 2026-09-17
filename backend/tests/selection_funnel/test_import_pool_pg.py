"""test_import_pool_pg.py — 漏斗导入存储 PG 层测试（2026-09-18 PG 化）。

覆盖：
  1. 工厂分发纯函数：默认 postgres → PG 子类；SELECTION_FUNNEL_DB_BACKEND=sqlite → SQLite 逃生舱
  2. PostgresImportPoolStore：add_batch/list_candidates 往返（哑管道全量）、
     类目/平台过滤、clear_batch、count（批次 id 随机后缀防同秒碰撞）
  3. PostgresMarketStore：keywords 排序与类目过滤、reviews、clear_batch 跨两表

前置：本机 agent_business 库可达（并行会话 PG 容器 127.0.0.1:5433 或本地 5432）。
不可达时整文件 skip；unit-only 运行用 -m "not pg"。
隔离：专用表前缀 pgtest_sf_（SELECTION_FUNNEL_PG_TABLE_PREFIX + importlib.reload
重建模块级表名常量），fixture 前后 DROP。
"""
from __future__ import annotations

import importlib

import psycopg2
import pytest

from backend.config.database import SELECTION_PG_CONFIG

PG_PREFIX = "pgtest_sf_"
_TABLES = [
    f"{PG_PREFIX}import_candidates",
    f"{PG_PREFIX}keyword_stats",
    f"{PG_PREFIX}product_reviews",
]


def _pg_alive() -> bool:
    try:
        conn = psycopg2.connect(**SELECTION_PG_CONFIG, connect_timeout=2)
        conn.close()
        return True
    except Exception:
        return False


def _drop_tables():
    conn = psycopg2.connect(**SELECTION_PG_CONFIG)
    try:
        cur = conn.cursor()
        for t in _TABLES:
            cur.execute(f"DROP TABLE IF EXISTS {t}")
        conn.commit()
    finally:
        conn.close()


pytestmark = [
    pytest.mark.pg,
    pytest.mark.skipif(not _pg_alive(), reason="No PostgreSQL reachable (agent_business)"),
]


def _row(title: str, url: str = "", price: float | None = None,
         category: str = "宠物零食", platform: str = "淘宝") -> dict:
    return {"title": title, "url": url, "price": price,
            "category": category, "platform": platform}


@pytest.fixture()
def pg_sf(monkeypatch):
    """测试表前缀 + reload 重建模块级表名常量；前后 DROP 隔离表。"""
    monkeypatch.setenv("SELECTION_FUNNEL_PG_TABLE_PREFIX", PG_PREFIX)
    import backend.selection_funnel.import_pool_pg as ipp
    import backend.selection_funnel.market_data_pg as mdp
    importlib.reload(ipp)
    importlib.reload(mdp)
    _drop_tables()
    yield ipp, mdp
    _drop_tables()
    monkeypatch.setenv("SELECTION_FUNNEL_PG_TABLE_PREFIX", "")
    importlib.reload(ipp)
    importlib.reload(mdp)


class TestFactoryDispatch:
    """分发纯函数直测（绕开 conftest 对 get_import_store 的 monkeypatch）。"""

    def test_default_pg(self, pg_sf):
        import backend.selection_funnel.import_pool as ip
        import backend.selection_funnel.market_data as md
        ipp, mdp = pg_sf
        assert isinstance(ip._new_default_store(), ipp.PostgresImportPoolStore)
        assert isinstance(md._new_default_market(), mdp.PostgresMarketStore)

    def test_sqlite_escape_hatch(self, monkeypatch, tmp_path):
        import backend.selection_funnel.import_pool as ip
        import backend.selection_funnel.market_data as md
        monkeypatch.setenv("SELECTION_FUNNEL_DB_BACKEND", "sqlite")
        # fake 类捕获分发结果，不打真实磁盘/库
        created = {}

        class _FakeIP(ip.ImportPoolStore):
            def __init__(self):  # noqa: super 不建库
                created["ip"] = True

        class _FakeMD(md.MarketStore):
            def __init__(self):  # noqa: super 不建库
                created["md"] = True

        monkeypatch.setattr(ip, "ImportPoolStore", _FakeIP)
        monkeypatch.setattr(md, "MarketStore", _FakeMD)
        assert isinstance(ip._new_default_store(), _FakeIP)
        assert isinstance(md._new_default_market(), _FakeMD)


class TestImportPoolPG:
    def test_roundtrip_and_filters(self, pg_sf):
        ipp, _ = pg_sf
        store = ipp.PostgresImportPoolStore()
        batch, n = store.add_batch(
            [_row("冻干鸡肉 500g", "https://e.com/1", 149.0),
             _row("冻干牛肉 400g", "https://e.com/2", 129.0, platform="京东")],
            category="宠物零食")
        assert batch.startswith("imp-") and n == 2

        all_rows = store.list_candidates()
        assert len(all_rows) == 2
        top = all_rows[0]
        assert top["title"] == "冻干鸡肉 500g"
        assert top["price"] == 149.0
        assert top["category"] == "宠物零食"
        assert top["batch_id"] == batch and top["extra"] == {}

        assert len(store.list_candidates(category="宠物")) == 2
        assert store.list_candidates(category="美妆") == []
        assert [r["title"] for r in store.list_candidates(platform="京东")] == ["冻干牛肉 400g"]

    def test_list_returns_all_batches(self, pg_sf):
        """list_candidates 是哑管道（不去重）：跨批次同款全量返回，
        去重语义由 build_pool 的 duplicate 规则统一负责（见 test_pool_sources）。"""
        ipp, _ = pg_sf
        store = ipp.PostgresImportPoolStore()
        store.add_batch([_row("冻干鸡肉 500g", "https://e.com/1", 100.0)],
                        category="宠物零食")
        store.add_batch([_row("冻干鸡肉 500g", "https://e.com/1", 80.0)],
                        category="宠物零食")
        store.add_batch([_row("冻干牛肉 400g", "", 60.0)], category="宠物零食")

        rows = store.list_candidates()
        assert len(rows) == 3, "哑管道返回全部批次行（id ASC）"
        assert [r["price"] for r in rows] == [100.0, 80.0, 60.0]
        # 类目/平台过滤仍生效
        assert len(store.list_candidates(category="宠物")) == 3
        assert store.list_candidates(category="美妆") == []

    def test_clear_and_count(self, pg_sf):
        ipp, _ = pg_sf
        store = ipp.PostgresImportPoolStore()
        b1, _ = store.add_batch([_row("A", "https://e.com/a", 1.0)])
        b2, _ = store.add_batch([_row("B", "https://e.com/b", 2.0)])
        assert store.count() == 2
        assert store.clear_batch(b1) == 1
        assert store.count() == 1
        assert store.clear_batch("imp-none") == 0
        assert [r["batch_id"] for r in store.list_candidates()] == [b2]


class TestMarketPG:
    def test_keywords_sorted_and_filtered(self, pg_sf):
        _, mdp = pg_sf
        store = mdp.PostgresMarketStore()
        batch, n = store.add_keywords(
            [{"keyword": "冻干鸡肉", "search_pop": 8900, "click_rate": 0.12,
              "pay_rate": 0.08, "competition": 4200},
             {"keyword": "冻干牛肉", "search_pop": 12000}],
            category="宠物零食")
        assert batch.startswith("kw-") and n == 2

        kws = store.keywords("宠物")
        by_kw = {k["keyword"]: k for k in kws}
        assert len(kws) == 2
        assert by_kw["冻干牛肉"]["search_pop"] == 12000
        assert by_kw["冻干鸡肉"]["competition"] == 4200
        assert store.keywords("美妆") == []

    def test_reviews_and_clear_batch_cross_tables(self, pg_sf):
        _, mdp = pg_sf
        store = mdp.PostgresMarketStore()
        kw_batch, _ = store.add_keywords(
            [{"keyword": "冻干鸡肉", "search_pop": 1}], category="宠物零食")
        rv_batch, n = store.add_reviews(
            [{"product_title": "冻干鸡肉 500g", "content": "物流很慢", "star": 2},
             {"product_title": "冻干鸡肉 500g", "content": "包装压坏", "star": None}],
            category="宠物零食")
        assert rv_batch.startswith("rv-") and n == 2

        rvs = store.reviews("宠物")
        assert len(rvs) == 2 and rvs[0]["product_title"] == "冻干鸡肉 500g"

        removed = store.clear_batch(rv_batch)
        assert removed == 2, "只删差评批次"
        assert len(store.reviews()) == 0 and len(store.keywords()) == 1
        assert store.clear_batch(kw_batch) == 1
