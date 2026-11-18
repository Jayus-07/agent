"""Token 看板的计价展示只接受可核验来源，并对历史费用单独估算。"""

from backend.observability.llm_usage_store_pg import (
    token_dashboard_cost_sql_expr,
    token_dashboard_historical_estimate_sql_expr,
    token_dashboard_classified_call_predicate,
    token_dashboard_local_billing_predicate,
    token_dashboard_subscription_billing_predicate,
    token_dashboard_unknown_call_predicate,
    token_dashboard_verified_cost_predicate,
)


def test_only_v2_approved_or_local_rows_are_trusted_as_recorded_cost():
    predicate = token_dashboard_verified_cost_predicate("usage_row")

    assert "usage_row.billing_schema_version, 0) >= 2" in predicate
    assert "usage_row.cost_status, '') IN ('exact', 'estimated')" in predicate
    assert "'approved_price_table', 'approved_table', 'local_inference'" in predicate
    assert "registry_fallback" not in predicate


def test_historical_estimates_require_supplier_price_and_matching_usage():
    estimate = token_dashboard_historical_estimate_sql_expr("usage_row")

    assert "volcengine_ark_doubao_seed_2_0_mini" in estimate
    assert "observed.currency = 'CNY'" in estimate
    assert "observed.observed_at <= usage_row.ts::timestamptz" in estimate
    assert "usage_row.prompt_tokens <= 32000" in estimate
    assert "price.effective_from <= usage_row.ts::timestamptz" in estimate
    assert "usage_row.provider = 'custom-api'" in estimate
    assert "usage_row.model = 'qwen-vl-ocr'" in estimate
    assert "THEN NULL" in estimate


def test_plan_endpoint_usage_is_separated_from_token_price_estimates():
    plan = token_dashboard_subscription_billing_predicate("usage_row")
    estimate = token_dashboard_historical_estimate_sql_expr("usage_row")

    assert "usage_row.pricing_source, '') = 'subscription_plan'" in plan
    assert "plan_config.object_key = usage_row.provider" in plan
    assert "plan_config.changed_at <= (usage_row.ts)::timestamptz" in plan
    assert "baseUrl" in plan and "/api/plan" in plan
    assert "ILIKE '%%/api/plan%%'" in plan
    assert "COALESCE(usage_row.total_tokens, 0) > 0" in plan
    assert plan in estimate
    assert plan in token_dashboard_classified_call_predicate("usage_row")


def test_subscription_plan_wins_over_stale_verified_token_cost():
    plan = token_dashboard_subscription_billing_predicate("usage_row")
    verified = token_dashboard_verified_cost_predicate("usage_row")
    amount = token_dashboard_cost_sql_expr("usage_row")

    plan_guard = f"WHEN ({plan}) THEN NULL"
    verified_amount = f"WHEN ({verified}) THEN"
    assert amount.index(plan_guard) < amount.index(verified_amount)


def test_local_zero_cost_requires_provider_billing_history_at_event_time():
    local = token_dashboard_local_billing_predicate("usage_row")
    amount = token_dashboard_cost_sql_expr("usage_row")
    unknown = token_dashboard_unknown_call_predicate("usage_row")

    assert "local_config.object_key = usage_row.provider" in local
    assert "local_config.changed_at <= (usage_row.ts)::timestamptz" in local
    assert "later_config.changed_at <= (usage_row.ts)::timestamptz" in local
    assert "WHEN (" in amount and "ELSE 0 END" in amount
    assert "NOT (((COALESCE(usage_row.billing_schema_version" in unknown
