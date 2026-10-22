"""旅游 Planner 的 LLM 候选契约、硬约束和回退行为。"""
from __future__ import annotations

import json
from types import SimpleNamespace

from backend.travel.services import llm_planning_service


def _enable_planner_llm(monkeypatch, content: str):
    from backend.config import model_roles
    from backend.config import travel as travel_config

    monkeypatch.setattr(travel_config, "TRAVEL_PLANNING_LLM_ENABLED", True)
    monkeypatch.setattr(model_roles, "resolve_effective", lambda _role: {
        "role": "main", "value": "test-main-model",
    })
    calls = []
    bindings = []
    response = SimpleNamespace(content=content)
    fake_llm = SimpleNamespace(
        invoke=lambda messages: calls.append(messages) or response,
    )
    monkeypatch.setattr(
        "backend.infra.llm.proxy.bind_llm_for_role",
        lambda role, **kwargs: bindings.append((role, kwargs)) or fake_llm,
    )
    monkeypatch.setattr(
        "backend.infra.async_utils.sync_call_with_timeout",
        lambda call, _timeout: call(),
    )
    monkeypatch.setattr(
        "backend.prompts.service.prompt_service.render_sync",
        lambda _key: SimpleNamespace(text="受约束的 Planner Prompt", version=3),
    )
    return calls, bindings


def test_proposal_uses_governed_model_role_and_strict_json(monkeypatch):
    calls, bindings = _enable_planner_llm(
        monkeypatch,
        json.dumps({"days": [["poi-a"], ["poi-b"]], "dropped_poi_ids": []}),
    )

    outcome = llm_planning_service.propose_day_plan({
        "brief": {"destination": "福州", "days": 2},
        "candidates": [{"poi_id": "poi-a"}, {"poi_id": "poi-b"}],
    })

    assert outcome.status == "ok"
    assert outcome.proposal.days == [["poi-a"], ["poi-b"]]
    assert outcome.prompt_version == "travel.planning_agent@3"
    assert len(calls) == 1
    assert bindings == [("main", {"temperature": 0, "max_tokens": 3000})]


def test_unapproved_fields_are_rejected_and_fall_back(monkeypatch):
    _enable_planner_llm(
        monkeypatch,
        '{"days":[["poi-a"]],"dropped_poi_ids":[],"tool":"delete_trip"}',
    )

    outcome = llm_planning_service.propose_day_plan({
        "brief": {"destination": "福州", "days": 1},
        "candidates": [{"poi_id": "poi-a"}],
    })

    assert outcome.proposal is None
    assert outcome.status == "schema_invalid"
    assert outcome.fallback_reason == "schema_validation_failed"


def _candidates(count: int):
    from backend.travel.models.poi import Poi

    return [
        Poi(
            poi_id=f"poi-{index}", name=f"景点{index}", city="福州",
            lat=26.0 + index * 0.01, lng=119.3 + index * 0.01,
            suggested_minutes=90, rating=5 - index * 0.1,
        )
        for index in range(count)
    ]


def test_planning_agent_applies_valid_llm_day_assignments(monkeypatch):
    from backend.travel.agents.planning_agent import PlanningAgent
    from backend.travel.models.brief import TravelBrief

    proposal = llm_planning_service.TravelDayPlanProposal(
        days=[["poi-0", "poi-2"], ["poi-1", "poi-3"]],
    )
    monkeypatch.setattr(
        llm_planning_service, "propose_day_plan",
        lambda _context: llm_planning_service.PlanningProposalOutcome(
            proposal=proposal, status="ok", fallback_reason="",
            model="test-model", prompt_version="travel.planning_agent@3",
        ),
    )

    result = PlanningAgent().build_skeleton(
        TravelBrief(destination="福州", days=2), _candidates(4),
    )

    assert [set(poi.poi_id for poi in day) for day in result.days] == [
        {"poi-0", "poi-2"}, {"poi-1", "poi-3"},
    ]
    assert result.planner_decision["status"] == "ok"
    assert result.planner_decision["model"] == "test-model"


def test_planner_rejects_missing_required_poi_and_uses_rule_skeleton(monkeypatch):
    from backend.travel.agents.planning_agent import PlanningAgent
    from backend.travel.models.brief import TravelBrief
    from backend.travel.services import poi_service

    candidates = _candidates(3)
    candidates[0] = candidates[0].model_copy(update={"required": True})
    proposal = llm_planning_service.TravelDayPlanProposal(
        days=[["poi-1", "poi-2"]], dropped_poi_ids=["poi-0"],
    )
    monkeypatch.setattr(
        llm_planning_service, "propose_day_plan",
        lambda _context: llm_planning_service.PlanningProposalOutcome(
            proposal=proposal, status="ok", fallback_reason="",
        ),
    )
    brief = TravelBrief(destination="福州", days=1)

    result = PlanningAgent().build_skeleton(brief, candidates)
    expected = poi_service.build_skeleton(brief, candidates)

    assert [p.poi_id for p in result.days[0]] == [p.poi_id for p in expected.days[0]]
    assert "poi-0" in {p.poi_id for p in result.days[0]}
    assert result.planner_decision["status"] == "rejected"
    assert result.planner_decision["fallback_reason"] == "required_poi_dropped"


def test_planner_rejects_daily_capacity_violation(monkeypatch):
    from backend.travel.agents.planning_agent import PlanningAgent
    from backend.travel.models.brief import TravelBrief

    candidates = _candidates(6)
    proposal = llm_planning_service.TravelDayPlanProposal(
        days=[[poi.poi_id for poi in candidates], []],
    )
    monkeypatch.setattr(
        llm_planning_service, "propose_day_plan",
        lambda _context: llm_planning_service.PlanningProposalOutcome(
            proposal=proposal, status="ok", fallback_reason="",
        ),
    )

    result = PlanningAgent().build_skeleton(
        TravelBrief(destination="福州", days=2), candidates,
    )

    assert result.planner_decision["status"] == "rejected"
    assert result.planner_decision["fallback_reason"] == "daily_poi_capacity_exceeded"
    assert sum(len(day) for day in result.days) == 6
