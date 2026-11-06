"""客服回复使用的递归业务事实白名单。"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from backend.customer_service.understanding.llm_runtime import mask_for_llm


_ORDER_STATUS = {"pending", "paid", "shipped", "completed", "cancelled", "unknown"}
_SHIPPING_STATUS = {"not_shipped", "shipped", "delivered", "cancelled", "unknown"}
_TASK_STATUS = {"success", "needs_clarification", "skipped", "failed"}
_ERROR_TYPES = {
    "timeout", "network_error", "permission_denied", "validation_error",
    "business_error", "contract_error", "provider_error",
}
_SERVICE_SOURCES = {
    "sandbox_logistics_service", "business_logistics_service",
    "sandbox_order_service", "business_order_service",
    "sandbox_payment_service", "business_payment_service",
    "sandbox_business_service", "unknown",
}
_OPERATION_STATUS = {
    "pending_confirmation", "executed", "success", "failed", "cancelled",
    "skipped", "unknown",
}
_OPERATION_ACTION = {"refund", "return", "exchange", "repair", "unknown"}
_COMPLAINT_SEVERITY = {"low", "medium", "high", "critical", "unknown"}
_COMPLAINT_CLASS = {"complaint", "not_complaint", "unknown"}
_CURRENCY = {"cny", "usd", "eur", "jpy", "hkd", "unknown"}
_LEDGER_DIRECTION = {"in", "out"}
_TIER = {"normal", "silver", "gold", "platinum"}
_COUPON_KIND = {"full_reduction", "discount", "no_threshold"}
_COUPON_STATUS = {"available", "expired", "used"}
_COUPON_REASON = {
    "applicable", "threshold_not_met", "not_stackable", "expired",
    "excluded_category", "unknown",
}
_INVOICE_TITLE_TYPE = {"individual", "company", "unknown"}
_INVOICE_TITLE_STATUS = {"active", "inactive", "unknown"}
_INVOICE_TYPE = {"electronic", "unknown"}
_INVOICE_STATUS = {"simulated", "unknown"}
_PAYMENT_STATUS = {"success", "failed", "unknown"}
_PAYMENT_KIND = {"alipay", "wechat_pay", "bank_card", "unknown"}


def project_fact_set(raw: object) -> dict[str, Any]:
    """只投影已登记业务字段；嵌套对象与列表逐层应用对应 schema。"""
    if not isinstance(raw, dict):
        return {}

    combined = dict(raw)
    service_facts = raw.get("service_facts")
    if isinstance(service_facts, dict):
        combined.update(service_facts)

    result: dict[str, Any] = {}
    projectors = {
        "order": lambda value: _project_order(value),
        "orders": lambda value: _project_list(value, _project_order),
        "logistics": lambda value: _project_logistics(value),
        "refund": lambda value: _project_refund(value),
        "balance": lambda value: _project_balance(value),
        "points": lambda value: _project_points(value),
        "coupons": lambda value: _project_list(value, _project_coupon),
        "coupon_check": lambda value: _project_coupon_check(value),
        "invoice_titles": lambda value: _project_list(value, _project_invoice_title),
        "invoices": lambda value: _project_list(value, _project_invoice),
        "invoice_summary": lambda value: _project_invoice_summary(value),
        "payment": lambda value: _project_payment(value),
        "operation": lambda value: _project_operation(value),
        "task_result": lambda value: _project_task_result(value),
        "complaint": lambda value: _project_complaint(value),
        "tickets": lambda value: _project_list(value, _project_ticket),
        "evidence": lambda value: _project_list(value, _project_evidence),
    }
    for key, projector in projectors.items():
        if key in combined:
            projected = projector(combined[key])
            if projected:
                result[key] = projected

    source = _enum_value(combined.get("source"), _SERVICE_SOURCES)
    if source is not None:
        result["source"] = source
    query_time = _safe_text(combined.get("query_time"), limit=40)
    if query_time:
        result["query_time"] = query_time
    return result


def build_fact_set(expert_result: object) -> dict[str, Any]:
    """从 Expert 实际结构化结果组装 FactSet，不读取模板草稿或原始状态。"""
    if not isinstance(expert_result, dict):
        return {}
    data = expert_result.get("data")
    raw = dict(data) if isinstance(data, dict) else {}
    evidence = expert_result.get("evidence")
    if isinstance(evidence, list):
        raw["evidence"] = evidence

    if any(key in raw for key in ("severity", "escalated", "complaint_classified")):
        raw["complaint"] = {
            "severity": raw.get("severity"),
            "escalated": raw.get("escalated"),
            "classification": raw.get("complaint_classified"),
        }
    return project_fact_set(raw)


def _project_order(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for key in ("order_no", "status", "shipping_status", "created_at"):
        if key == "status":
            item = _enum_value(value.get(key), _ORDER_STATUS)
        elif key == "shipping_status":
            item = _enum_value(value.get(key), _SHIPPING_STATUS)
        else:
            item = _safe_text(value.get(key), limit=40)
        if item is not None and item != "":
            result[key] = item
    amount = _safe_amount(value.get("total_amount"))
    if amount is not None:
        result["total_amount"] = amount
    currency = _enum_value(value.get("currency"), _CURRENCY)
    if currency is not None:
        result["currency"] = currency
    eligible = value.get("refund_eligible")
    if isinstance(eligible, bool):
        result["refund_eligible"] = eligible
    return result


def _project_logistics(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    order_no = _safe_text(value.get("order_no"), limit=40)
    if order_no:
        result["order_no"] = order_no
    for key in ("status", "shipping_status"):
        enum = _SHIPPING_STATUS if key == "shipping_status" else _ORDER_STATUS
        item = _enum_value(value.get(key), enum)
        if item is not None:
            result[key] = item
    eta = _safe_text(value.get("estimated_delivery"), limit=40)
    if eta:
        result["estimated_delivery"] = eta
    query_time = _safe_text(value.get("query_time"), limit=40)
    if query_time:
        result["query_time"] = query_time
    return result


def _project_refund(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    order_no = _safe_text(value.get("order_no"), limit=40)
    if order_no:
        result["order_no"] = order_no
    estimated_arrival = _safe_text(value.get("estimated_arrival"), limit=40)
    if estimated_arrival:
        result["estimated_arrival"] = estimated_arrival
    if isinstance(value.get("eligible"), bool):
        result["eligible"] = value["eligible"]
    status = _enum_value(value.get("status"), _OPERATION_STATUS)
    if status is not None:
        result["status"] = status
    amount = _safe_amount(value.get("amount"))
    if amount is not None:
        result["amount"] = amount
    currency = _enum_value(value.get("currency"), _CURRENCY)
    if currency is not None:
        result["currency"] = currency
    reason = _enum_value(value.get("reason_code"), {
        "eligible", "ineligible", "unknown",
    })
    if reason is not None:
        result["reason_code"] = reason
    return result


def _project_balance(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    amount = _safe_amount(value.get("balance"))
    if amount is not None:
        result["balance"] = amount
    currency = _enum_value(value.get("currency"), _CURRENCY)
    if currency is not None:
        result["currency"] = currency
    direction = _enum_value(value.get("recent_direction"), _LEDGER_DIRECTION)
    if direction is not None:
        result["recent_direction"] = direction
    recent_amount = _safe_amount(value.get("recent_amount"))
    if recent_amount is not None:
        result["recent_amount"] = recent_amount
    memo = _safe_text(value.get("recent_memo"), limit=40)
    if memo:
        result["recent_memo"] = memo
    return result


def _project_points(value: object) -> dict[str, Any]:
    """B2 积分/等级投影：账户事实白名单（流水只留最近一笔，W1 同口径）。"""
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    points = _safe_amount(value.get("points"))
    if points is not None:
        result["points"] = points
    tier = _enum_value(value.get("tier"), _TIER)
    if tier is not None:
        result["tier"] = tier
    tier_label = _safe_text(value.get("tier_label"), limit=20)
    if tier_label:
        result["tier_label"] = tier_label
    tier_note = _safe_text(value.get("tier_note"), limit=40)
    if tier_note:
        result["tier_note"] = tier_note
    direction = _enum_value(value.get("recent_direction"), _LEDGER_DIRECTION)
    if direction is not None:
        result["recent_direction"] = direction
    recent_points = _safe_amount(value.get("recent_points"))
    if recent_points is not None:
        result["recent_points"] = recent_points
    memo = _safe_text(value.get("recent_memo"), limit=40)
    if memo:
        result["recent_memo"] = memo
    return result


def _project_coupon(value: object) -> dict[str, Any]:
    """B3 优惠券投影：券面额/门槛是元金额，经白名单进 composer 守卫。"""
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    currency = _enum_value(value.get("currency"), _CURRENCY)
    if currency is not None:
        result["currency"] = currency
    coupon_code = _safe_text(value.get("coupon_code"), limit=40)
    if coupon_code:
        result["coupon_code"] = coupon_code
    kind = _enum_value(value.get("kind"), _COUPON_KIND)
    if kind is not None:
        result["kind"] = kind
    kind_label = _safe_text(value.get("kind_label"), limit=20)
    if kind_label:
        result["kind_label"] = kind_label
    for key in ("threshold_amount", "discount_value"):
        amount = _safe_amount(value.get(key))
        if amount is not None:
            result[key] = amount
    if value.get("max_discount") is not None:
        max_discount = _safe_amount(value.get("max_discount"))
        if max_discount is not None:
            result["max_discount"] = max_discount
    if isinstance(value.get("stackable"), bool):
        result["stackable"] = value["stackable"]
    status = _enum_value(value.get("status"), _COUPON_STATUS)
    if status is not None:
        result["status"] = status
    valid_until = _safe_text(value.get("valid_until"), limit=40)
    if valid_until:
        result["valid_until"] = valid_until
    return result


def _project_invoice_title(value: object) -> dict[str, Any]:
    """B8 发票抬头投影不透出税号、邮箱等敏感字段。"""
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for key, limit in (("title_id", 64), ("title_name", 120)):
        item = _safe_text(value.get(key), limit=limit)
        if item:
            result[key] = item
    title_type = _enum_value(value.get("title_type"), _INVOICE_TITLE_TYPE)
    if title_type is not None:
        result["title_type"] = title_type
    if isinstance(value.get("is_default"), bool):
        result["is_default"] = value["is_default"]
    status = _enum_value(value.get("status"), _INVOICE_TITLE_STATUS)
    if status is not None:
        result["status"] = status
    return result


def _project_invoice(value: object) -> dict[str, Any]:
    """B8 模拟开票记录只公开抬头摘要，不声称产生税务发票。"""
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for key, limit in (
        ("invoice_no", 64), ("order_no", 64), ("title_name", 120),
        ("created_at", 40),
    ):
        item = _safe_text(value.get(key), limit=limit)
        if item:
            result[key] = item
    title_type = _enum_value(value.get("title_type"), _INVOICE_TITLE_TYPE)
    if title_type is not None:
        result["title_type"] = title_type
    invoice_type = _enum_value(value.get("invoice_type"), _INVOICE_TYPE)
    if invoice_type is not None:
        result["invoice_type"] = invoice_type
    status = _enum_value(value.get("status"), _INVOICE_STATUS)
    if status is not None:
        result["status"] = status
    if isinstance(value.get("real_invoice_issued"), bool):
        result["real_invoice_issued"] = value["real_invoice_issued"]
    return result


def _project_invoice_summary(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for key in ("title_count", "invoice_count"):
        count = value.get(key)
        if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
            result[key] = min(count, 20)
    if isinstance(value.get("has_invoice_records"), bool):
        result["has_invoice_records"] = value["has_invoice_records"]
    return result


def _project_coupon_check(value: object) -> dict[str, Any]:
    """B3 用券校验投影：适用性 + 不生效原因（四类）+ 所验券摘要。"""
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    currency = _enum_value(value.get("currency"), _CURRENCY)
    if currency is not None:
        result["currency"] = currency
    if isinstance(value.get("applicable"), bool):
        result["applicable"] = value["applicable"]
    reason_code = _enum_value(value.get("reason_code"), _COUPON_REASON)
    if reason_code is not None:
        result["reason_code"] = reason_code
    reason_label = _safe_text(value.get("reason_label"), limit=30)
    if reason_label:
        result["reason_label"] = reason_label
    reason_text = _safe_text(value.get("reason_text"), limit=80)
    if reason_text:
        result["reason_text"] = reason_text
    order_no = _safe_text(value.get("order_no"), limit=40)
    if order_no:
        result["order_no"] = order_no
    order_total = _safe_amount(value.get("order_total"))
    if order_total is not None:
        result["order_total"] = order_total
    coupon = value.get("coupon")
    if isinstance(coupon, dict):
        projected_coupon = _project_coupon(coupon)
        if projected_coupon:
            result["coupon"] = projected_coupon
    return result


def _project_payment_record(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    status = _enum_value(value.get("status"), _PAYMENT_STATUS)
    kind = _enum_value(value.get("kind"), _PAYMENT_KIND)
    amount = _safe_amount(value.get("amount"))
    paid_at = _safe_text(value.get("paid_at"), limit=40)
    kind_label = _safe_text(value.get("kind_label"), limit=20)
    if status is not None:
        result["status"] = status
    if kind is not None:
        result["kind"] = kind
    if amount is not None:
        result["amount"] = amount
    if paid_at:
        result["paid_at"] = paid_at
    if kind_label:
        result["kind_label"] = kind_label
    return result


def _project_payment(value: object) -> dict[str, Any]:
    """B4 支付事实白名单；只输出查询所需流水，不暴露租户/客户标识。"""
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    order_no = _safe_text(value.get("order_no"), limit=40)
    if order_no:
        result["order_no"] = order_no
    total = _safe_amount(value.get("total_amount"))
    if total is not None:
        result["total_amount"] = total
    currency = _enum_value(value.get("currency"), _CURRENCY)
    if currency is not None:
        result["currency"] = currency
    count = value.get("success_count")
    if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
        result["success_count"] = count
    for key in (
        "duplicate_payment", "refund_candidate", "payments_complete",
    ):
        if isinstance(value.get(key), bool):
            result[key] = value[key]
    payments = value.get("payments")
    if isinstance(payments, list):
        projected = _project_list(payments, _project_payment_record)
        if projected:
            result["payments"] = projected
    return result


def _project_operation(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    status = _enum_value(value.get("status"), _OPERATION_STATUS)
    action = _enum_value(value.get("action"), _OPERATION_ACTION)
    source = _enum_value(value.get("source"), _SERVICE_SOURCES)
    if status is not None:
        result["status"] = status
    if action is not None:
        result["action"] = action
    if source is not None:
        result["source"] = source
    if isinstance(value.get("simulated"), bool):
        result["simulated"] = value["simulated"]
    return result


def _project_task_result(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    task_id = _safe_text(value.get("task_id"), limit=20)
    if task_id and task_id.isascii():
        result["task_id"] = task_id
    status = _enum_value(value.get("status"), _TASK_STATUS)
    source = _enum_value(value.get("source"), _SERVICE_SOURCES)
    error_type = _enum_value(value.get("error_type"), _ERROR_TYPES)
    if status is not None:
        result["status"] = status
    if source is not None:
        result["source"] = source
    if error_type is not None:
        result["error_type"] = error_type
    facts = value.get("facts")
    if isinstance(facts, dict):
        safe_facts: dict[str, Any] = {}
        count = facts.get("order_count")
        if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
            safe_facts["order_count"] = count
        order_no = _safe_text(facts.get("order_no"), limit=40)
        if order_no:
            safe_facts["order_no"] = order_no
        order_status = _enum_value(facts.get("order_status"), _ORDER_STATUS)
        shipping_status = _enum_value(
            facts.get("shipping_status"), _SHIPPING_STATUS,
        )
        if order_status is not None:
            safe_facts["order_status"] = order_status
        if shipping_status is not None:
            safe_facts["shipping_status"] = shipping_status
        success_count = facts.get("success_count")
        if (
            isinstance(success_count, int)
            and not isinstance(success_count, bool)
            and success_count >= 0
        ):
            safe_facts["success_count"] = success_count
        for key in (
            "duplicate_payment", "refund_candidate", "payments_complete",
        ):
            if isinstance(facts.get(key), bool):
                safe_facts[key] = facts[key]
        amount = _safe_amount(facts.get("total_amount"))
        if amount is not None:
            safe_facts["total_amount"] = amount
        currency = _enum_value(facts.get("currency"), _CURRENCY)
        if currency is not None:
            safe_facts["currency"] = currency
        if safe_facts:
            result["facts"] = safe_facts
    return result


def _project_complaint(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    severity = _enum_value(value.get("severity"), _COMPLAINT_SEVERITY)
    classification = _enum_value(value.get("classification"), _COMPLAINT_CLASS)
    if severity is not None:
        result["severity"] = severity
    if classification is not None:
        result["classification"] = classification
    if isinstance(value.get("escalated"), bool):
        result["escalated"] = value["escalated"]
    return result


def _project_ticket(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    ticket_type = _enum_value(value.get("type"), {
        "complaint", "handoff", "inquiry", "repair",
    })
    status = _enum_value(value.get("status"), {
        "open", "processing", "pending_user", "resolved", "closed",
    })
    if ticket_type is not None:
        result["type"] = ticket_type
    if status is not None:
        result["status"] = status
    return result


def _project_evidence(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for key in ("source", "doc_id"):
        item = _safe_text(value.get(key), limit=100)
        if item:
            result[key] = item
    score = value.get("score")
    if isinstance(score, (int, float)) and not isinstance(score, bool):
        if 0 <= float(score) <= 1:
            result["score"] = float(score)
    return result


def _project_list(value: object, projector) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value[:20]:
        projected = projector(item)
        if projected:
            result.append(projected)
    return result


def _enum_value(value: object, allowed: set[str]) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized not in allowed:
        return None
    return normalized.upper() if allowed is _CURRENCY else normalized


def _safe_text(value: object, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return mask_for_llm(value[:limit]).strip()


def _safe_amount(value: object) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        amount = Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    if not amount.is_finite() or amount < 0:
        return None
    return format(amount.normalize(), "f")


__all__ = ["build_fact_set", "project_fact_set"]
