"""B6 价保退差价：确认后仅向 business-mock 写演示钱包流水。"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from urllib.parse import quote, urlsplit

from backend.customer_service.action import ActionProposal, ActionType, AgentActionRecord
from backend.customer_service.errors import (
    DatabaseError,
    OrderNotEligibleError,
    OrderNotFoundError,
    PriceMatchUnavailableError,
)
from backend.customer_service.risk import RiskLevel

_CENT = Decimal("0.01")
_ELIGIBLE_STATUSES = frozenset({"paid", "completed"})


def _money(value: object, field: str) -> Decimal:
    try:
        amount = Decimal(str(value)).quantize(_CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        raise OrderNotEligibleError(f"{field}无法核验，价保未执行") from None
    if not amount.is_finite() or amount <= 0:
        raise OrderNotEligibleError(f"{field}无效，价保未执行")
    return amount


def _unwrap(response: dict) -> dict:
    if not isinstance(response, dict) or response.get("code", 0) != 0:
        code = response.get("code") if isinstance(response, dict) else "invalid"
        if code in (40401, 40901):
            raise OrderNotEligibleError("订单或商品价格已变化，价保未执行；请重新查询")
        raise DatabaseError("价保业务网关返回失败")
    data = response.get("data")
    if not isinstance(data, dict):
        raise DatabaseError("价保业务网关响应格式异常")
    return data


class PriceMatchService:
    """用户确认后在 business-mock 演示钱包记一笔差价入账。"""

    def _ensure_mock_mode(self) -> int:
        from backend.config.customer_service import (
            CS_BUSINESS_GATEWAY_MODE,
            CS_PRICE_MATCH_MODE,
            CS_PRICE_MATCH_WINDOW_DAYS,
        )

        if CS_PRICE_MATCH_MODE != "mock":
            raise PriceMatchUnavailableError("CS_PRICE_MATCH_MODE 未显式设为 mock")
        if CS_BUSINESS_GATEWAY_MODE != "http":
            raise PriceMatchUnavailableError("价保只允许使用 HTTP business-mock")
        try:
            from backend.config.messaging import BUSINESS_SERVICE_URL

            endpoint = urlsplit(str(BUSINESS_SERVICE_URL).strip())
            allowed = {
                ("business-mock", 8081),
                ("127.0.0.1", 8081),
                ("localhost", 8081),
            }
            if (
                endpoint.scheme != "http"
                or endpoint.hostname is None
                or (endpoint.hostname.lower(), endpoint.port) not in allowed
                or endpoint.username
                or endpoint.password
                or endpoint.query
                or endpoint.fragment
                or endpoint.path not in ("", "/")
            ):
                raise ValueError("business-mock endpoint not allowlisted")
        except (TypeError, ValueError):
            raise PriceMatchUnavailableError(
                "价保目标地址不是受允许的 business-mock",
            ) from None
        try:
            window_days = int(str(CS_PRICE_MATCH_WINDOW_DAYS).strip())
        except (TypeError, ValueError):
            window_days = 0
        if window_days <= 0:
            raise PriceMatchUnavailableError(
                "价保窗口尚未配置，未创建提案",
            )
        return window_days

    def _query_order(self, user_id: str, order_id: str) -> dict:
        if not order_id:
            raise OrderNotEligibleError("请先提供订单号，系统不会替您选择订单")
        from backend.customer_service.service.order_service import get_order_service

        result = get_order_service().query_orders(
            user_id, order_id=order_id, query_type="detail",
        )
        if not result.orders:
            raise OrderNotFoundError()
        return result.orders[0]

    def _query_current_price(self, product_no: str) -> tuple[Decimal, str]:
        from backend.infra.http.business_client import (
            BusinessServiceError,
            get_json_sync,
        )

        try:
            response = get_json_sync(
                f"/business/products/{quote(product_no, safe='')}/current-price",
            )
        except BusinessServiceError as exc:
            if exc.status_code == 404:
                raise OrderNotEligibleError(
                    "当前商品没有可核验的自营售价，价保未执行",
                ) from exc
            raise DatabaseError("当前商品售价查询暂不可用") from exc
        data = _unwrap(response)
        if str(data.get("product_no") or "") != product_no:
            raise DatabaseError("当前售价响应商品编号不匹配")
        return _money(data.get("price"), "当前售价"), str(data.get("currency") or "")

    def _facts(self, user_id: str, order_id: str, window_days: int) -> dict:
        order = self._query_order(user_id, order_id)
        status = str(order.get("status") or "")
        payment_status = str(order.get("payment_status") or "")
        if status not in _ELIGIBLE_STATUSES or payment_status != "paid":
            raise OrderNotEligibleError("该订单当前状态不符合价保条件，未发起退款")
        if str(order.get("currency") or "") != "CNY":
            raise OrderNotEligibleError("目前仅支持人民币订单的价保模拟")

        items = order.get("items")
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
            raise OrderNotEligibleError("订单商品明细不足以安全核价，未发起退款")
        item = items[0]
        try:
            quantity = int(item.get("quantity", 0) or 0)
        except (TypeError, ValueError):
            quantity = 0
        if quantity != 1:
            raise OrderNotEligibleError("目前仅支持单件商品的价保模拟")
        product_no = str(item.get("product_no") or "").strip()
        if not product_no:
            raise OrderNotEligibleError("订单缺少可核验的商品编号，未发起退款")

        original_price = _money(item.get("price"), "订单商品价格")
        order_amount = _money(order.get("total_amount"), "订单金额")
        if original_price != order_amount:
            raise OrderNotEligibleError("订单金额与商品金额不一致，未发起退款")

        paid_at_value = order.get("paid_at")
        try:
            paid_at = (
                paid_at_value if isinstance(paid_at_value, datetime)
                else datetime.fromisoformat(
                    str(paid_at_value).replace("Z", "+00:00"),
                )
            )
            if paid_at.tzinfo is None:
                raise ValueError("paid_at has no timezone")
            paid_at = paid_at.astimezone(timezone.utc)
        except (TypeError, ValueError):
            raise OrderNotEligibleError("订单支付时间无法核验，未发起退款") from None
        age_seconds = (datetime.now(timezone.utc) - paid_at).total_seconds()
        if age_seconds < 0 or age_seconds > window_days * 86400:
            raise OrderNotEligibleError("订单不在当前配置的价保窗口内，未发起退款")

        current_price, price_currency = self._query_current_price(product_no)
        if price_currency != "CNY":
            raise OrderNotEligibleError("当前售价币种无法核验，未发起退款")
        difference = (original_price - current_price).quantize(_CENT)
        if difference <= 0:
            raise OrderNotEligibleError("当前自营售价未低于订单商品价，暂无可退差价")
        return {
            "order_id": str(order.get("id") or ""),
            "order_no": str(order.get("order_no") or ""),
            "status": status,
            "payment_status": payment_status,
            "currency": "CNY",
            "paid_at": paid_at.isoformat(),
            "window_days": window_days,
            "product_no": product_no,
            "product_name": str(item.get("product_name") or ""),
            "quantity": 1,
            "original_price": original_price,
            "current_price": current_price,
            "amount": difference,
        }

    def build_proposal(
        self, user_id: str, tenant_id: str, order_id: str,
    ) -> ActionProposal:
        window_days = self._ensure_mock_mode()
        facts = self._facts(user_id, order_id, window_days)
        amount_text = f"{facts['amount']:.2f}"
        proposal_text = (
            f"已核对订单 {facts['order_no']}：{facts['product_name']} "
            f"购买价 ¥{facts['original_price']:.2f}，当前自营售价 "
            f"¥{facts['current_price']:.2f}，差价 ¥{amount_text}。"
            "确认后只会在 business-mock 演示钱包记入 price_match 流水，"
            "不会影响真实资金；执行前会再次核对订单和当前售价。"
            "请确认是否继续。"
        )
        return ActionProposal(
            action_type=ActionType.PRICE_MATCH,
            target_type="order",
            target_id=facts["order_id"],
            risk_level=RiskLevel.HIGH,
            proposal_text=proposal_text,
            before_state={
                **{
                    key: facts[key]
                    for key in (
                        "order_id", "order_no", "status", "payment_status",
                        "currency", "paid_at", "window_days", "product_no",
                        "product_name", "quantity",
                    )
                },
                "original_price": str(facts["original_price"]),
                "current_price": str(facts["current_price"]),
                "amount": str(facts["amount"]),
            },
            after_state={
                "action": "price_match",
                "status": "simulated",
                "amount": str(facts["amount"]),
                "currency": "CNY",
                "real_funds_affected": False,
            },
        )

    def execute_confirmed_action(
        self,
        pending_action: dict,
        *,
        confirmation_id: str,
        user_id: str,
        tenant_id: str,
        conversation_id: str,
    ) -> AgentActionRecord:
        window_days = self._ensure_mock_mode()
        if not user_id or user_id == "anonymous" or not tenant_id or not conversation_id:
            raise PriceMatchUnavailableError("价保确认缺少可信用户、租户或会话身份")
        if pending_action.get("action_type") != ActionType.PRICE_MATCH:
            raise OrderNotEligibleError("价保确认动作类型无效")
        try:
            stable_confirmation_id = str(uuid.UUID(str(confirmation_id)))
        except (ValueError, TypeError, AttributeError):
            raise OrderNotEligibleError("确认标识无效，价保未执行") from None

        before = pending_action.get("before_state")
        after = pending_action.get("after_state")
        target_id = str(pending_action.get("target_id") or "")
        if (
            not isinstance(before, dict)
            or not isinstance(after, dict)
            or not target_id
            or str(before.get("order_id") or "") != target_id
            or str(after.get("action") or "") != "price_match"
            or str(before.get("window_days") or "") != str(window_days)
        ):
            raise OrderNotEligibleError("价保提案信息不完整或配置已变化，未执行")

        current = self._facts(user_id, target_id, window_days)
        for key in (
            "order_id", "order_no", "status", "payment_status", "currency",
            "paid_at", "product_no", "product_name", "quantity",
        ):
            if str(before.get(key) or "") != str(current[key]):
                raise OrderNotEligibleError("订单事实已变化，价保未执行；请重新查询")
        if (
            _money(before.get("original_price"), "提案订单价") != current["original_price"]
            or _money(before.get("current_price"), "提案当前价") != current["current_price"]
            or _money(before.get("amount"), "提案差价") != current["amount"]
            or _money(after.get("amount"), "确认差价") != current["amount"]
            or str(after.get("currency") or "") != "CNY"
            or after.get("real_funds_affected") is not False
        ):
            raise OrderNotEligibleError("提案金额或售价已变化，价保未执行；请重新查询")

        from backend.customer_service.service.demo_mode import resolve_user_id
        from backend.infra.http.business_client import (
            BusinessServiceError,
            request_json_sync,
        )
        from backend.shared.idempotency import SideEffectOutcomeUnknown

        payload = {
            "operation_key": stable_confirmation_id,
            "user_id": str(resolve_user_id(user_id)),
            "tenant_id": tenant_id,
            "order_no": current["order_no"],
            "expected_status": current["status"],
            "expected_payment_status": current["payment_status"],
            "expected_paid_at": current["paid_at"],
            "expected_currency": current["currency"],
            "product_no": current["product_no"],
            "expected_original_price": str(current["original_price"]),
            "expected_current_price": str(current["current_price"]),
            "expected_amount": str(current["amount"]),
            "window_days": window_days,
        }
        try:
            response = request_json_sync(
                "POST",
                f"/business/orders/{quote(current['order_no'], safe='')}/partial-refund",
                json_body=payload,
            )
        except BusinessServiceError as exc:
            if exc.status_code in (404, 409, 422):
                raise OrderNotEligibleError(
                    "订单或售价在确认时已变化，价保未执行；请重新查询",
                ) from exc
            raise SideEffectOutcomeUnknown(
                "价保写入结果无法确认，已暂停重复执行",
            ) from exc
        except Exception as exc:
            raise SideEffectOutcomeUnknown(
                "价保写入响应异常，结果无法确认，已暂停重复执行",
            ) from exc

        try:
            data = _unwrap(response)
        except (DatabaseError, OrderNotEligibleError) as exc:
            raise SideEffectOutcomeUnknown(
                "价保写入已发送，但响应无法核实，已暂停重复执行",
            ) from exc
        refund = data.get("refund")
        wallet = data.get("wallet")
        if (
            not isinstance(refund, dict)
            or not isinstance(wallet, dict)
            or str(refund.get("kind") or "") != "price_match"
            or str(refund.get("status") or "") != "simulated"
            or refund.get("real_funds_affected") is not False
            or _money(refund.get("amount"), "模拟入账金额") != current["amount"]
        ):
            raise SideEffectOutcomeUnknown(
                "business-mock 已返回写入，但结果格式无法核实",
            )

        return AgentActionRecord(
            action_id=stable_confirmation_id,
            action_type=ActionType.PRICE_MATCH,
            agent_type="ai",
            target_type="order",
            target_id=current["order_no"],
            data={
                "before_state": {
                    key: before.get(key)
                    for key in (
                        "order_no", "status", "payment_status", "paid_at",
                        "currency", "product_no", "original_price",
                        "current_price", "amount", "window_days",
                    )
                },
                "after_state": dict(after),
                "refund": refund,
                "wallet": wallet,
                "execution_mode": "business_mock",
                "real_funds_affected": False,
            },
            status="executed",
            executed_at=datetime.now(timezone.utc),
        )


def get_price_match_service() -> PriceMatchService:
    return PriceMatchService()
