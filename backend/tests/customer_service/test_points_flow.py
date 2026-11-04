"""cs-capability-gap B2：积分/等级查询全链路（http 模式，走真实 Mock 契约）。

覆盖（FINAL-PLAN §八 W1 三件套纪律）：
1. 路由：coarse 域判定 + fine 意图命中，钉住 golden T-015（「会员积分怎么用」
   仍为 KNOWLEDGE 政策问句）与出域豁免词表；
2. Mock 契约：懒种子数字与 points-policy.md / ai.cs_faq id=8/16 一致
   （余额 8000、黄金档 spend_90d∈[2000,8000)、两笔 earn 流水）；
3. Service：http 模式走 mock + 等级文案归一（tier_label/tier_note）；
4. facts 三件套：QueryExpert 导出 service_facts、FactSet 投影白名单、
   composer 守卫放行/拒绝（积分换算元金额 fail-closed）；
5. sandbox 直查：隔离 PG 按真实迁移（003 基表 + 132 积分）验证
   _points_local 账本净和与等级档案（env 守卫，无隔离库自动跳过）。
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import psycopg2
import pytest
from sqlalchemy.engine import make_url

from backend.customer_service.experts.query import execute_query
from backend.customer_service.service.points_service import PointsService
from backend.mock.business_service import main as business_mock

_TOKEN = "mock-internal-test-token"
_HEADERS = {"X-Internal-Token": _TOKEN}


@pytest.fixture
def business_client(monkeypatch):
    business_mock.reset_mock_state()
    monkeypatch.setattr(business_mock, "_INTERNAL_TOKEN", _TOKEN)
    from fastapi.testclient import TestClient

    with TestClient(business_mock.app) as client:
        yield client
    business_mock.reset_mock_state()


@pytest.fixture
def http_wire(business_client, monkeypatch):
    """http 网关模式 + 业务适配器走真实 Mock 路由（仿 test_order_cancel_flow）。"""
    from backend.config import customer_service as cfg
    from backend.infra.http import business_client as http_client
    from backend.infra.http.business_client import BusinessServiceError

    def request_json_sync(method, path, *, params=None, json_body=None):
        response = business_client.request(
            method, path, params=params, json=json_body, headers=_HEADERS,
        )
        if not 200 <= response.status_code < 300:
            raise BusinessServiceError(
                f"business-service {method} {path} -> {response.status_code}",
                status_code=response.status_code,
            )
        return response.json()

    monkeypatch.setattr(cfg, "CS_BUSINESS_GATEWAY_MODE", "http")
    monkeypatch.setattr(http_client, "request_json_sync", request_json_sync)
    monkeypatch.setattr(http_client, "get_json_sync", lambda path, params=None: request_json_sync("GET", path, params=params))
    monkeypatch.setattr(http_client, "post_json_sync", lambda path, json_body: request_json_sync("POST", path, json_body=json_body))
    return business_client


# ── 1. 路由与词表 ────────────────────────────────────────────────


def test_points_intent_routing_pins_policy_question_to_knowledge():
    """「我的积分有多少」走 TRANSACTION 积分查询；「会员积分怎么用？」
    是政策问句仍归 KNOWLEDGE（golden T-015 口径，词表扩圈不得翻域）。"""
    from backend.customer_service.router.coarse_router import CSCoarseRouter
    from backend.customer_service.router.fine_router import CSFineRouter
    from backend.customer_service.router.types import CSDomain

    domain, _, _ = CSCoarseRouter().classify("我的积分有多少")
    assert domain == CSDomain.TRANSACTION
    intent, conf, _ = CSFineRouter().classify("我的积分有多少", CSDomain.TRANSACTION)
    assert intent == "t_points_query"
    assert conf > 0

    policy_domain, _, _ = CSCoarseRouter().classify("会员积分怎么用？")
    assert policy_domain == CSDomain.KNOWLEDGE


def test_points_words_exempt_from_out_of_scope():
    from backend.customer_service.vocab import match_cs_signal_exempt

    assert match_cs_signal_exempt("我的等级权益有哪些")
    assert match_cs_signal_exempt("积分明细帮我看看")


# ── 2/3/4. Mock 契约 + Service http 模式 ─────────────────────────


def test_mock_points_lazy_seed_matches_policy(http_wire):
    """懒种子数字必须与 points-policy.md / FAQ id=8/16 一致（互为事实源）。"""
    data = http_wire.get(
        "/business/points",
        params={"user_id": "customer-points", "tenant_id": "tenant-a"},
        headers=_HEADERS,
    ).json()["data"]["points"]
    assert data["points"] == 8000
    assert data["tier"] == "gold"
    # FAQ id=8：黄金档 = 近 90 天实付 ≥2000 且 <8000 元
    assert 2000 <= data["spend_90d"] < 8000
    ledger = data["ledger"]
    assert len(ledger) == 2
    assert all(entry["kind"] == "earn" for entry in ledger)
    assert sum(entry["points"] for entry in ledger) == 8000
    assert ledger[0]["balance_after"] == 8000


def test_points_service_http_mode_uses_mock_contract(http_wire):
    profile = PointsService().get_points("customer-points", "tenant-a")
    assert profile["points"] == 8000
    assert profile["tier"] == "gold"
    # http 模式由 Service 归一级别文案（mock 只回数据面 tier 码）
    assert profile["tier_label"] == "黄金会员"
    assert profile["tier_note"] == "积分获取 1.5 倍、生日礼包"
    assert profile["ledger"][0]["memo"] == "订单完成奖励"


# ── 4. facts 三件套（W1 纪律） ──────────────────────────────────


class TestQueryExpertPointsFacts:
    @patch("backend.customer_service.security.permission.PermissionChecker.validate_user_identity", return_value="872")
    @patch("backend.customer_service.experts.query._llm_decompose_intents", return_value=None)
    @patch("backend.customer_service.service.points_service.PointsService")
    def test_points_query_exports_points_facts(
        self, points_service, _decompose, _identity,
    ):
        """积分/等级/最近流水进入 service_facts（composer 唯一事实源）。"""
        points_service.return_value.get_points.return_value = {
            "points": 8000, "tier": "gold", "tier_label": "黄金会员",
            "tier_note": "积分获取 1.5 倍、生日礼包",
            "ledger": [
                {"direction": "in", "points": 200, "memo": "订单完成奖励"},
            ],
        }

        result = execute_query(
            "我的积分有多少",
            {"intent": "t_points_query", "route_path": "business_query"},
            {"user_id": "872", "tenant_id": "default", "session_id": "s1"},
        )

        assert "8000" in result["response_draft"]
        assert "黄金会员" in result["response_draft"]
        assert result["data"]["service_facts"]["points"] == {
            "points": 8000, "tier": "gold", "tier_label": "黄金会员",
            "tier_note": "积分获取 1.5 倍、生日礼包",
            "recent_direction": "in", "recent_points": 200,
            "recent_memo": "订单完成奖励",
        }

    @patch("backend.customer_service.security.permission.PermissionChecker.validate_user_identity", return_value="872")
    @patch("backend.customer_service.experts.query._llm_decompose_intents", return_value=None)
    @patch("backend.customer_service.service.points_service.PointsService")
    def test_points_query_failure_degrades_politely(
        self, points_service, _decompose, _identity,
    ):
        points_service.return_value.get_points.side_effect = Exception("boom")

        result = execute_query(
            "我的积分有多少",
            {"intent": "t_points_query", "route_path": "business_query"},
            {"user_id": "872", "tenant_id": "default", "session_id": "s1"},
        )

        assert result["response_draft"] == "积分查询服务暂时不可用，请稍后再试。"


def test_points_projection_whitelists_profile():
    from backend.customer_service.response.facts import project_fact_set

    projected = project_fact_set({
        "points": {
            "points": 8000, "tier": "gold", "tier_label": "黄金会员",
            "tier_note": "积分获取 1.5 倍、生日礼包",
            "recent_direction": "in", "recent_points": "200",
            "recent_memo": "订单完成奖励",
            # 非白名单字段必须剥离
            "customer_id": "872", "spend_90d": 3500.0,
            "updated_at": "2026-11-04", "internal_token": "secret",
        },
    })

    assert projected == {
        "points": {
            "points": "8000", "tier": "gold", "tier_label": "黄金会员",
            "tier_note": "积分获取 1.5 倍、生日礼包",
            "recent_direction": "in", "recent_points": "200",
            "recent_memo": "订单完成奖励",
        },
    }


def test_points_projection_drops_unknown_tier():
    """非法 tier 码丢弃（枚举白名单）；tier_label 是自有服务结构化展示
    字段，保留（长度受 _safe_text 约束）。"""
    from backend.customer_service.response.facts import project_fact_set

    projected = project_fact_set({
        "points": {"points": 100, "tier": "diamond", "tier_label": "钻石"},
    })

    assert projected == {"points": {"points": "100", "tier_label": "钻石"}}


def test_points_amount_guard_fact_backed_passes_and_conversion_rejected():
    from backend.customer_service.response import composer

    facts = {"points": {
        "points": "8000", "tier": "gold", "recent_direction": "in",
        "recent_points": "200", "recent_memo": "订单完成奖励",
    }}
    assert composer._guard_fact_consistency(
        "当前积分余额 8000 分，最近一笔入账 200 分。", facts,
    )
    # 无 facts 支撑的积分→元换算 fail-closed（W1 红线：宁可打回模板）
    assert not composer._guard_fact_consistency(
        "积分余额 8000 分，可抵扣 ¥80。", facts,
    )


def _cs_route(intent="t_points_query"):
    return {"intent": intent}


def _state(msg="我的积分有多少"):
    return {"user_message": msg}


def test_points_llm_rewrite_passes_fact_guard(monkeypatch):
    from backend.customer_service.response import composer
    from backend.customer_service.response.contracts import CSRenderedResponse

    monkeypatch.setattr(
        "backend.customer_service.response.composer._compose_with_llm",
        lambda *_a, **_k: (
            CSRenderedResponse(
                answer="您当前积分余额 8000 分，等级黄金会员。",  # 无元金额，直接放行
            ),
            3,
        ),
    )
    text, source = composer.compose_reply(
        "query",
        "当前积分余额：8000 分（黄金会员）。",
        {"data": {"service_facts": {"points": {
            "points": 8000, "tier": "gold", "tier_label": "黄金会员",
            "recent_direction": "in", "recent_points": 200,
            "recent_memo": "订单完成奖励",
        }}}},
        _cs_route(), _state(),
    )
    assert source == "llm"
    assert "8000" in text


def test_points_llm_invented_conversion_falls_back(monkeypatch):
    from backend.customer_service.response import composer
    from backend.customer_service.response.contracts import CSRenderedResponse

    draft = "当前积分余额：8000 分（黄金会员）。"
    monkeypatch.setattr(
        "backend.customer_service.response.composer._compose_with_llm",
        lambda *_a, **_k: (
            CSRenderedResponse(answer="积分余额 8000 分，可抵扣 ¥80。"),
            3,
        ),
    )
    text, source = composer.compose_reply(
        "query", draft,
        {"data": {"service_facts": {"points": {
            "points": 8000, "tier": "gold", "tier_label": "黄金会员",
            "recent_direction": "in", "recent_points": 200,
            "recent_memo": "订单完成奖励",
        }}}},
        _cs_route(), _state(),
    )
    assert source == "llm_fallback"
    assert text == draft  # 回模板初稿，越权换算不落地


# ── 5. sandbox 直查（隔离 PG，真实迁移建表） ──────────────────────

# 与 mall sandbox 同纪律：DSN 优先，其次 PG* 环境变量 + 专属库名；
# 只允许指向名称明确标记为 test / sandbox 的隔离数据库。
_SANDBOX_MIGRATIONS = ("003_agent_business_schema.sql", "132_cs_points.sql")


def _resolve_conn_kwargs() -> dict[str, object] | None:
    dsn = os.getenv("POINTS_SANDBOX_PG_TEST_DSN", "").strip()
    if dsn:
        url = make_url(dsn)
        return {
            "host": url.host,
            "port": url.port,
            "dbname": url.database,
            "user": url.username,
            "password": url.password,
        }
    dbname = os.getenv("POINTS_SANDBOX_PG_DBNAME", "").strip()
    if dbname:
        return {
            "host": os.getenv("PGHOST", "localhost"),
            "port": int(os.getenv("PGPORT", "5432")),
            "dbname": dbname,
            "user": os.getenv("PGUSER", "postgres"),
            "password": os.getenv("PGPASSWORD", ""),
        }
    return None


@pytest.fixture(scope="module")
def points_sandbox_admin():
    conn_kwargs = _resolve_conn_kwargs()
    if conn_kwargs is None:
        pytest.skip(
            "设置 POINTS_SANDBOX_PG_TEST_DSN（或 POINTS_SANDBOX_PG_DBNAME + PG* 环境）"
            "后运行积分 sandbox 隔离库验收",
        )
    admin = psycopg2.connect(**conn_kwargs, connect_timeout=10)
    admin.autocommit = True
    try:
        with admin.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            database_name = str(cursor.fetchone()[0]).lower()
        if not (
            database_name.endswith("_test")
            or database_name.startswith("test_")
            or "sandbox" in database_name
        ):
            pytest.fail(
                "积分 sandbox 验收只允许名称明确标记为 test 或 sandbox 的隔离数据库"
            )

        migrations_dir = Path(__file__).resolve().parents[2] / "sql" / "migrations"
        with admin.cursor() as cursor:
            cursor.execute(
                "SELECT to_regclass('finance.points_ledger') IS NOT NULL"
                " AND to_regclass('customer.tier_profiles') IS NOT NULL",
            )
            points_ready = bool(cursor.fetchone()[0])
        if not points_ready:
            with admin.cursor() as cursor:
                # 003 含非幂等 CREATE INDEX，仅在基表缺失时重放（B0 同纪律）
                cursor.execute(
                    "SELECT to_regclass('\"order\".orders') IS NOT NULL",
                )
                if not bool(cursor.fetchone()[0]):
                    cursor.execute(
                        (migrations_dir / _SANDBOX_MIGRATIONS[0]).read_text(encoding="utf-8"),
                    )
                # 132 全量 IF NOT EXISTS，可重复执行
                cursor.execute(
                    (migrations_dir / _SANDBOX_MIGRATIONS[1]).read_text(encoding="utf-8"),
                )
        yield admin
    finally:
        admin.close()


def test_sandbox_points_local_reads_migration_seed(points_sandbox_admin, monkeypatch):
    """sandbox 直查：账本净和 + 等级档案来自真实迁移（132 幂等种子）。"""
    from backend.config import customer_service as cs_cfg
    from backend.infra import db as infra_db

    test_cfg = _resolve_conn_kwargs()
    monkeypatch.setattr(cs_cfg, "CS_BUSINESS_GATEWAY_MODE", "sandbox")
    monkeypatch.setattr(infra_db, "BUSINESS_DB_CONFIG", dict(test_cfg))
    infra_db.reset_engines()
    try:
        profile = PointsService().get_points("872", "default")
    finally:
        infra_db.reset_engines()

    assert profile["points"] == 8000
    assert profile["tier"] == "gold"
    assert profile["tier_label"] == "黄金会员"
    assert len(profile["ledger"]) == 2
    assert profile["ledger"][0]["memo"] == "订单完成奖励"
    assert profile["ledger"][0]["balance_after"] == 8000
