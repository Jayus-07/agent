"""Import hygiene tests — AST-scan backend/prompts/*.py for forbidden imports.

The prompts module must remain a leaf dependency: it should NOT import from
agents, tools, rag, sql, orchestration, or other domain modules.
"""
import ast
from pathlib import Path

import pytest

_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"

FORBIDDEN_PREFIXES = (
    "backend.agents",
    "backend.tools",
    "backend.rag",
    "backend.sql",
    "backend.orchestration",
    "backend.competitor",
    "backend.selection_decision",
    "backend.business_report",
    "backend.evaluation",
    "backend.memory",
    "backend.security",
)

ALLOWED_BACKEND_IMPORTS = (
    "backend.prompts",
    "backend.shared",
    "backend.infra",
    "backend.llm",
    "backend.memory.database",
    "backend.memory.repository",
    "backend.memory.models",
)


def _get_python_files():
    if not _PROMPTS_DIR.is_dir():
        return []
    return sorted(_PROMPTS_DIR.glob("*.py"))


def _check_imports(node: ast.AST) -> list[str]:
    violations = []
    for child in ast.walk(node):
        if isinstance(child, ast.Import):
            for alias in child.names:
                if alias.name.startswith("backend."):
                    if not any(alias.name.startswith(p) for p in ALLOWED_BACKEND_IMPORTS):
                        if any(alias.name.startswith(p) for p in FORBIDDEN_PREFIXES):
                            violations.append(f"import {alias.name}")
        elif isinstance(child, ast.ImportFrom):
            if child.module and child.module.startswith("backend."):
                if not any(child.module.startswith(p) for p in ALLOWED_BACKEND_IMPORTS):
                    if any(child.module.startswith(p) for p in FORBIDDEN_PREFIXES):
                        violations.append(f"from {child.module} import ...")
    return violations


class TestImportHygiene:
    @pytest.mark.parametrize("filepath", _get_python_files(), ids=lambda p: p.name)
    def test_no_forbidden_imports(self, filepath):
        source = filepath.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(filepath))
        violations = _check_imports(tree)
        assert not violations, (
            f"{filepath.name} has forbidden imports: {violations}\n"
            f"The prompts module must not import from domain modules."
        )
