"""STOP D：域适配器统一结果协议测试。"""

from __future__ import annotations

from backend.orchestration.runtime_result_adapter import attach_runtime_result


def test_cs_output_normalizes_without_dropping_contract_fields():
    update = attach_runtime_result(
        {
            "final_answer": "客服已为你处理完成",
            "cs_context": {"handoff_state": "ai_active"},
        },
        runtime_id="customer_service",
        sources=[{"title": "订单记录"}],
        clarification={"question": "需要订单号"},
        handoff={"target_domain": "customer_service"},
        tool_calls=[{"tool_id": "order.query"}],
        metadata={"trace_id": "trace-1"},
    )

    result = update["runtime_result"]
    assert update["final_answer"] == "客服已为你处理完成"
    assert result["answer"] == "客服已为你处理完成"
    assert result["sources"] == [{"title": "订单记录"}]
    assert result["clarification"] == {"question": "需要订单号"}
    assert result["handoff"] == {"target_domain": "customer_service"}
    assert result["tool_calls"] == [{"tool_id": "order.query"}]
    assert result["metadata"] == {
        "trace_id": "trace-1",
        "runtime_id": "customer_service",
    }


def test_travel_output_keeps_structured_itinerary_in_ui_payload():
    update = attach_runtime_result(
        {
            "final_answer": "行程已生成",
            "travel_context": {
                "itinerary": {"days": [{"day": 1, "pois": ["鼓浪屿"]}]},
            },
        },
        runtime_id="travel",
        ui_payload={
            "itinerary": {"days": [{"day": 1, "pois": ["鼓浪屿"]}]},
            "validation": {"passed": True},
        },
    )

    result = update["runtime_result"]
    assert result["answer"] == "行程已生成"
    assert result["ui_payload"]["itinerary"]["days"][0]["pois"] == ["鼓浪屿"]
    assert result["ui_payload"]["validation"]["passed"] is True
    assert update["travel_context"]["itinerary"]["days"][0]["day"] == 1


def test_selection_output_keeps_report_and_metadata():
    update = attach_runtime_result(
        {
            "final_answer": "选品报告",
            "funnel_context": {"top": [{"title": "商品 A"}]},
        },
        runtime_id="selection_funnel",
        ui_payload={"funnel_context": {"top": [{"title": "商品 A"}]}},
        metadata={"run_id": "sel-1"},
    )

    result = update["runtime_result"]
    assert result["answer"] == "选品报告"
    assert result["ui_payload"]["funnel_context"]["top"][0]["title"] == "商品 A"
    assert result["metadata"] == {
        "run_id": "sel-1",
        "runtime_id": "selection_funnel",
    }


def test_result_normalization_is_pure_and_does_not_call_llm(monkeypatch):
    class _ExplodingLLM:
        def __getattr__(self, name):
            raise AssertionError(f"不应调用 LLM: {name}")

    monkeypatch.setattr("backend.infra.llm.llm", _ExplodingLLM())
    update = attach_runtime_result(
        {"final_answer": "纯适配结果"},
        runtime_id="travel",
    )
    assert update["runtime_result"]["answer"] == "纯适配结果"
