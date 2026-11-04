"""customer_service/service/coupon_service.py — 优惠券域服务（B3，查询型）

双模式与 points_service 同构（cs-capability-gap B3，FINAL-PLAN §五 模板 A）：
  - sandbox（默认）：直查 agent_business —— 券档案 marketing.coupons +
    核销记录 marketing.coupon_redemptions（迁移 133）
  - http：经 business_client 调 business-mock（契约见
    backend/mock/business_service/contract.md v1.3 优惠券节）

口径：券面额/门槛是元金额（与 B2 积分不同），经 facts 白名单进 composer
守卫；「为什么没优惠」四类原因（未满足门槛/不可叠加/已过期/排除品类）与
coupon-policy.md §6、FAQ id=23 一致。
"""
from __future__ import annotations

from typing import Any


class CouponError(Exception):
    """优惠券域业务错误：查询型，仅承载网关/契约失败语义。"""


def _use_http_gateway() -> bool:
    from backend.config.customer_service import CS_BUSINESS_GATEWAY_MODE

    return CS_BUSINESS_GATEWAY_MODE == "http"


def _unwrap(resp: dict) -> dict:
    code = resp.get("code", 0)
    if code != 0:
        raise CouponError(f"业务网关返回失败 code={code}")
    data = resp.get("data")
    if not isinstance(data, dict):
        raise CouponError("业务网关响应缺少 data 字段")
    return data


def _mock_error(exc: Exception) -> CouponError:
    from backend.infra.http.business_client import BusinessServiceError

    if isinstance(exc, BusinessServiceError):
        text = str(exc)
        if "-> 404" in text:
            return CouponError("订单或优惠券不存在，请核对后再试。")
    return CouponError(str(exc))


_COUPON_KIND_LABELS = {
    "full_reduction": "满减券",
    "discount": "折扣券",
    "no_threshold": "无门槛券",
}

_REASONS = {
    "applicable": "可用",
    "threshold_not_met": "未满足门槛",
    "not_stackable": "不可叠加（一单仅可用一张）",
    "expired": "已过期",
    "excluded_category": "排除商品/品类",
}


def _coupon_view(raw: dict[str, Any]) -> dict[str, Any]:
    """统一两模式券条目结构（金额一律 float 元口径）。

    currency 由本服务归一：演示契约单一币种（CNY），数据面 mock/DB 不落
    该字段——composer 金额守卫按币种比对（元/¥ → CNY），facts 缺币种会
    把带「元」的券面额一律 fail-closed。
    """
    return {
        "currency": "CNY",
        "coupon_code": str(raw.get("coupon_code") or ""),
        "kind": str(raw.get("kind") or "full_reduction"),
        "kind_label": _COUPON_KIND_LABELS.get(
            str(raw.get("kind") or ""), "满减券",
        ),
        "threshold_amount": float(raw.get("threshold_amount") or 0),
        "discount_value": float(raw.get("discount_value") or 0),
        "max_discount": (
            float(raw["max_discount"])
            if raw.get("max_discount") is not None else None
        ),
        "stackable": bool(raw.get("stackable") or False),
        "exclusions": str(raw.get("exclusions") or ""),
        "valid_until": str(raw.get("valid_until") or ""),
        "status": str(raw.get("status") or "available"),
    }


