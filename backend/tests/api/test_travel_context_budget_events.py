from backend.context_budget.metrics import (
    ContextEventScope,
    emit_context_event,
)
from backend.app.api.routes.travel import _travel_context_budget_event_scope


def test_travel_context_budget_events_are_request_scoped_and_allowlisted():
    events = []
    scope = ContextEventScope(
        product_id="travel",
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="trip-a",
        request_id="run-a",
    )

    with _travel_context_budget_event_scope(
        tenant_id=scope.tenant_id,
        user_id=scope.user_id,
        session_id=scope.session_id,
        request_id=scope.request_id,
        queue_event=events.append,
    ):
        emit_context_event(
            level="L4",
            action="collapse",
            before_tokens=100,
            after_tokens=60,
            question="行程对话原文不应发送",
        )
        emit_context_event(
            level="L4",
            action="collapse",
            before_tokens=200,
            after_tokens=100,
            event_scope=ContextEventScope(
                product_id="customer_service",
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                session_id=scope.session_id,
                request_id=scope.request_id,
            ),
        )

    assert len(events) == 1
    assert events[0]["event"] == "context"
    assert events[0]["source"] == "context_budget"
    assert events[0]["saved_tokens"] == 40
    assert "question" not in events[0]
    assert "tenant_id" not in events[0]
    assert "session_id" not in events[0]
