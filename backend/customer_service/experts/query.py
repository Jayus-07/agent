"""customer_service/experts/query.py — QueryExpert

业务查询 Expert：订单查询、物流查询等只读操作。
从 graph/nodes.py cs_business_query 提取核心逻辑。

设计参考: docs/customer-service/langgraph-multi-expert-design.md §6.2
"""
from __future__ import annotations

import json
from typing import Any

from backend.customer_service.experts.base import ExpertResult, ExpertStatus
from backend.customer_service.prompting import render_prompt
from backend.shared.logger import logger

_INTENT_SERVICE_MAP = {
    "t_order_status": "order",
    "t_logistics": "logistics",
    "t_ticket_status": "ticket",  # 批次C：工单进度
    "as_repair": "order",
    "as_quality_issue": "order",
}


class _QueryServiceResponse(str):
    """保留文本兼容，同时携带本次服务实际返回的结构化事实。"""

    def __new__(cls, answer: str, facts: dict[str, Any]):
        instance = super().__new__(cls, answer)
        instance.facts = facts
        return instance

# 复合问题预判：问题命中 ≥2 个不同服务域的关键词 → 疑似复合诉求


def execute_query(
    user_message: str,
    cs_route: dict,
    state: dict[str, Any],
) -> ExpertResult:
    """QueryExpert 核心逻辑。

    Args:
        user_message: 用户原始问题
        cs_route: CS Router 输出（含 intent）
        state: CSGraphState（用于身份验证）

    Returns:
        ExpertResult — response_draft 为格式化的查询结果
    """
    from backend.customer_service.security.permission import PermissionChecker

    intent = cs_route.get("intent", "t_order_status")
    user_id = PermissionChecker.validate_user_identity(state)

    logger.info(
        "[QueryExpert] intent=%s user_id=%s question=%s...",
        intent, user_id, user_message[:60],
    )

    current_task = state.get("current_task") or {}
    if current_task.get("capability") in ("query_logistics", "query_order_status"):
        return _execute_task_query(
            current_task, user_id, user_message, cs_route, state,
        )

    intents = None
    if _compound_suspected(user_message):
        intents = _llm_decompose_intents(user_message)

    if intents and len(intents) > 1:
        # 复合问题：逐意图查询后合并（同一服务去重，避免重复输出）
        sections: list[str] = []
        service_facts: list[dict[str, Any]] = []
        dispatched_services: list[str] = []
        for it in intents:
            service_type = _INTENT_SERVICE_MAP.get(it, "order")
            if service_type in dispatched_services:
                continue
            dispatched_services.append(service_type)
            section = _dispatch_service(
                user_id, it, user_message, cs_route,
                tenant_id=str(state.get("tenant_id") or "default"),
                session_id=str(state.get("session_id") or ""),
            )
            sections.append(str(section))
            if isinstance(section, _QueryServiceResponse):
                service_facts.append(section.facts)
        answer = "\n\n---\n\n".join(sections)
        data = {"intent": intents, "user_id": user_id, "decomposed": True}
        merged_facts = _merge_service_facts(service_facts)
        if merged_facts:
            data["service_facts"] = merged_facts
        return ExpertResult(
            expert="query",
            status=ExpertStatus.SUCCESS.value,
            response_draft=answer,
            data=data,
        )

    tenant_id = str(state.get("tenant_id") or "default")
    session_id = str(state.get("session_id") or "")
    service_response = _dispatch_service(
        user_id, intent, user_message, cs_route,
        tenant_id=tenant_id, session_id=session_id,
    )
    data = {"intent": intent, "user_id": user_id}
    if isinstance(service_response, _QueryServiceResponse):
        data["service_facts"] = service_response.facts
    return ExpertResult(
        expert="query",
        status=ExpertStatus.SUCCESS.value,
        response_draft=str(service_response),
        data=data,
    )


