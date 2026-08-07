"""
router.py — 表筛选 (Schema Routing) + 关键词缓存

根据用户自然语言问题，用 LLM 选出可能相关的表。
优化（P2 perf）:
  - 关键词显式匹配优先（跳过 LLM）
  - LRU 缓存相似问题结果（TTL 5min）
  - LLM 失败回退所有表
"""
import json
import time
import threading
from typing import List

from backend.infra.llm import llm
from backend.sql.schema_loader import schema_loader
from backend.shared.logger import logger

ROUTER_PROMPT = """你是数据库表路由助手。给定用户问题和可用表列表，选出回答问题可能需要的表。

规则:
1. **表名采用 `<schema>.<table>` 全限定形式**（如 `product.products`、`order.orders`）；只从给定列表选择
2. 选择最少但足够的表（通常 1-2 张；跨域分析如"商品+订单"可多选）
3. 如果问题不涉及任何表，返回空数组
4. 严格输出 JSON 数组格式

可用表（schema-qualified）:
{table_list}

用户问题: {question}

请输出 JSON 数组，不要添加任何解释。"""

# ── P2 性能优化：关键词快路径 + LRU 缓存 ──

# 表名关键词 → 表全限定名映射（业务语义 → schema.table）
_KEYWORD_TABLE_MAP: dict[str, list[str]] = {}
_map_lock = threading.Lock()

_QUERY_CACHE: dict[str, tuple[float, list[str]]] = {}
_cache_lock = threading.Lock()
_CACHE_TTL = 300  # 5 分钟
_CACHE_MAX = 128


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
            "库存": ["inventory.inventory", "inventory.warehouses"],
            "缺货": ["inventory.inventory"],
            "补货": ["inventory.inventory", "inventory.purchase_orders"],
            "采购": ["inventory.purchase_orders"],
            "仓库": ["inventory.warehouses", "inventory.inventory"],
            "退款": ["order.refunds", "order.orders"],
            "退货": ["order.refunds"],
            "利润": ["finance.daily_profit", "order.order_items"],
            "财务": ["finance.expenses", "finance.daily_profit"],
            "竞品": ["crawler.competitor_products", "crawler.competitor_price"],
            "爬虫": ["crawler.competitor_products", "crawler.competitor_price"],
            "评论": ["crawler.product_reviews"],
            "评分": ["crawler.product_reviews"],
            "客户": ["customer.customers", "customer.customer_behavior"],
            "用户行为": ["customer.customer_behavior"],
            "会员": ["customer.customers"],
            "分类": ["product.categories"],
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


def _cache_get(question: str) -> list[str] | None:
    """缓存查询（LRU + TTL）。"""
    _cache_lock.acquire()
    try:
        # 清理过期条目
        now = time.time()
        expired = [k for k, v in _QUERY_CACHE.items() if now - v[0] > _CACHE_TTL]
        for k in expired:
            del _QUERY_CACHE[k]

        # 精确匹配
        if question in _QUERY_CACHE:
            ts, result = _QUERY_CACHE[question]
            if now - ts <= _CACHE_TTL:
                logger.info(f"[Router] 缓存命中: {result}")
                return result
            del _QUERY_CACHE[question]
    finally:
        _cache_lock.release()
    return None


def _cache_set(question: str, result: list[str]) -> None:
    """写入缓存。"""
    _cache_lock.acquire()
    try:
        # LRU eviction
        if len(_QUERY_CACHE) >= _CACHE_MAX:
            oldest = min(_QUERY_CACHE.items(), key=lambda x: x[1][0])
            del _QUERY_CACHE[oldest[0]]
        _QUERY_CACHE[question] = (time.time(), result)
    finally:
        _cache_lock.release()


def select_tables(question: str) -> List[str]:
    """根据用户问题，用 LLM 选出相关表名。

    优化（P2 perf）:
      1. 关键词快路径：问题含明确业务词 → 直接匹配，跳过 LLM
      2. LRU 缓存：相同问题 5min 内复用
      3. 表数量 ≤ 2 → 直接返回
    """
    all_tables = schema_loader.get_all_table_names()
    if len(all_tables) <= 2:
        logger.info(f"[Router] 表数量 ≤ 2，直接返回全部: {all_tables}")
        return all_tables

    # 1. 缓存优先
    cached = _cache_get(question)
    if cached is not None:
        return cached

    # 2. 关键词快路径
    kw_matched = _keyword_match(question)
    # 显式表名匹配（用户问题中直接出现了全限定表名）
    explicit = [t for t in all_tables if t.lower() in question.lower()]
    fast_match = list(set(kw_matched + explicit))

    # 快路径条件：匹配 1-3 张表时跳过 LLM（精确场景，不需要 LLM 选表）
    if 1 <= len(fast_match) <= 3:
        # 如果同时有显式表名且关键词结果覆盖了它，用精确结果
        logger.info(
            f"[Router] 关键词快路径: 问题 '{question[:50]}...' → {fast_match}"
        )
        _cache_set(question, fast_match)
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
    prompt = ROUTER_PROMPT.format(table_list=table_list, question=question)

    try:
        resp = llm.invoke(prompt)
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
            _cache_set(question, valid)
            return valid

    except json.JSONDecodeError as e:
        logger.warning(f"[Router] JSON 解析失败: {e}，回退全部")
    except Exception as e:
        logger.error(f"[Router] LLM 调用失败: {e}，回退全部")

    _cache_set(question, all_tables)
    return all_tables
