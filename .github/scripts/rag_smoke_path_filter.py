#!/usr/bin/env python3
"""RAG Smoke 路径相关性判定；Workflow 与回归测试共用本模块。

路径列表（每行一个仓库根相对路径）从 stdin 输入；输出 relevant=true|false。
SQL migration 只在涉及 RAG、模型计费或其文档/记忆契约时触发真实评测。
"""
from __future__ import annotations

import argparse
import sys

EXACT_ENTRIES = frozenset({
    "pyproject.toml",
    "requirements-lock.txt",
    "backend/app/api/routes/_rag_shared.py",
    "backend/core/tool_runtime/policy.py",
    "backend/shared/crypto.py",
    "backend/services/rag_server.py",
    "backend/infra/mcp_client.py",
    "mcp_servers/servers/rag.py",
    "backend/scripts/ingest_eval_fixtures.py",
    "backend/observability/llm_usage_store.py",
    "backend/observability/llm_usage_store_pg.py",
    "backend/observability/usage_parse.py",
    "backend/tasks/budget_reconciliation_tasks.py",
    "backend/app/api/routes/budgets.py",
    "backend/services/model_config.py",
    "backend/app/api/routes/model_config.py",
})

# 非 RAG 的模型价格巡检路径。它们分别命中通用 LLM 前缀和迁移中的
# `model` 标记，但不改变检索或模型调用计费行为。
NON_RAG_EXACT_ENTRIES = frozenset({
    "backend/infra/llm/price_page_monitor.py",
    "backend/sql/migrations/099_model_price_source_monitor.sql",
})

# 评测准备文件和 Workflow 的 PR 变化运行免费预检，main 更新不触发真实 Provider。
PR_STATIC_ONLY_ENTRIES = frozenset({
    "scripts/init_db.py",
    ".github/workflows/rag_smoke.yml",
    ".github/workflows/rag_regression.yml",
    ".github/scripts/rag_smoke_path_filter.py",
    "backend/scripts/seed_prompt_eval_db.py",
    "scripts/seed_eval_model_bindings.py",
    "tests/evaluation/test_rag_ci_workflow_contracts.py",
    "tests/test_rag_smoke_path_filter.py",
})
PR_STATIC_ONLY_PREFIXES = ("backend/sql/migrations/",)
PREFIX_ENTRIES = (
    "backend/rag/",
    "backend/evaluation/",
    "backend/infra/llm/",
    "backend/config/",
    "backend/orchestration/graph/",
    "backend/prompts/",
    "data/docs/",
    "backend/app/api/routes/rag",
)

