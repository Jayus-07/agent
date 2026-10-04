"""请求级 Prompt pin：快照切换时仍使用请求开始时的模板。"""
from __future__ import annotations

from backend.prompts.service import PromptService, _SnapshotEntry


def _service_with_snapshot() -> PromptService:
    service = PromptService()
    service._snapshot = {
        "customer_service.query_intent": _SnapshotEntry(
            template="old:{question}", version=1, variables=["question"]
        )
    }
    return service


def test_pin_snapshot_keeps_old_template_after_refresh():
    service = _service_with_snapshot()

    with service.pin_snapshot() as versions:
        assert versions["customer_service.query_intent"] == 1
        service._snapshot["customer_service.query_intent"] = _SnapshotEntry(
            template="new:{question}", version=2, variables=["question"]
        )
        rendered = service.render_sync(
            "customer_service.query_intent", question="hello"
        )

    assert rendered.text == "old:hello"
    assert rendered.version == 1


def test_bind_prompt_versions_uses_historical_template():
    service = _service_with_snapshot()
    service._snapshot_history[("customer_service.query_intent", 1)] = service._snapshot[
        "customer_service.query_intent"
    ]
    service._snapshot["customer_service.query_intent"] = _SnapshotEntry(
        template="new:{question}", version=2, variables=["question"]
    )

    with service.bind_prompt_versions({"customer_service.query_intent": 1}):
        rendered = service.render_sync(
            "customer_service.query_intent", question="hello"
        )

    assert rendered.text == "old:hello"
    assert rendered.version == 1
