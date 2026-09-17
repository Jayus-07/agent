"""删除 2 个 sqlite legacy migration 测试函数（sqlite 轨删除后语义失效）。"""
import ast
from pathlib import Path

JOBS = [
    ("backend/tests/rag/test_doc_registry_version_fields.py", "test_sqlite_legacy_db_lazy_column_migration"),
    ("backend/tests/rag/test_quality_gate.py", "test_legacy_db_migration"),
]

for fp, fn_name in JOBS:
    p = Path(fp)
    src = p.read_text(encoding="utf-8")
    lines = src.splitlines(keepends=True)
    tree = ast.parse(src)
    target = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == fn_name:
            target = node
            break
    if target is None:
        print(f"MISS {fp}:{fn_name}")
        continue
    start = target.lineno - 1
    if target.decorator_list:
        start = min(d.lineno for d in target.decorator_list) - 1
    end = target.end_lineno
    while end < len(lines) and lines[end].strip() == "":
        end += 1
    del lines[start:end]
    p.write_text("".join(lines), encoding="utf-8", newline="")
    ast.parse("".join(lines))  # syntax check
    print(f"OK   {fp}:{fn_name} (lines {start+1}-{end})")
