"""客服支付流水查询服务（B4，查询型）。"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any
from urllib.parse import urlsplit


class PaymentError(Exception):
    """支付查询契约或业务服务错误。"""


class PaymentNotFoundError(PaymentError):
    """订单不存在或不属于当前用户。"""


_PAYMENT_STATUSES = {"success", "failed"}
_PAYMENT_KINDS = {"alipay", "wechat_pay", "bank_card", "wallet", "unknown"}
_PAYMENT_KIND_LABELS = {
    "alipay": "支付宝",
    "wechat_pay": "微信支付",
    "bank_card": "银行卡",
    "wallet": "账户余额",
    "unknown": "支付渠道未知",
}


def _use_http_gateway() -> bool:
    from backend.config.customer_service import CS_BUSINESS_GATEWAY_MODE

    return CS_BUSINESS_GATEWAY_MODE == "http"


def _unwrap(resp: dict[str, Any]) -> dict[str, Any]:
    if resp.get("code", 0) != 0:
        raise PaymentError("业务网关返回支付查询失败")
    data = resp.get("data")
    if not isinstance(data, dict):
        raise PaymentError("业务网关响应缺少 data 字段")
    payment = data.get("payment")
    if not isinstance(payment, dict):
        raise PaymentError("业务网关响应缺少 payment 字段")
    return payment


def _mock_error(exc: Exception) -> PaymentError:
    from backend.infra.http.business_client import BusinessServiceError

    if isinstance(exc, BusinessServiceError) and "-> 404" in str(exc):
        return PaymentNotFoundError("订单不存在或无权查询支付记录。")
    return PaymentError(str(exc))


def _decimal_amount(value: object) -> Decimal | None:
    try:
        amount = Decimal(str(value)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP,
        )
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not amount.is_finite() or amount < 0:
        return None
    return amount


def _payment_view(raw: dict[str, Any]) -> dict[str, Any]:
    """归一支付流水；截断时禁止生成确定性结论或退款候选。"""
    total = _decimal_amount(raw.get("total_amount"))
    total_amount = float(total) if total is not None else None
    currency = str(raw.get("currency") or "").strip().upper()
    raw_records = raw.get("payments")
    has_records = isinstance(raw_records, list)
    reported_complete = raw.get("payments_complete")
    reported_payment_count = raw.get("payment_count")
    if isinstance(reported_complete, bool):
        payments_complete = reported_complete
    elif (
        isinstance(reported_payment_count, int)
        and not isinstance(reported_payment_count, bool)
        and reported_payment_count >= 0
    ):
        payments_complete = (
            has_records
            and reported_payment_count <= 20
            and len(raw_records) >= reported_payment_count
        )
    else:
        payments_complete = has_records and len(raw_records) <= 20
    if has_records and len(raw_records) > 20:
        payments_complete = False
    if (
        isinstance(reported_payment_count, int)
        and not isinstance(reported_payment_count, bool)
        and reported_payment_count >= 0
        and (
            reported_payment_count > 20
            or not has_records
            or reported_payment_count != len(raw_records)
        )
    ):
        payments_complete = False

    normalized: list[dict[str, Any]] = []
    observed_success_count = 0
    for item in (raw_records if has_records else []):
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "unknown").lower()
        if status == "success":
            observed_success_count += 1
        if status not in _PAYMENT_STATUSES:
            status = "unknown"
        kind = str(item.get("kind") or "unknown").lower()
        if kind not in _PAYMENT_KINDS:
            kind = "unknown"
        amount = _decimal_amount(item.get("amount"))
        if amount is None:
            continue
        paid_at = item.get("paid_at")
        if hasattr(paid_at, "isoformat"):
            paid_at = paid_at.isoformat()
        normalized.append({
            "status": status,
            "amount": float(amount),
            "paid_at": str(paid_at or ""),
            "kind": kind,
            "kind_label": _PAYMENT_KIND_LABELS[kind],
            **({"refund_status": "simulated"}
               if str(item.get("refund_status") or "").lower()
               == "simulated" else {}),
            **({"refund_id": str(item.get("refund_id") or "")}
               if item.get("refund_id") else {}),
        })

    reported_success_count = raw.get("success_count")
    if (
        isinstance(reported_success_count, int)
        and not isinstance(reported_success_count, bool)
        and reported_success_count >= observed_success_count
    ):
        success_count = reported_success_count
    else:
        success_count = observed_success_count

    successful = [
        item for item in normalized if item["status"] == "success"
    ]
    refunded = any(item.get("refund_status") for item in normalized)
    refund_candidate = bool(
        payments_complete
        and has_records
        and len(raw_records) <= 20
        and total is not None
        and len(successful) == 2
        and success_count == 2
        and not refunded
        and all(_decimal_amount(item["amount"]) == total for item in successful)
    )
    duplicate_payment = (
        True if success_count > 1
        else False if payments_complete
        else None
    )
    return {
        "order_no": str(raw.get("order_no") or ""),
        "total_amount": total_amount,
        "currency": currency,
        "payments": normalized[:20],
        "payments_complete": payments_complete,
        "success_count": success_count,
        "duplicate_payment": duplicate_payment,
        "refund_candidate": refund_candidate,
    }


def auto_duplicate_refund_simulation_eligible(
    payment: dict[str, Any],
    *,
    source: str,
) -> bool:
    """只允许显式 mock 模式下的小额、同币种重复扣款模拟退款。"""
    from backend.config.customer_service import (
        CS_AUTO_DUPLICATE_REFUND_MAX_AMOUNT,
        CS_AUTO_DUPLICATE_REFUND_MODE,
        CS_BUSINESS_GATEWAY_MODE,
    )
    from backend.config.messaging import BUSINESS_SERVICE_URL

    amount = _decimal_amount(payment.get("total_amount"))
    limit = _decimal_amount(CS_AUTO_DUPLICATE_REFUND_MAX_AMOUNT)
    try:
        mock_target = (
            urlsplit(BUSINESS_SERVICE_URL.strip()).scheme == "http"
            and urlsplit(BUSINESS_SERVICE_URL.strip()).hostname == "business-mock"
            and urlsplit(BUSINESS_SERVICE_URL.strip()).port == 8081
        )
    except ValueError:
        mock_target = False
    return bool(
        CS_BUSINESS_GATEWAY_MODE == "http"
        and CS_AUTO_DUPLICATE_REFUND_MODE == "mock"
        and source == "business_payment_service"
        and mock_target
        and payment.get("refund_candidate") is True
        and str(payment.get("currency") or "").strip().upper() == "CNY"
        and amount is not None and amount > 0
        and limit is not None and limit > 0
        and amount <= limit
    )


class PaymentService:
    """支付流水只读查询；不提供支付或退款写入接口。"""

    def get_payments(
        self, user_id: str, tenant_id: str, order_no: str,
    ) -> dict[str, Any]:
        if _use_http_gateway():
            try:
                from backend.infra.http.business_client import get_json_sync

                raw = _unwrap(get_json_sync(
                    "/business/payments",
                    params={
                        "user_id": str(user_id),
                        "tenant_id": str(tenant_id),
                        "order_no": str(order_no),
                    },
                ))
                return _payment_view(raw)
            except PaymentError:
                raise
            except Exception as exc:
                raise _mock_error(exc) from exc
        return self._payments_local(user_id, tenant_id, order_no)

    def _payments_local(
        self, user_id: str, tenant_id: str, order_no: str,
    ) -> dict[str, Any]:
        from sqlalchemy import text

        from backend.infra.db import get_business_engine

        sql = text(
            'SELECT o.order_no, o.total_amount, p.status, p.amount, p.paid_at, p.kind'
            ' FROM "order".orders AS o'
            ' LEFT JOIN payment.payment_records AS p'
            '   ON p.order_no = o.order_no'
            '  AND p.customer_id = o.customer_id'
            '  AND p.tenant_id = :tenant_id'
            ' WHERE o.order_no = :order_no AND o.customer_id::text = :user_id'
            # 多取一条作为截断哨兵；超过展示上限时不据不完整记录生成退款提案。
            ' ORDER BY p.id LIMIT 21'
        )
        with get_business_engine().connect() as conn:
            rows = conn.execute(sql, {
                "tenant_id": str(tenant_id),
                "order_no": str(order_no),
                "user_id": str(user_id),
            }).mappings().all()
        if not rows:
            raise PaymentNotFoundError("订单不存在或无权查询支付记录。")
        first = rows[0]
        payments = [
            {
                "status": row["status"], "amount": row["amount"],
                "paid_at": row["paid_at"], "kind": row["kind"],
            }
            for row in rows if row["status"] is not None
        ]
        return _payment_view({
            "order_no": first["order_no"],
            "total_amount": first["total_amount"],
            "currency": "CNY",
            "payments": payments,
        })
