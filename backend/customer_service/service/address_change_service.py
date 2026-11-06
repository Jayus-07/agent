"""B7 在途订单改址：用户确认后只在 business-mock 留存模拟请求。"""
from __future__ import annotations

import re
import unicodedata
import uuid
from urllib.parse import quote, urlsplit

from backend.customer_service.action import ActionProposal, ActionType, AgentActionRecord
from backend.customer_service.errors import (
    AddressChangeUnavailableError,
    DatabaseError,
    OrderNotEligibleError,
    OrderNotFoundError,
)
from backend.customer_service.risk import RiskLevel

_ADDRESS_MARKERS = re.compile(r"省|市|区|县|镇|乡|街道|街|路|弄|号|小区|楼|栋|室")
_QUESTION_MARKERS = re.compile(r"吗|可以|能不能|怎么|如何|政策|规则|时效|多久|为什么")
_ADDRESS_PATTERNS = (
    re.compile(r"(?:新地址|收货地址)(?:是|为|改到|改成|[:：])\s*(.{6,255})$"),
    re.compile(r"(?:改到|换到|寄到|送到|改为|改成)\s*(.{6,255})$"),
)


def normalize_new_address(value: object) -> str:
    """校验用户明确提供的地址；不接受空值、问句或不完整片段。"""
    address = unicodedata.normalize("NFKC", str(value or "")).strip()
    address = "".join(
        char for char in address
        if char not in {"\u200b", "\ufeff"} and ord(char) >= 32
    )
    if (
        len(address) < 6
        or len(address) > 255
        or not _ADDRESS_MARKERS.search(address)
        or _QUESTION_MARKERS.search(address)
    ):
        raise OrderNotEligibleError("请提供完整的新收货地址，当前没有提交改址请求")
    return address


def extract_new_address(cs_route: dict | None, user_message: str) -> str:
    """从已抽取槽或当前轮显式文本中提取地址；无法确认时返回空串。"""
    metadata = (cs_route or {}).get("metadata") or {}
    candidates = [metadata.get("new_address")]
    raw = str(user_message or "").strip()
    for pattern in _ADDRESS_PATTERNS:
        match = pattern.search(raw)
        if match:
            candidates.append(match.group(1))
    candidates.append(raw)

    for candidate in candidates:
        if not candidate:
            continue
        text = re.split(r"[，。；;！!？?\n]", str(candidate), maxsplit=1)[0].strip()
        try:
            return normalize_new_address(text)
        except OrderNotEligibleError:
            continue
    return ""


