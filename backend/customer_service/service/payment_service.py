"""客服支付流水查询服务（B4，查询型）。"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


class PaymentError(Exception):
    """支付查询契约或业务服务错误。"""


class PaymentNotFoundError(PaymentError):
    """订单不存在或不属于当前用户。"""


_PAYMENT_STATUSES = {"success", "failed"}
_PAYMENT_KINDS = {"alipay", "wechat_pay", "bank_card", "unknown"}
_PAYMENT_KIND_LABELS = {
    "alipay": "支付宝",
    "wechat_pay": "微信支付",
    "bank_card": "银行卡",
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
    """归一两种数据模式的流水，并严格判断可否走订单全额退款提案。"""
    total = _decimal_amount(raw.get("total_amount"))
    total_amount = float(total) if total is not None else None
    records: list[dict[str, Any]] = []
    raw_records = raw.get("payments")
    if isinstance(raw_records, list):
        for item in raw_records[:20]:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "unknown").lower()
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
            records.append({
                "status": status,
                "amount": float(amount),
                "paid_at": str(paid_at or ""),
                "kind": kind,
                "kind_label": _PAYMENT_KIND_LABELS[kind],
            })

    successful = [item for item in records if item["status"] == "success"]
    refund_candidate = bool(
        total is not None
        and isinstance(raw_records, list)
        and len(raw_records) <= 20
        and len(successful) == 2
        and all(_decimal_amount(item["amount"]) == total for item in successful)
    )
    return {
        "order_no": str(raw.get("order_no") or ""),
        "total_amount": total_amount,
        "currency": "CNY",
        "payments": records,
        "success_count": len(successful),
        "duplicate_payment": len(successful) > 1,
        "refund_candidate": refund_candidate,
    }


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
            "payments": payments,
        })
