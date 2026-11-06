"""B8 发票查询与模拟申请；不连接真实税务或开票渠道。"""
from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import urlsplit

from backend.customer_service.action import ActionProposal, AgentActionRecord
from backend.customer_service.errors import (
    DatabaseError,
    OrderNotEligibleError,
    OrderNotFoundError,
)
from backend.customer_service.risk import RiskLevel

_ELIGIBLE_ORDER_STATUSES = frozenset({"paid", "shipped", "completed"})
_INVOICE_TYPE = "electronic"


def _use_http_gateway() -> bool:
    from backend.config.customer_service import CS_BUSINESS_GATEWAY_MODE

    return CS_BUSINESS_GATEWAY_MODE == "http"


def _ensure_business_mock_endpoint() -> None:
    """拒绝把发票数据或写请求发送到非本地 business-mock 地址。"""
    try:
        from backend.config.messaging import BUSINESS_SERVICE_URL

        endpoint = urlsplit(str(BUSINESS_SERVICE_URL).strip())
        allowed = {
            ("business-mock", 8081),
            ("127.0.0.1", 8081),
            ("localhost", 8081),
        }
        allowlisted = (
            endpoint.scheme == "http"
            and endpoint.hostname is not None
            and (endpoint.hostname.lower(), endpoint.port) in allowed
            and not endpoint.username
            and not endpoint.password
            and not endpoint.query
            and not endpoint.fragment
            and endpoint.path in ("", "/")
        )
    except (TypeError, ValueError):
        allowlisted = False
    if not allowlisted:
        raise DatabaseError(
            "发票服务目标不是受允许的 business-mock",
            user_message="当前开票模拟服务未正确配置，请联系人工客服协助。",
        )


def _ensure_mock_mode() -> None:
    from backend.config import customer_service as cfg

    if str(getattr(cfg, "CS_INVOICE_MODE", "disabled")).lower() != "mock":
        raise OrderNotEligibleError(
            "当前部署未开放模拟开票申请，请联系人工客服协助。",
        )
    if not _use_http_gateway():
        raise OrderNotEligibleError(
            "开票申请仅允许写入 business-mock，请联系人工客服协助。",
        )
    _ensure_business_mock_endpoint()


def _unwrap(response: object) -> dict[str, Any]:
    if not isinstance(response, dict) or response.get("code", 0) != 0:
        raise DatabaseError("开票网关返回失败")
    data = response.get("data")
    if not isinstance(data, dict):
        raise DatabaseError("开票网关响应缺少 data")
    return data


def _map_gateway_error(exc: Exception) -> Exception:
    from backend.infra.http.business_client import BusinessServiceError

    if isinstance(exc, BusinessServiceError):
        if exc.status_code == 404:
            return OrderNotFoundError("订单或发票抬头不存在")
        if exc.status_code in (400, 409, 422):
            return OrderNotEligibleError(
                "订单状态或发票信息已变化，本次模拟申请未登记；请重新核对。",
            )
    return DatabaseError(
        "开票业务服务暂不可用",
        user_message="开票服务暂时不可用，请稍后重试或联系人工客服。",
    )


def _title_view(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "title_id": str(raw.get("title_id") or raw.get("id") or ""),
        "title_name": str(raw.get("title_name") or "")[:120],
        "title_type": str(raw.get("title_type") or "unknown"),
        "is_default": bool(raw.get("is_default") or False),
        "status": str(raw.get("status") or "active"),
    }


def _invoice_view(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "invoice_id": str(raw.get("invoice_id") or raw.get("id") or ""),
        "invoice_no": str(raw.get("invoice_no") or ""),
        "order_no": str(raw.get("order_no") or ""),
        "title_id": str(raw.get("title_id") or raw.get("invoice_title_id") or ""),
        "title_name": str(raw.get("title_name") or "")[:120],
        "title_type": str(raw.get("title_type") or "unknown"),
        "invoice_type": str(raw.get("invoice_type") or _INVOICE_TYPE),
        "status": str(raw.get("status") or "unknown"),
        "real_invoice_issued": raw.get("real_invoice_issued") is True,
        "created_at": str(raw.get("created_at") or "")[:40],
    }


