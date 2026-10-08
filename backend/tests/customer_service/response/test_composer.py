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
            {"data": {"intent": "t_order_status"}},
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
        assert facts["severity"] == "medium"
