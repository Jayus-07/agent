"""受限 CSTaskPlan、事实条件与 Supervisor 单步调度测试。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.customer_service.graph_state import (
    CS_ACTION_EXPERT,
    CS_QUERY_EXPERT,
    CS_REPORTER,
    new_cs_graph_input,
)
from backend.customer_service.supervisor import cs_supervisor_node
from backend.customer_service.understanding.task_plan import (
    CSTaskCondition,
    CSTaskPlan,
    CSTaskResult,
    build_rule_task_plan,
    evaluate_task_condition,
)


def _plan() -> dict:
    return {
        "schema_version": 1,
        "primary_intent": "t_logistics",
        "tasks": [
            {"task_id": "q1", "capability": "query_logistics", "depends_on": []},
            {
                "task_id": "a1", "capability": "propose_refund",
                "depends_on": ["q1"],
                "condition": {
                    "fact": "shipping_status", "operator": "eq",
                    "value": "not_shipped",
                },
            },
        ],
    }


def _supervisor_state(**overrides) -> dict:
    base = {
        "user_id": "u1", "tenant_id": "tenant-1",
        "conversation_id": "c1", "session_id": "s1",
        "user_message": "查下物流，如果没发货就申请退款",
        "cs_route": {
            "intent": "t_logistics", "route_path": "business_query",
            "confidence": 0.99,
        },
        "handoff_state": "ai_active",
        "handling_mode": "ai",
        "confirmation_state": "not_required",
        "expert_loop_count": 0,
        "expert_history": [],
        "task_plan": _plan(),
        "task_cursor": 0,
        "task_results": [],
        "current_task": None,
    }
    base.update(overrides)
    return base


class TestCSTaskPlanSchema:
    def test_rejects_untrusted_capability(self):
        plan = _plan()
        plan["tasks"][0]["capability"] = "sql.execute"
        with pytest.raises(ValidationError):
            CSTaskPlan.model_validate(plan)

    def test_rejects_duplicate_task_id_and_dangling_dependency(self):
        plan = _plan()
        plan["tasks"][1]["task_id"] = "q1"
        with pytest.raises(ValidationError):
            CSTaskPlan.model_validate(plan)

        plan = _plan()
        plan["tasks"][1]["depends_on"] = ["missing"]
        with pytest.raises(ValidationError):
            CSTaskPlan.model_validate(plan)

    def test_rejects_cycle_and_task_count_over_limit(self):
        plan = _plan()
        plan["tasks"][0]["depends_on"] = ["a1"]
        with pytest.raises(ValidationError):
            CSTaskPlan.model_validate(plan)

        plan = _plan()
        plan["tasks"].extend([
            {"task_id": "q2", "capability": "query_order_status", "depends_on": []},
            {"task_id": "q3", "capability": "query_logistics", "depends_on": []},
        ])
        with pytest.raises(ValidationError):
            CSTaskPlan.model_validate(plan)

    @pytest.mark.parametrize("unsafe_field", ["order_id", "user_id", "url", "sql"])
    def test_rejects_model_supplied_business_ids_and_execution_fields(self, unsafe_field):
        plan = _plan()
        plan["tasks"][0][unsafe_field] = "DEMO-1001"
        with pytest.raises(ValidationError):
            CSTaskPlan.model_validate(plan)

    def test_rejects_unbounded_condition(self):
        plan = _plan()
        plan["tasks"][1]["condition"] = {
            "fact": "order_total", "operator": "gt", "value": 0,
        }
        with pytest.raises(ValidationError):
            CSTaskPlan.model_validate(plan)

    def test_result_contract_is_structured(self):
        result = CSTaskResult(
            task_id="q1", status="success",
            facts={"shipping_status": "not_shipped", "order_count": 1},
            source="sandbox_logistics_service",
        )
        assert result.model_dump() == {
            "task_id": "q1", "status": "success",
            "facts": {"shipping_status": "not_shipped", "order_count": 1},
            "source": "sandbox_logistics_service", "error_type": None,
        }

    def test_graph_input_transfers_only_validated_plan_and_resets_cursor(self):
        cs_input = new_cs_graph_input(
            "查下物流，如果没发货就申请退款", "u1", "s1", "c1",
            {"metadata": {
                "task_plan_candidate": _plan(),
                "task_plan_source": "rule_candidate",
            }},
            tenant_id="tenant-1",
        )

        assert cs_input["task_plan"]["tasks"][0]["capability"] == "query_logistics"
        assert cs_input["task_plan_source"] == "rule_candidate"
        assert cs_input["task_cursor"] == 0
        assert cs_input["task_results"] == []
        assert cs_input["current_task"] is None

    def test_graph_input_clears_prior_plan_when_request_has_no_candidate(self):
        cs_input = new_cs_graph_input(
            "订单现在到哪了", "u1", "s1", "c1", {"metadata": {}},
            tenant_id="tenant-1",
        )

        assert cs_input["task_plan"] is None
        assert cs_input["task_plan_source"] == ""
        assert cs_input["task_cursor"] == 0
        assert cs_input["task_results"] == []
        assert cs_input["current_task"] is None


class TestTaskConditions:
    def test_missing_or_unknown_shipping_fact_never_matches(self):
        condition = CSTaskCondition(
            fact="shipping_status", operator="eq", value="not_shipped",
        )
        assert evaluate_task_condition(condition, {}) is False
        assert evaluate_task_condition(condition, None) is False
        assert evaluate_task_condition(
            condition, {"shipping_status": "unknown"},
        ) is False

    def test_condition_matches_only_authoritative_enum_value(self):
        condition = CSTaskCondition(
            fact="shipping_status", operator="eq", value="not_shipped",
        )
        assert evaluate_task_condition(
            condition, {"shipping_status": "not_shipped"},
        ) is True
        assert evaluate_task_condition(
            condition, {"shipping_status": "shipped"},
        ) is False


class TestRulePlanIntent:
    @pytest.mark.parametrize("message", [
        "查下物流，如果没发货就不要申请退款",
        "查下物流，如果没发货就别申请退款",
        "查下物流，如果没发货就不需要退款",
        "查下物流，如果没发货先别帮我申请退款",
        "查下物流，如果没发货就不要帮我退款",
        "查下物流，如果没发货我没打算申请退款",
        "查下物流，如果没发货就没必要申请退款",
        "查下物流，如果没发货我不考虑退款",
        "查下物流，如果没发货就申请退款，算了",
        "查下物流，如果没发货就申请退款，不用了",
        "查下物流，如果没发货就申请退款，算了，先告诉我物流状态",
        "查下物流，如果没发货就申请退款，不用了，先帮我查订单",
        "查下物流，如果没发货就申请退款，我改主意了",
        "查下物流，如果没发货就申请退款，我不想了",
        "查下物流，如果没发货就申请退款，不想办了",
        "查下物流，如果没发货就申请退款，我反悔了",
        "查下物流，如果没发货就申请退款，我改了主意",
        "查下物流，如果没发货就申请退款，我收回申请",
        "查下物流，如果没发货就申请退款，我放弃了",
    ])
    def test_explicit_refund_negation_never_creates_write_plan(self, message):
        assert build_rule_task_plan(message) is None

    def test_positive_conditional_refund_still_creates_plan(self):
        assert build_rule_task_plan(
            "查下物流，如果没发货就申请退款",
        ) is not None


class TestSupervisorTaskPlan:
    def test_schedules_authoritative_query_before_action(self):
        cmd = cs_supervisor_node(_supervisor_state())

        assert cmd.goto == CS_QUERY_EXPERT
        assert cmd.update["current_task"]["task_id"] == "q1"
        assert cmd.update["task_cursor"] == 0

    def test_condition_true_schedules_proposal_task(self):
        query_result = CSTaskResult(
            task_id="q1", status="success",
            facts={"shipping_status": "not_shipped", "order_count": 1},
            source="sandbox_logistics_service",
        ).model_dump()
        cmd = cs_supervisor_node(_supervisor_state(
            task_cursor=1, task_results=[query_result],
        ))

        assert cmd.goto == CS_ACTION_EXPERT
        assert cmd.update["current_task"]["task_id"] == "a1"

    @pytest.mark.parametrize("status", ["shipped", "unknown"])
    def test_condition_false_or_unknown_skips_action(self, status):
        query_result = CSTaskResult(
            task_id="q1", status="success",
            facts={"shipping_status": status, "order_count": 1},
            source="sandbox_logistics_service",
        ).model_dump()
        cmd = cs_supervisor_node(_supervisor_state(
            task_cursor=1, task_results=[query_result],
        ))

        assert cmd.goto == CS_REPORTER
        assert cmd.update["task_results"][-1]["status"] == "skipped"
        assert cmd.update["task_results"][-1]["task_id"] == "a1"
        assert cmd.goto != CS_ACTION_EXPERT

    def test_failed_or_ambiguous_query_never_schedules_write(self):
        query_result = CSTaskResult(
            task_id="q1", status="needs_clarification",
            facts={"order_count": 2}, source="sandbox_business_service",
        ).model_dump()
        cmd = cs_supervisor_node(_supervisor_state(
            task_cursor=1, task_results=[query_result],
        ))

        assert cmd.goto == CS_REPORTER
        assert cmd.goto != CS_ACTION_EXPERT

    def test_human_takeover_gate_precedes_task_plan(self):
        cmd = cs_supervisor_node(_supervisor_state(
            handoff_state="human_active",
        ))

        assert cmd.goto == CS_REPORTER
        assert cmd.update["supervisor_decision"]["requires_handoff"] is True
        assert "current_task" not in cmd.update

    def test_risk_gate_precedes_task_plan(self, monkeypatch):
        from backend.config import customer_service as cs_config

        monkeypatch.setattr(cs_config, "CS_SIGNAL_GATE_ENABLED", True)
        cmd = cs_supervisor_node(_supervisor_state(
            cs_route={
                "intent": "t_logistics",
                "route_path": "business_query",
                "confidence": 0.99,
                "metadata": {"risk_hits": ["prompt_injection"]},
            },
        ))

        assert cmd.goto == CS_REPORTER
        assert cmd.update["supervisor_decision"]["is_finished"] is True
        assert "current_task" not in cmd.update