# 付费门禁涉及模型请求/费用/检索契约的迁移采用精确清单；新增迁移需在
# PR 免费契约测试中明确归类，避免模糊关键词漏掉账本基础表或误触发付费评测。
PAID_RELEVANT_MIGRATION_ENTRIES = frozenset({
    "backend/sql/migrations/010_doc_registry_pg.sql",
    "backend/sql/migrations/011_doc_registry_version_governance.sql",
    "backend/sql/migrations/013_obs_analytics_pg.sql",
    "backend/sql/migrations/014_rag_stores_pg.sql",
    "backend/sql/migrations/018_prompts_pg.sql",
    "backend/sql/migrations/020_alembic_gaps_pg.sql",
    "backend/sql/migrations/024_rag_eval_fixture_set.sql",
    "backend/sql/migrations/025_metadata_rule_governance.sql",
    "backend/sql/migrations/027_rag_processing_lineage.sql",
    "backend/sql/migrations/031_model_governance_pg.sql",
    "backend/sql/migrations/032_memory_embedding_vector.sql",
    "backend/sql/migrations/034_model_runtime_governance_pg.sql",
    "backend/sql/migrations/035_llm_usage_cost_columns.sql",
    "backend/sql/migrations/038_llm_models_upstream_name.sql",
    "backend/sql/migrations/039_llm_models_ocr_kind.sql",
    "backend/sql/migrations/045_llm_models_governance_columns.sql",
    "backend/sql/migrations/046_llm_usage_identity_billing.sql",
    "backend/sql/migrations/047_memory_provenance.sql",
    "backend/sql/migrations/048_memory_scope_and_versioning.sql",
    "backend/sql/migrations/053_model_price_immutable_trigger.sql",
    "backend/sql/migrations/055_llm_usage_attribution.sql",
    "backend/sql/migrations/056_eval_run_records.sql",
    "backend/sql/migrations/057_prompt_alias.sql",
    "backend/sql/migrations/063_release_records.sql",
    "backend/sql/migrations/064_trace_summary_ttft.sql",
    "backend/sql/migrations/065_prompt_runtime_hot_reload.sql",
    "backend/sql/migrations/066_budget_cny_identity_needs_review.sql",
    "backend/sql/migrations/067_rag_candidate_publish.sql",
    "backend/sql/migrations/069_prompt_release_records.sql",
    "backend/sql/migrations/071_rag_reconcile_reports.sql",
    "backend/sql/migrations/074_eval_run_lifecycle.sql",
    "backend/sql/migrations/075_eval_run_samples.sql",
    "backend/sql/migrations/078_eval_run_validity.sql",
    "backend/sql/migrations/079_llm_usage_billing_v2.sql",
    "backend/sql/migrations/087_llm_provider_extra_body.sql",
    "backend/sql/migrations/088_memory_profile_scope_verification.sql",
    "backend/sql/migrations/089_memory_extraction_outbox.sql",
    "backend/sql/migrations/090_memory_turn_idempotency.sql",
    "backend/sql/migrations/091_chat_message_langchain_metadata.sql",
    "backend/sql/migrations/092_chat_session_tenant_scope.sql",
    "backend/sql/migrations/093_revoke_readonly_default_password.sql",
    "backend/sql/migrations/097_llm_model_health_probe_source.sql",
    "backend/sql/migrations/105_rag_tenant_isolation.sql",
    "backend/sql/migrations/107_auth_knowledge_bases.sql",
    "backend/sql/migrations/108_evaluation_v2_domain.sql",
    "backend/sql/migrations/109_evaluation_v2_gate_approvals.sql",
    "backend/sql/migrations/110_evaluation_v2_human_reviews.sql",
    "backend/sql/migrations/111_evaluation_v2_case_revision_candidates.sql",
    "backend/sql/migrations/112_evaluation_v2_domain_runner.sql",
})