class AddressChangeService:
    """确认后登记在途订单的 business-mock 模拟改址请求。"""

    def _ensure_mock_mode(self) -> None:
        from backend.config.customer_service import (
            CS_ADDRESS_CHANGE_MODE,
            CS_BUSINESS_GATEWAY_MODE,
        )

        if CS_ADDRESS_CHANGE_MODE != "mock":
            raise AddressChangeUnavailableError("订单改址模拟通道未显式开放")
        if CS_BUSINESS_GATEWAY_MODE != "http":
            raise AddressChangeUnavailableError(
                "订单改址只允许访问 business-mock HTTP 网关",
            )
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
            raise AddressChangeUnavailableError(
                "订单改址目标不是受允许的 business-mock",
            ) from None

    def _facts(self, user_id: str, order_id: str) -> dict:
        from backend.customer_service.service.order_service import get_order_service

        order_service = get_order_service()
        result = order_service.query_orders(
            user_id=user_id, order_id=order_id, query_type="detail",
        )
        if not result.orders:
            raise OrderNotFoundError()
        order = result.orders[0]
        order_no = str(order.get("order_no") or "").strip()
        if not order_no:
            raise DatabaseError("订单详情缺少订单号，无法安全改址")
        logistics = order_service.query_logistics(user_id, order_no)
        shipping_status = str(logistics.get("shipping_status") or "").lower()
        order_status = str(order.get("status") or "").lower()
        if order_status != "shipped" or shipping_status != "shipped":
            raise OrderNotEligibleError(
                "该订单已不处于可改址的运输途中；没有提交改址请求，请联系人工客服协助",
            )
        return {
            "order_id": str(order.get("id") or order_no),
            "order_no": order_no,
            "order_status": order_status,
            "shipping_status": shipping_status,
        }

    def build_proposal(
        self, user_id: str, order_id: str, new_address: str,
    ) -> ActionProposal:
        self._ensure_mock_mode()
        address = normalize_new_address(new_address)
        facts = self._facts(user_id, order_id)
        proposal_text = (
            f"已核对订单 {facts['order_no']} 当前处于运输途中。\n\n"
            f"**拟改为：** {address}\n\n"
            "改址可能影响配送时效 1-3 天，且每单限 1 次。确认后只会在 "
            "business-mock 演示环境登记模拟请求，"
            "不会修改真实订单地址，也不会通知真实承运方。执行前会再次核对物流状态。"
            "请确认是否继续？"
        )
        return ActionProposal(
            action_type=ActionType.ADDRESS_CHANGE,
            target_type="order",
            target_id=facts["order_no"],
            risk_level=RiskLevel.MEDIUM,
            proposal_text=proposal_text,
            before_state=facts,
            after_state={
                "new_address": address,
                "status": "simulated",
                "execution_mode": "business_mock",
                "real_carrier_notified": False,
                "real_order_address_changed": False,
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
        self._ensure_mock_mode()
        if not user_id or user_id == "anonymous" or not tenant_id or not conversation_id:
            raise AddressChangeUnavailableError(
                "订单改址确认缺少可信用户、租户或会话身份",
            )
        if pending_action.get("action_type") != ActionType.ADDRESS_CHANGE:
            raise OrderNotEligibleError("订单改址动作类型无效，未提交请求")
        try:
            action_id = str(uuid.UUID(str(confirmation_id)))
        except (TypeError, ValueError, AttributeError):
            raise OrderNotEligibleError("订单改址确认标识无效，未提交请求") from None

        before = pending_action.get("before_state")
        after = pending_action.get("after_state")
        order_no = str(pending_action.get("target_id") or "").strip()
        if (
            not isinstance(before, dict)
            or not isinstance(after, dict)
            or pending_action.get("target_type") != "order"
            or not order_no
            or str(before.get("order_no") or "") != order_no
            or after.get("status") != "simulated"
            or after.get("real_carrier_notified") is not False
            or after.get("real_order_address_changed") is not False
        ):
            raise OrderNotEligibleError("订单改址提案信息不完整，未提交请求")
        address = normalize_new_address(after.get("new_address"))
        current = self._facts(user_id, order_no)
        if any(str(before.get(key) or "") != str(current[key]) for key in current):
            raise OrderNotEligibleError(
                "订单或物流状态已变化，改址请求未提交；请重新核对或联系人工客服",
            )

        from backend.customer_service.service.demo_mode import resolve_user_id
        from backend.infra.http.business_client import (
            BusinessServiceError,
            request_json_sync,
        )
        from backend.shared.idempotency import SideEffectOutcomeUnknown

        payload = {
            "operation_key": action_id,
            "user_id": str(resolve_user_id(user_id)),
            "tenant_id": tenant_id,
            "order_no": order_no,
            "expected_shipping_status": current["shipping_status"],
            "new_address": address,
        }
        try:
            response = request_json_sync(
                "POST",
                f"/business/logistics/{quote(order_no, safe='')}/change-address",
                json_body=payload,
            )
        except BusinessServiceError as exc:
            if exc.status_code in (404, 409, 422):
                raise OrderNotEligibleError(
                    "订单或物流状态已变化，改址请求未提交；请重新核对或联系人工客服",
                ) from exc
            raise SideEffectOutcomeUnknown(
                "订单改址请求结果无法确认，已暂停重复提交",
            ) from exc
        except Exception as exc:
            raise SideEffectOutcomeUnknown(
                "订单改址响应异常，结果无法确认，已暂停重复提交",
            ) from exc

        envelope = response.get("data") if isinstance(response, dict) else None
        record = envelope.get("address_change_request") if isinstance(envelope, dict) else None
        if (
            not isinstance(record, dict)
            or str(record.get("order_no") or "") != order_no
            or str(record.get("new_address") or "") != address
            or record.get("status") != "simulated"
            or record.get("real_carrier_notified") is not False
            or record.get("real_order_address_changed") is not False
        ):
            raise SideEffectOutcomeUnknown(
                "business-mock 已返回写入，但改址结果无法核验",
            )

        return AgentActionRecord(
            action_id=action_id,
            action_type=ActionType.ADDRESS_CHANGE,
            agent_type="ai",
            target_type="order",
            target_id=order_no,
            data={
                "before_state": dict(before),
                "after_state": {
                    **dict(after),
                    "request_id": str(record.get("request_id") or ""),
                    "request_status": str(record.get("status") or ""),
                },
                "address_change_request": record,
                "execution_mode": "business_mock",
                "real_carrier_notified": False,
                "real_order_address_changed": False,
            },
            status="simulated",
        )


def get_address_change_service() -> AddressChangeService:
    return AddressChangeService()
