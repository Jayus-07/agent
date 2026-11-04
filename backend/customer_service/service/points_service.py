"""customer_service/service/points_service.py — 会员积分/等级域服务（B2，查询型）

双模式与 mall_service 同构（cs-capability-gap B2，FINAL-PLAN §五 模板 A）：
  - sandbox（默认）：直查 agent_business —— 积分账本 finance.points_ledger
    （余额=流水净和，单一事实源）+ 等级档案 customer.tier_profiles（迁移 132）
  - http：经 business_client 调 business-mock（契约见
    backend/mock/business_service/contract.md v1.2 会员积分与等级节）

口径：等级按近 90 天实付消费滚动评定（points-policy.md §3【业务确认】）；
100 积分抵 1 元（FAQ id=16）——本服务只报账户事实，抵扣换算不在话术内展开
（composer 金额守卫对无事实支撑的元金额 fail-closed）。
"""
from __future__ import annotations

from typing import Any

_TIER_LABELS = {
    "normal": "普通会员",
    "silver": "白银会员",
    "gold": "黄金会员",
    "platinum": "铂金会员",
}
# 权益文案与 points-policy.md §3 权益列一致（定性描述，不带元金额）
_TIER_NOTES = {
    "normal": "基础积分获取",
    "silver": "积分获取 1.2 倍",
    "gold": "积分获取 1.5 倍、生日礼包",
    "platinum": "积分获取 2 倍、专属客服优先接入",
}


class PointsError(Exception):
    """积分域业务错误：查询型，仅承载网关/契约失败语义。"""


def _use_http_gateway() -> bool:
    from backend.config.customer_service import CS_BUSINESS_GATEWAY_MODE

    return CS_BUSINESS_GATEWAY_MODE == "http"


def _unwrap(resp: dict) -> dict:
    code = resp.get("code", 0)
    if code != 0:
        raise PointsError(f"业务网关返回失败 code={code}")
    data = resp.get("data")
    if not isinstance(data, dict):
        raise PointsError("业务网关响应缺少 data 字段")
    return data


def _mock_error(exc: Exception) -> PointsError:
    from backend.infra.http.business_client import BusinessServiceError

    if isinstance(exc, BusinessServiceError):
        text = str(exc)
        if "-> 404" in text:
            return PointsError("用户不存在或无权查询积分。")
    return PointsError(str(exc))


def _with_tier_presentation(profile: dict[str, Any]) -> dict[str, Any]:
    """归一级别展示文案：数据面（mock/DB）只回 tier 码，等级名称与权益
    由本服务按 points-policy.md §3 统一定义，两模式出参结构一致。"""
    tier = str(profile.get("tier") or "normal")
    profile.setdefault("tier_label", _TIER_LABELS.get(tier, "普通会员"))
    profile.setdefault("tier_note", _TIER_NOTES.get(tier, "基础积分获取"))
    return profile


class PointsService:
    """会员积分/等级只读查询（两模式返回结构一致）。"""

    def get_points(self, user_id: str, tenant_id: str) -> dict[str, Any]:
        if _use_http_gateway():
            try:
                from backend.infra.http.business_client import get_json_sync

                data = _unwrap(get_json_sync(
                    "/business/points",
                    params={"user_id": str(user_id), "tenant_id": str(tenant_id)},
                ))
                return _with_tier_presentation(data["points"])
            except PointsError:
                raise
            except Exception as exc:
                raise _mock_error(exc) from exc
        return self._points_local(user_id)

    def _points_local(self, user_id: str) -> dict[str, Any]:
        from sqlalchemy import text

        from backend.infra.db import get_business_engine

        balance_sql = """
            SELECT COALESCE(SUM(
                CASE WHEN direction = 'in' THEN points ELSE -points END
            ), 0) AS balance
            FROM finance.points_ledger WHERE customer_id::text = :cid
        """
        ledger_sql = """
            SELECT direction, points, balance_after, kind, memo, created_at
            FROM finance.points_ledger
            WHERE customer_id::text = :cid
            ORDER BY id DESC LIMIT 5
        """
        tier_sql = """
            SELECT tier, spend_90d, assessed_at, updated_at
            FROM customer.tier_profiles WHERE customer_id::text = :cid
        """
        with get_business_engine().connect() as conn:
            balance_row = conn.execute(
                text(balance_sql), {"cid": str(user_id)},
            ).mappings().first()
            ledger = conn.execute(
                text(ledger_sql), {"cid": str(user_id)},
            ).mappings().all()
            tier_row = conn.execute(
                text(tier_sql), {"cid": str(user_id)},
            ).mappings().first()
        tier = str((tier_row or {}).get("tier") or "normal")
        return {
            "points": int((balance_row or {}).get("balance") or 0),
            "tier": tier,
            "tier_label": _TIER_LABELS.get(tier, "普通会员"),
            "tier_note": _TIER_NOTES.get(tier, "基础积分获取"),
            "spend_90d": float((tier_row or {}).get("spend_90d") or 0),
            "updated_at": (tier_row or {}).get("updated_at"),
            "ledger": [
                {
                    "direction": r["direction"],
                    "points": int(r["points"]),
                    "balance_after": int(r["balance_after"]),
                    "kind": r["kind"],
                    "memo": r["memo"],
                    "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                }
                for r in ledger
            ],
        }
