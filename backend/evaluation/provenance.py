"""评测运行的结构化 provenance。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from backend.evaluation.models import EvalReport


def build_eval_provenance(config: Any, report: EvalReport) -> dict[str, Any]:
    """从实际 report scope 和配置快照构建可审计的运行来源。"""
    metadata = report.metadata or {}
    scope = metadata.get("evaluation_scope") or {}
    prompt_snapshot = (
        dict(getattr(config, "prompt_versions", {}) or {})
        or dict(metadata.get("prompt_snapshot") or {})
        or dict(report.prompt_versions or {})
        or _current_prompt_snapshot()
    )
    dataset_version = metadata.get("dataset_version") or getattr(
        config, "dataset_version", ""
    )
    return {
        "suite": getattr(config, "selection", None) or metadata.get("selection") or "default",
        "dataset_version": dataset_version,
        "kb_id": scope.get("kb_id") or getattr(config, "kb_id", None) or "",
        "fixture_set": scope.get("fixture_set") or getattr(config, "fixture_set", None) or "",
        "version_id": scope.get("version_id") or "",
        "multiquery": bool(scope.get("multiquery", getattr(config, "multiquery", False))),
        "prompt_snapshot": prompt_snapshot,
        "release_id": str(getattr(config, "release_id", "") or ""),
        "model_binding_fingerprint": _model_binding_fingerprint(),
        "tool_contract_fingerprint": _tool_contract_fingerprint(),
        "git_sha": _git_sha(),
    }


def _current_prompt_snapshot() -> dict[str, Any]:
    try:
        from backend.evaluation.run_records import collect_prompt_snapshot

        return collect_prompt_snapshot()
    except Exception:
        return {}


def _model_binding_fingerprint() -> str:
    try:
        from backend.evaluation.run_records import collect_model_binding_fingerprint

        return collect_model_binding_fingerprint()
    except Exception:
        return ""


def _tool_contract_fingerprint() -> str:
    """从仓库 Tool lock 派生指纹，不复制 Tool 清单。"""
    lock_path = Path(__file__).resolve().parents[1] / "tool_contracts.lock.json"
    try:
        snapshot = json.loads(lock_path.read_text(encoding="utf-8"))
        content = {
            name: entry.get("content_hash", "")
            for name, entry in sorted((snapshot.get("tools") or {}).items())
        }
        return hashlib.sha256(
            json.dumps(content, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]
    except (OSError, TypeError, ValueError):
        return ""


def _git_sha() -> str:
    try:
        from backend.evaluation.storage import get_git_sha

        return get_git_sha()
    except Exception:
        return "unknown"


__all__ = ["build_eval_provenance"]
