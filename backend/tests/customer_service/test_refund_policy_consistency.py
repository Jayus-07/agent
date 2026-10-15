"""退款政策口径一致性回归（作品集改造 T1，2026-10-14）。

此前：候选列表用「RAG 正则解析的窗口天数」，资格判断完全不看窗口，
于是「列表里没有、直接报订单号却能过」。现在两处统一读
customer_service/policy/refund_policy.get_refund_policy()。

覆盖：同源 / 配置改动两处同步 / RAG 不参与 / 非法配置 fail-closed /
下单时间缺失 fail-closed / 状态集合同源 / 政策版本进入审计。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from backend.customer_service.errors import (
    DatabaseError,
    OrderNotEligibleError,
    RefundPolicyUnavailableError,
)
from backend.customer_service.policy.refund_policy import (
    RefundPolicy,
    get_refund_policy,
    parse_order_created_at,
)
from backend.customer_service.service.refund_service import RefundService

CFG = "backend.config.customer_service"


def _order(age_days: float, status: str = "paid", created_at=None) -> dict:
    return {
        "id": "1",
        "order_no": "ORD-001",
        "total_amount": 100.0,
        "status": status,
        "created_at": created_at
        if created_at is not None
        else (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat(),
    }


@pytest.fixture
def cfg(monkeypatch):
    """便捷改配置：cfg(window=7, statuses='paid', version='v')."""

    def _set(window=None, statuses=None, version=None):
        if window is not None:
            monkeypatch.setattr(f"{CFG}.CS_REFUND_WINDOW_DAYS", str(window))
        if statuses is not None:
            monkeypatch.setattr(f"{CFG}.CS_REFUNDABLE_STATUSES", statuses)
        if version is not None:
            monkeypatch.setattr(f"{CFG}.CS_REFUND_POLICY_VERSION", version)

    return _set


@pytest.fixture
def order_service():
    """候选查询的订单服务 mock，记录传入的政策参数。"""
    svc = MagicMock()
    svc.list_refundable_orders.return_value = []
    with patch(
        "backend.customer_service.service.order_service.get_order_service",
        return_value=svc,
    ):
        yield svc


def _eligibility(order: dict, duplicate: bool = False):
    with patch.object(RefundService, "_get_order", return_value=order), \
         patch.object(RefundService, "_has_existing_refund", return_value=duplicate):
        return RefundService().check_refund_eligibility("u1", "1")


# ── 1. 同源：窗口内外，候选与资格口径一致 ─────────────────────────


class TestSameWindowBothPaths:
    def test_order_outside_window_rejected_directly(self, cfg):
        cfg(window=7)
        with pytest.raises(OrderNotEligibleError, match="超过 7 天"):
            _eligibility(_order(age_days=8))

    def test_order_inside_window_accepted(self, cfg):
        cfg(window=7)
        assert _eligibility(_order(age_days=6)).eligible is True

    def test_candidates_use_same_window_as_eligibility(self, cfg, order_service):
        cfg(window=7)
        bundle = RefundService().list_refund_candidates("u1")
        kwargs = order_service.list_refundable_orders.call_args.kwargs
        assert kwargs["window_days"] == 7
        assert bundle["window_days"] == 7

    def test_changing_config_moves_both_paths(self, cfg, order_service):
        order = _order(age_days=10)
        cfg(window=7)
        with pytest.raises(OrderNotEligibleError):
            _eligibility(order)
        cfg(window=14)
        assert _eligibility(order).eligible is True
        RefundService().list_refund_candidates("u1")
        assert order_service.list_refundable_orders.call_args.kwargs["window_days"] == 14

    def test_window_boundary(self):
        policy = RefundPolicy("v", 7, frozenset({"paid"}))
        now = datetime(2026, 10, 14, tzinfo=timezone.utc)
        assert policy.is_within_window(now - timedelta(days=7), now) is True
        assert policy.is_within_window(now - timedelta(days=7, seconds=1), now) is False


# ── 2. 可退状态同源 ───────────────────────────────────────────────


def test_statuses_come_from_policy_for_both_paths(cfg, order_service):
    cfg(statuses="paid")
    with pytest.raises(OrderNotEligibleError, match="不允许退款"):
        _eligibility(_order(age_days=1, status="shipped"))
    RefundService().list_refund_candidates("u1")
    assert order_service.list_refundable_orders.call_args.kwargs["statuses"] == ["paid"]


# ── 3. RAG 不参与放行 ─────────────────────────────────────────────


def test_rag_is_never_consulted(cfg, order_service, monkeypatch):
    cfg(window=7)

    def _boom(*a, **k):
        raise AssertionError("退款资格/候选不得再查询 RAG")

    monkeypatch.setattr("backend.rag.pipeline.get_rag_pipeline", _boom)
    assert _eligibility(_order(age_days=1)).eligible is True
    RefundService().list_refund_candidates("u1")


# ── 4. 非法配置 fail-closed ───────────────────────────────────────


class TestPolicyUnavailableFailsClosed:
    @pytest.mark.parametrize("bad", ["abc", "", "0", "-3", "9999", "7.5"])
    def test_bad_window_rejected(self, cfg, bad):
        cfg(window=bad)
        with pytest.raises(RefundPolicyUnavailableError):
            get_refund_policy()

    def test_empty_statuses_rejected(self, cfg):
        cfg(statuses=" , ")
        with pytest.raises(RefundPolicyUnavailableError):
            get_refund_policy()

    def test_missing_version_rejected(self, cfg, monkeypatch):
        monkeypatch.setattr(f"{CFG}.CS_REFUND_POLICY_VERSION", "")
        with pytest.raises(RefundPolicyUnavailableError):
            get_refund_policy()

    def test_eligibility_blocked_before_any_order_lookup(self, cfg):
        cfg(window="oops")
        with patch.object(RefundService, "_get_order") as get_order:
            with pytest.raises(RefundPolicyUnavailableError):
                RefundService().check_refund_eligibility("u1", "1")
        get_order.assert_not_called()

    def test_candidates_blocked(self, cfg, order_service):
        cfg(window="oops")
        with pytest.raises(RefundPolicyUnavailableError):
            RefundService().list_refund_candidates("u1")
        order_service.list_refundable_orders.assert_not_called()

    def test_reuses_existing_unavailable_handling(self):
        """动作专家已有 except DatabaseError 降级话术；政策不可用走同一条。"""
        assert issubclass(RefundPolicyUnavailableError, DatabaseError)
        assert RefundPolicyUnavailableError().code == "REFUND_POLICY_UNAVAILABLE"


# ── 5. 下单时间缺失 / 无法解析 fail-closed ─────────────────────────


@pytest.mark.parametrize("bad", [None, "", "not-a-date", 12345])
def test_missing_or_garbage_created_at_rejected(cfg, bad):
    cfg(window=30)
    order = _order(age_days=1)
    order["created_at"] = bad
    with pytest.raises(OrderNotEligibleError, match="下单时间"):
        _eligibility(order)


class TestParseCreatedAt:
    def test_iso_with_z(self):
        assert parse_order_created_at("2026-10-01T00:00:00Z").tzinfo is not None

    def test_date_only_assumed_utc(self):
        dt = parse_order_created_at("2026-10-01")
        assert dt.tzinfo == timezone.utc

    def test_datetime_passthrough_naive_to_utc(self):
        assert parse_order_created_at(datetime(2026, 10, 1)).tzinfo == timezone.utc

    @pytest.mark.parametrize("bad", [None, "", "  ", "x", 3])
    def test_unparseable_is_none(self, bad):
        assert parse_order_created_at(bad) is None


# ── 6. 政策版本进入审计 ───────────────────────────────────────────


def test_policy_version_in_eligibility_and_proposal(cfg):
    cfg(version="2026-10-14.test")
    with patch.object(RefundService, "_get_order", return_value=_order(age_days=1)), \
         patch.object(RefundService, "_has_existing_refund", return_value=False):
        eligibility = RefundService().check_refund_eligibility("u1", "1")
        proposal = RefundService().build_refund_proposal("u1", "1")
    assert eligibility.policy_version == "2026-10-14.test"
    assert proposal.before_state["policy_version"] == "2026-10-14.test"


# ── 7. 重复退款闸门仍在窗口之后生效 ───────────────────────────────


def test_duplicate_still_rejected_inside_window(cfg):
    cfg(window=30)
    with pytest.raises(OrderNotEligibleError, match="已存在退款记录"):
        _eligibility(_order(age_days=1), duplicate=True)
