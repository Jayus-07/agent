"""SQL 高频示例的表路由回归测试。"""

import json
import re
from pathlib import Path
from types import SimpleNamespace

from backend.evaluation.dataset.loader import verify_dataset_integrity
from backend.prompts.loader import load_defaults
from backend.sql import router, sql_generator
from backend.sql.query_examples import SQL_QUERY_EXAMPLES, select_query_examples
from backend.sql.sql_validator import sql_validator


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


def test_query_examples_match_chinese_aliases_within_selected_tables():
    examples = select_query_examples(
        "请查库存不足的商品及其库存量",
        ["inventory.inventory", "product.products"],
    )

    assert [example.key for example in examples] == ["inventory_low_stock"]


def test_query_examples_require_every_example_table_in_selected_scope():
    examples = select_query_examples(
        "排除已取消订单，按商品明细数量乘以单价统计各分类销售额排名",
        ["product.categories", "product.products"],
    )

    assert examples == []


def test_refund_rate_router_uses_metric_specific_fact_tables(monkeypatch):
    _disable_router_cache(monkeypatch)
    captured = {}
    question = "退款率最高的商品是哪个？"

    def fake_render_sync(_key, **variables):
        captured.update(variables)
        return SimpleNamespace(text="router prompt")

    monkeypatch.setattr(router.prompt_service, "render_sync", fake_render_sync)
    monkeypatch.setattr(
        router,
        "llm",
        SimpleNamespace(
            invoke=lambda _prompt: SimpleNamespace(
                content='["product.products", "order.order_items", '
                '"order.refunds"]'
            )
        ),
    )

    keyword_tables = set(router._keyword_match(question))
    tables = router.select_tables(question)

    assert keyword_tables == {
        "product.products", "order.order_items", "order.orders",
        "order.refunds",
    }
    assert set(tables) == {
        "product.products", "order.order_items", "order.refunds",
    }
    assert "order.order_items" in captured["table_list"]
    assert "order.refunds" in captured["table_list"]


def test_query_examples_match_explicit_project_refund_rate_definition():
    examples = select_query_examples(
        "退款率最高的商品是哪个？",
        ["product.products", "order.order_items", "order.refunds"],
    )

    assert [example.key for example in examples] == ["top_refund_rate_products"]
    assert "SUM(quantity * price) AS sales_amt" in examples[0].sql
    assert "SUM(refund_amount) AS refund_amt" in examples[0].sql
    assert "LEFT JOIN (" in examples[0].sql
    assert "NULLIF(s.sales_amt, 0)" in examples[0].sql
    assert "WHERE s.sales_amt > 0" in examples[0].sql
    assert "ORDER BY refund_rate DESC NULLS LAST" in examples[0].sql
    assert "COUNT(r.id)" not in examples[0].sql

    count_example = next(
        example for example in SQL_QUERY_EXAMPLES
        if example.key == "top_refunded_products"
    )
    assert "COUNT(r.id) AS refund_count" in count_example.sql
    assert "refund_rate" not in count_example.sql


def test_sql_generator_default_prompt_handles_undefined_refund_rates():
    template = load_defaults()["sql.generator"]

    assert "退款率排名必须排除销售额分母为 NULL 或小于等于零的商品" in template
    assert "显式使用 NULLS LAST" in template


def test_sql_generator_preserves_calendar_comparison_and_empty_sum_semantics():
    template = load_defaults()["sql.generator"]
    contract = sql_generator.SQL_QUERY_SEMANTIC_CONTRACT

    assert "只查询这两个自然月" in template
    assert "空结果上的 SUM 是 NULL" in template
    assert "只生成这两个自然月" in contract
    assert "没有记录就是 0" in contract
    assert "稳定主键和展示名称" in template
    assert "稳定主键和展示名称" in contract


def test_sql_v4_time_semantics_golden_has_closed_period_and_explicit_empty_sum():
    dataset = (
        Path(__file__).resolve().parents[2]
        / "evaluation" / "datasets" / "sql_v4" / "cases.jsonl"
    )
    manifest = json.loads(
        (dataset.parent / "manifest.json").read_text(encoding="utf-8")
    )
    verify_dataset_integrity("sql_v4", dataset.parent)
    cases = {
        item["id"]: item
        for item in map(json.loads, dataset.read_text(encoding="utf-8").splitlines())
    }

    assert manifest["version"] == "4.0"
    assert manifest["parent_dataset"] == "sql_v3"
    compare_sql = cases["SC-121"]["expected"]["gold_sql"]
    assert compare_sql.count("UNION ALL") == 1
    assert compare_sql.count("< date_trunc('month', CURRENT_DATE) + INTERVAL '1 month'") == 2
    assert "ELSE NULL" in compare_sql
    assert "ORDER BY m.month_start" in compare_sql

    daily_sql = cases["SC-127"]["expected"]["gold_sql"]
    assert "COALESCE(SUM(oi.quantity), 0)" in daily_sql
    assert "o.created_at < CURRENT_DATE + INTERVAL '1 day'" in daily_sql


