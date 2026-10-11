"""RAG PR Smoke 路径过滤回归测试（2026-10-11 GPT 复核整改 2/4）。

单一事实源：.github/scripts/rag_smoke_path_filter.py（workflow
detect-changes 与本测试共用，禁止复制第二份模式列表）。

两条通道都测：
- import is_relevant：逻辑断言（正反触发）；
- subprocess python3：stdin→stdout 端到端（与 workflow 管道用法同形）。

覆盖（GPT 要求）：
- MCP RAG 实现（mcp_servers/servers/rag.py）与评测初始化脚本
  （backend/scripts/seed_prompt_eval_db.py）变化必定触发；
- 非相关纯文档/前端/非 RAG 后端变化跳过。
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "rag_smoke_path_filter.py"
_spec = importlib.util.spec_from_file_location("rag_smoke_path_filter", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def _filter_import(paths: list[str]) -> bool:
    return any(_mod.is_relevant(p) for p in paths)


def _filter_subprocess(paths: list[str]) -> bool:
    out = subprocess.run(
        [sys.executable, str(_SCRIPT)],
        input="\n".join(paths) + "\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    line = out.stdout.strip().splitlines()[-1]
    assert line.startswith("relevant="), out.stdout
    return line.split("=", 1)[1] == "true"


# ── 正例：RAG 相关变化必定触发 ────────────────────────────────────────


def test_mcp_rag_change_triggers_smoke():
    """MCP RAG 实现变化必定触发（GPT 整改 2 回归）。"""
    paths = ["mcp_servers/servers/rag.py"]
    assert _filter_import(paths) is True
    assert _filter_subprocess(paths) is True


def test_seed_prompt_eval_db_change_triggers_smoke():
    """评测初始化脚本（backend/scripts 真实路径）变化必定触发。"""
    paths = ["backend/scripts/seed_prompt_eval_db.py"]
    assert _filter_import(paths) is True
    assert _filter_subprocess(paths) is True


def test_seed_eval_bindings_change_triggers_smoke():
    paths = ["scripts/seed_eval_model_bindings.py"]
    assert _filter_import(paths) is True
    assert _filter_subprocess(paths) is True


def test_core_rag_paths_trigger_smoke():
    for paths in (
        ["backend/rag/pipeline.py"],
        ["backend/evaluation/runners/rag.py"],
        ["backend/app/api/routes/rag_upload.py"],
        ["backend/services/rag_server.py"],
        ["backend/infra/llm/credentials.py"],
        [".github/workflows/rag_smoke.yml"],
    ):
        assert _filter_import(paths) is True, paths
        assert _filter_subprocess(paths) is True, paths


# ── 反例：非相关变化跳过 ─────────────────────────────────────────────


def test_irrelevant_docs_pr_skips_smoke():
    paths = ["docs/operations/ci-evaluation-environment.md", "README.md",
             "frontend/src/components/Foo.tsx"]
    assert _filter_import(paths) is False
    assert _filter_subprocess(paths) is False


def test_unrelated_backend_code_skips_smoke():
    paths = ["backend/tools/registry.py", "backend/domains/travel/graph.py"]
    assert _filter_import(paths) is False
    assert _filter_subprocess(paths) is False


def test_empty_diff_skips_smoke():
    assert _filter_import([]) is False
    assert _filter_subprocess([]) is False
