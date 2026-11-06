"""客服物流催办服务（B5）。

只允许向明确配置的 business-mock HTTP 端点登记；sandbox 或真实业务 URL
均 fail-closed。接口失败不会伪报登记成功，结果未知时由现有人工队列核查。
"""
from __future__ import annotations

from urllib.parse import urlsplit

from backend.infra.http.business_client import BusinessServiceError


class UrgeServiceError(Exception):
    """催办失败的用户安全语义。"""

    def __init__(
        self,
        user_message: str,
        *,
        handoff_required: bool = False,
        handoff_reason: str = "",
    ) -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.handoff_required = handoff_required
        self.handoff_reason = handoff_reason


def _is_business_mock_enabled() -> bool:
    """只对白名单本机/Compose business-mock 地址开放写入。"""
    from backend.config.customer_service import CS_BUSINESS_GATEWAY_MODE
    from backend.config.messaging import BUSINESS_SERVICE_URL

    if CS_BUSINESS_GATEWAY_MODE != "http":
        return False
    try:
        parsed = urlsplit(BUSINESS_SERVICE_URL)
        return (
            parsed.scheme == "http"
            and parsed.hostname in {"business-mock", "127.0.0.1", "localhost"}
            and parsed.port == 8081
            and parsed.path in ("", "/")
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        return False


def _unwrap(response: dict) -> dict:
    if response.get("code") != 0:
        raise UrgeServiceError(
            "催办服务未能确认登记结果，正在转人工核查。",
            handoff_required=True,
            handoff_reason="催办接口返回异常，登记结果需要人工核对",
        )
    data = response.get("data")
    if not isinstance(data, dict):
        raise UrgeServiceError(
            "催办服务响应异常，登记结果需要人工核对。",
            handoff_required=True,
            handoff_reason="催办接口响应格式异常，登记结果需要人工核对",
        )
    return data


class UrgeService:
    """向状态化 business-mock 登记发货/配送催办。"""

    def register_urge(
        self,
        *,
        user_id: str,
        tenant_id: str,
        conversation_id: str,
        order_no: str,
        request_type: str,
    ) -> dict:
        from backend.customer_service.errors import ValidationError
        from backend.customer_service.security.permission import PermissionChecker

        try:
            PermissionChecker.validate_order_id(order_no)
        except ValidationError as exc:
            raise UrgeServiceError(exc.user_message) from exc
        if request_type not in {"shipping", "delivery"}:
            raise UrgeServiceError("催办类型不受支持，本次没有登记。")
        if not _is_business_mock_enabled():
            raise UrgeServiceError(
                "催办登记仅在 business-mock 演示环境开放；"
                "当前没有发起任何申请，可回复「转人工」由客服协助。",
            )

        from backend.infra.http.business_client import post_json_sync

        try:
            response = post_json_sync(
                "/business/urge-requests",
                {
                    "user_id": str(user_id),
                    "tenant_id": str(tenant_id),
                    "conversation_id": str(conversation_id),
                    "order_no": str(order_no),
                    "request_type": request_type,
                },
            )
            data = _unwrap(response)
        except UrgeServiceError:
            raise
        except BusinessServiceError as exc:
            if exc.status_code == 404:
                raise UrgeServiceError(
                    "没有找到这笔订单，请核对订单号后再试；本次没有登记催办。",
                ) from exc
            if exc.status_code == 409:
                raise UrgeServiceError(
                    "当前订单状态不支持该类型催办，本次没有登记申请。",
                ) from exc
            if exc.status_code == 422:
                raise UrgeServiceError(
                    "催办信息不完整或格式不正确，本次没有登记申请。",
                ) from exc
            raise UrgeServiceError(
                "暂时无法确认催办是否登记，正在转人工核查，请勿重复提交。",
                handoff_required=True,
                handoff_reason="催办接口结果未知，需人工核对后续状态",
            ) from exc
        except Exception as exc:
            raise UrgeServiceError(
                "暂时无法确认催办是否登记，正在转人工核查，请勿重复提交。",
                handoff_required=True,
                handoff_reason="催办接口调用异常，需人工核对后续状态",
            ) from exc

        if data.get("handoff_required") is True:
            return {
                "handoff_required": True,
                "handoff_reason": str(data.get("handoff_reason") or "物流状态异常"),
                "logistics": data.get("logistics") or {},
                "stale_days": data.get("stale_days"),
            }

        request = data.get("urge_request")
        logistics = data.get("logistics")
        if not isinstance(request, dict) or not request.get("request_id"):
            raise UrgeServiceError(
                "催办服务未返回登记编号，正在转人工核查。",
                handoff_required=True,
                handoff_reason="催办接口未返回登记编号，需人工核对",
            )
        if not isinstance(logistics, dict) or not logistics.get("status_display"):
            raise UrgeServiceError(
                "催办服务未返回当前物流状态，正在转人工核查。",
                handoff_required=True,
                handoff_reason="催办接口缺少物流状态，需人工核对",
            )
        return {
            "urge_request": request,
            "already_registered": bool(data.get("already_registered")),
            "logistics": logistics,
            "handoff_required": False,
        }


_service = UrgeService()


def get_urge_service() -> UrgeService:
    return _service
