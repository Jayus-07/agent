"""评测运行的结构化 provenance。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from backend.evaluation.models import EvalReport


def build_snapshot_hash(inputs: dict[str, Any]) -> str:
    """C3-3/REPRO-09：统一 evaluation_snapshot_hash（sha256 前 16 位）。

    纯函数：对 canonical JSON（sorted keys、ensure_ascii=False）取摘要。
    任一关键输入变化 → hash 必变（REPRO-10，守护测试覆盖每个维度）。
    """
    canonical = json.dumps(inputs, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def collect_snapshot_inputs(config: Any, report: EvalReport) -> dict[str, Any]:
    """收集快照哈希的全部关键输入（REPRO-09 清单逐项对应）。

    任一项采集失败记为明确占位（"" / {}）而非抛异常——哈希永远可算，
    但输入残缺本身会体现在哈希输入里（诚实口径）。
    """
    from backend.evaluation.gate.evaluator import TIER_THRESHOLDS

    metadata = report.metadata or {}
    scope = metadata.get("evaluation_scope") or {}
    governance = metadata.get("suite_governance") or {}
    prompt_versions = {
        str(k): str(v)
        for k, v in (report.prompt_versions or {}).items()
    }
    return {
        "suite": {
            "name": getattr(config, "selection", None) or metadata.get("selection") or "default",
            "dataset_version": str(metadata.get("dataset_version") or ""),
            "suite_content_hash": str(governance.get("content_hash", "")),
            "cases_hash": str(governance.get("content_hash", "")) or _canonical_cases_hash(),
        },
        "prompt_versions": prompt_versions,
        "prompt_template_hashes": collect_template_hashes(list(prompt_versions)),
        "model_config": {
            "binding_fingerprint": str(metadata.get("model_binding_fingerprint")
                                       or _model_binding_fingerprint()),
            "eval_model": _eval_model_identity(),
        },
        "retrieval_config": _retrieval_config(scope),
        "judge_config": metadata.get("judge_config") or {},
        "ragas_config": metadata.get("ragas_config") or {},
        "tool_contract_fingerprint": str(metadata.get("tool_contract_fingerprint")
                                         or _tool_contract_fingerprint()),
        "corpus_version_id": str(scope.get("version_id") or ""),
        "git_sha": str(metadata.get("git_sha") or _git_sha()),
        "threshold_snapshot": {
            "tier_thresholds": dict(TIER_THRESHOLDS),
            "min_samples": governance.get("min_samples"),
            "min_valid_samples": governance.get("min_valid_samples"),
        },
    }


def collect_template_hashes(keys: list[str]) -> dict[str, str]:
    """REPRO-04：prompt key → 模板内容哈希（PG 权威，软失败回退空）。

    get_template_hash 是 async；评测在 worker 线程执行（无运行中 loop），
    asyncio.run 安全。DB 不可达时返回 {}——哈希输入显式缺失，可辨识。
    """
    if not keys:
        return {}
    try:
        from backend.prompts.service import prompt_service

        async def _collect() -> dict[str, str]:
            out: dict[str, str] = {}
            for key in keys:
                version = prompt_service.current_versions().get(key)
                if version is None:
                    out[key] = ""
                    continue
                try:
                    out[key] = str(await prompt_service.get_template_hash(key, int(version)))
                except Exception:
                    out[key] = ""
            return out

        return asyncio.run(_collect())
    except Exception:
        return {}


def _canonical_cases_hash() -> str:
    """无 suite 配置时回退 canonical 内容指纹（P0-02）。"""
    try:
        from backend.evaluation.dataset.loader import (
            DATASET_DIR,
            file_content_hash,
        )

        path = DATASET_DIR / "rag" / "cases.jsonl"
        if path.exists():
            return file_content_hash(path)
    except Exception:
        pass
    return ""


def _eval_model_identity() -> dict[str, str]:
    """eval_gen 角色 → (model, provider)；不可用记 unknown。"""
    try:
        from backend.evaluation.generation import resolve_eval_model

        model, provider = resolve_eval_model()
        return {"model": model, "provider": provider}
    except Exception:
        return {"model": "unknown", "provider": "unknown"}


def _retrieval_config(scope: dict[str, Any]) -> dict[str, Any]:
    """检索面配置快照（REPRO-02：top_k / reranker / multiquery）。"""
    try:
        from backend.config.rag import (
            METADATA_CASCADE_L1_TOP_K,
            MULTI_QUERY_TOP_K_PER,
            RERANK_SCORE_THRESHOLD,
            RERANK_TOP_K,
        )

        return {
            "top_k": int(RERANK_TOP_K),
            "metadata_cascade_l1_top_k": int(METADATA_CASCADE_L1_TOP_K),
            "multiquery_top_k_per": int(MULTI_QUERY_TOP_K_PER),
            "rerank_score_threshold": float(RERANK_SCORE_THRESHOLD),
            "multiquery": bool(scope.get("multiquery", False)),
        }
    except Exception:
        return {"multiquery": bool(scope.get("multiquery", False)), "unavailable": True}


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
