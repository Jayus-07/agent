"""CS Response Composer 单测（2026-10-08 收口改造）。

断言：
  - action/handoff 恒模板 0 LLM（P0-19）
  - knowledge 直通（RAG 上游已是 LLM，不二次烧模型）
  - query/complaint LLM 转写成功/失败回模板（P0-13，一次调用无重试）
  - 守卫：输出新增订单号 = 拒绝回模板（P0-18）
"""
from __future__ import annotations

from backend.customer_service.response import composer
from backend.customer_service.response.contracts import EXPERT_RESPONSE_POLICY
from backend.customer_service.response.contracts import ResponsePolicy


def _cs_route(intent="t_order_status"):
    return {"intent": intent}


def _state(msg="帮我看下订单"):
    return {"user_message": msg}


class TestPolicyTable:
    def test_action_and_handoff_template(self):
        """P0-19：Action/Handoff 高风险回复模板优先（策略表硬编码）。"""
        assert EXPERT_RESPONSE_POLICY["action"] is ResponsePolicy.TEMPLATE
        assert EXPERT_RESPONSE_POLICY["handoff"] is ResponsePolicy.TEMPLATE

    def test_knowledge_llm_upstream_query_llm_complaint_guard(self):
        assert EXPERT_RESPONSE_POLICY["knowledge"] is ResponsePolicy.LLM_UPSTREAM
        assert EXPERT_RESPONSE_POLICY["query"] is ResponsePolicy.LLM_PREFERRED
        assert EXPERT_RESPONSE_POLICY["complaint"] is ResponsePolicy.LLM_WITH_GUARD