# 与模型调用 / 检索 / 计费无关的迁移：显式列出，禁止靠模糊关键词推断。
# 新增迁移必须加入本清单或 PAID_RELEVANT_MIGRATION_ENTRIES；两者都不在时
# 归类为 unknown —— PR 免费预检直接失败，main push 按受信任门禁处理（不静默跳过）。
NON_AI_MIGRATION_ENTRIES = frozenset({
    "backend/sql/migrations/001_business_warehouse.sql",
    "backend/sql/migrations/002_agent_memory_schema.sql",
    "backend/sql/migrations/003_agent_business_schema.sql",
    "backend/sql/migrations/003_agent_memory_seed.sql",
    "backend/sql/migrations/004_readonly_role.sql",
    "backend/sql/migrations/005_schema_hardening.sql",
    "backend/sql/migrations/006_customer_service.sql",
    "backend/sql/migrations/007_tool_approval.sql",
    "backend/sql/migrations/008_local_auth.sql",
    "backend/sql/migrations/009_auth_roles.sql",
    "backend/sql/migrations/012_obs_trace_store_pg.sql",
    "backend/sql/migrations/014_cs_rating.sql",
    "backend/sql/migrations/015_workflow_runs_pg.sql",
    "backend/sql/migrations/016_inventory_alerts_pg.sql",
    "backend/sql/migrations/017_business_stores_pg.sql",
    "backend/sql/migrations/019_gateway_access_logs_pg.sql",
    "backend/sql/migrations/021_selection_funnel_pg.sql",
    "backend/sql/migrations/022_feedback_review_candidates.sql",
    "backend/sql/migrations/023_auth_sessions.sql",
    "backend/sql/migrations/026_metadata_shadow_jobs.sql",
    "backend/sql/migrations/028_cs_dispatch.sql",
    "backend/sql/migrations/029_rbac_audit.sql",
    "backend/sql/migrations/030_cs_dispatch_hardening.sql",
    "backend/sql/migrations/033_auth_user_lifecycle.sql",
    "backend/sql/migrations/036_cs_tickets.sql",
    "backend/sql/migrations/037_cs_qa_reports.sql",
    "backend/sql/migrations/040_chat_sessions_summary_frontier.sql",
    "backend/sql/migrations/041_auth_departments.sql",
    "backend/sql/migrations/042_sql_query_audits.sql",
    "backend/sql/migrations/043_readonly_public_revoke.sql",
    "backend/sql/migrations/044_chat_sessions_summary_version.sql",
    "backend/sql/migrations/047_side_effect_idempotency.sql",
    "backend/sql/migrations/049_task_pending_recovery.sql",
    "backend/sql/migrations/050_agent_actions_status_enum.sql",
    "backend/sql/migrations/051_cs_business_operation_guard.sql",
    "backend/sql/migrations/052_travel_booking.sql",
    "backend/sql/migrations/054_auth_super_admin.sql",
    "backend/sql/migrations/058_cs_case.sql",
    "backend/sql/migrations/059_security_events.sql",
    "backend/sql/migrations/060_task_operation_audits.sql",
    "backend/sql/migrations/062_tool_contract_changes.sql",
    "backend/sql/migrations/068_auth_session_client_app.sql",
    "backend/sql/migrations/070_unanswered_questions.sql",
    "backend/sql/migrations/071_clarify_funnel_events.sql",
    "backend/sql/migrations/072_travel_decision_audit.sql",
    "backend/sql/migrations/073_travel_decision_idempotency.sql",
    "backend/sql/migrations/076_cs_faq_tables.sql",
    "backend/sql/migrations/077_sql_query_audits_sql_text.sql",
    "backend/sql/migrations/078_cs_handoff_state_alignment.sql",
    "backend/sql/migrations/080_question_ledger.sql",
    "backend/sql/migrations/081_auth_user_phone.sql",
    "backend/sql/migrations/082_cs_confirmation_versioned_claim.sql",
    "backend/sql/migrations/082_travel_plan_tenant_scope.sql",
    "backend/sql/migrations/083_readonly_ai_grant_scope.sql",
    "backend/sql/migrations/084_travel_v2_schema.sql",
    "backend/sql/migrations/085_drop_legacy_travel_planning.sql",
    "backend/sql/migrations/086_travel_v2_arrangements.sql",
    "backend/sql/migrations/094_travel_v2_ai_edit_requests.sql",
    "backend/sql/migrations/095_travel_v2_ai_edit_pending_resolution.sql",
    "backend/sql/migrations/096_travel_v2_revision_shares.sql",
    "backend/sql/migrations/098_cs_refund_order_unique.sql",
    "backend/sql/migrations/100_model_price_change_alert_outbox.sql",
    "backend/sql/migrations/101_travel_v2_trip_conversations.sql",
    "backend/sql/migrations/102_rbac_department_audits.sql",
    "backend/sql/migrations/103_auth_department_version.sql",
    "backend/sql/migrations/104_rbac_department_type_manager.sql",
    "backend/sql/migrations/106_approval_tenant_isolation.sql",
    "backend/sql/migrations/113_security_events_admin_action.sql",
    "backend/sql/migrations/114_mcp_capability_center.sql",
    "backend/sql/migrations/115_http_api_tools.sql",
    "backend/sql/migrations/116_capability_bundles.sql",
    "backend/sql/migrations/117_governance_releases.sql",
    "backend/sql/migrations/118_mcp_health_monitor.sql",
    "backend/sql/migrations/119_tool_discovery_policy.sql",
    "backend/sql/migrations/120_tool_semantic_contract.sql",
    "backend/sql/migrations/121_tool_failure_analysis.sql",
    "backend/sql/migrations/122_task_result_refs.sql",
    "backend/sql/migrations/123_user_automations.sql",
    "backend/sql/migrations/124_plan_revisions.sql",
    # 商城演示钱包账本（cs-workstation-v2 P2，finance schema）：非 RAG 业务迁移，
    # init_db.py 已同步登记为 business——漏登记会让迁移守卫把 PR 免费预检判死
    # （v3 run 41151145995 repo-tests 实证）
    "backend/sql/migrations/125_mall_wallet.sql",
    # 客服坐席状态/SLA 分级列（cs-workstation-v2 P3，customer_service schema）：
    # 非 RAG 业务迁移——漏登记会让迁移守卫把 PR 免费预检判死（PR #164 实证）
    "backend/sql/migrations/126_cs_agent_status_sla.sql",
    # 坐席快捷回复话术表（cs-workstation-v2 P4，customer_service schema）
    "backend/sql/migrations/127_cs_canned_responses.sql",
})