def _merge_service_facts(fact_sets: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for facts in fact_sets:
        for key, value in facts.items():
            if isinstance(value, list) and isinstance(merged.get(key), list):
                merged[key].extend(value)
            else:
                merged[key] = value
    return merged


def _order_service_facts(orders: list[dict]) -> dict[str, Any]:
    allowed = (
        "order_no", "status", "total_amount", "currency", "created_at",
        "refund_eligible",
    )
    return {
        "orders": [
            {key: order[key] for key in allowed if key in order}
            for order in orders[:10] if isinstance(order, dict)
        ],
        "source": _task_query_source("order"),
    }


def _execute_task_query(
    current_task: dict[str, Any],
    user_id: str,
    user_message: str,
    cs_route: dict,
    state: dict[str, Any],
) -> ExpertResult:
    """执行单个计划查询，订单身份只取业务服务核验后的记录。"""
    from backend.customer_service.errors import DatabaseError, OrderNotFoundError
    from backend.customer_service.understanding.task_plan import CSTaskResult

    task_id = str(current_task.get("task_id") or "")
    facts: dict[str, Any] = {}
    metadata = cs_route.get("metadata") or {}
    order_ref = str(metadata.get("order_id") or "").strip()
    if not order_ref:
        from backend.customer_service.context_manager import resolve_order_slot

        order_ref = resolve_order_slot(cs_route, user_message)

    try:
        from backend.customer_service.service.order_service import get_order_service

        order_service = get_order_service()
        if order_ref:
            order_response = order_service.query_orders(
                user_id=user_id, order_id=order_ref, query_type="detail",
            )
        else:
            order_response = order_service.query_orders(user_id=user_id)
        orders = list(getattr(order_response, "orders", []) or [])
        facts["order_count"] = len(orders)
        if len(orders) != 1:
            answer = (
                "您有多笔订单，请告诉我订单号，或回复「查我的所有订单」后"
                "选择要查询的那一笔。"
                if len(orders) > 1
                else "暂时没有找到可核实的订单，请告诉我订单号后我再帮您查询。"
            )
            result = CSTaskResult(
                task_id=task_id, status="needs_clarification", facts=facts,
                source=_task_query_source("order"),
            )
            return ExpertResult(
                expert="query", status=ExpertStatus.SUCCESS.value,
                response_draft=answer,
                data={
                    "task_result": result.model_dump(),
                    "intent": current_task.get("capability"),
                },
            )

        order = orders[0]
        trusted_order_id = str(
            order.get("id") or order.get("order_id") or order.get("order_no") or ""
        ).strip()
        order_no = str(order.get("order_no") or trusted_order_id)
        if not trusted_order_id:
            result = CSTaskResult(
                task_id=task_id, status="failed", facts={"order_count": 1},
                source="unknown", error_type="contract_error",
            )
            return ExpertResult(
                expert="query", status=ExpertStatus.SUCCESS.value,
                response_draft="订单服务返回的信息不完整，暂时无法安全继续。",
                data={"task_result": result.model_dump()},
            )

        raw_order_status = str(order.get("status") or "unknown").lower()
        normalized_order_status = raw_order_status if raw_order_status in {
            "pending", "paid", "shipped", "completed", "cancelled",
        } else "unknown"
        facts.update({
            "order_id": trusted_order_id,
            "order_no": order_no,
            "order_status": normalized_order_status,
        })
        if current_task.get("capability") == "query_order_status":
            result = CSTaskResult(
                task_id=task_id, status="success", facts=facts,
                source=_task_query_source("order"),
            )
            return ExpertResult(
                expert="query", status=ExpertStatus.SUCCESS.value,
                response_draft=_format_order_list([order]),
                data={"task_result": result.model_dump(), "intent": "t_order_status"},
            )

        from backend.customer_service.service.logistics_service import (
            get_logistics_service,
        )

        logistics = get_logistics_service().query_logistics(
            user_id=user_id, order_id=trusted_order_id,
        )
        raw_status = str(getattr(logistics, "status", "") or "unknown").lower()
        shipping_status = {
            "pending": "not_shipped", "paid": "not_shipped",
            "shipped": "shipped", "completed": "delivered",
            "cancelled": "cancelled",
        }.get(raw_status, "unknown")
        facts.update({
            "order_id": str(getattr(logistics, "order_id", "") or trusted_order_id),
            "order_no": str(getattr(logistics, "order_no", "") or order_no),
            "shipping_status": shipping_status,
            "order_status": normalized_order_status,
        })
        result = CSTaskResult(
            task_id=task_id, status="success", facts=facts,
            source=_task_query_source("logistics"),
        )
        return ExpertResult(
            expert="query", status=ExpertStatus.SUCCESS.value,
            response_draft=_format_logistics(logistics),
            data={"task_result": result.model_dump(), "intent": "t_logistics"},
        )
    except OrderNotFoundError:
        result = CSTaskResult(
            task_id=task_id, status="needs_clarification", facts={},
            source=_task_query_source("order"), error_type="business_error",
        )
        return ExpertResult(
            expert="query", status=ExpertStatus.SUCCESS.value,
            response_draft="没有找到这笔订单，请核对订单号后再试。",
            data={"task_result": result.model_dump()},
        )
    except DatabaseError:
        logger.warning("[QueryExpert] task query business service unavailable")
        return _failed_task_query(task_id)
    except Exception:
        logger.warning("[QueryExpert] task query failed", exc_info=True)
        return _failed_task_query(task_id)


def _task_query_source(kind: str) -> str:
    from backend.config.customer_service import CS_BUSINESS_GATEWAY_MODE

    prefix = "business" if CS_BUSINESS_GATEWAY_MODE == "http" else "sandbox"
    if kind == "order":
        return f"{prefix}_order_service"
    return f"{prefix}_logistics_service"


def _failed_task_query(task_id: str) -> ExpertResult:
    from backend.customer_service.understanding.task_plan import CSTaskResult

    result = CSTaskResult(
        task_id=task_id, status="failed", facts={}, source="unknown",
        error_type="provider_error",
    )
    return ExpertResult(
        expert="query", status=ExpertStatus.SUCCESS.value,
        response_draft="订单或物流服务暂时不可用，这次没有发起退款申请，请稍后重试。",
        data={"task_result": result.model_dump()},
    )


def _compound_suspected(question: str) -> bool:
    """规则预判：关键词命中 ≥2 个服务域才触发 LLM 分解，控制成本。"""
    from backend.customer_service.vocab import QUERY_SERVICE_KEYWORDS

    hits = {
        svc for svc, keywords in QUERY_SERVICE_KEYWORDS.items()
        if any(k in question for k in keywords)
    }
    return len(hits) >= 2


def _llm_decompose_intents(question: str) -> list[str] | None:
    """LLM 意图分解 → intent 列表。失败或格式无效返回 None（确定性降级）。

    仅在规则预判疑似复合问题时调用；单意图问题不付这笔 LLM 延迟。
    """
    try:
        from langchain_core.messages import HumanMessage

        from backend.config.customer_service import (
            CS_QUERY_LLM_DECOMPOSE_ENABLED,
            CS_QUERY_LLM_TIMEOUT_MS,
        )
        from backend.infra.async_utils import sync_call_with_timeout
        from backend.infra.llm import llm  # 代理：限流/韧性/llm_usage 记账

        if not CS_QUERY_LLM_DECOMPOSE_ENABLED:
            return None

        # E4b：分解只需意图语义，payload 掩码后不还原
        from backend.customer_service.pii import mask_pii
        masked_question, _vault = mask_pii(question)
        prompt = render_prompt(
            "customer_service.query_intent",
            question=masked_question[:200],
        )
        # config={"timeout"} 实测不生效（2026-09），须线程级限时
        response = sync_call_with_timeout(
            llm.invoke,
            CS_QUERY_LLM_TIMEOUT_MS / 1000.0,
            [HumanMessage(content=prompt)],
        )
        content = response.content.strip()
        if content.startswith("```"):
            content = content.strip("`")
            if content.startswith("json"):
                content = content[4:]
        intents = json.loads(content)
        if not isinstance(intents, list):
            logger.warning("[QueryExpert] LLM 分解返回非数组，回退单意图")
            return None
        valid = [i for i in intents if isinstance(i, str) and i in _INTENT_SERVICE_MAP]
        if not valid:
            return None
        logger.info("[QueryExpert] 复合问题分解: %s", valid)
        return valid
    except Exception as e:
        logger.warning("[QueryExpert] LLM 意图分解失败，回退单意图: %s", e)
        return None


def query_expert_node(state: dict[str, Any]) -> dict[str, Any]:
    """CS Graph QueryExpert 节点函数。"""
    from backend.config.customer_service import CS_EXPERT_TIMEOUT_S
    from backend.customer_service.experts.base import run_expert_safely

    user_message = state.get("user_message", "")
    cs_route = state.get("cs_route", {})

    # 专家级兜底限时：内部各环节自有限时，此层保证整节点上界（与
    # knowledge/action 同口径）；不传则任一环节挂起即无上界
    result = run_expert_safely(
        expert_name="query",
        fn=lambda _state: execute_query(user_message, cs_route, state),
        state=state,
        timeout_s=CS_EXPERT_TIMEOUT_S,
    )

    expert_history = list(state.get("expert_history", []))
    expert_history.append({
        "expert": "query",
        "status": result.get("status", "failed"),
        "duration_ms": result.get("duration_ms", 0),
    })

    return {
        "last_expert_result": dict(result),
        "expert_history": expert_history,
    }


def _dispatch_service(
    user_id: str, intent: str, question: str, cs_route: dict,
    tenant_id: str = "default", session_id: str = "",
) -> str:
    """根据 intent 分发到对应的 Service。"""
    service_type = _INTENT_SERVICE_MAP.get(intent, "order")

    if service_type == "order":
        from backend.customer_service.context_manager import resolve_order_slot
        from backend.customer_service.service.order_service import get_order_service
        order_service = get_order_service()

        if intent == "t_order_status":
            # 缺陷9（2026-09-23）：回指承接解析出的订单号经 cs_route.metadata
            # 注入，优先于当前轮文本抽取——「那它到哪了」resolved_query 里
            # 的订单号是权威 referent。
            # P3.5：问句带具体订单号 → detail 精确查（此前一律全量列表，
            # 「DEMO-1001 到哪了」返回整个订单列表，答非所问）。
            # B4 收敛：注入值 > 当前轮实体的优先级在 context_manager 单一实现
            # STOP C（2026-10-07）：Service 层已区分 OrderNotFoundError（业务
            # 不存在）与 DatabaseError（网关挂/超时/5xx/DB down），此处必须
            # 分类承接——provider 故障不得译成「订单不存在」这种业务假信息。
            from backend.customer_service.errors import (
                DatabaseError,
                OrderNotFoundError,
                ValidationError,
            )
            order_no = resolve_order_slot(cs_route, question) or None
            if not order_no:
                # 语义槽位唯一匹配（2026-10-08）：「买耳机的那单什么状态」
                # 类自然引用 → 精确查单而非全量列表。解析失败/多候选/无
                # 匹配保持既有列表查询（多候选时列表本身就是选择入口）。
                order_no = _semantic_unique_order(cs_route, user_id, tenant_id)
            if order_no:
                try:
                    result = order_service.query_orders(
                        user_id=user_id, order_id=order_no, query_type="detail",
                    )
                except OrderNotFoundError:
                    return (
                        f"没有找到订单 {order_no} 的记录。请核对订单号，"
                        "或告诉我「查我的所有订单」，我来帮您列出全部订单。"
                    )
                except ValidationError as exc:
                    logger.warning(
                        "[QueryExpert] 订单号校验失败: %s (%s)", order_no, exc,
                    )
                    return f"订单号「{order_no}」格式有误，请核对后再告诉我。"
                except DatabaseError:
                    return "订单服务暂时不可用，请稍后再试，稍后我来帮您再查一次。"
                except Exception:
                    # 未分类异常按安全失败处置：宁可说「不可用」，绝不说「不存在」
                    logger.warning(
                        "[QueryExpert] 订单查询失败（未分类异常）", exc_info=True,
                    )
                    return "订单服务暂时不可用，请稍后再试。"
                # 缺陷9：精确查单返回唯一订单 → 记录会话业务上下文，
                # 供下一轮回指（「那它到哪了」）继承。列表查询不写
                # （多订单无唯一 referent，不得猜测）。
                if result.orders:
                    from backend.customer_service.context_resolver import (
                        record_recent_order,
                    )

                    record_recent_order(
                        tenant_id, user_id, session_id, order_no,
                        source_intent=intent,
                    )
                return _QueryServiceResponse(
                    _format_order_list(result.orders),
                    _order_service_facts(result.orders),
                )
            try:
                result = order_service.query_orders(user_id=user_id)
            except DatabaseError:
                return "订单服务暂时不可用，请稍后再试。"
            except Exception:
                logger.warning(
                    "[QueryExpert] 订单列表查询失败（未分类异常）", exc_info=True,
                )
                return "订单服务暂时不可用，请稍后再试。"
            if result.orders:
                from backend.customer_service.context_resolver import (
                    record_recent_orders,
                )

                record_recent_orders(
                    tenant_id,
                    user_id,
                    session_id,
                    [str(order.get("order_no") or order.get("id") or "")
                     for order in result.orders],
                    source_intent=intent,
                )
            return _QueryServiceResponse(
                _format_order_list(result.orders),
                _order_service_facts(result.orders),
            )
        return _QueryServiceResponse(
            _format_order_list([]), _order_service_facts([]),
        )

    if service_type == "logistics":
        from backend.customer_service.errors import (
            DatabaseError,
            OrderNotFoundError,
        )
        from backend.customer_service.service.logistics_service import get_logistics_service
        logistics = get_logistics_service()
        injected_order = str(
            (cs_route.get("metadata") or {}).get("order_id") or ""
        ).strip()
        # 语义槽位唯一匹配（2026-10-08）优先于 latest 兜底：「耳机那单到
        # 哪了」按商品引用精确锁定订单，而非盲查最近一单。解析失败保持
        # latest 既有路径（读侧放宽口径不变）。
        order_id = injected_order
        if not order_id:
            order_id = _semantic_unique_order(cs_route, user_id, tenant_id) or ""
        # 缺陷9：会话上下文注入的订单号优先；_get_latest_order_id（按用户
        # 全局最近一单）是既有 fallback，保留原行为不在本轮扩大。
        # STOP C（2026-10-07）：订单/物流服务故障必须译成「服务不可用」，
        # 不得落到「暂无物流信息」（没有物流记录 ≠ 查不到物流）。
        try:
            order_id = order_id or _get_latest_order_id(user_id)
        except DatabaseError:
            return "订单服务暂时不可用，暂时无法查询物流，请稍后再试。"
        except Exception:
            logger.warning(
                "[QueryExpert] 物流前置取单失败（未分类异常）", exc_info=True,
            )
            return "订单服务暂时不可用，暂时无法查询物流，请稍后再试。"
        if not order_id:
            # 真没有可查物流的订单（用户名下无订单），如实说明
            return "您名下暂时没有可查询物流的订单，查询订单后即可查看物流。"
        try:
            result = logistics.query_logistics(user_id=user_id, order_id=order_id)
            shipping_status = {
                "pending": "not_shipped", "paid": "not_shipped",
                "shipped": "shipped", "completed": "delivered",
                "cancelled": "cancelled",
            }.get(str(getattr(result, "status", "") or "").lower(), "unknown")
            return _QueryServiceResponse(
                _format_logistics(result),
                {
                    "order": {
                        "order_no": str(getattr(result, "order_no", "") or ""),
                        "status": str(getattr(result, "status", "") or "unknown"),
                        "shipping_status": shipping_status,
                    },
                    "logistics": {
                        "status": str(getattr(result, "status", "") or "unknown"),
                        "shipping_status": shipping_status,
                        "estimated_delivery": str(
                            getattr(result, "estimated_delivery", "") or ""
                        ),
                    },
                    "source": _task_query_source("logistics"),
                },
            )
        except OrderNotFoundError:
            return (
                f"没有找到订单 {order_id} 的物流记录。请核对订单号，"
                "或告诉我「查我的所有订单」。"
            )
        except DatabaseError:
            return "物流服务暂时不可用，请稍后再试，稍后我来帮您再查一次。"
        except Exception:
            logger.warning(
                "[QueryExpert] 物流查询失败（未分类异常）", exc_info=True,
            )
            return "物流服务暂时不可用，请稍后再试。"

    if service_type == "ticket":
        # 批次C：工单进度查询（统一工单表，投诉/转人工落库后可查）
        try:
            from backend.customer_service.ticket_store import (
                get_ticket_store,
            )

            tickets = get_ticket_store().list_for_user_sync(
                user_id=user_id, limit=10,
            )
            return _QueryServiceResponse(
                _format_ticket_list(tickets),
                {"tickets": [
                    {key: ticket[key] for key in ("type", "status")
                     if isinstance(ticket, dict) and key in ticket}
                    for ticket in tickets[:10]
                ]},
            )
        except Exception:
            logger.warning("[QueryExpert] 工单查询失败", exc_info=True)
            return "暂时查不到您的工单信息，请稍后再试。"

    return "该功能正在建设中，请稍后再试。"


def _extract_order_no(question: str) -> str | None:
    """订单号提取（B4 收敛：委托 context_manager 单一实现）。

    规范化文本上抽取（字母数字混合段/关键词纯数字），识别不到返回 None
    走全量列表语义。
    """
    from backend.customer_service.context_manager import extract_order_entity

    return extract_order_entity(question) or None


def _semantic_unique_order(cs_route: dict, user_id: str, tenant_id: str) -> str:
    """语义槽位唯一匹配（2026-10-08，读侧增强）。

    语义候选（product/time 引用）按真实业务数据解析，唯一匹配返回订单号；
    多候选/无匹配/服务故障一律返回空串（读侧放宽口径：调用方保持既有
    列表查询或 latest 兜底），绝不抛异常打断查询主链。
    """
    try:
        from backend.customer_service.context.semantic_slots import (
            resolve_from_metadata,
        )

        resolution = resolve_from_metadata(tenant_id, user_id, cs_route)
        if resolution.resolved:
            return resolution.order_id
        if resolution.ambiguous:
            logger.info(
                "[QueryExpert] semantic slots ambiguous (%d candidates) "
                "→ 保持既有查询路径", len(resolution.candidates),
            )
    except Exception:
        logger.warning("[QueryExpert] semantic slot resolve failed", exc_info=True)
    return ""


def _get_latest_order_id(user_id: str) -> str | None:
    """获取用户最新订单 ID（用于无指定 order_id 的物流查询）。

    STOP C（2026-10-07）：不吞异常——订单服务不可用（DatabaseError 等）
    必须上抛给调用方按「服务不可用」话术处置；在此处吞成 None 会把
    服务故障降级成「暂无物流信息」式的业务假信息。
    """
    from backend.customer_service.service.order_service import get_order_service
    result = get_order_service().query_orders(user_id=user_id)
    if result.orders:
        return str(result.orders[0].get("id") or result.orders[0].get("order_no"))
    return None


_TICKET_STATUS_LABELS = {
    "open": "已受理",
    "processing": "处理中",
    "pending_user": "等待您补充信息",
    "resolved": "已解决",
    "closed": "已关闭",
}

_TICKET_TYPE_LABELS = {
    "complaint": "投诉",
    "handoff": "人工服务",
    "inquiry": "咨询",
    "repair": "报修",
}


def _format_ticket_list(tickets: list[dict]) -> str:
    """格式化工单列表为 Markdown（批次C）。"""
    if not tickets:
        return (
            "您当前没有进行中的工单。如需帮助，可以描述您的问题，"
            "或回复「转人工」由客服人员为您处理。"
        )

    lines = ["## 您的工单\n"]
    for i, t in enumerate(tickets[:10], 1):
        status = _TICKET_STATUS_LABELS.get(t.get("status", ""), t.get("status", ""))
        type_label = _TICKET_TYPE_LABELS.get(t.get("type", ""), t.get("type", ""))
        title = (t.get("title") or type_label)[:40]
        lines.append(
            f"**{i}.** [{type_label}] {title} | "
            f"工单号: `{t.get('ticket_id', '')}` | "
            f"状态: {status}"
        )
    lines.append("\n如需了解工单详情，请回复工单号，或回复「转人工」咨询客服。")
    return "\n".join(lines)


def _format_order_list(orders: list[dict]) -> str:
    """格式化订单列表为 Markdown。"""
    if not orders:
        return "您当前没有相关订单记录。"

    lines = ["## 您的订单\n"]
    for i, o in enumerate(orders[:10], 1):
        order_no = o.get("order_no", "N/A")
        status = o.get("status", "未知")
        amount = o.get("total_amount", "0")
        created = o.get("created_at", "")
        if hasattr(created, "strftime"):
            created = created.strftime("%Y-%m-%d")
        else:
            created = str(created)[:10] if created else ""
        lines.append(
            f"**{i}.** 订单号: `{order_no}` | "
            f"状态: {status} | "
            f"金额: ¥{amount} | "
            f"时间: {created}"
        )

    if len(orders) > 10:
        lines.append(f"\n*仅显示最近 10 条，共 {len(orders)} 条订单。*")

    return "\n".join(lines)


def _format_logistics(result: Any) -> str:
    """格式化物流信息为 Markdown。"""
    lines = [
        "## 物流信息\n",
        f"**订单号:** `{result.order_no}`",
        f"**物流状态:** {result.status_display}",
    ]
    if result.tracking_info:
        lines.append(f"\n{result.tracking_info}")
    return "\n".join(lines)