class TestComposeReply:
    def test_llm_fallback_reason_is_recorded_in_trace(self, monkeypatch):
        tracer = type("Tracer", (), {"tags": {}})()
        monkeypatch.setattr(
            "backend.observability.tracer.trace_collector.current",
            lambda: tracer,
        )
        monkeypatch.setattr(
            "backend.customer_service.response.composer._compose_with_llm",
            lambda *_a, **_k: None,
        )

        text, source = composer.compose_reply(
            "query", "模板回复", {}, _cs_route(), _state(),
        )

        assert (text, source) == ("模板回复", "llm_fallback")
        assert tracer.tags["cs_response_fallback_reason"] == "llm_error"

    def test_action_zero_llm_template_passthrough(self, monkeypatch):
        """P0-19：action draft 原样直出，不调 LLM。"""
        monkeypatch.setattr(
            "backend.customer_service.response.composer._compose_with_llm",
            lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("action must not call LLM")),
        )
        text, source = composer.compose_reply(
            "action", "请确认退款订单 DEMO-1001，金额 ¥199。", {}, _cs_route(), _state(),
        )
        assert source == "template"
        assert text.startswith("请确认退款")

    def test_handoff_zero_llm(self, monkeypatch):
        monkeypatch.setattr(
            "backend.customer_service.response.composer._compose_with_llm",
            lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("handoff must not call LLM")),
        )
        text, source = composer.compose_reply(
            "handoff", "已为您转接人工客服，请稍候。", {}, _cs_route("h_handoff"), _state(),
        )
        assert source == "template"

    def test_knowledge_passthrough_llm_upstream(self):
        text, source = composer.compose_reply(
            "knowledge", "退货政策为 7 天无理由。", {}, _cs_route("k_policy"), _state(),
        )
        assert source == "llm"  # RAG 上游生成
        assert "7 天" in text

    def test_disabled_switch_all_template(self, monkeypatch):
        from backend.config import customer_service as cs_config
        monkeypatch.setattr(cs_config, "CS_RESPONSE_COMPOSER_ENABLED", False)
        monkeypatch.setattr(
            "backend.customer_service.response.composer._compose_with_llm",
            lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("disabled must not call LLM")),
        )
        _text, source = composer.compose_reply(
            "query", "## 您的订单\n**1.** DEMO-1001", {}, _cs_route(), _state(),
        )
        assert source == "template"

    def test_query_llm_rewrite_success(self, monkeypatch):
        from backend.customer_service.response.contracts import CSRenderedResponse

        monkeypatch.setattr(
            "backend.customer_service.response.composer._compose_with_llm",
            lambda *_a, **_k: (
                CSRenderedResponse(answer="您的订单 DEMO-1001 已发货，预计 10 月 10 日送达。"),
                3,
            ),
        )
        text, source = composer.compose_reply(
            "query", "## 您的订单\n**1.** 订单号: `DEMO-1001` | 状态: shipped",
            {"data": {"service_facts": {
                "order": {
                    "order_no": "DEMO-1001", "status": "shipped",
                    "shipping_status": "shipped",
                },
                "logistics": {
                    "shipping_status": "shipped",
                    "estimated_delivery": "2026-10-10",
                },
            }}},
            _cs_route(), _state(),
        )
        assert source == "llm"
        assert "DEMO-1001" in text

    def test_query_llm_failure_falls_back_to_template(self, monkeypatch):
        """P0-13：Response LLM 超时/异常 → Template fallback。"""
        monkeypatch.setattr(
            "backend.customer_service.response.composer._compose_with_llm",
            lambda *_a, **_k: None,
        )
        draft = "## 您的订单\n**1.** 订单号: `DEMO-1001` | 状态: shipped"
        text, source = composer.compose_reply(
            "query", draft, {}, _cs_route(), _state(),
        )
        assert source == "llm_fallback"
        assert text == draft

    def test_guard_rejects_new_order_no(self, monkeypatch):
        """P0-18：LLM 输出引入事实里没有的订单号 → 拒绝回模板。"""
        from backend.customer_service.response.contracts import CSRenderedResponse

        monkeypatch.setattr(
            "backend.customer_service.response.composer._compose_with_llm",
            lambda *_a, **_k: (
                CSRenderedResponse(answer="您的订单 DEMO-9999 已发货。"),  # 幻觉订单号
                3,
            ),
        )
        draft = "## 您的订单\n**1.** 订单号: `DEMO-1001` | 状态: shipped"
        text, source = composer.compose_reply(
            "query", draft, {}, _cs_route(), _state(),
        )
        assert source == "llm_fallback"
        assert text == draft  # 回模板初稿，幻觉不落地

    def test_fact_guard_falls_back_when_llm_invents_order_amount(self, monkeypatch):
        from backend.customer_service.response.contracts import CSRenderedResponse

        monkeypatch.setattr(
            "backend.customer_service.response.composer._compose_with_llm",
            lambda *_a, **_k: (CSRenderedResponse(answer="订单金额为 ¥299。"), 3),
        )
        draft = "订单金额为 ¥199。"

        text, source = composer.compose_reply(
            "query", draft,
            {"data": {"service_facts": {
                "orders": [{"total_amount": "199", "currency": "CNY"}],
            }}},
            _cs_route(), _state(),
        )

        assert source == "llm_fallback"
        assert text == draft

    def test_fact_guard_falls_back_when_pending_proposal_is_called_executed(
        self, monkeypatch,
    ):
        from backend.customer_service.response.contracts import CSRenderedResponse

        monkeypatch.setattr(
            "backend.customer_service.response.composer._compose_with_llm",
            lambda *_a, **_k: (
                CSRenderedResponse(answer="已为您申请退款。"), 3,
            ),
        )
        draft = "已为您生成待确认的退款申请，请确认。"

        text, source = composer.compose_reply(
            "query", draft,
            {"data": {"operation": {
                "status": "pending_confirmation", "action": "refund",
            }}},
            _cs_route(), _state(),
        )

        assert source == "llm_fallback"
        assert text == draft


