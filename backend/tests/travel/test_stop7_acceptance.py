"""STOP 7：旅游 Trace 语义投影契约。"""
from __future__ import annotations

from backend.travel.trace_semantics import build_trace_semantics


def test_social_trace_is_non_planning_and_has_stable_scope_fields():
    got = build_trace_semantics({
        "conversation_id": "conv-social",
        "intent": "social",
        "brief": {},
        "expert_history": [],
    }, {"status": "answered", "intent": "social"})
    assert got["conversation_id"] == "conv-social"
    assert got["planning_mode"] == "social"
    assert got["full_replan"] is False
    assert got["partial_replan"] is False
    assert got["tool_count"] == 0
    assert got["modification_operation"] == ""


def test_partial_trace_contains_versions_fingerprints_and_changed_days():
    base = {
        "plan_version": 4,
        "brief": {"destination": "杭州", "days": 3},
    }
    current = {
        "plan_version": 5,
        "brief": {"destination": "杭州", "days": 3},
    }
    got = build_trace_semantics({
        "conversation_id": "conv-modify",
        "intent": "modify",
        "brief": current["brief"],
        "itinerary": current,
        "plan_stability_baseline": {
            "itinerary": base,
            "changed_fields": ["partial:add_poi"],
        },
        "partial_replan": {"operation": "add_poi", "target_day": 2},
        "partial_replan_result": {
            "partial_replan": True, "changed_days": [2],
            "operation": "add_poi", "validation_failed": False,
        },
        "changed_days": [2],
        "expert_history": [{"expert": "partial_replan", "status": "applied"}],
    }, {"status": "success", "itinerary": current})
    assert got["conversation_id"] == "conv-modify"
    assert got["planning_mode"] == "modify"
    assert got["base_plan_version"] == 4
    assert got["active_plan_version"] == 4
    assert got["draft_plan_version"] == 5
    assert got["base_brief_fingerprint"]
    assert got["candidate_brief_fingerprint"]
    assert got["semantic_change"] is False
    assert got["modification_operation"] == "add_poi"
    assert got["modified_days"] == [2]
    assert got["full_replan"] is False
    assert got["partial_replan"] is True
    assert got["tool_count"] == 1


def test_query_trace_never_claims_a_full_replan():
    got = build_trace_semantics({
        "conversation_id": "conv-query",
        "intent": "query_static",
        "itinerary": {"plan_version": 8, "brief": {"destination": "杭州"}},
        "expert_history": [{"expert": "poi"}],
    }, {"intent": "query_static", "status": "answered"})
    assert got["planning_mode"] == "query"
    assert got["full_replan"] is False
    assert got["partial_replan"] is False
    assert got["tool_count"] == 0
    assert got["draft_plan_version"] == ""


def test_new_plan_trace_is_the_only_full_replan_mode():
    got = build_trace_semantics({
        "conversation_id": "conv-new-plan",
        "intent": "plan",
        "itinerary": {"plan_version": 1, "brief": {
            "destination": "杭州", "days": 2,
        }},
        "brief": {"destination": "杭州", "days": 2},
        "expert_history": [{"expert": "poi"}, {"expert": "transit"}],
    }, {"intent": "plan", "status": "success"})
    assert got["planning_mode"] == "plan"
    assert got["full_replan"] is True
    assert got["partial_replan"] is False
    assert got["draft_plan_version"] == 1


def test_trace_contains_task_scope_and_reporter_attribution_without_raw_content():
    got = build_trace_semantics({
        "intent": "modify",
        "scope_expansion_reason": "需要同步调整交通",
        "turn_decision": {
            "primary_action": "modify_plan",
            "additional_tasks": [{"task_id": "train-1", "type": "query_train"}],
        },
        "task_results": [{
            "task_id": "train-1", "type": "query_train", "status": "success",
            "data_status": "verified", "result_count": 2, "duration_ms": 21,
            "message": "private detail must not enter Trace",
        }],
        "reporter_meta": {
            "source": "llm", "model": "main-test",
            "prompt_version": "travel.reporter@3", "prompt_source": "snapshot",
            "latency_ms": 42, "input_tokens": 31, "output_tokens": 12,
        },
    })

    assert got["primary_action"] == "modify_plan"
    assert '"task_id":"train-1"' in got["task_plan_summary"]
    assert '"result_count":2' in got["task_result_summary"]
    assert got["scope_expansion_reason"] == "需要同步调整交通"
    assert got["reporter_source"] == "llm"
    assert got["reporter_prompt_version"] == "travel.reporter@3"
    assert got["reporter_input_tokens"] == 31
    assert "private detail" not in str(got)
