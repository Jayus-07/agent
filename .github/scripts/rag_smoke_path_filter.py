#!/usr/bin/env python3
"""rag_smoke_path_filter.py — RAG PR Smoke 路径相关性判定（单一事实源）。

用法：路径列表（每行一个，如 `git diff --name-only` 输出）从 stdin 输入；
输出 `relevant=true|false` 到 stdout 并以 0 退出。workflow（detect-changes）
与回归测试（tests/test_rag_smoke_path_filter.py）共用本模块，禁止复制
第二份模式列表。

覆盖范围（2026-10-11 GPT 复核补齐）：
  - RAG 检索与评测代码（backend/rag、backend/evaluation、检索编排）
  - MCP RAG 实现（mcp_servers/servers/rag.py）
  - RAG Tool 超时策略（backend/core/tool_runtime/policy.py）
  - 模型绑定面（backend/infra/llm/）与评测/数据库初始化脚本（真实路径：
    backend/scripts/seed_prompt_eval_db.py、scripts/seed_eval_model_bindings.py、
    scripts/init_db.py）

匹配语义：git diff 输出为仓库根相对路径；精确文件名完全匹配，
目录/前缀条目按前缀匹配。反斜杠统一为正斜杠。
"""
from __future__ import annotations

import sys

# 精确文件/目录条目：完全匹配
EXACT_ENTRIES = frozenset({
    "pyproject.toml",
    "requirements-lock.txt",
    "backend/app/api/routes/_rag_shared.py",
    "backend/core/tool_runtime/policy.py",
    "backend/services/rag_server.py",
    "backend/infra/mcp_client.py",
    "mcp_servers/servers/rag.py",
    "backend/scripts/seed_prompt_eval_db.py",
    "backend/scripts/ingest_eval_fixtures.py",
    "scripts/seed_eval_model_bindings.py",
    "scripts/init_db.py",
    ".github/workflows/rag_smoke.yml",
    ".github/workflows/rag_regression.yml",
    ".github/scripts/rag_smoke_path_filter.py",
})

# 目录前缀条目：startswith 匹配（含尾部 /）
PREFIX_ENTRIES = (
    "backend/rag/",
    "backend/evaluation/",
    "backend/infra/llm/",
    "backend/config/",
    "backend/orchestration/graph/",
    "backend/prompts/",
    "backend/sql/migrations/",
    "data/docs/",
    "backend/app/api/routes/rag",
)


def is_relevant(path: str) -> bool:
    path = path.strip().replace("\\", "/")
    if not path:
        return False
    if path in EXACT_ENTRIES:
        return True
    return any(path.startswith(prefix) for prefix in PREFIX_ENTRIES)


def main(argv: list[str] | None = None) -> int:
    relevant = any(is_relevant(line) for line in sys.stdin)
    print(f"relevant={str(relevant).lower()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
