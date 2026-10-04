"""评测集目录、候选审核和不可变版本 API 测试。"""
from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest


@pytest.fixture
def governance(monkeypatch, tmp_path):
    import backend.app.api.routes.evaluation_datasets as module

    dataset_root = tmp_path / "datasets"
    rag_root = dataset_root / "rag"
    rag_root.mkdir(parents=True)
    cases = [
        {
            "id": "R-001",
            "question": "退货需要几天？",
            "module": "rag",
            "expected": {"expected_answer": "七天"},
            "metadata": {"tier": "smoke", "source": "curated"},
        }
    ]
    (rag_root / "cases.jsonl").write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in cases) + "\n",
        encoding="utf-8",
    )
    (rag_root / "manifest.json").write_text(
        json.dumps({
            "version": "5.0.0",
            "module": "rag",
            "owner": "quality",
            "kb_id": "rag_eval_kb",
            "fixture_set": "baseline",
            "review_status": "approved",
        }),
        encoding="utf-8",
    )
    suites = rag_root / "suites"
    suites.mkdir()
    (suites / "pr_baseline.json").write_text(
        json.dumps({
            "name": "pr_baseline",
            "version": "5.0.0",
            "dataset_version": "5.0.0",
            "kb_id": "rag_eval_kb",
            "fixture_set": "baseline",
            "case_ids": ["R-001"],
            "thresholds": {"pass_rate": 0.85},
        }),
        encoding="utf-8",
    )

    monkeypatch.setattr(module, "DATASET_DIR", dataset_root)
    monkeypatch.setattr(module, "VERSION_ROOT", tmp_path / "versions")
    monkeypatch.setattr(module, "CANDIDATE_ROOT", tmp_path / "candidates")
    module.clear_dataset_candidate_store()

    app = FastAPI()
    app.include_router(module.router, prefix="/api")
    # P0-01：候选审核端点已挂 require_admin_user；单测聚焦审核业务规则，
    # 以固定管理员身份覆写鉴权依赖（鉴权本体由 deps 层自己的测试覆盖）
    from backend.app.api.deps import OperatorIdentity, require_admin_user

    app.dependency_overrides[require_admin_user] = lambda: OperatorIdentity(
        role="admin", actor="user:test-admin",
    )
    return module, TestClient(app)


def test_unredacted_candidate_cannot_enter_gate(governance):
    module, client = governance
    candidate = module.register_dataset_candidate(
        candidate_id="cand-raw",
        module="rag",
        question="包含手机号 13800138000 的问题",
        expected={"expected_answer": "不能直接进入"},
        metadata={"source": "production_log", "phone": "13800138000"},
        source_type="trace",
        redacted=False,
    )

    response = client.post(
        f"/api/evaluation/dataset-candidates/{candidate.candidate_id}/approve",
        json={"reviewer": "reviewer-1"},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "DATASET_REDACTION_REQUIRED"


def test_approved_candidate_creates_new_immutable_version(governance):
    module, client = governance
    candidate = module.register_dataset_candidate(
        candidate_id="cand-redacted",
        module="rag",
        question="退货地址在哪里？",
        expected={"expected_answer": "订单页可查看"},
        metadata={"source": "production_log", "ground_truth_verified": True},
        source_type="feedback",
        redacted=True,
    )

    response = client.post(
        f"/api/evaluation/dataset-candidates/{candidate.candidate_id}/approve",
        json={"reviewer": "reviewer-1"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "approved"
    assert body["approved_version"] != "5.0.0"
    assert body["content_hash"]
    assert (module.VERSION_ROOT / "rag" / body["approved_version"] / "manifest.json").exists()


def test_catalog_and_suite_expose_scope_and_hash(governance):
    _module, client = governance

    catalog = client.get("/api/evaluation/datasets")
    suites = client.get("/api/evaluation/suites")

    assert catalog.status_code == 200
    assert catalog.json()["items"][0]["dataset_version"] == "5.0.0"
    assert catalog.json()["items"][0]["content_hash"]
    assert suites.json()["items"][0]["name"] == "pr_baseline"
    assert suites.json()["items"][0]["kb_id"] == "rag_eval_kb"