def classify_migration(path: str) -> str:
    """把一条迁移路径分类为 paid / non_ai / unknown（只认显式清单）。"""
    normalized = _normalize(path)
    if normalized in PAID_RELEVANT_MIGRATION_ENTRIES:
        return "paid"
    if normalized in NON_AI_MIGRATION_ENTRIES or normalized in NON_RAG_EXACT_ENTRIES:
        return "non_ai"
    return "unknown"


def unknown_migrations(paths: list[str]) -> list[str]:
    """返回未分类的迁移文件（按输入顺序去重）。"""
    unknown: list[str] = []
    for path in paths:
        normalized = _normalize(path)
        if normalized.startswith("backend/sql/migrations/") and classify_migration(normalized) == "unknown":
            if normalized not in unknown:
                unknown.append(normalized)
    return unknown


def _normalize(path: str) -> str:
    return path.strip().replace("\\", "/")


def _is_relevant_migration(path: str) -> bool:
    return path in PAID_RELEVANT_MIGRATION_ENTRIES


def is_relevant(path: str) -> bool:
    path = _normalize(path)
    if not path:
        return False
    if path in NON_RAG_EXACT_ENTRIES:
        return False
    if path in EXACT_ENTRIES:
        return True
    if _is_relevant_migration(path):
        return True
    return any(path.startswith(prefix) for prefix in PREFIX_ENTRIES)


def is_pr_static_only(path: str) -> bool:
    path = _normalize(path)
    return path in PR_STATIC_ONLY_ENTRIES or any(
        path.startswith(prefix) for prefix in PR_STATIC_ONLY_PREFIXES
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", choices=("push", "pull_request"), default="push")
    args = parser.parse_args(argv)
    paths = list(sys.stdin)
    unknown = unknown_migrations(paths)
    relevant = any(
        is_relevant(line)
        or (args.event == "pull_request" and is_pr_static_only(line))
        for line in paths
    )
    if unknown and args.event == "pull_request":
        # 完整性检查：新迁移必须显式分类，未分类时 PR 免费预检失败（可操作报错），
        # 避免「改个迁移名就绕过付费/免费门禁」。
        print(
            "未分类迁移：请在 .github/scripts/rag_smoke_path_filter.py 中把下列迁移加入",
            "PAID_RELEVANT_MIGRATION_ENTRIES（影响 RAG/模型/费用/预算）或",
            "NON_AI_MIGRATION_ENTRIES（与模型调用无关）：",
            file=sys.stderr,
        )
        for item in unknown:
            print("  - " + item, file=sys.stderr)
        return 2
    if unknown:
        # main push：未分类迁移不得被静默跳过，按受信任门禁处理；
        # 付费步骤自身的批准价格门禁仍然失败关闭。
        relevant = True
    print("relevant=" + str(relevant).lower())
    return 0


if __name__ == "__main__":
    sys.exit(main())
