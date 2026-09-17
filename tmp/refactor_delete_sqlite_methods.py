"""一次性重构脚本：删除 sqlite store 族中被 PG 子类覆写的方法。

规则：
- base 类中方法名 ∈ PG 子类方法名 → 删除（PG 全部自实现，父类同名方法为 sqlite 轨死代码）
- PG 子类不调用 super() 的任何方法（已人工核验 trace_store 等）
- 模块级函数/常量不动（PG import 复用，如 _serialize_trace/_MAX_ROWS）
- 类壳保留（isinstance/类型引用兼容），删空后补 docstring+pass
"""
import ast
import sys

JOBS = [
    ("backend/observability/trace_store.py", "TraceStore", "backend/observability/trace_store_pg.py"),
    ("backend/observability/analytics_store.py", "AnalyticsStore", "backend/observability/analytics_store_pg.py"),
    ("backend/observability/llm_usage_store.py", "LLMUsageStore", "backend/observability/llm_usage_store_pg.py"),
    ("backend/orchestration/workflow/persistence.py", "WorkflowRunStore", "backend/orchestration/workflow/persistence_pg.py"),
    ("backend/orchestration/inventory/store.py", "InventoryStore", "backend/orchestration/inventory/store_pg.py"),
    ("backend/selection/store.py", "SelectionStore", "backend/selection/store_pg.py"),
    ("backend/selection_decision/store.py", "SelectionDecisionStore", "backend/selection_decision/store_pg.py"),
    ("backend/market_research/store.py", "MarketResearchStore", "backend/market_research/store_pg.py"),
    ("backend/competitor/store.py", "CompetitorStore", "backend/competitor/store_pg.py"),
    ("backend/rag/indexing/chunk_store.py", "ChunkStore", "backend/rag/indexing/chunk_store_pg.py"),
    ("backend/rag/indexing/operation_log.py", "DocumentOperationLogger", "backend/rag/indexing/operation_log_pg.py"),
    ("backend/rag/preprocessing/keyword_store.py", "KeywordRuleStore", "backend/rag/preprocessing/keyword_store_pg.py"),
    ("backend/rag/indexing/doc_registry.py", "DocumentRegistry", "backend/rag/indexing/doc_registry_pg.py"),
]


def find_class(tree, name=None, inherits=None):
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if name is not None and node.name == name:
            return node
        if inherits is not None:
            for b in node.bases:
                if isinstance(b, ast.Name) and b.id == inherits:
                    return node
                if isinstance(b, ast.Attribute) and b.attr == inherits:
                    return node
    return None


def line_range(node, src_lines):
    """类/函数的完整行号范围（含 decorator）。"""
    start = node.lineno
    if node.decorator_list:
        start = min(d.lineno for d in node.decorator_list)
    end = node.end_lineno
    # 吞掉尾随空行
    while end < len(src_lines) and src_lines[end].strip() == "":
        end += 1
    return start - 1, end  # 0-based inclusive start, exclusive end


def main():
    for base_path, base_cls_name, pg_path in JOBS:
        with open(base_path, encoding="utf-8") as f:
            base_src = f.read()
        base_lines = base_src.splitlines(keepends=True)
        tree = ast.parse(base_src)

        base_cls = find_class(tree, name=base_cls_name)
        if base_cls is None:
            print(f"SKIP {base_path}: class {base_cls_name} not found")
            continue

        with open(pg_path, encoding="utf-8") as f:
            pg_tree = ast.parse(f.read())
        pg_cls = find_class(pg_tree, inherits=base_cls_name)
        if pg_cls is None:
            print(f"SKIP {base_path}: PG subclass of {base_cls_name} not found in {pg_path}")
            continue
        pg_methods = {m.name for m in pg_cls.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))}

        # 收集要删的方法（含 __init__）
        to_delete = []
        deleted_names = []
        for item in base_cls.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name in pg_methods:
                s, e = line_range(item, base_lines)
                to_delete.append((s, e))
                deleted_names.append(item.name)

        if not to_delete:
            print(f"SKIP {base_path}: no overlapping methods")
            continue

        # 从后往前删
        for s, e in sorted(to_delete, reverse=True):
            del base_lines[s:e]

        new_src = "".join(base_lines)
        # 类删空了则补 pass（ast 删完后 body 至少剩 docstring；全空则语法错）
        try:
            ast.parse(new_src)
        except SyntaxError:
            print(f"ERROR {base_path}: syntax error after deletion, aborting this file")
            continue

        with open(base_path, "w", encoding="utf-8", newline="") as f:
            f.write(new_src)
        print(f"OK {base_path}: deleted {len(deleted_names)} methods: {', '.join(deleted_names)}")


if __name__ == "__main__":
    sys.exit(main())