class TestGuard:
    def test_date_like_tokens_not_order_nos(self):
        # 10-10 / 2026-10-10 是日期不是订单号，不得触发守卫误杀
        assert composer._guard_no_new_facts(
            "预计 10-10 送达", "预计 10-10 送达", {},
        )
        assert composer._collect_order_nos("预计 10-10 送达") == set()

    def test_new_real_order_no_detected(self):
        baseline = composer._collect_order_nos("订单 DEMO-1001")
        out = composer._collect_order_nos("订单 DEMO-1001 和 DEMO-8888")
        assert out - baseline == {"DEMO-8888"}

    def test_rejects_amount_not_present_in_fact_set(self):
        facts = {"orders": [{"total_amount": "199.00", "currency": "CNY"}]}

        assert composer._guard_fact_consistency("订单金额为 ¥199.00", facts)
        assert not composer._guard_fact_consistency("订单金额为 ¥299.00", facts)

    def test_rejects_amount_with_wrong_currency_or_order(self):
        wrong_currency = {
            "orders": [{"order_no": "MO-001", "total_amount": "199.00", "currency": "USD"}],
        }
        two_orders = {"orders": [
            {"order_no": "MO-001", "total_amount": "100.00", "currency": "CNY"},
            {"order_no": "MO-002", "total_amount": "200.00", "currency": "CNY"},
        ]}

        assert not composer._guard_fact_consistency("订单 MO-001 金额为 ¥199", wrong_currency)
        assert not composer._guard_fact_consistency("订单 MO-001 金额为 200 元", two_orders)
        assert not composer._guard_fact_consistency("第一笔订单金额为 200 元", two_orders)
        assert not composer._guard_fact_consistency(
            "第一笔订单金额 200 元，第二笔订单金额 100 元。", two_orders,
        )
        assert not composer._guard_fact_consistency(
            "第一笔订单金额 100 元，另一笔订单金额 100 元。", two_orders,
        )
        assert not composer._guard_fact_consistency(
            "第一笔订单金额 100 元。另外一笔订单金额 100 元。", two_orders,
        )
        assert not composer._guard_fact_consistency(
            "第一笔订单金额 100 元，剩下那笔订单金额 100 元。", two_orders,
        )
        assert not composer._guard_fact_consistency(
            "第一笔订单金额 100 元，另外一单金额 100 元。", two_orders,
        )
        assert not composer._guard_fact_consistency(
            "第一笔订单金额 100 元，余下那单金额 100 元。", two_orders,
        )
        assert not composer._guard_fact_consistency(
            "第一笔订单金额 100 元，剩下的那笔订单金额 100 元。", two_orders,
        )
        assert not composer._guard_fact_consistency("订单金额为 ¥199.999", {
            "orders": [{"total_amount": "199.99", "currency": "CNY"}],
        })
        cny_order = {"orders": [{"total_amount": "199.00", "currency": "CNY"}]}
        assert not composer._guard_fact_consistency("订单金额为 -199 元", cny_order)
        assert not composer._guard_fact_consistency("订单金额为－199元", cny_order)
        assert not composer._guard_fact_consistency("订单金额为 199 HK$", cny_order)

    def test_rejects_shipping_claim_that_conflicts_with_fact_set(self):
        facts = {"logistics": {"shipping_status": "not_shipped"}}

        assert not composer._guard_fact_consistency("您的包裹已经发货。", facts)
        assert composer._guard_fact_consistency("目前显示尚未发货。", facts)

    def test_negated_shipping_claim_does_not_skip_other_order_claims(self):
        facts = {"orders": [
            {"order_no": "MO-001", "shipping_status": "shipped"},
            {"order_no": "MO-002", "shipping_status": "not_shipped"},
        ], "logistics": {"shipping_status": "not_shipped"}}

        assert not composer._guard_fact_consistency(
            "订单 MO-001 并不是已发货，订单 MO-002 已发货。", facts,
        )
        assert composer._guard_fact_consistency(
            "订单 MO-001 并不是已发货，订单 MO-002 已发货。", {
                "orders": [
                    {"order_no": "MO-001", "shipping_status": "not_shipped"},
                    {"order_no": "MO-002", "shipping_status": "shipped"},
                ],
            },
        )

    def test_rejects_unverified_refund_and_sandbox_success_claims(self):
        facts = {"source": "sandbox_business_service"}

        assert not composer._guard_fact_consistency("退款已到账。", facts)
        assert not composer._guard_fact_consistency("真实平台已退款成功。", facts)

    def test_pending_proposal_cannot_be_described_as_executed(self):
        facts = {"operation": {"status": "pending_confirmation", "action": "refund"}}

        assert not composer._guard_fact_consistency("已为您申请退款。", facts)
        assert composer._guard_fact_consistency(
            "已生成待确认的退款申请，请您确认。", facts,
        )
        assert not composer._guard_fact_consistency(
            "已生成待确认的退款申请，请您确认。", {},
        )

    def test_rejects_unverified_refund_waiting_for_confirmation_claim(self):
        assert not composer._guard_fact_consistency(
            "退款申请正在等待您的确认。", {},
        )
        assert not composer._guard_fact_consistency(
            "退款申请需要您点击确认。", {},
        )
        assert not composer._guard_fact_consistency(
            "退款申请已准备好，等您批准后提交。", {},
        )
        assert not composer._guard_fact_consistency(
            "退款已到账。", {
                "operation": {"status": "success", "action": "exchange"},
            },
        )
        assert not composer._guard_fact_consistency(
            "退款申请待您确认。", {
                "operation": {
                    "status": "pending_confirmation", "action": "exchange",
                },
            },
        )
        assert not composer._guard_fact_consistency(
            "退款已到账。", {
                "operation": {
                    "status": "success", "action": "refund", "simulated": True,
                },
            },
        )
        assert composer._guard_fact_consistency(
            "模拟退款已到账。", {
                "operation": {
                    "status": "success", "action": "refund", "simulated": True,
                },
            },
        )

    def test_order_completion_does_not_support_refund_completion(self):
        assert not composer._guard_fact_consistency(
            "该订单退款已完成。", {
                "order": {"order_no": "MO-001", "status": "completed"},
            },
        )
        assert not composer._guard_fact_consistency("退款申请已完成。", {})
        assert composer._guard_fact_consistency(
            "退款已完成。", {"refund": {"status": "success"}},
        )
        assert not composer._guard_fact_consistency(
            "该订单已完成退款。",
            {"order": {"order_no": "MO-001", "status": "completed"}},
        )

    def test_conflicting_refund_statuses_fail_closed(self):
        assert not composer._guard_fact_consistency(
            "退款已到账。", {
                "refund": {"status": "failed"},
                "operation": {"action": "refund", "status": "success"},
            },
        )
        assert not composer._guard_fact_consistency("退款已成功。", {})
        assert not composer._guard_fact_consistency("申请退款已提交。", {})
        assert not composer._guard_fact_consistency("已成功申请退款。", {})

    def test_rejects_new_arrival_promise(self):
        facts = {"logistics": {"shipping_status": "shipped"}}

        assert not composer._guard_fact_consistency("保证两天内退款到账。", facts)
        assert not composer._guard_fact_consistency("保证两天内送到。", {})
        assert not composer._guard_fact_consistency("预计明天签收。", {})
        assert not composer._guard_fact_consistency("预计明天收货。", {})
        for answer in (
            "预计明天送货。", "预计明天配送。", "预计明天投递。",
            "预计明天妥投。", "预计明天派送。",
        ):
            assert not composer._guard_fact_consistency(answer, {})
        assert not composer._guard_fact_consistency("两天后送达。", facts)
        assert not composer._guard_fact_consistency("明天退款到账。", facts)
        assert not composer._guard_fact_consistency("预计10月10日到达。", {})
        assert not composer._guard_fact_consistency("下周三送达。", {})
        assert not composer._guard_fact_consistency("周五送达。", {})
        assert not composer._guard_fact_consistency(
            "预计10月10日发货。",
            {"logistics": {"estimated_delivery": "2026-10-10"}},
        )
        assert not composer._guard_fact_consistency(
            "预计10月10日揽收。",
            {"logistics": {"estimated_delivery": "2026-10-10"}},
        )
        assert not composer._guard_fact_consistency(
            "预计10月10日出库。",
            {"logistics": {"estimated_delivery": "2026-10-10"}},
        )
        assert not composer._guard_fact_consistency(
            "预计10月10日寄出。",
            {"logistics": {"estimated_delivery": "2026-10-10"}},
        )

    def test_shipping_synonyms_require_matching_facts(self):
        assert not composer._guard_fact_consistency("订单已寄出。", {})
        assert composer._guard_fact_consistency(
            "订单已寄出。",
            {"logistics": {"shipping_status": "shipped"}},
        )
        assert not composer._guard_fact_consistency("快递已揽收。", {})
        assert composer._guard_fact_consistency(
            "快递已揽收。",
            {"logistics": {"shipping_status": "shipped"}},
        )
        assert not composer._guard_fact_consistency("订单已出库。", {})
        assert not composer._guard_fact_consistency("快递已发出。", {})

    def test_real_source_claim_requires_explicit_business_service_source(self):
        answer = "这条结果来自真实业务数据。"

        assert not composer._guard_fact_consistency(answer, {"source": "unknown"})
        assert not composer._guard_fact_consistency(answer, {})
        assert composer._guard_fact_consistency(
            answer, {"source": "business_order_service"},
        )

    def test_refund_eligibility_claim_polarity_must_match_fact(self):
        assert not composer._guard_fact_consistency(
            "该订单不符合退款资格。",
            {"order": {"refund_eligible": True}},
        )
        assert composer._guard_fact_consistency(
            "该订单不符合退款资格。",
            {"order": {"refund_eligible": False}},
        )
        assert composer._guard_fact_consistency(
            "该订单符合退款资格。",
            {"order": {"refund_eligible": True}},
        )
        assert not composer._guard_fact_consistency(
            "该订单并非不符合退款资格。",
            {"order": {"refund_eligible": False}},
        )
        assert not composer._guard_fact_consistency(
            "该订单不算不符合退款资格。",
            {"order": {"refund_eligible": False}},
        )
        assert not composer._guard_fact_consistency(
            "该订单符合退款资格。",
            {"order": {"refund_eligible": False}},
        )
        assert not composer._guard_fact_consistency(
            "该订单可以退款。",
            {"order": {"refund_eligible": False}},
        )
        assert not composer._guard_fact_consistency(
            "该订单支持退款。",
            {"order": {"refund_eligible": False}},
        )
        assert composer._guard_fact_consistency(
            "该订单可以退款。",
            {"order": {"refund_eligible": True}},
        )

    def test_logistics_eta_cannot_support_refund_arrival_eta(self):
        facts = {"logistics": {"estimated_delivery": "2026-10-10"}}

        assert not composer._guard_fact_consistency(
            "退款预计10月10日到账。", facts,
        )
        assert composer._guard_fact_consistency(
            "退款预计10月10日到账。", {
                "refund": {"estimated_arrival": "2026-10-10"},
            },
        )

    def test_each_eta_claim_uses_its_own_fact_and_preserves_year(self):
        facts = {
            "refund": {"estimated_arrival": "2026-10-11"},
            "logistics": {"estimated_delivery": "2026-10-10"},
        }

        assert not composer._guard_fact_consistency(
            "退款预计10月10日到账，包裹预计10月11日送达。", facts,
        )
        assert composer._guard_fact_consistency(
            "退款预计10月11日到账，包裹预计10月10日送达。", facts,
        )
        assert not composer._guard_fact_consistency(
            "预计2027-10-10送达。", {
                "logistics": {"estimated_delivery": "2026-10-10"},
            },
        )

    def test_order_amount_and_refund_amount_cannot_substitute_each_other(self):
        facts = {
            "order": {"total_amount": "200", "currency": "CNY"},
            "refund": {"amount": "100", "currency": "CNY"},
        }

        assert not composer._guard_fact_consistency(
            "订单金额100元，退款金额200元。", facts,
        )
        assert not composer._guard_fact_consistency(
            "订单金额100元（这不是退款金额）。", {
                "order": {"total_amount": "200", "currency": "CNY"},
                "refund": {"amount": "100", "currency": "CNY"},
            },
        )

    def test_order_status_claim_must_match_referenced_order(self):
        facts = {"orders": [
            {"order_no": "MO-001", "status": "pending",
             "shipping_status": "not_shipped"},
            {"order_no": "MO-002", "status": "paid",
             "shipping_status": "shipped"},
        ]}

        assert not composer._guard_fact_consistency("订单 MO-001 已付款。", facts)
        assert composer._guard_fact_consistency("订单 MO-002 已付款。", facts)
        assert not composer._guard_fact_consistency("订单 MO-001 已发货。", facts)
        assert composer._guard_fact_consistency("订单 MO-002 已发货。", facts)
        assert composer._guard_fact_consistency(
            "第一笔订单已付款，剩下的那笔订单已发货。", {
                "orders": [
                    {"order_no": "MO-001", "status": "paid",
                     "shipping_status": "not_shipped"},
                    {"order_no": "MO-002", "status": "paid",
                     "shipping_status": "shipped"},
                ],
            },
        )

    def test_conflicting_status_sources_for_same_order_fail_closed(self):
        assert not composer._guard_fact_consistency(
            "订单已完成。", {
                "order": {"order_no": "X1", "status": "pending"},
                "logistics": {"order_no": "X1", "status": "completed"},
            },
        )

    def test_sandbox_and_simulation_claims_require_positive_qualification(self):
        simulated = {
            "operation": {
                "status": "success", "action": "refund", "simulated": True,
            },
        }
        sandbox_refund = {
            "source": "sandbox_business_service",
            "refund": {"status": "success"},
        }

        assert not composer._guard_fact_consistency(
            "退款已到账，但这不是模拟结果。", simulated,
        )
        assert not composer._guard_fact_consistency(
            "退款已到账，但这不是一笔模拟退款。", simulated,
        )
        assert not composer._guard_fact_consistency(
            "退款已到账（这并非真实的模拟退款）。", simulated,
        )
        assert not composer._guard_fact_consistency(
            "退款已到账但不能算作模拟退款。", simulated,
        )
        assert not composer._guard_fact_consistency(
            "退款已到账但我不觉得这是模拟退款。", simulated,
        )
        assert not composer._guard_fact_consistency(
            "模拟流程已跑通但这不是模拟退款且退款已到账。", simulated,
        )
        assert not composer._guard_fact_consistency(
            "模拟退款已到账。退款成功。", simulated,
        )
        assert not composer._guard_fact_consistency("退款已到账。", sandbox_refund)

    def test_negated_status_claim_cannot_pass_on_matching_positive_fact(self):
        facts = {"logistics": {"shipping_status": "shipped"}}

        assert not composer._guard_fact_consistency("并不是已经发货。", facts)

    def test_sandbox_cannot_be_described_as_a_real_order(self):
        facts = {
            "source": "sandbox_logistics_service",
            "order": {"order_no": "MO-001", "shipping_status": "shipped"},
        }

        assert not composer._guard_fact_consistency(
            "我已查到您的真实订单 MO-001 物流已发货。", facts,
        )
        assert not composer._guard_fact_consistency(
            "线上实际物流显示已发货。", facts,
        )
        assert not composer._guard_fact_consistency(
            "这条物流来自真实数据库，状态已发货。", facts,
        )
        assert not composer._guard_fact_consistency(
            "这条物流来自真实的数据库，状态已发货。", facts,
        )
        assert not composer._guard_fact_consistency(
            "根据真实数据，订单 MO-001 物流已发货。", facts,
        )
        assert not composer._guard_fact_consistency(
            "这条结果根据真实的业务数据得出。", facts,
        )
        assert not composer._guard_fact_consistency(
            "这条结果来自真实可靠的业务数据。", facts,
        )
        assert not composer._guard_fact_consistency(
            "这条结果来自真实、可靠的业务数据。", facts,
        )
        assert composer._guard_fact_consistency(
            "这不是一个真实订单，而是模拟查询。", facts,
        )

    def test_unresolved_relative_order_status_claim_is_rejected(self):
        facts = {"orders": [{
            "order_no": "MO-001", "shipping_status": "shipped",
        }]}

        assert not composer._guard_fact_consistency("另一笔订单已发货。", facts)

