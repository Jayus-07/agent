"""Reporter 回复闭环的事实守卫顺序测试。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from backend.customer_service import reporter


def test_output_guard_runs_after_response_composer(monkeypatch):
    calls = []

    monkeypatch.setattr(reporter, "_assemble_answer", lambda *_a: "模板初稿")

    def _compose(*_args):
        calls.append("composer")
        return "自然回复", "llm"

    def _guard(answer, _state):
        calls.append("output_guard")
        return f"guarded:{answer}"

    monkeypatch.setattr(
        "backend.customer_service.response.composer.compose_reply", _compose,
    )
    monkeypatch.setattr(reporter, "_run_output_guard", _guard)

    result = reporter.cs_reporter_node({
        "supervisor_decision": {"next_action": "run_expert"},
        "last_expert_result": {"expert": "query", "data": {}},
        "handoff_state": "ai_active", "user_message": "查一下",
    })

    assert result["final_answer"] == "guarded:自然回复"
    assert calls == ["composer", "output_guard"]


def test_output_guard_failure_returns_safe_template(monkeypatch):
    monkeypatch.setattr(
        "backend.customer_service.security.output_guard.get_output_guard",
        lambda: (_ for _ in ()).throw(RuntimeError("guard unavailable")),
    )

    answer = "已为您申请退款，退款金额为 999 元。"
    guarded = reporter._run_output_guard(answer, {})

    assert guarded != answer
    assert "999" not in guarded
    assert "退款" not in guarded


def test_reporter_records_task_and_pending_trace_fields(monkeypatch):
    tracer = SimpleNamespace(tags={})
    monkeypatch.setattr(
        "backend.observability.tracer.trace_collector.current",
        lambda: tracer,
    )

    reporter._tag_reporter_trace({
        "task_plan_source": "rule_candidate",
        "task_plan": {"tasks": [{"task_id": "q1"}, {"task_id": "a1"}]},
        "task_results": [
            {"status": "success"}, {"status": "skipped"},
        ],
        "pending_turn_decision": "READ_ONLY_QUERY",
        "cs_route": {"metadata": {"understanding_source": "rule+llm"}},
    })

    assert tracer.tags["cs_understanding_source"] == "rule+llm"
    assert tracer.tags["cs_task_plan_source"] == "rule_candidate"
    assert tracer.tags["cs_task_count"] == 2
    assert tracer.tags["cs_task_status"] == "success,skipped"
    assert tracer.tags["cs_pending_turn_kind"] == "READ_ONLY_QUERY"


def test_knowledge_response_does_not_make_a_second_model_call(monkeypatch):
    calls = []

    from backend.customer_service.response import composer

    monkeypatch.setattr(
        "backend.config.customer_service.CS_RESPONSE_COMPOSER_ENABLED", True,
    )
    monkeypatch.setattr(
        composer, "_compose_with_llm",
        lambda *_a, **_k: calls.append("llm") or None,
    )

    result = composer.compose_reply(
        "knowledge", "退货政策为 7 天无理由。", {}, {}, {},
    )

    assert result == ("退货政策为 7 天无理由。", "llm")
    assert calls == []


@patch("backend.customer_service.security.permission.PermissionChecker.validate_user_identity", return_value="u1")
@patch("backend.customer_service.context_resolver.record_recent_order")
@patch("backend.customer_service.context_manager.resolve_order_slot", return_value="MO-001")
@patch("backend.customer_service.service.order_service.get_order_service")
def test_query_expert_exports_facts_from_the_service_result(
    get_orders, _resolve_slot, _record_order, _identity,
):
    get_orders.return_value.query_orders.return_value = SimpleNamespace(
        orders=[{
            "id": "internal-order-id", "order_no": "MO-001",
            "status": "paid", "total_amount": 199.0,
            "created_at": "2026-10-01T10:00:00Z", "customer_id": "u1",
        }],
    )

    from backend.customer_service.experts.query import execute_query

    result = execute_query(
        "MO-001 订单状态是什么？", {"intent": "t_order_status"},
        {"user_id": "u1", "tenant_id": "tenant-1", "session_id": "s1"},
    )

    assert result["data"]["service_facts"]["orders"] == [{
        "order_no": "MO-001", "status": "paid", "total_amount": 199.0,
        "created_at": "2026-10-01T10:00:00Z",
    }]
