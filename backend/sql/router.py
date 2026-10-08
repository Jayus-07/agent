"""
router.py — 表筛选 (Schema Routing) + 关键词缓存

根据用户自然语言问题，用 LLM 选出可能相关的表。
优化（P2 perf）:
  - 关键词显式匹配优先（跳过 LLM）
  - LRU 缓存相似问题结果（TTL 5min）
  - LLM 失败回退所有表
"""
import json
import threading
from typing import List, Sequence

from backend.infra.cache import get_cache
from backend.infra.llm import llm
from backend.prompts.service import prompt_service
from backend.shared.logger import logger
from backend.sql.schema_loader import schema_loader

# ── P2 性能优化：关键词快路径 + 统一缓存 ──

# 表名关键词 → 表全限定名映射（业务语义 → schema.table）
_KEYWORD_TABLE_MAP: dict[str, list[str]] = {}
_map_lock = threading.Lock()


def _build_keyword_map() -> None:
    """构造关键词→表名映射（懒初始化）。

    只记录强关联的关键词（单关键词命中 ≤3 张表），避免泛词如"商品"命中全部 product schema。
    """
    global _KEYWORD_TABLE_MAP
    if _KEYWORD_TABLE_MAP:
        return
    with _map_lock:
        if _KEYWORD_TABLE_MAP:
            return
        # 精确关键词映射（1 个关键词 → 1-3 张表）
        _precise = {
            # 高频示例的依赖表必须在快路径一次性补齐，不能只命中维表。
            "库存": [
                "inventory.inventory", "inventory.warehouses",
                "product.products",
            ],
            "缺货": ["inventory.inventory"],
            "补货": ["inventory.inventory", "inventory.purchase_orders"],
            "采购": ["inventory.purchase_orders"],
            "仓库": ["inventory.warehouses", "inventory.inventory"],
            "退款": ["order.refunds", "order.orders", "product.products"],
            "退货": ["order.refunds"],
            "利润": ["finance.daily_profit", "order.order_items", "product.products"],
            "财务": ["finance.expenses", "finance.daily_profit"],
            "竞品": [
                "crawler.competitor_products", "crawler.competitor_price",
                "product.products",
            ],
            "爬虫": [
                "crawler.competitor_products", "crawler.competitor_price",
                "product.products",
            ],
            "评论": ["crawler.product_reviews"],
            "评分": ["crawler.product_reviews"],
            "客户": ["customer.customers", "customer.customer_behavior"],
            "用户行为": ["customer.customer_behavior"],
            "会员": ["customer.customers"],
            "分类": [
                "product.categories", "product.products", "order.order_items",
            ],
            "销售额": [
                "product.categories", "product.products", "order.order_items",
            ],
            "销量": ["product.products", "order.order_items"],
            "订单": ["order.orders", "order.order_items"],
            "金额": ["order.orders", "order.order_items"],
            "售价": ["product.products", "crawler.competitor_price"],
            "标签": ["product.product_tags"],
            "爆款": ["product.product_tags", "order.order_items"],
            "agent": ["ai.agent_tasks", "ai.agent_trace"],
            "trace": ["ai.agent_trace"],
        }
        _KEYWORD_TABLE_MAP = _precise


def _keyword_match(question: str) -> list[str]:
    """关键词快路径：问题中包含已知业务词 → 直接匹配表。"""
    _build_keyword_map()
    matched: set[str] = set()
    q_lower = question.lower()
    for kw, tables in _KEYWORD_TABLE_MAP.items():
        if kw in q_lower:
            matched.update(tables)
    return list(matched)


_sql_router_cache = get_cache("sql_router", ttl=300)


def _normalize_tables(tables: Sequence[str] | None, all_tables: Sequence[str]) -> list[str]:
    """只保留 schema_loader 已登记的表，并按数据字典顺序去重。"""
    if tables is None:
        return list(all_tables)
    allowed = {str(t).lower() for t in tables}
    return [table for table in all_tables if table.lower() in allowed]