class TestSanitizeFacts:
    def test_internal_ticket_id_blocked(self):
        # 内部工单号（COMPLAINT-*）不进可引用事实：LLM 引用它会被
        # output_guard 打码留下「（编号[已过滤]）」（2026-10-08 实测）
        facts = composer._sanitize_facts({
            "ticket_id": "COMPLAINT-AB12CD34",
            "severity": "medium",
            "duplicate": True,
        })
        assert "ticket_id" not in facts
        assert facts["complaint"]["severity"] == "medium"

    def test_prompt_uses_projected_fact_set_and_no_template_draft(self, monkeypatch):
        captured = {}

        def _render(_key, **kwargs):
            captured.update(kwargs)
            return "registered prompt", 4

        monkeypatch.setattr(
            "backend.customer_service.prompting.render_prompt_with_version",
            _render,
        )
        monkeypatch.setattr(
            "backend.customer_service.understanding.llm_runtime.llm_invoke_once",
            lambda *_a, **_k: '{"answer":"订单目前尚未发货。"}',
        )

        result = composer._compose_with_llm(
            "query", "模板里含订单金额 ¥199", {
                "data": {"task_result": {
                    "task_id": "q1", "status": "success",
                    "facts": {"shipping_status": "not_shipped"},
                    "source": "sandbox_logistics_service",
                }},
            }, _cs_route(), _state(),
        )

        assert result is not None
        assert captured["draft"] == ""
        assert "¥199" not in captured["facts"]
        assert "not_shipped" in captured["facts"]
