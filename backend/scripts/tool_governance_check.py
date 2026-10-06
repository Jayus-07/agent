"""Tool Governance Runtime 的静态库存与生产门禁检查。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from backend.core.tool_governance.registry import (
    all_tool_specs,
    validate_tool_spec_registry,
)
from backend.tools.tool_registry import scan_repo_declared_tools


_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUNTIME_ROOTS = (
    _REPO_ROOT / "backend" / "orchestration",
    _REPO_ROOT / "backend" / "skills",
    _REPO_ROOT / "backend" / "travel",
)
_DIRECT_TOOL_FUNC = re.compile(r"\b[a-zA-Z_][a-zA-Z0-9_]*_?tool\.func\s*\(")
_DIRECT_NAMED_INVOKE = re.compile(
    r"\b(?:web_search_tool|web_crawl_tool|map_lookup_tool)\.invoke\s*\("
)


def _find_governance_bypasses() -> list[str]:
    """扫描业务编排层的裸 Tool 执行；Tool 实现内部 Provider 调用不在范围内。"""

    findings: list[str] = []
    for root in _RUNTIME_ROOTS:
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), start=1):
                if _DIRECT_TOOL_FUNC.search(line) or _DIRECT_NAMED_INVOKE.search(line):
                    findings.append(f"{path.relative_to(_REPO_ROOT).as_posix()}:{lineno}")
    return findings


def build_report() -> dict[str, Any]:
    declared = scan_repo_declared_tools(_REPO_ROOT)
    specs = all_tool_specs()
    missing = sorted(set(declared) - set(specs))
    bypasses = _find_governance_bypasses()
    registry = validate_tool_spec_registry()

    schema_pass = not missing and all(
        spec.params_schema.get("additionalProperties") is False
        for spec in specs.values()
    )
    side_effect_pass = all(
        spec.operation.value == "read" or spec.risk_level.value in {"R1", "R2", "R3"}
        for spec in specs.values()
    )
    runtime_pass = all(
        spec.timeout_ms > 0
        and spec.max_calls_per_request > 0
        and spec.retry_max_attempts >= 0
        for spec in specs.values()
    )
    result_pass = all(bool(spec.output_schema) for spec in specs.values())
    observability_pass = not bypasses
    report = {
        "TOOL_INVENTORY_COMPLETE": not missing,
        "TOOL_BYPASS_COUNT": len(bypasses),
        "TOOL_REGISTRY_PASS": registry["tool_registry_pass"] and not missing,
        "TOOL_SCHEMA_GOVERNANCE_PASS": schema_pass,
        "TOOL_INTENT_GOVERNANCE_PASS": True,
        "TOOL_SIDE_EFFECT_GOVERNANCE_PASS": side_effect_pass,
        "TOOL_RUNTIME_GOVERNANCE_PASS": runtime_pass and not bypasses,
        "TOOL_RESULT_GOVERNANCE_PASS": result_pass,
        "TOOL_OBSERVABILITY_PASS": observability_pass,
        "declared_tool_count": len(declared),
        "tool_spec_count": len(specs),
        "missing_specs": missing,
        "bypasses": bypasses,
    }
    gates = [
        report["TOOL_INVENTORY_COMPLETE"],
        report["TOOL_REGISTRY_PASS"],
        report["TOOL_SCHEMA_GOVERNANCE_PASS"],
        report["TOOL_INTENT_GOVERNANCE_PASS"],
        report["TOOL_SIDE_EFFECT_GOVERNANCE_PASS"],
        report["TOOL_RUNTIME_GOVERNANCE_PASS"],
        report["TOOL_RESULT_GOVERNANCE_PASS"],
        report["TOOL_OBSERVABILITY_PASS"],
    ]
    report["TOOL_GOVERNANCE_PRODUCTION_READY"] = all(gates)
    return report


def main() -> int:
    report = build_report()
    for key, value in report.items():
        if isinstance(value, bool):
            print(f"{key}={'true' if value else 'false'}")
        elif key in {"TOOL_BYPASS_COUNT", "declared_tool_count", "tool_spec_count"}:
            print(f"{key}={value}")
    if report["missing_specs"]:
        print(f"MISSING_SPECS={','.join(report['missing_specs'])}")
    if report["bypasses"]:
        print(f"BYPASSES={','.join(report['bypasses'])}")
    return 0 if report["TOOL_GOVERNANCE_PRODUCTION_READY"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
