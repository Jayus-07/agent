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
})


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
    relevant = any(
        is_relevant(line)
        or (args.event == "pull_request" and is_pr_static_only(line))
        for line in sys.stdin
    )
    print("relevant=" + str(relevant).lower())
    return 0


if __name__ == "__main__":
    sys.exit(main())
