"""按问题匹配、受授权表范围约束的 SQL 查询示例。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class SQLQueryExample:
    """用于 SQL 生成参考的固定业务查询示例。"""

    key: str
    question: str
    aliases: tuple[str, ...]
    tables: frozenset[str]
    sql: str

    @property
    def match_phrases(self) -> tuple[str, ...]:
        """返回标准问法和常见说法。"""
        return (self.question, *self.aliases)


SQL_QUERY_EXAMPLES: tuple[SQLQueryExample, ...] = (
    SQLQueryExample(
        key="inventory_low_stock",
        question="查询库存低于安全库存的商品及其库存量",
        aliases=(
            "低于安全库存的商品",
            "库存不足的商品",
            "需要补货的商品",
        ),
        tables=frozenset({"inventory.inventory", "product.products"}),
        sql=(
            "SELECT p.product_name, i.warehouse_id, "
            "i.stock_quantity, i.safety_stock\n"
            "FROM inventory.inventory i\n"
            "JOIN product.products p ON p.id = i.product_id\n"
            "WHERE i.stock_quantity < i.safety_stock\n"
            "ORDER BY i.stock_quantity ASC\n"
            "LIMIT 100"
        ),
    ),
    SQLQueryExample(
        key="category_sales_rank",
        question="排除已取消订单，按商品明细数量乘以单价统计各分类销售额排名",
        aliases=(
            "排除已取消订单后按商品明细统计分类销售额",
        ),
        tables=frozenset({
            "product.categories",
            "product.products",
            "order.order_items",
            "order.orders",
        }),
        sql=(
            "SELECT c.name AS category_name, "
            "SUM(oi.quantity * oi.price) AS sales_amount\n"
            "FROM product.categories c\n"
            "JOIN product.products p ON p.category_id = c.id\n"
            "JOIN \"order\".order_items oi ON oi.product_id = p.id\n"
            "JOIN \"order\".orders o ON o.id = oi.order_id\n"
            "WHERE o.status <> 'cancelled'\n"
            "GROUP BY c.name\n"
            "ORDER BY sales_amount DESC\n"
            "LIMIT 100"
        ),
    ),
    SQLQueryExample(
        key="recent_order_count",
        question="查询最近30天的订单量",
        aliases=(
            "近30天订单量",
            "最近30天订单数",
        ),
        tables=frozenset({"order.orders"}),
        sql=(
            "SELECT COUNT(*) AS order_count\n"
            "FROM \"order\".orders\n"
            "WHERE created_at >= CURRENT_TIMESTAMP - INTERVAL '30 days'\n"
            "LIMIT 100"
        ),
    ),
    SQLQueryExample(
        key="top_refunded_products",
        question="退款次数最多的前10个商品",
        aliases=(
            "退款次数最多的商品",
        ),
        tables=frozenset({"product.products", "order.refunds"}),
        sql=(
            "SELECT p.product_name, COUNT(r.id) AS refund_count\n"
            "FROM product.products p\n"
            "JOIN \"order\".refunds r ON r.product_id = p.id\n"
            "GROUP BY p.product_name\n"
            "ORDER BY refund_count DESC\n"
            "LIMIT 10"
        ),
    ),
    SQLQueryExample(
        key="top_refund_rate_products",
        question="退款率最高的商品是哪个？",
        aliases=("退款率最高的商品",),
        tables=frozenset({
            "product.products",
            "order.order_items",
            "order.refunds",
        }),
        sql=(
            "SELECT p.product_name, "
            "ROUND(COALESCE(r.refund_amt, 0) / "
            "NULLIF(s.sales_amt, 0), 4) AS refund_rate\n"
            "FROM product.products p\n"
            "JOIN (\n"
            "    SELECT product_id, SUM(quantity * price) AS sales_amt\n"
            '    FROM "order".order_items\n'
            "    GROUP BY product_id\n"
            ") s ON s.product_id = p.id\n"
            "LEFT JOIN (\n"
            "    SELECT product_id, SUM(refund_amount) AS refund_amt\n"
            '    FROM "order".refunds\n'
            "    GROUP BY product_id\n"
            ") r ON r.product_id = p.id\n"
            "WHERE s.sales_amt > 0\n"
            "  AND COALESCE(r.refund_amt, 0) > 0\n"
            "ORDER BY refund_rate DESC NULLS LAST\n"
            "LIMIT 1"
        ),
    ),
)

_NEGATION_PREFIXES = (
    "不要", "不必", "无需", "不用", "不需要", "不想", "不查", "不看",
    "不", "没", "无", "非", "别", "勿",
)
_NEGATION_SCOPE_BREAKS = (
    "但是", "但", "不过", "而是", "改为", "改成", "换成", "改查",
    "改看", "改统计", "需要", "现在", "这次", "本次", "另外", "然后",
    "同时", "并且", "而要",
)


def _normalize_phrase(value: str) -> str:
    """忽略大小写、空格和标点，保留中文词组内容。"""
    return re.sub(r"[\W_]+", "", value.casefold())


def _phrase_occurrences(question: str, phrase: str) -> list[tuple[int, int]]:
    """返回词组在归一化问题中的所有位置。"""
    occurrences = []
    start = 0
    while (match_at := question.find(phrase, start)) != -1:
        occurrences.append((match_at, match_at + len(phrase)))
        start = match_at + len(phrase)
    return occurrences


def _is_negated_occurrence(question: str, match_at: int) -> bool:
    """识别词组前的短否定语境；遇到转折/改查词后不沿用旧否定。"""
    prefix = question[max(0, match_at - 16):match_at]
    negations = [
        (prefix.rfind(negation), negation)
        for negation in _NEGATION_PREFIXES
        if prefix.rfind(negation) >= 0
    ]
    if not negations:
        return False

    position, negation = max(negations, key=lambda item: item[0])
    after_negation = prefix[position + len(negation):]
    if len(after_negation) > 10:
        return False
    return not any(marker in after_negation for marker in _NEGATION_SCOPE_BREAKS)


def select_query_examples(
    question: str,
    table_names: Sequence[str] | None,
    limit: int = 2,
) -> list[SQLQueryExample]:
    """按明确词组匹配示例，且仅返回所需表全部处于当前范围内的示例。

    使用确定性的词组匹配，避免为了取示例额外调用模型或检索服务。
    表范围由调用方传入；生产 SQL 链路在这里传入策略过滤后的表集合。
    """
    if limit <= 0:
        return []

    normalized_question = _normalize_phrase(question)
    if not normalized_question:
        return []

    available_tables = {
        table.casefold() for table in (table_names or ())
    }
    ranked: list[tuple[int, str, SQLQueryExample]] = []

    for example in SQL_QUERY_EXAMPLES:
        if not {table.casefold() for table in example.tables} <= available_tables:
            continue

        phrases = [
            normalized_phrase
            for phrase in example.match_phrases
            if (normalized_phrase := _normalize_phrase(phrase))
        ]
        occurrences = {
            phrase: _phrase_occurrences(normalized_question, phrase)
            for phrase in phrases
        }
        negated_spans = [
            (start, end)
            for phrase, phrase_occurrences in occurrences.items()
            for start, end in phrase_occurrences
            if _is_negated_occurrence(normalized_question, start)
        ]
        matched_lengths = [
            len(phrase)
            for phrase, phrase_occurrences in occurrences.items()
            for start, end in phrase_occurrences
            if not _is_negated_occurrence(normalized_question, start)
            and not any(
                negated_start <= start and end <= negated_end
                for negated_start, negated_end in negated_spans
            )
        ]
        if matched_lengths:
            ranked.append((max(matched_lengths), example.key, example))

    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [example for _, _, example in ranked[:limit]]
