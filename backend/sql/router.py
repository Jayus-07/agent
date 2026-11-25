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

# 竞品采集域已退役。即使某条旧问题没有对应关键词，也不能让它进入
# LLM 兜底后被误路由到其他业务表。
_RETIRED_SQL_DOMAIN_MARKERS = (
    "竞品",
    "竞对",
    "竞争对手",
    "对手品牌",
    "crawler.",
    "competitor_",
)


def _build_keyword_map() -> None:
    """构造关键词→表名映射（懒初始化）。

    记录精确业务词和依赖表；命中 1-3 张可走快路径，4 张以上交给 LLM 细选。
    """
    global _KEYWORD_TABLE_MAP
    if _KEYWORD_TABLE_MAP:
        return
    with _map_lock:
        if _KEYWORD_TABLE_MAP:
            return
        # 精确关键词映射（复杂场景可映射 4 张以上以强制进入 LLM 细选）
        _precise = {
            # 零售 ERP 单店试点：新旧商品、库存、采购和 POS 表并存，
            # 使用业务短语明确落到 retail，避免泛词误选旧 demo 表。
            "在售 SKU": ["retail.products"],
            "在售商品": ["retail.products"],
            "SKU": ["retail.products"],
            "条码": ["retail.products"],
            "仓库库存": [
                "retail.inventory_balances", "retail.products",
                "retail.warehouses", "retail.stores",
            ],
            "门店库存": [
                "retail.inventory_balances", "retail.products",
                "retail.stores",
            ],
            "库存低于安全线": [
                "retail.inventory_balances", "retail.products",
                "retail.warehouses", "retail.stores",
            ],
            "库存成本金额": [
                "retail.inventory_balances", "retail.products",
            ],
            "库存流水": [
                "retail.inventory_movements", "retail.stores",
            ],
            "采购申请": [
                "retail.purchase_requests", "retail.purchase_request_lines",
                "retail.products",
            ],
            "采购申请状态": ["retail.purchase_requests"],
            "申请人": ["retail.purchase_requests", "retail.stores"],
            "采购订单金额": [
                "retail.purchase_orders", "retail.purchase_order_lines",
                "retail.suppliers",
            ],
            "有效采购订单金额": [
                "retail.purchase_orders", "retail.purchase_order_lines",
                "retail.suppliers",
            ],
            "采购订单": [
                "retail.purchase_orders", "retail.suppliers", "retail.stores",
            ],
            "预计到货": [
                "retail.purchase_orders", "retail.suppliers", "retail.stores",
            ],
            "供应商": ["retail.suppliers"],
            "收货日期": [
                "retail.receipts", "retail.receipt_lines", "retail.stores",
            ],
            "实际收货": [
                "retail.purchase_orders", "retail.purchase_order_lines",
                "retail.products", "retail.receipts", "retail.receipt_lines",
            ],
            "验收合格率": [
                "retail.suppliers", "retail.purchase_orders", "retail.receipts",
                "retail.receipt_lines",
            ],
            "到货验收合格率": [
                "retail.suppliers", "retail.purchase_orders", "retail.receipts",
                "retail.receipt_lines",
            ],
            "收货": [
                "retail.receipts", "retail.receipt_lines",
                "retail.purchase_orders",
            ],
            "到货": [
                "retail.purchase_orders", "retail.receipts",
                "retail.receipt_lines",
            ],
            "调拨": [
                "retail.stock_transfers", "retail.stock_transfer_lines",
                "retail.products",
            ],
            "调拨单": [
                "retail.stock_transfers", "retail.stock_transfer_lines",
                "retail.products", "retail.warehouses",
            ],
            "发往哪个仓库": [
                "retail.stock_transfers", "retail.stock_transfer_lines",
                "retail.products", "retail.warehouses",
            ],
            "盘点差异": [
                "retail.stock_counts", "retail.stock_count_lines",
                "retail.products",
            ],
            "盘点": [
                "retail.stock_counts", "retail.stock_count_lines",
                "retail.products",
            ],
            "批次": [
                "retail.product_batches", "retail.products",
                "retail.warehouses",
            ],
            "到期": [
                "retail.product_batches", "retail.products",
                "retail.warehouses",
            ],
            "销售净额": [
                "retail.pos_sales", "retail.pos_returns",
                "retail.pos_return_lines", "retail.stores",
            ],
            "退款日扣减": [
                "retail.pos_returns", "retail.pos_return_lines",
                "retail.stores",
            ],
            "净销售额": [
                "retail.pos_sales", "retail.pos_sale_lines",
                "retail.pos_returns", "retail.pos_return_lines",
                "retail.products", "retail.stores",
            ],
            "已完成退款": [
                "retail.pos_returns", "retail.pos_return_lines",
                "retail.products", "retail.stores",
            ],
            "退货原因": [
                "retail.pos_returns", "retail.pos_return_lines",
                "retail.products", "retail.stores",
            ],
            "取消销售": ["retail.pos_sales", "retail.stores"],
            "取消销售单": ["retail.pos_sales", "retail.stores"],
            "POS": [
                "retail.pos_sales", "retail.pos_sale_lines", "retail.products",
            ],
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
            "退款率": [
                "order.refunds", "order.order_items", "order.orders",
                "product.products",
            ],
            "退货": ["order.refunds"],
            "利润": ["finance.daily_profit", "order.order_items", "product.products"],
            "财务": ["finance.expenses", "finance.daily_profit"],
            "客户": ["customer.customers", "customer.customer_behavior"],
            "用户行为": ["customer.customer_behavior"],
            "会员": ["customer.customers"],
            "分类": [
                "product.categories", "product.products", "order.order_items",
            ],
            "销售额": [
                "product.categories", "product.products", "order.order_items",
            ],
            "卖得": [
                "product.products", "order.order_items", "order.orders",
            ],
            # 销量排名按项目 SC-109 口径排除已取消订单，必须能关联订单状态。
            "销量": [
                "product.products", "order.order_items", "order.orders",
            ],
            "订单": ["order.orders", "order.order_items"],
            "金额": ["order.orders", "order.order_items"],
            "售价": ["product.products"],
            "标签": ["product.product_tags"],
            "爆款": ["product.product_tags", "order.order_items"],
            "agent": ["ai.agent_tasks", "ai.agent_trace"],
            "trace": ["ai.agent_trace"],
        }
        _KEYWORD_TABLE_MAP = _precise


