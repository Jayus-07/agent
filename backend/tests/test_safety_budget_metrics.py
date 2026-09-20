"""WP6 新增安全/预算指标的契约与标签白名单回归。"""

from backend.observability import metrics


EXPECTED = {
    "idempotency_claim_total": {"operation", "result"},
    "idempotency_execution_total": {"operation", "result"},
    "budget_request_total": {"mode", "result"},
    "budget_quota_total": {"scope_type", "period_type", "result"},
    "budget_threshold_total": {"scope_type", "period_type", "threshold"},
    "budget_price_total": {"component", "result"},
    "side_effect_budget_total": {"result"},
    "semantic_validation_total": {"layer", "result"},
    "feedback_candidate_total": {"action", "result"},
}

FORBIDDEN_LABEL_PARTS = (
    "user", "tenant", "trace", "request", "idempot", "key",
)


def test_new_safety_budget_metrics_have_fixed_safe_labels():
    collectors = {
        name: getattr(metrics, name)
        for name in EXPECTED
    }
    assert set(collectors) == set(EXPECTED)
    for name, collector in collectors.items():
        label_names = set(collector._labelnames)
        assert label_names == EXPECTED[name]
        assert not any(
            any(part in label.lower() for part in FORBIDDEN_LABEL_PARTS)
            for label in label_names
        ), name


def test_new_safety_budget_metrics_are_scrapable():
    metrics.idempotency_claim_total.labels(
        operation="email.send", result="new"
    ).inc()
    metrics.budget_threshold_total.labels(
        scope_type="tenant", period_type="day", threshold="0.8000"
    ).inc()
    body, content_type = metrics.render_metrics()
    assert "text/plain" in content_type
    text = body.decode()
    assert "idempotency_claim_total" in text
    assert "budget_threshold_total" in text
