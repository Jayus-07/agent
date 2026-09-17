# -*- coding: utf-8 -*-
"""给 12 个存储壳类插入 __new__ 分发（无条件返回 PG 实现）。幂等。"""
import io
import re
import sys

SHELLS = [
    ("backend/observability/trace_store.py", "TraceStore",
     "backend.observability.trace_store_pg", "PostgresTraceStore"),
    ("backend/observability/analytics_store.py", "AnalyticsStore",
     "backend.observability.analytics_store_pg", "PostgresAnalyticsStore"),
    ("backend/observability/llm_usage_store.py", "LLMUsageStore",
     "backend.observability.llm_usage_store_pg", "PostgresLLMUsageStore"),
    ("backend/orchestration/workflow/persistence.py", "WorkflowRunStore",
     "backend.orchestration.workflow.persistence_pg", "PostgresWorkflowRunStore"),
    ("backend/orchestration/inventory/store.py", "InventoryStore",
     "backend.orchestration.inventory.store_pg", "PostgresInventoryStore"),
    ("backend/selection/store.py", "SelectionStore",
     "backend.selection.store_pg", "PostgresSelectionStore"),
    ("backend/selection_decision/store.py", "SelectionDecisionStore",
     "backend.selection_decision.store_pg", "PostgresSelectionDecisionStore"),
    ("backend/market_research/store.py", "MarketResearchStore",
     "backend.market_research.store_pg", "PostgresMarketResearchStore"),
    ("backend/competitor/store.py", "CompetitorStore",
     "backend.competitor.store_pg", "PostgresCompetitorStore"),
    ("backend/rag/indexing/chunk_store.py", "ChunkStore",
     "backend.rag.indexing.chunk_store_pg", "PostgresChunkStore"),
    ("backend/rag/indexing/operation_log.py", "DocumentOperationLogger",
     "backend.rag.indexing.operation_log_pg", "PostgresDocumentOperationLogger"),
    ("backend/rag/preprocessing/keyword_store.py", "KeywordRuleStore",
     "backend.rag.preprocessing.keyword_store_pg", "PostgresKeywordRuleStore"),
]


def method_text(pg_module: str, pg_class: str) -> str:
    return (
        "\n"
        "    def __new__(cls, *args, **kwargs):\n"
        "        # 2026-09-17 SQLite 轨删除：无条件返回 PG 实现"
        "（db_path 等参数兼容保留，表名由 env 决定）。\n"
        f"        from {pg_module} import {pg_class}\n"
        f"        return super().__new__({pg_class})\n"
    )


def find_insert_pos(lines, class_name):
    """返回插入行号（0-based，插在该行之前）。定位 class 行 -> 跳过 docstring。"""
    class_idx = None
    pat = re.compile(r"^class\s+" + re.escape(class_name) + r"\b")
    for i, ln in enumerate(lines):
        if pat.match(ln):
            class_idx = i
            break
    if class_idx is None:
        return None
    pos = class_idx + 1
    # 跳过紧随的 docstring（单行或三引号块）
    if pos < len(lines) and lines[pos].lstrip().startswith(('"""', "'''")):
        quote = lines[pos].lstrip()[:3]
        if lines[pos].strip().endswith(quote) and len(lines[pos].strip()) > 6:
            return pos + 1  # 单行 docstring
        pos += 1
        while pos < len(lines) and quote not in lines[pos]:
            pos += 1
        return pos + 1
    # 无 docstring：跳过紧跟的注释/空行，插在第一个实体行前
    return pos


def process(path, class_name, pg_module, pg_class):
    src = io.open(path, encoding="utf-8").read()
    if "__new__" in src:
        print(f"SKIP (has __new__): {path}")
        return True
    lines = src.splitlines(keepends=True)
    pos = find_insert_pos(lines, class_name)
    if pos is None:
        print(f"FAIL class not found: {path} :: {class_name}")
        return False
    snippet = method_text(pg_module, pg_class)
    if not snippet.endswith("\n"):
        snippet += "\n"
    lines.insert(pos, snippet)
    out = "".join(lines)
    import ast

    ast.parse(out)  # 语法校验
    io.open(path, "w", encoding="utf-8", newline="").write(out)
    print(f"OK: {path} :: {class_name} (line {pos + 1})")
    return True


def main():
    ok = all(process(*spec) for spec in SHELLS)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