def _is_sales_ranking_question(question: str) -> bool:
    """识别需要按订单状态过滤的销量/销售额排名或高低比较。"""
    q_lower = question.casefold()
    has_sales_metric = any(
        term in q_lower
        for term in ("销量", "销售额", "销售金额", "销售收入", "卖得")
    )
    has_rank_signal = any(
        term in q_lower
        for term in (
            "排名", "排行", "最高", "最低", "最多", "最少", "更高",
            "更低", "最好", "最差", "top",
        )
    ) or (
        "前" in q_lower
        and any(term in q_lower for term in ("商品", "产品", "品牌", "分类"))
    )
    return has_sales_metric and has_rank_signal


def _keyword_match(question: str) -> list[str]:
    """关键词快路径：问题中包含已知业务词 → 直接匹配表。"""
    _build_keyword_map()
    matched: set[str] = set()
    q_lower = question.lower()
    if (
        all(term in q_lower for term in ("本月", "上月", "销售额"))
        and any(term in q_lower for term in ("同期", "增长", "同比", "环比"))
        and not any(term in q_lower for term in (
            "分类", "商品", "品牌", "地区", "店铺", "客户", "按",
        ))
    ):
        # SC-112 的期间销售额比较使用订单总额，其他维表/明细表会让
        # SQL 生成器误选数量×单价并漏掉订单级时间口径。
        return ["order.orders"]
    matched_keywords = [
        (kw, tables)
        for kw, tables in _KEYWORD_TABLE_MAP.items()
        if kw.lower() in q_lower
    ]
    for keyword, tables in matched_keywords:
        # 更具体的业务词（如“退款率”）覆盖其子串泛词（“退款”），
        # 避免泛词覆盖具体指标所需的完整候选表集合。
        if any(
            keyword != specific_keyword
            and keyword.lower() in specific_keyword.lower()
            for specific_keyword, _ in matched_keywords
        ):
            continue
        matched.update(tables)
    # 新 ERP 业务词明确进入 retail 域时，不混入旧 demo 的同义泛词表，
    # 例如“门店库存”不能同时带上 inventory.inventory。
    if any(table.startswith("retail.") for table in matched):
        matched = {table for table in matched if table.startswith("retail.")}
    if (
        _is_sales_ranking_question(question)
        and not any(table.startswith("retail.") for table in matched)
    ):
        # 销量/销售额排名须基于订单状态过滤已取消订单。
        matched.add("order.orders")
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
    normalized_question = question.casefold()
    if any(marker.casefold() in normalized_question
           for marker in _RETIRED_SQL_DOMAIN_MARKERS):
        logger.info("[Router] 竞品采集数据域已退役，拒绝 SQL 路由")
        return []

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
    # 不可用时静默回退全部纳管表，选表质量塌成「全给」）。
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

    # 精确关键词通常限制为 1-3 张表；销售排名可额外带订单状态表，最多 4 张。
    is_sales_ranking = _is_sales_ranking_question(question)
    if (1 <= len(fast_match) <= 3
            or (is_sales_ranking and 1 <= len(fast_match) <= 4)):
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

    invoke_kwargs: dict = {}
    try:
        from backend.infra.llm.models import supports_thinking_flag, thinking_disabled_extra_body
        from backend.infra.llm.proxy import get_active_model_name

        active_model = get_active_model_name()
        if supports_thinking_flag(active_model):
            extra_body = thinking_disabled_extra_body(active_model)
            if extra_body is not None:
                invoke_kwargs["extra_body"] = extra_body
                invoke_kwargs["temperature"] = 0.0
    except Exception as e:
        logger.debug(f"[Router] thinking 参数解析异常（降级常规调用）: {e}")

    try:
        if invoke_kwargs:
            try:
                resp = llm.invoke(r.text, **invoke_kwargs)
            except TypeError:
                resp = llm.invoke(r.text)
        else:
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
