"""SQL 高频示例的表路由回归测试。"""

from backend.sql import router


def _disable_router_cache(monkeypatch):
    monkeypatch.setattr(router._sql_router_cache, "get_json", lambda _key: None)
    monkeypatch.setattr(router._sql_router_cache, "set_json", lambda *_args: None)


def test_category_sales_includes_order_item_price_tables(monkeypatch):
    _disable_router_cache(monkeypatch)

    tables = router.select_tables("统计各商品分类的销售额排名")

    assert {
        "product.categories",
        "product.products",
        "order.order_items",
    } <= set(tables)


def test_context_tables_are_intersected_with_allowed_tables(monkeypatch):
    _disable_router_cache(monkeypatch)

    tables = router.select_tables(
        "按月展示",
        allowed_tables=["product.products"],
        context_tables=["product.products", "order.order_items"],
    )

    assert tables == ["product.products"]


def test_keyword_fast_path_skips_llm_without_context(monkeypatch):
    """回归：无 context_tables 时必须走关键词快路径，不得调用 LLM。

    缺陷（2026-10-08 实测）：`_normalize_tables(None, all_tables)` 的语义是
    「不过滤 = 全部表」（授权面正需要这个语义），把它直接当 `remembered`
    会让 fast_match 恒等于全表，`1 <= len(fast_match) <= 3` 永不成立——
    关键词快路径变死代码，每次选表都打 LLM；LLM 不可用时静默回退 18 张表。
    """
    _disable_router_cache(monkeypatch)

    class _NoLLM:
        """选表 LLM 替身：一旦被调用即判定快路径失守。"""

        def invoke(self, *_args, **_kwargs):
            raise AssertionError("关键词命中 1~3 张表时不应调用 LLM")

    monkeypatch.setattr(router, "llm", _NoLLM())

    tables = router.select_tables("最近 30 天销量最高的 5 个商品")

    # 集合断言：关键词命中集合来自 set 迭代，顺序不保证
    assert set(tables) == {"product.products", "order.order_items"}
    assert len(tables) == 2


def test_builtin_examples_have_their_fact_tables(monkeypatch):
    _disable_router_cache(monkeypatch)
    examples = {
        "查询库存低于安全库存的商品及其库存量": {
            "inventory.inventory", "product.products",
        },
        "统计各商品分类的销售额排名": {
            "product.categories", "product.products", "order.order_items",
        },
        "列出最近 10 个订单的金额": {"order.orders"},
        "对比竞品价格与我们售价的差异": {
            "crawler.competitor_products", "crawler.competitor_price",
            "product.products",
        },
    }

    for question, required_tables in examples.items():
        assert required_tables <= set(router.select_tables(question))
