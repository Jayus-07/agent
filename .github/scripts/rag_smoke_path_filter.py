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
})

# 评测准备文件和 Workflow 的 PR 变化运行免费预检，main 更新不触发真实 Provider。
PR_STATIC_ONLY_ENTRIES = frozenset({
    "scripts/init_db.py",
    ".github/workflows/rag_smoke.yml",
    ".github/workflows/rag_regression.yml",
    ".github/scripts/rag_smoke_path_filter.py",
    "backend/scripts/seed_prompt_eval_db.py",
    "scripts/seed_eval_model_bindings.py",
})
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

# 文件名先移除迁移编号，再按所属领域判断，避免退款、旅游等迁移误触发
# 带真实 Provider 调用的 RAG 评测。
RAG_MIGRATION_MARKERS = (
    "agent_memory",
    "doc_registry",
    "rag",
    "model",
    "llm",
    "memory",
    "prompt",
    "eval",
    "metadata",
)


def _normalize(path: str) -> str:
    return path.strip().replace("\\", "/")


def _is_relevant_migration(path: str) -> bool:
    prefix = "backend/sql/migrations/"
    if not path.startswith(prefix):
        return False
    name = path.rsplit("/", 1)[-1].lower()
    parts = name.split("_", 1)
    stem = parts[1] if len(parts) == 2 and parts[0].isdigit() else name
    return any(marker in stem for marker in RAG_MIGRATION_MARKERS)


def is_relevant(path: str) -> bool:
    path = _normalize(path)
    if not path:
        return False
    if path in EXACT_ENTRIES:
        return True
    if _is_relevant_migration(path):
        return True
    return any(path.startswith(prefix) for prefix in PREFIX_ENTRIES)


def is_pr_static_only(path: str) -> bool:
    return _normalize(path) in PR_STATIC_ONLY_ENTRIES


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
