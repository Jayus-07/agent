from backend.infra.llm import budget as budget_module
from backend.infra.llm.budget import RequestBudgetLimits
from backend.tasks import model_health_tasks


def test_scheduled_health_scan_has_isolated_system_budget(monkeypatch):
    monkeypatch.setattr(budget_module, "_config_mode", lambda: "enforce")
    monkeypatch.setattr(
        budget_module,
        "_config_limits",
        lambda: RequestBudgetLimits(
            max_calls=8,
            max_total_tokens=100,
            max_cost=0.5,
            max_retries=1,
            max_fallbacks=1,
        ),
    )
    observed = {}

    def scan():
        state = budget_module.current_request_budget()
        observed["state"] = state
        observed["request_id"] = budget_module.current_request_id()
        return {"healthy": 1}

    result = model_health_tasks._run_with_system_probe_budget(scan)

    assert result == {"healthy": 1}
    assert observed["state"] is not None
    assert observed["state"].mode == "enforce"
    assert observed["state"].user_id == ""
    assert observed["state"].tenant_id == ""
    assert observed["request_id"].startswith("model-health:")
    assert budget_module.current_request_budget() is None
