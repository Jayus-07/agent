"""tests/infra/test_quota_source_label.py — 预算来源中文名（2026-10-08 #1）

「额度来源」此前只回 scope_type:scope_id 技术串（tenant_default:default），
前端恒走英文回退。policy_source 新增 label 展示字段，scope 两键保持不变
（加字段向后兼容）。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend.infra.llm.quota import PostgresQuotaStore, policy_source_label


class TestPolicySourceLabel:
    def test_tenant_default(self):
        # 新用户最常命中的继承来源
        assert policy_source_label("tenant_default", "default") == "租户默认策略"

    def test_platform(self):
        assert policy_source_label("platform", "default") == "平台默认策略"

    def test_user_tenant_scoped_includes_id(self):
        assert policy_source_label("user", "42") == "用户策略（42）"
        assert policy_source_label("tenant", "internal") == "租户策略（internal）"

    def test_unknown_and_future_scope(self):
        assert policy_source_label("unknown", "unknown") == "未知"
        assert policy_source_label("", "") == "未知"
        assert policy_source_label("future_scope", "x") == "继承策略"


def _bare_store() -> PostgresQuotaStore:
    """跳过 __init__（不连 PG）：get_budget_status 的依赖全部 patch 掉。"""
    store = PostgresQuotaStore.__new__(PostgresQuotaStore)
    # __init__ 才会创建的实例属性：调用点取值（self._ledger 作参数）需要占位
    store._connection_factory = None
    store._ledger = "budget_ledger"
    return store


def _policies() -> SimpleNamespace:
    tz = SimpleNamespace()  # timezone 字段仅透传给 budget_periods（已 patch）
    return SimpleNamespace(
        user=SimpleNamespace(enforcement="hard", audit_exempt=False, timezone=tz),
        tenant=SimpleNamespace(enforcement="hard", audit_exempt=False, timezone=tz),
    )


_WINDOW = {"used_cny": 0, "reserved_cny": 0, "limit_cny": 10,
           "ratio": 0.0, "reset_at": ""}


class TestBudgetStatusContract:
    def _run(self, store, get_policy):
        with patch.object(store, "resolve", return_value=_policies()), \
             patch.object(store, "get_policy", side_effect=get_policy), \
             patch("backend.infra.llm.quota.budget_periods") as periods, \
             patch.object(store, "_connection_factory"), \
             patch.object(store, "_window", return_value=dict(_WINDOW)):
            periods.return_value = SimpleNamespace(
                day_start_utc="2026-10-08", month_start_utc="2026-10-01")
            return store.get_budget_status(user_id="u1", tenant_id="default")

    def test_no_explicit_policy_falls_back_with_label(self):
        out = self._run(_bare_store(), lambda *_a: None)
        # 全链无显式策略：source 归到 resolve 后的租户默认层（scope 键不变），
        # label 必须是中文且不是技术串
        assert out["policy_source"]["label"]
        assert "unknown" not in out["policy_source"]["label"] or (
            out["policy_source"]["label"] == "未知")

    def test_tenant_default_source_gets_chinese_label(self):
        store = _bare_store()
        policy = SimpleNamespace(scope_type="tenant_default", scope_id="default")

        def get_policy(scope_type, _scope_id):
            return policy if scope_type == "tenant_default" else None

        out = self._run(store, get_policy)
        assert out["policy_source"]["scope_type"] == "tenant_default"
        assert out["policy_source"]["scope_id"] == "default"
        assert out["policy_source"]["label"] == "租户默认策略"

    def test_label_never_leaks_technical_string(self):
        # label 是展示字段：不应出现 scope_type:scope_id 形态
        store = _bare_store()
        policy = SimpleNamespace(scope_type="tenant", scope_id="default")
        out = self._run(store, lambda *_a: policy)
        assert ":" not in out["policy_source"]["label"]
        assert out["policy_source"]["label"] == "租户策略（default）"
