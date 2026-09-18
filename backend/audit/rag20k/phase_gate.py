"""RAG 20k 阶段 0 出口门：只认 0.1—0.6 六项机器可读证据全部通过。

出口门映射审计报告第 6 节的阶段 0 编号：
``baseline``=0.1、``decisions``=0.2、``evaluation``=0.3（100 文档双跑）、
``corpus``=0.4（20k 清单）、``golden``=0.5（500 黄金集）、
``risk_register``=0.6。签字类证据（decisions）另须全部 confirmed。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from backend.audit.rag20k.decisions import summarize_decisions

_EVIDENCE_IDS = ("baseline", "decisions", "corpus", "golden", "evaluation", "risk_register")


@dataclass(frozen=True)
class PhaseGateResult:
    """阶段 0 出口门结果；``missing`` 指证据文件不存在，``failed`` 指存在但未通过。"""

    passed: bool
    missing: list[str]
    failed: list[str]
    schema_errors: list[str]
    details: dict[str, str]


def _load_json(path: Path) -> tuple[object | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, None
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"{path}: {exc}"


def _check_baseline(root: Path) -> tuple[str, str]:
    manifests = sorted(root.glob("baseline/*/baseline-manifest.json"))
    if not manifests:
        return "missing", "缺少 baseline-manifest.json"
    dirty = []
    for manifest_path in manifests:
        data, error = _load_json(manifest_path)
        if error:
            return "schema_error", error
        if not isinstance(data, dict):
            return "schema_error", f"{manifest_path}: 顶层必须是对象"
        if data.get("blocking_items") or data.get("docker", {}).get("status") != "ok":
            dirty.append(manifest_path.parent.name)
    if dirty:
        return "failed", f"capture 带阻断项: {', '.join(dirty)}"
    return "passed", f"{len(manifests)} 份 capture 全部干净"


def _check_decisions(root: Path) -> tuple[str, str]:
    path = root / "decisions" / "Q1-Q10.json"
    if not path.is_file():
        return "missing", "缺少 Q1-Q10.json"
    data, error = _load_json(path)
    if error:
        return "schema_error", error
    summary = summarize_decisions(data)  # type: ignore[arg-type]
    if summary.issues:
        return "schema_error", f"{len(summary.issues)} 条 schema 错误"
    if summary.blocked_ids:
        return "failed", "blocked 决策: " + ", ".join(summary.blocked_ids)
    return "passed", f"{len(summary.confirmed_ids)}/10 confirmed"


def _check_status_summary(root: Path, subdir: str, filename: str) -> tuple[str, str]:
    path = root / subdir / filename
    if not path.is_file():
        return "missing", f"缺少 {subdir}/{filename}"
    data, error = _load_json(path)
    if error:
        return "schema_error", error
    if not isinstance(data, dict):
        return "schema_error", f"{path}: 顶层必须是对象"
    status = data.get("status")
    if status != "ok":
        return "failed", f"status={status}"
    return "passed", "status=ok"


def _check_evaluation(root: Path) -> tuple[str, str]:
    path = root / "evaluation" / "reproducibility.json"
    if not path.is_file():
        return "missing", "缺少 evaluation/reproducibility.json（100 文档双跑结论）"
    data, error = _load_json(path)
    if error:
        return "schema_error", error
    if not isinstance(data, dict):
        return "schema_error", f"{path}: 顶层必须是对象"
    if not (data.get("comparable") and data.get("passed")):
        return "failed", "双跑不可比或主指标差值超限"
    return "passed", "双跑可复现"


def evaluate_phase0(evidence_root: Path) -> PhaseGateResult:
    """评估阶段 0 证据目录；任一缺失/未通过/schema 错误都不放行。"""

    root = Path(evidence_root)
    statuses: dict[str, tuple[str, str]] = {
        "baseline": _check_baseline(root),
        "decisions": _check_decisions(root),
        "corpus": _check_status_summary(root, "corpus", "validation-summary.json"),
        "golden": _check_status_summary(root, "golden", "validation-summary.json"),
        "evaluation": _check_evaluation(root),
    }
    register = root / "risk-register.md"
    statuses["risk_register"] = (
        ("passed", "risk-register.md 存在") if register.is_file()
        else ("missing", "缺少 risk-register.md")
    )

    missing = [eid for eid in _EVIDENCE_IDS if statuses[eid][0] == "missing"]
    failed = [eid for eid in _EVIDENCE_IDS if statuses[eid][0] == "failed"]
    schema_errors = [
        f"{eid}: {statuses[eid][1]}" for eid in _EVIDENCE_IDS if statuses[eid][0] == "schema_error"
    ]
    return PhaseGateResult(
        passed=not missing and not failed and not schema_errors,
        missing=missing,
        failed=failed,
        schema_errors=schema_errors,
        details={eid: f"{state}: {message}" for eid, (state, message) in statuses.items()},
    )