def test_sql_v5_groupings_use_stable_entity_keys():
    dataset = (
        Path(__file__).resolve().parents[2]
        / "evaluation" / "datasets" / "sql_v5" / "cases.jsonl"
    )
    manifest = json.loads(
        (dataset.parent / "manifest.json").read_text(encoding="utf-8")
    )
    verify_dataset_integrity("sql_v5", dataset.parent)
    cases = {
        item["id"]: item
        for item in map(json.loads, dataset.read_text(encoding="utf-8").splitlines())
    }

    assert manifest["version"] == "5.0"
    assert manifest["parent_dataset"] == "sql_v4"
    for case_id in (
        "SC-003", "SC-005", "SC-007", "SC-009", "SC-015", "SC-104",
        "SC-109", "SC-113", "SC-115", "SC-119", "SC-120", "SC-122",
        "SC-125", "SC-126",
    ):
        sql = cases[case_id]["expected"]["gold_sql"]
        assert re.search(r"GROUP BY\s+(?:[a-z]+\.id|[^\n]*\b[a-z]+\.id\s*,)", sql, re.I), case_id

    distinct_sql = cases["SC-111"]["expected"]["gold_sql"]
    assert "DISTINCT ON (c.id)" in distinct_sql
    assert "ORDER BY c.id, p.sale_price DESC, p.id" in distinct_sql


def test_refund_rate_example_matches_sql_v3_sc117():
    dataset = (
        Path(__file__).resolve().parents[2]
        / "evaluation" / "datasets" / "sql_v3" / "cases.jsonl"
    )
    manifest = json.loads(
        (dataset.parent / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["version"] == "3.0"
    verify_dataset_integrity("sql_v3", dataset.parent)
    golden_case = next(
        json.loads(line)
        for line in dataset.read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("id") == "SC-117"
    )
    example = next(
        item for item in SQL_QUERY_EXAMPLES
        if item.key == "top_refund_rate_products"
    )

    def normalize_sql(sql: str) -> str:
        normalized = " ".join(sql.split()).casefold()
        normalized = re.sub(r"\(\s+", "(", normalized)
        return re.sub(r"\s+\)", ")", normalized)

    assert normalize_sql(example.sql) == normalize_sql(
        golden_case["expected"]["gold_sql"]
    )


def test_query_examples_skip_unrelated_questions():
    examples = select_query_examples(
        "请查找退货审批的制度流程",
        ["product.products", "order.refunds"],
    )

    assert examples == []


def test_query_examples_skip_ambiguous_business_terms():
    ambiguous_questions = (
        "近一个月订单量",
        "退货次数最多的商品",
        "退款商品数量排名",
        "退款最多的商品排名",
        "高退款商品",
    )

    for question in ambiguous_questions:
        assert select_query_examples(
            question,
            ["order.orders", "product.products", "order.refunds"],
        ) == []


def test_query_examples_do_not_match_negated_request_phrases():
    category_tables = [
        "product.categories", "product.products", "order.order_items",
        "order.orders",
    ]
    inventory_tables = ["inventory.inventory", "product.products"]
    negated_cases = (
        (
            "不要排除已取消订单，按商品明细数量乘以单价统计各分类销售额排名",
            category_tables,
        ),
        (
            "不排除已取消订单后按商品明细统计分类销售额",
            category_tables,
        ),
        (
            "不要查询库存低于安全库存的商品及其库存量",
            inventory_tables,
        ),
    )

    for question, tables in negated_cases:
        assert select_query_examples(question, tables) == []

    examples = select_query_examples(
        "不要查退款次数，改查最近30天订单量",
        ["order.orders"],
    )
    assert [example.key for example in examples] == ["recent_order_count"]


def test_all_query_examples_pass_sql_validator_and_match_declared_tables():
    for example in SQL_QUERY_EXAMPLES:
        _, tables, _ = sql_validator.validate(example.sql)

        assert set(tables) == set(example.tables)


def test_generate_sql_includes_only_scope_safe_query_examples(monkeypatch):
    captured = {}

    def fake_render_sync(key, **variables):
        captured["key"] = key
        captured.update(variables)
        return SimpleNamespace(text="rendered prompt")

    monkeypatch.setattr(
        sql_generator.prompt_service, "render_sync", fake_render_sync
    )
    monkeypatch.setattr(
        sql_generator,
        "llm",
        SimpleNamespace(
            invoke=lambda _prompt: SimpleNamespace(content="SELECT 1")
        ),
    )

    sql_generator.generate_sql(
        "排除已取消订单，按商品明细数量乘以单价统计各分类销售额排名",
        [
            "product.categories", "product.products", "order.order_items",
            "order.orders",
        ],
    )

    assert captured["key"] == "sql.generator"
    assert "与当前问题相关的 SQL 查询示例" in captured["table_info"]
    assert "SUM(oi.quantity * oi.price)" in captured["table_info"]
    assert "o.status <> 'cancelled'" in captured["table_info"]
    assert "适用表：" in captured["table_info"]
    assert (
        "必须保留问题中明确提出的时间范围、筛选条件、分组维度"
        in captured["table_info"]
    )
    assert "退款率必须有明确分子和分母" in captured["question"]
    assert "本项目商品级退款率口径" in captured["question"]
    assert (
        "SUM(order.order_items.quantity * order.order_items.price)"
        in captured["question"]
    )
    assert "退款率排名必须排除销售额分母" in captured["question"]
    assert "只生成这两个自然月" in captured["question"]
    assert "空结果上的 `SUM` 是 NULL" in captured["question"]
    assert "稳定主键和展示名称" in captured["question"]
    assert "order.refunds" not in captured["table_info"]

    captured.clear()
    sql_generator.generate_sql(
        "排除已取消订单，按商品明细数量乘以单价统计各分类销售额排名",
        ["product.categories", "product.products"],
    )

    assert "与当前问题相关的 SQL 查询示例" not in captured["table_info"]