class CouponService:
    """优惠券只读查询 + 用券校验（两模式返回结构一致）。"""

    def get_coupons(self, user_id: str, tenant_id: str) -> list[dict[str, Any]]:
        if _use_http_gateway():
            try:
                from backend.infra.http.business_client import get_json_sync

                data = _unwrap(get_json_sync(
                    "/business/coupons",
                    params={"user_id": str(user_id), "tenant_id": str(tenant_id)},
                ))
                return [_coupon_view(item) for item in data.get("coupons") or []]
            except CouponError:
                raise
            except Exception as exc:
                raise _mock_error(exc) from exc
        return self._coupons_local(user_id)

    def check_coupon(
        self, user_id: str, tenant_id: str, order_no: str,
        coupon_code: str = "",
    ) -> dict[str, Any]:
        if _use_http_gateway():
            try:
                from backend.infra.http.business_client import post_json_sync

                data = _unwrap(post_json_sync("/business/coupons/check", {
                    "user_id": str(user_id),
                    "tenant_id": str(tenant_id),
                    "order_no": str(order_no),
                    "coupon_code": str(coupon_code or ""),
                }))
                return self._check_view(data.get("coupon_check") or {})
            except CouponError:
                raise
            except Exception as exc:
                raise _mock_error(exc) from exc
        return self._check_local(user_id, order_no, coupon_code)

    # ── 展示字段归一（两模式共用） ────────────────────────────────

    def _check_view(self, raw: dict[str, Any]) -> dict[str, Any]:
        coupon = raw.get("coupon")
        return {
            "currency": str(raw.get("currency") or "CNY"),
            "applicable": bool(raw.get("applicable") or False),
            "reason_code": str(raw.get("reason_code") or "unknown"),
            "reason_label": _REASONS.get(
                str(raw.get("reason_code") or ""), "原因未知",
            ),
            "reason_text": str(raw.get("reason_text") or ""),
            "order_no": str(raw.get("order_no") or ""),
            "order_total": float(raw.get("order_total") or 0),
            "coupon": _coupon_view(coupon) if isinstance(coupon, dict) else None,
        }

    # ── sandbox 直查 ─────────────────────────────────────────────

    def _coupons_local(self, user_id: str) -> list[dict[str, Any]]:
        from sqlalchemy import text

        from backend.infra.db import get_business_engine

        sql = """
            SELECT coupon_code, kind, threshold_amount, discount_value,
                   max_discount, stackable, exclusions, valid_until, status
            FROM marketing.coupons
            WHERE customer_id::text = :cid
            ORDER BY id LIMIT 10
        """
        with get_business_engine().connect() as conn:
            rows = conn.execute(text(sql), {"cid": str(user_id)}).mappings().all()
        return [
            _coupon_view({
                **r,
                "valid_until": r["valid_until"].isoformat() if r["valid_until"] else "",
            })
            for r in rows
        ]

    def _check_local(
        self, user_id: str, order_no: str, coupon_code: str,
    ) -> dict[str, Any]:
        from sqlalchemy import text

        from backend.infra.db import get_business_engine

        with get_business_engine().connect() as conn:
            order = conn.execute(text(
                'SELECT order_no, total_amount FROM "order".orders'
                " WHERE order_no = :no AND customer_id::text = :cid",
            ), {"no": order_no, "cid": str(user_id)}).mappings().first()
            if order is None:
                raise CouponError("订单不存在或无权查询。")
            coupon_row = conn.execute(text(
                "SELECT coupon_code, kind, threshold_amount, discount_value,"
                " max_discount, stackable, exclusions, valid_until, status"
                " FROM marketing.coupons WHERE customer_id::text = :cid"
                " AND (:code = '' OR coupon_code = :code)"
                " ORDER BY (status = 'available') DESC, id LIMIT 1",
            ), {"cid": str(user_id), "code": str(coupon_code or "")}).mappings().first()
            if coupon_row is None:
                raise CouponError("优惠券不存在。")
            redeemed = conn.execute(text(
                "SELECT count(*) FROM marketing.coupon_redemptions"
                " WHERE customer_id::text = :cid AND order_no = :no",
            ), {"cid": str(user_id), "no": str(order["order_no"])}).scalar() or 0
        coupon = _coupon_view({
            **coupon_row,
            "valid_until": coupon_row["valid_until"].isoformat()
            if coupon_row["valid_until"] else "",
        })
        total = float(order["total_amount"])
        threshold = float(coupon["threshold_amount"])
        if coupon["status"] == "expired":
            result = {
                "applicable": False, "reason_code": "expired",
                "reason_text": "该优惠券已过有效期，过期不可恢复、不可延期。",
            }
        elif threshold > total:
            result = {
                "applicable": False, "reason_code": "threshold_not_met",
                "reason_text": (
                    f"未满足门槛：该券需满 {threshold:.0f} 元"
                    f"（按商品实付合计、不含运费），当前订单 ¥{total:.2f}。"
                ),
            }
        elif redeemed:
            result = {
                "applicable": False, "reason_code": "not_stackable",
                "reason_text": "该订单已使用过一张优惠券：一单仅可使用一张，不可叠加。",
            }
        else:
            result = {"applicable": True, "reason_code": "applicable",
                      "reason_text": "订单可使用该优惠券。"}
        return self._check_view({
            **result,
            "order_no": str(order["order_no"]),
            "order_total": total,
            "coupon": {
                "coupon_code": coupon["coupon_code"], "kind": coupon["kind"],
                "name": coupon["kind_label"],
                "threshold_amount": coupon["threshold_amount"],
                "discount_value": coupon["discount_value"],
            },
        })