class InvoiceService:
    """发票抬头/模拟记录双模式读取，申请写入只走 business-mock。"""

    def get_titles(self, user_id: str, tenant_id: str) -> list[dict[str, Any]]:
        if _use_http_gateway():
            try:
                _ensure_business_mock_endpoint()
                from backend.infra.http.business_client import get_json_sync

                data = _unwrap(get_json_sync(
                    "/business/invoice-titles",
                    params={"user_id": str(user_id), "tenant_id": str(tenant_id)},
                ))
                return [
                    _title_view(item)
                    for item in data.get("invoice_titles") or []
                    if isinstance(item, dict)
                ]
            except (DatabaseError, OrderNotFoundError, OrderNotEligibleError):
                raise
            except Exception as exc:
                raise _map_gateway_error(exc) from exc
        return self._titles_local(user_id, tenant_id)

    def get_invoices(
        self, user_id: str, tenant_id: str, order_no: str = "",
    ) -> list[dict[str, Any]]:
        if _use_http_gateway():
            try:
                _ensure_business_mock_endpoint()
                from backend.infra.http.business_client import get_json_sync

                params = {"user_id": str(user_id), "tenant_id": str(tenant_id)}
                if order_no:
                    params["order_no"] = str(order_no)
                data = _unwrap(get_json_sync("/business/invoices", params=params))
                return [
                    _invoice_view(item)
                    for item in data.get("invoices") or []
                    if isinstance(item, dict)
                ]
            except (DatabaseError, OrderNotFoundError, OrderNotEligibleError):
                raise
            except Exception as exc:
                raise _map_gateway_error(exc) from exc
        return self._invoices_local(user_id, tenant_id, order_no)

    def _titles_local(
        self, user_id: str, tenant_id: str,
    ) -> list[dict[str, Any]]:
        from sqlalchemy import text

        from backend.infra.db import get_business_engine

        sql = """
            SELECT id AS title_id, title_name, title_type, is_default, status
            FROM customer.invoice_titles
            WHERE customer_id::text = :customer_id
              AND tenant_id = :tenant_id AND status = 'active'
            ORDER BY is_default DESC, id LIMIT 20
        """
        try:
            with get_business_engine().connect() as conn:
                rows = conn.execute(text(sql), {
                    "customer_id": str(user_id), "tenant_id": str(tenant_id),
                }).mappings().all()
        except Exception as exc:
            raise DatabaseError(
                "发票抬头查询失败",
                user_message="发票抬头暂时无法查询，请稍后重试。",
            ) from exc
        return [_title_view(dict(row)) for row in rows]

    def _invoices_local(
        self, user_id: str, tenant_id: str, order_no: str,
    ) -> list[dict[str, Any]]:
        from sqlalchemy import text

        from backend.infra.db import get_business_engine

        sql = """
            SELECT i.id AS invoice_id, i.invoice_no, i.order_no,
                   i.invoice_title_id AS title_id, t.title_name, t.title_type,
                   i.invoice_type, i.status, i.real_invoice_issued,
                   i.created_at
            FROM finance.invoices AS i
            JOIN customer.invoice_titles AS t
              ON t.id = i.invoice_title_id
             AND t.customer_id = i.customer_id AND t.tenant_id = i.tenant_id
            WHERE i.customer_id::text = :customer_id
              AND i.tenant_id = :tenant_id
              AND (:order_no = '' OR i.order_no = :order_no)
            ORDER BY i.id DESC LIMIT 20
        """
        try:
            with get_business_engine().connect() as conn:
                rows = conn.execute(text(sql), {
                    "customer_id": str(user_id),
                    "tenant_id": str(tenant_id),
                    "order_no": str(order_no or ""),
                }).mappings().all()
        except Exception as exc:
            raise DatabaseError(
                "模拟开票记录查询失败",
                user_message="开票记录暂时无法查询，请稍后重试。",
            ) from exc
        return [_invoice_view(dict(row)) for row in rows]

    def _get_order(self, user_id: str, order_no: str) -> dict[str, Any]:
        from backend.customer_service.service.order_service import get_order_service

        result = get_order_service().query_orders(
            user_id=user_id, order_id=order_no, query_type="detail",
        )
        order = next(iter(result.orders or []), None)
        if not isinstance(order, dict):
            raise OrderNotFoundError()
        return order

    @staticmethod
    def _select_title(
        titles: list[dict[str, Any]], title_selector: str,
    ) -> dict[str, Any]:
        active = [t for t in titles if t.get("status") == "active"]
        selector = str(title_selector or "").strip()
        if selector:
            matches = [
                t for t in active
                if selector == str(t.get("title_id") or "")
                or str(t.get("title_name") or "") in selector
                or (
                    t.get("title_type") == "company"
                    and any(word in selector for word in ("企业", "公司", "单位"))
                )
                or (
                    t.get("title_type") == "individual"
                    and any(word in selector for word in ("个人", "自然人"))
                )
            ]
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                raise OrderNotEligibleError("发票抬头不唯一，请指定查询结果中的抬头名称")
            raise OrderNotEligibleError("未找到所选发票抬头，请先查询名下抬头")
        defaults = [t for t in active if t.get("is_default")]
        if len(defaults) == 1:
            return defaults[0]
        if not active:
            raise OrderNotEligibleError("当前账户没有可用发票抬头，请先联系人工客服登记")
        raise OrderNotEligibleError("请先查询并明确选择发票抬头，再提交开票申请")

    def build_invoice_proposal(
        self,
        user_id: str,
        tenant_id: str,
        order_no: str,
        title_selector: str = "",
        invoice_type: str = _INVOICE_TYPE,
    ) -> ActionProposal:
        _ensure_mock_mode()
        if invoice_type != _INVOICE_TYPE:
            raise OrderNotEligibleError("当前模拟流程只支持电子开票申请")
        order = self._get_order(user_id, order_no)
        status = str(order.get("status") or "")
        if status not in _ELIGIBLE_ORDER_STATUSES:
            raise OrderNotEligibleError(
                "该订单当前状态不支持开票申请；未提交任何申请。",
            )
        titles = self.get_titles(user_id, tenant_id)
        title = self._select_title(titles, title_selector)
        order_no = str(order.get("order_no") or order_no)
        if any(
            item.get("order_no") == order_no
            and item.get("title_id") == title["title_id"]
            for item in self.get_invoices(user_id, tenant_id, order_no)
        ):
            raise OrderNotEligibleError("该订单已使用此抬头登记过模拟开票申请")
        proposal_text = (
            "## 模拟开票申请\n\n"
            f"**订单号：** `{order_no}`\n"
            f"**发票抬头：** {title['title_name']}\n"
            "**类型：** 电子发票\n\n"
            "确认后只会在 business-mock 登记模拟记录，不会实际开票，"
            "也不会连接税务系统。"
        )
        return ActionProposal(
            action_type="invoice_request",
            target_type="order",
            target_id=order_no,
            risk_level=RiskLevel.MEDIUM,
            proposal_text=proposal_text,
            before_state={"order_status": status, "order_no": order_no},
            after_state={
                "title_id": title["title_id"],
                "title_name": title["title_name"],
                "title_type": title["title_type"],
                "invoice_type": _INVOICE_TYPE,
                "status": "simulated",
                "real_invoice_issued": False,
            },
        )

    def execute_confirmed_invoice(
        self,
        pending_action: dict[str, Any],
        *,
        confirmation_id: str,
        user_id: str,
        tenant_id: str,
        conversation_id: str,
    ) -> AgentActionRecord:
        _ensure_mock_mode()
        if pending_action.get("action_type") != "invoice_request":
            raise OrderNotEligibleError("开票确认动作类型无效")
        if not user_id or user_id == "anonymous" or not tenant_id or not conversation_id:
            raise OrderNotEligibleError("开票申请缺少可信用户、租户或会话身份")
        try:
            stable_confirmation_id = str(uuid.UUID(str(confirmation_id)))
        except (ValueError, TypeError, AttributeError):
            raise OrderNotEligibleError("确认标识无效，未提交开票申请") from None

        before = dict(pending_action.get("before_state") or {})
        after = dict(pending_action.get("after_state") or {})
        order_no = str(pending_action.get("target_id") or before.get("order_no") or "")
        order = self._get_order(user_id, order_no)
        if (
            str(order.get("status") or "") != str(before.get("order_status") or "")
            or str(order.get("status") or "") not in _ELIGIBLE_ORDER_STATUSES
        ):
            raise OrderNotEligibleError("订单状态已变化，模拟开票申请未提交")
        title_id = str(after.get("title_id") or "")
        titles = self.get_titles(user_id, tenant_id)
        title = next(
            (item for item in titles if item.get("title_id") == title_id), None,
        )
        if title is None or title.get("status") != "active":
            raise OrderNotEligibleError("发票抬头已变化，模拟开票申请未提交")
        if any(
            item.get("order_no") == order_no and item.get("title_id") == title_id
            for item in self.get_invoices(user_id, tenant_id, order_no)
        ):
            raise OrderNotEligibleError("该订单已使用此抬头登记过模拟开票申请")

        from backend.customer_service.service.demo_mode import resolve_user_id
        from backend.infra.http.business_client import (
            BusinessServiceError,
            post_json_sync,
        )
        from backend.shared.idempotency import SideEffectOutcomeUnknown

        payload = {
            "operation_key": stable_confirmation_id,
            "user_id": str(resolve_user_id(user_id)),
            "tenant_id": tenant_id,
            "conversation_id": conversation_id,
            "order_no": order_no,
            "expected_order_status": str(order.get("status") or ""),
            "title_id": title_id,
            "invoice_type": _INVOICE_TYPE,
        }
        try:
            response = post_json_sync("/business/invoices", payload)
        except BusinessServiceError as exc:
            if exc.status_code in (400, 404, 409, 422):
                raise _map_gateway_error(exc) from exc
            raise SideEffectOutcomeUnknown(
                "模拟开票请求结果无法确认，已暂停重复提交",
            ) from exc
        except Exception as exc:
            raise SideEffectOutcomeUnknown(
                "模拟开票请求响应异常，结果无法确认，已暂停重复提交",
            ) from exc

        envelope = response.get("data") if isinstance(response, dict) else None
        record = envelope.get("invoice") if isinstance(envelope, dict) else None
        if (
            not isinstance(record, dict)
            or str(record.get("order_no") or "") != order_no
            or str(record.get("title_id") or "") != title_id
            or record.get("status") != "simulated"
            or record.get("real_invoice_issued") is not False
            or record.get("tax_system_contacted") is not False
            or not record.get("invoice_no")
        ):
            raise SideEffectOutcomeUnknown(
                "business-mock 已返回写入，但模拟开票结果无法核验",
            )

        return AgentActionRecord(
            action_id=stable_confirmation_id,
            action_type="invoice_request",
            agent_type="ai",
            target_type="order",
            target_id=order_no,
            data={
                "invoice": _invoice_view(record),
                "execution_mode": "business_mock",
                "real_invoice_issued": False,
                "tax_system_contacted": False,
            },
            status="simulated",
        )


def get_invoice_service() -> InvoiceService:
    return InvoiceService()
