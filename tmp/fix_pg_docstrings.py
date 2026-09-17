"""一次性脚本：更新 _pg.py 文件 docstring 中对已删除 BACKEND 开关的引用。"""
import re
from pathlib import Path

FILES = [
    "backend/competitor/store_pg.py",
    "backend/feedback/pg.py",
    "backend/market_research/store_pg.py",
    "backend/observability/analytics_store_pg.py",
    "backend/observability/llm_usage_store_pg.py",
    "backend/observability/trace_store_pg.py",
    "backend/orchestration/inventory/store_pg.py",
    "backend/orchestration/workflow/persistence_pg.py",
    "backend/rag/indexing/chunk_store_pg.py",
    "backend/rag/indexing/operation_log_pg.py",
    "backend/rag/indexing/doc_registry_pg.py",
    "backend/rag/preprocessing/keyword_store_pg.py",
    "backend/selection/store_pg.py",
    "backend/selection_decision/store_pg.py",
]

# 匹配两种常见 docstring 形态的开关说明行
PATTERNS = [
    re.compile(r"选型开关见 `backend/config/database\.py::\w+`\n（env `\w+=postgres` 启用；默认 sqlite，即回滚开关）。\n?"),
    re.compile(r"引擎开关见 `backend/config/database\.py::\w+`\n（env `\w+=postgres` 启用；默认 sqlite，即回滚开关）。\n?"),
    re.compile(r"`backend/config/database\.py::\w+`（env `\w+=postgres`）。\n?"),
    re.compile(r"存储引擎开关（R1/C19，默认 sqlite = 回滚开关）：\n    DOC_REGISTRY_BACKEND=postgres  → 返回 PostgresDocumentRegistry（同接口 PG 实现）\n    其余/未设置                     → SQLite 实现（行为与历史版本完全一致）\n调用方零改动：`DocumentRegistry\(path\)` 仍为唯一入口，PG 实现是其子类。\n?"),
]

for fp in FILES:
    p = Path(fp)
    if not p.exists():
        print(f"MISS {fp}")
        continue
    src = p.read_text(encoding="utf-8")
    orig = src
    for pat in PATTERNS:
        src = pat.sub("PG 为唯一实现（2026-09-17 SQLite 轨删除）。\n", src)
    if src != orig:
        p.write_text(src, encoding="utf-8", newline="")
        print(f"OK   {fp}")
    else:
        print(f"SKIP {fp} (no match)")
