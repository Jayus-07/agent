"""cs-capability-gap B3：优惠券查询/用券校验全链路（http 模式，走真实 Mock 契约）。

覆盖（复用 B2 查询型样板纪律）：
1. 路由：「我的优惠券有哪些」走 TRANSACTION 券查询；「优惠券怎么领/能不能
   叠加」政策问句仍归 KNOWLEDGE（golden 口径钉住）；cases.jsonl 退款语义的
   「用了优惠券想退了重拍」不被券词翻域；
2. Mock 契约：懒种子数字与 coupon-policy.md / FAQ id=23 一致（满 200 减 30 /
   5 元无门槛 / 9 折最高减 50 / 一张已过期券；stackable 恒 false）；
   check 端点确定性覆盖 expired / threshold_not_met / not_stackable / applicable；
3. Service：http 模式走 mock + 展示字段归一；
4. facts 三件套（W1 纪律）：QueryExpert 导出 coupons/coupon_check、FactSet
   投影白名单、composer coupon/order 金额守卫放行/拒绝；
5. sandbox 直查：隔离 PG 按真实迁移（003 基表 + 133 券）验证
   _coupons_local/_check_local（env 守卫，无隔离库自动跳过）。
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import psycopg2
import pytest
from sqlalchemy.engine import make_url

from backend.customer_service.experts.query import execute_query
from backend.customer_service.service.coupon_service import CouponService
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
    """http 网关模式 + 业务适配器走真实 Mock 路由（仿 test_points_flow）。"""
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


def _orders(client, user_id: str) -> list[dict]:
    return client.get(
        "/business/orders", params={"user_id": user_id}, headers=_HEADERS,
    ).json()["data"]["orders"]


# ── 1. 路由与词表 ────────────────────────────────────────────────


def test_coupon_intent_routing_pins_policy_questions():
    """「我的优惠券有哪些」走 TRANSACTION 券查询；「优惠券怎么领？」
    「优惠券能不能叠加？」是政策问句仍归 KNOWLEDGE（golden 口径不翻域）。"""
    from backend.customer_service.router.coarse_router import CSCoarseRouter
    from backend.customer_service.router.fine_router import CSFineRouter
    from backend.customer_service.router.types import CSDomain

    domain, _, _ = CSCoarseRouter().classify("我的优惠券有哪些")
    assert domain == CSDomain.TRANSACTION
    intent, conf, _ = CSFineRouter().classify("我的优惠券有哪些", CSDomain.TRANSACTION)
    assert intent == "t_coupon_query"
    assert conf > 0

    # 真机实锚（B3 验收 q2）：「订单 X 用券为什么没生效」不带「优惠」前缀，
    # fine 词表缺「用券」会落域默认 t_order_status 答成订单状态
    intent2, _, _ = CSFineRouter().classify(
        "订单 MO-6DEC4021 用券为什么没生效", CSDomain.TRANSACTION,
    )
    assert intent2 == "t_coupon_query"

    for policy_question in (
        "优惠券怎么领？",
        "优惠券能不能叠加？",
        "有没有店铺优惠券可以领",
    ):
        policy_domain, _, _ = CSCoarseRouter().classify(policy_question)
        assert policy_domain == CSDomain.KNOWLEDGE, policy_question


def test_coupon_words_do_not_flip_refund_cases():
    """cases.jsonl 退款语义样本（「用了优惠券想退了重拍」）不得被券词翻域。"""
    from backend.customer_service.router.coarse_router import CSCoarseRouter
    from backend.customer_service.router.types import CSDomain

    domain, conf, _ = CSCoarseRouter().classify("DEMO-1001 用了优惠券想退了重拍")
    # 规则票均 < 2 → 维持 UNKNOWN 走 hint（与改动前行为一致，不落 TRANSACTION）
    assert domain in (CSDomain.UNKNOWN, CSDomain.AFTER_SALES)


def test_coupon_words_exempt_from_out_of_scope():
    from backend.customer_service.vocab import match_cs_signal_exempt

    assert match_cs_signal_exempt("我的优惠券快过期了还能用吗")


# ── 2/3. Mock 契约 + Service http 模式 ───────────────────────────


def test_mock_coupons_lazy_seed_matches_policy(http_wire):
    """懒种子数字必须与 coupon-policy.md §1/§2 及 FAQ id=23 一致。"""
    data = http_wire.get(
        "/business/coupons",
        params={"user_id": "customer-coupon", "tenant_id": "tenant-a"},
        headers=_HEADERS,
    ).json()["data"]["coupons"]
    by_code = {c["coupon_code"]: c for c in data}
    assert set(by_code) == {"SAVE30", "NEW5", "D90", "OLD10"}
    assert by_code["SAVE30"]["threshold_amount"] == 200.0
    assert by_code["SAVE30"]["discount_value"] == 30.0
    assert by_code["NEW5"]["kind"] == "no_threshold"
    assert by_code["NEW5"]["threshold_amount"] == 0.0
    assert by_code["D90"]["kind"] == "discount"
    assert by_code["D90"]["discount_value"] == 0.9
    assert by_code["D90"]["max_discount"] == 50.0
    assert by_code["OLD10"]["status"] == "expired"
    # FAQ id=23：一单仅可使用一张优惠券，不可叠加
    assert all(c["stackable"] is False for c in data)


def test_mock_coupon_check_three_failure_reasons(http_wire):
    user_id = "customer-coupon-check"
    orders = _orders(http_wire, user_id)
    threshold_order = next(o for o in orders if float(o["total_amount"]) == 199.0)
    shipped_order = next(o for o in orders if o["status"] == "shipped")
    paid_order = next(o for o in orders if o["status"] == "paid")

    def check(order_no, coupon_code=""):
        resp = http_wire.post(
            "/business/coupons/check",
            json={"user_id": user_id, "tenant_id": "tenant-a",
                  "order_no": order_no, "coupon_code": coupon_code},
            headers=_HEADERS,
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["data"]["coupon_check"]

    # 已过期（显式券码）
    result = check(paid_order["order_no"], coupon_code="OLD10")
    assert result["applicable"] is False and result["reason_code"] == "expired"
    # 未满足门槛（199 < 满 200；政策 §3 门槛按实付合计不含运费）
    result = check(threshold_order["order_no"])
    assert result["applicable"] is False
    assert result["reason_code"] == "threshold_not_met"
    assert result["order_total"] == 199.0
    # 不可叠加（种子核销记录绑定已发货单：一单仅可用一张）
    result = check(shipped_order["order_no"])
    assert result["applicable"] is False
    assert result["reason_code"] == "not_stackable"
    # 可用（未核销的已支付单 ≥ 门槛）
    result = check(paid_order["order_no"])
    assert result["applicable"] is True and result["reason_code"] == "applicable"
    assert result["coupon"]["coupon_code"] == "SAVE30"
    # 订单不存在 → 404（check() helper 断言 200，此用例直连端点验证）
    resp = http_wire.post(
        "/business/coupons/check",
        json={"user_id": user_id, "tenant_id": "tenant-a",
              "order_no": "MO-NOTEXIST", "coupon_code": ""},
        headers=_HEADERS,
    )
    assert resp.status_code == 404


def test_coupon_service_http_mode_normalizes_display_fields(http_wire):
    coupons = CouponService().get_coupons("customer-coupon", "tenant-a")
    assert len(coupons) == 4
    save30 = next(c for c in coupons if c["coupon_code"] == "SAVE30")
    assert save30["kind_label"] == "满减券"
    assert save30["threshold_amount"] == 200.0 and save30["discount_value"] == 30.0
    d90 = next(c for c in coupons if c["coupon_code"] == "D90")
    assert d90["max_discount"] == 50.0

    orders = _orders(http_wire, "customer-coupon")
    paid_order = next(o for o in orders if o["status"] == "paid")
    check = CouponService().check_coupon(
        "customer-coupon", "tenant-a", paid_order["order_no"],
    )
    assert check["applicable"] is True
    assert check["reason_label"] == "可用"
    assert check["coupon"]["kind_label"] == "满减券"


# ── 4. facts 三件套（W1 纪律） ──────────────────────────────────


class TestQueryExpertCouponFacts:
    @patch("backend.customer_service.security.permission.PermissionChecker.validate_user_identity", return_value="872")
    @patch("backend.customer_service.experts.query._llm_decompose_intents", return_value=None)
    @patch("backend.customer_service.context_manager.resolve_order_slot", return_value="")
    @patch("backend.customer_service.service.coupon_service.CouponService")
    def test_coupon_query_exports_coupons_facts(
        self, coupon_service, _slot, _decompose, _identity,
    ):
        """券列表（含面额/门槛/叠加标记）进入 service_facts。"""
        coupon_service.return_value.get_coupons.return_value = [
            {"coupon_code": "SAVE30", "kind": "full_reduction",
             "kind_label": "满减券", "threshold_amount": 200.0,
             "discount_value": 30.0, "max_discount": None,
             "stackable": False, "exclusions": "", "valid_until":
             "2026-12-04T00:00:00+00:00", "status": "available"},
        ]

        result = execute_query(
            "我的优惠券有哪些",
            {"intent": "t_coupon_query", "route_path": "business_query"},
            {"user_id": "872", "tenant_id": "default", "session_id": "s1"},
        )

        assert "满 ¥200.00 减 ¥30.00" in result["response_draft"]
        assert result["data"]["service_facts"]["coupons"] == [{
            "currency": "CNY",
            "coupon_code": "SAVE30", "kind": "full_reduction",
            "kind_label": "满减券", "threshold_amount": 200.0,
            "discount_value": 30.0, "max_discount": None,
            "stackable": False, "status": "available",
            "valid_until": "2026-12-04",
        }]

    @patch("backend.customer_service.security.permission.PermissionChecker.validate_user_identity", return_value="872")
    @patch("backend.customer_service.experts.query._llm_decompose_intents", return_value=None)
    @patch("backend.customer_service.context_manager.resolve_order_slot", return_value="MO-ABCD1234")
    @patch("backend.customer_service.service.coupon_service.CouponService")
    def test_coupon_check_with_order_exports_check_facts(
        self, coupon_service, _slot, _decompose, _identity,
    ):
        """带订单号走 check 路径：不生效原因进入 service_facts。"""
        coupon_service.return_value.check_coupon.return_value = {
            "applicable": False, "reason_code": "threshold_not_met",
            "reason_label": "未满足门槛",
            "reason_text": "该券需满 200 元，当前订单 ¥199.00。",
            "order_no": "MO-ABCD1234", "order_total": 199.0,
            "coupon": {"coupon_code": "SAVE30", "kind": "full_reduction",
                       "kind_label": "满减券", "threshold_amount": 200.0,
                       "discount_value": 30.0},
        }

        result = execute_query(
            "订单 MO-ABCD1234 为什么用不了优惠券",
            {"intent": "t_coupon_query", "route_path": "business_query"},
            {"user_id": "872", "tenant_id": "default", "session_id": "s1"},
        )

        assert "MO-ABCD1234" in result["response_draft"]
        assert "未满足门槛" in result["response_draft"]
        check_facts = result["data"]["service_facts"]["coupon_check"]
        assert check_facts["reason_code"] == "threshold_not_met"
        # 金额守卫按币种比对：check facts 必须带 currency（含嵌套券摘要）
        assert check_facts["currency"] == "CNY"
        assert check_facts["coupon"]["currency"] == "CNY"

    @patch("backend.customer_service.security.permission.PermissionChecker.validate_user_identity", return_value="872")
    @patch("backend.customer_service.experts.query._llm_decompose_intents", return_value=None)
    @patch("backend.customer_service.context_manager.resolve_order_slot", return_value="")
    @patch("backend.customer_service.service.coupon_service.CouponService")
    def test_coupon_query_failure_degrades_politely(
        self, coupon_service, _slot, _decompose, _identity,
    ):
        coupon_service.return_value.get_coupons.side_effect = Exception("boom")

        result = execute_query(
            "我的优惠券有哪些",
            {"intent": "t_coupon_query", "route_path": "business_query"},
            {"user_id": "872", "tenant_id": "default", "session_id": "s1"},
        )

        assert result["response_draft"] == "优惠券查询服务暂时不可用，请稍后再试。"


def test_coupon_projection_whitelists_coupon_fields():
    from backend.customer_service.response.facts import project_fact_set

    projected = project_fact_set({
        "coupons": [{
            "coupon_code": "SAVE30", "kind": "full_reduction",
            "kind_label": "满减券", "threshold_amount": "200.00",
            "discount_value": "30.00", "max_discount": None,
            "stackable": False, "status": "available",
            "valid_until": "2026-12-04", "exclusions": "秒杀/拼团价商品",
            # 非白名单字段必须剥离
            "customer_id": "872", "internal_token": "secret",
        }],
        "coupon_check": {
            "applicable": False, "reason_code": "threshold_not_met",
            "reason_label": "未满足门槛", "reason_text": "需满 200 元。",
            "order_no": "MO-ABCD1234", "order_total": "199.00",
            "coupon": {"coupon_code": "SAVE30", "kind": "full_reduction",
                       "threshold_amount": "200.00", "discount_value": "30.00"},
        },
    })

    assert projected == {
        "coupons": [{
            "coupon_code": "SAVE30", "kind": "full_reduction",
            "kind_label": "满减券", "threshold_amount": "200",
            "discount_value": "30", "stackable": False,
            "status": "available", "valid_until": "2026-12-04",
        }],
        "coupon_check": {
            "applicable": False, "reason_code": "threshold_not_met",
            "reason_label": "未满足门槛", "reason_text": "需满 200 元。",
            "order_no": "MO-ABCD1234", "order_total": "199",
            "coupon": {"coupon_code": "SAVE30", "kind": "full_reduction",
                       "threshold_amount": "200", "discount_value": "30"},
        },
    }


def test_coupon_projection_drops_unknown_kind_and_reason():
    from backend.customer_service.response.facts import project_fact_set

    projected = project_fact_set({
        "coupons": [{"coupon_code": "X1", "kind": "mystery", "status": "weird"}],
        "coupon_check": {"applicable": True, "reason_code": "mystery"},
    })

    assert projected == {
        "coupons": [{"coupon_code": "X1"}],
        "coupon_check": {"applicable": True},
    }


def test_coupon_amount_guard_fact_backed_passes_and_invention_rejected():
    from backend.customer_service.response import composer

    facts = {"coupons": [{
        "currency": "CNY",
        "coupon_code": "SAVE30", "kind": "full_reduction",
        "kind_label": "满减券", "threshold_amount": "200",
        "discount_value": "30", "stackable": False, "status": "available",
    }]}
    assert composer._guard_fact_consistency(
        "您的满 200 元减 30 元优惠券可以使用。", facts,
    )
    # 无 facts 支撑的面额 fail-closed（「最高减 ¥99」无中生有）
    assert not composer._guard_fact_consistency(
        "您的优惠券最高可减 ¥99。", facts,
    )


def test_coupon_check_order_total_guard():
    from backend.customer_service.response import composer

    facts = {"coupon_check": {
        "currency": "CNY",
        "applicable": False, "reason_code": "threshold_not_met",
        "order_no": "MO-ABCD1234", "order_total": "199",
        "coupon": {"currency": "CNY", "coupon_code": "SAVE30",
                   "kind": "full_reduction",
                   "threshold_amount": "200", "discount_value": "30"},
    }}
    # 金额标签就近分类：199 紧邻「订单金额」→ order 类；200 紧邻「优惠券」
    # → coupon 类（order_label 距离更远的 200 不会被误判成订单金额）
    assert composer._guard_fact_consistency(
        "该订单金额为 ¥199.00，未达到优惠券满 200 元的门槛。", facts,
    )
    assert not composer._guard_fact_consistency(
        "该订单金额为 ¥999.00，未达到优惠券满 200 元的门槛。", facts,
    )


def _cs_route(intent="t_coupon_query"):
    return {"intent": intent}


def _state(msg="我的优惠券有哪些"):
    return {"user_message": msg}


def test_coupon_llm_rewrite_passes_fact_guard(monkeypatch):
    from backend.customer_service.response import composer
    from backend.customer_service.response.contracts import CSRenderedResponse

    monkeypatch.setattr(
        "backend.customer_service.response.composer._compose_with_llm",
        lambda *_a, **_k: (
            CSRenderedResponse(
                answer="您有一张满 200 元减 30 元的优惠券，不可叠加。",
            ),
            3,
        ),
    )
    text, source = composer.compose_reply(
        "query",
        "## 您的优惠券\n**1.** [满减券 SAVE30] 满 ¥200.00 减 ¥30.00 | 不可叠加",
        {"data": {"service_facts": {"coupons": [{
            "currency": "CNY",
            "coupon_code": "SAVE30", "kind": "full_reduction",
            "kind_label": "满减券", "threshold_amount": 200.0,
            "discount_value": 30.0, "stackable": False,
            "status": "available", "valid_until": "2026-12-04",
        }]}}},
        _cs_route(), _state(),
    )
    assert source == "llm"
    assert "200" in text


def test_coupon_llm_invented_discount_falls_back(monkeypatch):
    from backend.customer_service.response import composer
    from backend.customer_service.response.contracts import CSRenderedResponse

    draft = "## 您的优惠券\n**1.** [满减券 SAVE30] 满 ¥200.00 减 ¥30.00 | 不可叠加"
    monkeypatch.setattr(
        "backend.customer_service.response.composer._compose_with_llm",
        lambda *_a, **_k: (
            CSRenderedResponse(answer="您的优惠券最高可减 ¥99。"),
            3,
        ),
    )
    text, source = composer.compose_reply(
        "query", draft,
        {"data": {"service_facts": {"coupons": [{
            "currency": "CNY",
            "coupon_code": "SAVE30", "kind": "full_reduction",
            "kind_label": "满减券", "threshold_amount": 200.0,
            "discount_value": 30.0, "stackable": False,
            "status": "available", "valid_until": "2026-12-04",
        }]}}},
        _cs_route(), _state(),
    )
    assert source == "llm_fallback"
    assert text == draft  # 回模板初稿，无中生有的面额不落地


# ── 5. sandbox 直查（隔离 PG，真实迁移建表） ──────────────────────

_SANDBOX_MIGRATIONS = ("003_agent_business_schema.sql", "133_cs_coupon.sql")


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
def coupon_sandbox_admin():
    conn_kwargs = _resolve_conn_kwargs()
    if conn_kwargs is None:
        pytest.skip(
            "设置 POINTS_SANDBOX_PG_TEST_DSN（或 POINTS_SANDBOX_PG_DBNAME + PG* 环境）"
            "后运行优惠券 sandbox 隔离库验收",
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
                "优惠券 sandbox 验收只允许名称明确标记为 test 或 sandbox 的隔离数据库"
            )

        migrations_dir = Path(__file__).resolve().parents[2] / "sql" / "migrations"
        with admin.cursor() as cursor:
            cursor.execute(
                "SELECT to_regclass('marketing.coupons') IS NOT NULL"
                " AND to_regclass('marketing.coupon_redemptions') IS NOT NULL",
            )
            coupon_ready = bool(cursor.fetchone()[0])
        if not coupon_ready:
            with admin.cursor() as cursor:
                cursor.execute(
                    "SELECT to_regclass('\"order\".orders') IS NOT NULL",
                )
                if not bool(cursor.fetchone()[0]):
                    cursor.execute(
                        (migrations_dir / _SANDBOX_MIGRATIONS[0]).read_text(encoding="utf-8"),
                    )
                cursor.execute(
                    (migrations_dir / _SANDBOX_MIGRATIONS[1]).read_text(encoding="utf-8"),
                )
        yield admin
    finally:
        admin.close()


def test_sandbox_coupon_local_reads_migration_seed(coupon_sandbox_admin, monkeypatch):
    """sandbox 直查：券档案来自真实迁移（133 幂等种子）+ check 规则。"""
    from backend.config import customer_service as cs_cfg
    from backend.infra import db as infra_db

    test_cfg = _resolve_conn_kwargs()
    monkeypatch.setattr(cs_cfg, "CS_BUSINESS_GATEWAY_MODE", "sandbox")
    monkeypatch.setattr(infra_db, "BUSINESS_DB_CONFIG", dict(test_cfg))
    infra_db.reset_engines()
    service = CouponService()
    try:
        coupons = service.get_coupons("872", "default")
        assert {c["coupon_code"] for c in coupons} == {
            "SAVE30", "NEW5", "D90", "OLD10",
        }
        assert all(c["stackable"] is False for c in coupons)

        # 造一笔订单 + 核销记录 → not_stackable 确定性样本（重跑幂等）
        with coupon_sandbox_admin.cursor() as cursor:
            cursor.execute(
                'INSERT INTO "order".orders'
                " (order_no, customer_id, total_amount, status, payment_status)"
                " VALUES (%s, 872, %s, 'shipped', 'paid')"
                " ON CONFLICT (order_no) DO NOTHING",
                ("SBOX-COUPON-1", "699.00"),
            )
            cursor.execute(
                "DELETE FROM marketing.coupon_redemptions"
                " WHERE customer_id = 872 AND order_no = 'SBOX-COUPON-1'",
            )
            cursor.execute(
                "INSERT INTO marketing.coupon_redemptions"
                " (customer_id, coupon_code, order_no, amount_applied)"
                " VALUES (872, 'NEW5', 'SBOX-COUPON-1', 5.00)",
            )
        check = service.check_coupon("872", "default", "SBOX-COUPON-1")
        assert check["applicable"] is False
        assert check["reason_code"] == "not_stackable"

        expired = service.check_coupon(
            "872", "default", "SBOX-COUPON-1", coupon_code="OLD10",
        )
        assert expired["reason_code"] == "expired"
    finally:
        infra_db.reset_engines()
