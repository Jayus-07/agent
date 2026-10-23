"""将已有脱敏候选补齐证据后纳入统一、待审核的业务数据集。"""

from __future__ import annotations

import json

from backend.evaluation.dataset import imports as assets


def promote(candidate_id: str, manifest: dict, expected: dict, actor: str) -> dict:
    from backend.app.api.routes.evaluation_datasets import _load_candidates

    candidate = _load_candidates().get(candidate_id)
    if candidate is None:
        raise FileNotFoundError(candidate_id)
    if not candidate.redacted or candidate.module != "rag" or candidate.status == "rejected":
        raise ValueError("仅允许未驳回、已脱敏的 RAG 候选进入业务题集")
    payload = {
        "manifest": manifest,
        "cases": [
            {
                "id": candidate.candidate_id,
                "question": candidate.question,
                "query_type": (expected.get("reason") or "no_evidence") if expected.get("behavior") == "refuse" else "single_doc",
                "expected": expected,
            }
        ],
    }
    validation = assets.validate_upload("candidate.json", json.dumps(payload).encode(), {})
    if not validation["valid"]:
        raise ValueError(json.dumps(validation["errors"], ensure_ascii=False))
    result = assets.create_import(validation, actor)
    assets.write_once(
        "lineage",
        result["import_id"],
        {
            "candidate_id": candidate.candidate_id,
            "source_type": candidate.source_type,
            "created_at": assets.now(),
            "actor": actor,
        },
    )
    return result
