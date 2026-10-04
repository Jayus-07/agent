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
