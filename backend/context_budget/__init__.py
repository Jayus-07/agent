"""context_budget — 上下文预算管理（基础版 L1/L2/L3 + Preflight）

统一管理发送给模型的 active context 的 token 预算；原始 chat_messages
不受影响。完整设计见 docs/2026-09-22-context-budget-management-实施规格.md。

用法：
    from backend.context_budget import context_budget, guard_tool_result

    # L1：工具结果写入 step_results 前
    output = guard_tool_result(output, capability=cap, step_id=step_id)

    # L3：previous_outputs 注入下一 Skill prompt 前
    previous_outputs = compact_previous_outputs(previous_outputs, meta=...)

    # Preflight：LLM 调用前统一检查
    prepared = context_budget.prepare_llm_context(
        messages=messages, previous_outputs=po, rag_context=rag)
"""

from backend.context_budget.auto_compact import (
    ProtectedFact,
    SyncMemorySummaryStore,
    SummaryOutcome,
    extract_protected_facts,
    fold_rebuild,
    run_incremental_summary,
    validate_and_patch,
)
from backend.context_budget.collapse import (
    ContextFold,
    FoldRegistry,
    build_projection_text,
    fold_messages,
)
from backend.context_budget.manager import ContextBudgetManager, context_budget
from backend.context_budget.micro_compactor import compact_previous_outputs
from backend.context_budget.models import (
    ArtifactStore,
    ContextUsage,
    PreparedContext,
    ToolResultRef,
)
from backend.context_budget.tool_guard import (
    guard_tool_result,
    is_compacted_preview,
    serialize_for_count,
    truncate_text_to_tokens,
)

__all__ = [
    "ContextBudgetManager",
    "context_budget",
    "ContextUsage",
    "PreparedContext",
    "ToolResultRef",
    "ArtifactStore",
    "ProtectedFact",
    "SyncMemorySummaryStore",
    "SummaryOutcome",
    "extract_protected_facts",
    "fold_rebuild",
    "run_incremental_summary",
    "validate_and_patch",
    "ContextFold",
    "FoldRegistry",
    "build_projection_text",
    "fold_messages",
    "guard_tool_result",
    "compact_previous_outputs",
    "is_compacted_preview",
    "serialize_for_count",
    "truncate_text_to_tokens",
]
