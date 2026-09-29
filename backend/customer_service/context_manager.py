"""customer_service/context_manager.py — Context Manager（设计方案 §4.1，组件非 Agent）

迁移 B4（2026-09-29）：收敛订单槽位解析——此前 query/action 两个专家各自
维护一份「规范化 → understanding 实体抽取 → ORDER_ID 过滤」包装循环与
「预过滤注入值 > 当前轮显式实体」优先级拼接（逻辑相同、缺值形态不同：
query 返 None、action 返空串）。解析只此一份，缺槽语义由消费方决定
（query 降级列表查询；action 走结构化追问，绝不回退 "latest"，缺陷6.2）。

纯函数组件：零 LLM、零 IO，只读入参；实体抽取委托
understanding.entities 单一事实源（多段连字不截断、形近错别字零改写），
文本规范化复用 Input Guard 的 normalize_query。

有意不收敛的「差异」（非重复）：身份解析三种语义各有归属——
query/action 的强校验（PermissionChecker，缺身份抛 AuthenticationError）、
complaint 的匿名兜底（state.get 兜 "anonymous"，投诉允许匿名建单）、
action._resolve_tenant 的可信租户链（STOP D §67 冻结语义）。本组件
不触碰身份，只收口订单槽位。
"""
from __future__ import annotations

from typing import Any


def extract_order_entity(user_message: str) -> str:
    """当前轮消息显式订单号；识别不到返回空串。"""
    from backend.customer_service.understanding.entities import extract_entities
    from backend.customer_service.understanding.types import EntityType
    from backend.security.input_guard.normalize import normalize_query

    for e in extract_entities(normalize_query(user_message or "")):
        if e.type == EntityType.ORDER_ID:
            return e.match()
    return ""


def resolve_order_slot(cs_route: dict[str, Any] | None, user_message: str) -> str:
    """订单槽位统一优先级：预过滤注入 > 当前轮显式实体。

    ``cs_route.metadata.order_id`` 是预过滤层（缺陷9 实体感知改写/回指
    继承）注入的本轮权威 referent，优先于专家层对消息的再次抽取；为
    空白串时视同未注入。两处都取不到返回空串。
    """
    injected = str(
        ((cs_route or {}).get("metadata") or {}).get("order_id") or ""
    ).strip()
    if injected:
        return injected
    return extract_order_entity(user_message)