def select_tables(
    question: str,
    allowed_tables: Sequence[str] | None = None,
    context_tables: Sequence[str] | None = None,
) -> List[str]:
    """根据用户问题，用 LLM 选出相关表名。

    优化（P2 perf）:
      1. 关键词快路径：问题含明确业务词 → 直接匹配，跳过 LLM
      2. LRU 缓存：相同问题 5min 内复用
      3. 表数量 ≤ 2 → 直接返回
    """
    all_tables = schema_loader.get_all_table_names()
    all_tables = _normalize_tables(allowed_tables, all_tables)
    if not all_tables:
        logger.info("[Router] 当前授权范围内没有可查询表")
        return []

    # context_tables 只能帮助追问恢复上一轮涉及的表，永远先与本轮授权
    # 表集合求交集，避免上下文扩大权限边界。
    # ⚠️ 无上下文必须解析为「空集」：`_normalize_tables(None, ...)` 的语义是
    # 「不过滤 = 全部表」（授权面用得上），把它当 remembered 会让下面的
    # fast_match 恒等于全表 → `1 <= len(fast_match) <= 3` 永不成立 →
    # 关键词快路径成死代码，每次选表都打 LLM（2026-10-08 实测：本机 LLM 网关
    # 不可用时静默回退「全部 18 张表」，选表质量塌成「全给」）。
    remembered = _normalize_tables(context_tables or [], all_tables)
    if len(all_tables) <= 2:
        logger.info(f"[Router] 表数量 ≤ 2，直接返回全部: {all_tables}")
        return list(dict.fromkeys(remembered or all_tables))

    cache_key = json.dumps(
        {
            "question": question,
            "allowed_tables": list(all_tables),
            "context_tables": remembered,
        },
        ensure_ascii=False,
        sort_keys=True,
    )

    # 1. 缓存优先
    cached = _sql_router_cache.get_json(cache_key)
    if cached is not None:
        return _normalize_tables(cached, all_tables)

    # 2. 关键词快路径
    kw_matched = _keyword_match(question)
    # 显式表名匹配（用户问题中直接出现了全限定表名）
    explicit = [t for t in all_tables if t.lower() in question.lower()]
    fast_match = list(dict.fromkeys(
        [table for table in (kw_matched + explicit + remembered)
         if table in all_tables]
    ))

    # 快路径条件：匹配 1-3 张表时跳过 LLM（精确场景，不需要 LLM 选表）
    if 1 <= len(fast_match) <= 3:
        # 如果同时有显式表名且关键词结果覆盖了它，用精确结果
        logger.info(
            f"[Router] 关键词快路径: 问题 '{question[:50]}...' → {fast_match}"
        )
        _sql_router_cache.set_json(cache_key, fast_match)
        return fast_match

    # 如果关键词匹配了过多表（泛词），不走快路径，交给 LLM 精确选表
    if len(fast_match) > 3:
        logger.debug(
            f"[Router] 关键词匹配 {len(fast_match)} 张表（>3），交给 LLM 精确选表"
        )

    # 3. LLM 路由
    table_list = "\n".join(
        f"  - {t}: {schema_loader.get_table_description(t)}"
        for t in all_tables
    )
    r = prompt_service.render_sync("sql.router", table_list=table_list, question=question)

    try:
        resp = llm.invoke(r.text)
        content = resp.content.strip()

        start = content.find("[")
        end = content.rfind("]")
        if start != -1 and end != -1:
            content = content[start:end + 1]

        selected = json.loads(content)

        if isinstance(selected, list):
            valid = [t for t in selected if t in all_tables]
            for t in explicit:
                if t not in valid:
                    valid.append(t)
            if not valid:
                logger.warning(f"[Router] LLM 返回无效表名: {selected}，回退全部")
                return all_tables
            logger.info(f"[Router] 用户问题 '{question[:40]}...' → 选中表: {valid}")
            _sql_router_cache.set_json(cache_key, valid)
            return valid

    except json.JSONDecodeError as e:
        logger.warning(f"[Router] JSON 解析失败: {e}，回退全部")
    except Exception as e:
        logger.error(f"[Router] LLM 调用失败: {e}，回退全部")

    _sql_router_cache.set_json(cache_key, all_tables)
    return all_tables
