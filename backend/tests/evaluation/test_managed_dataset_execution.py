"""自定义题集执行范围、语料漂移与离线 Suite 选择。"""

import json
from pathlib import Path
from types import SimpleNamespace

from backend.evaluation.config import EvalConfig
from backend.evaluation.dataset import imports as assets
from backend.evaluation.models import EvalResult


def test_managed_execution_records_real_dataset_and_invalidates_corpus_drift(monkeypatch, tmp_path):
    from backend.evaluation import service

    monkeypatch.setenv("RAG_DATA_DIR", str(tmp_path))
    data = {
        "manifest": {
            "schema_version": "rag-eval/1.0",
            "dataset_key": "drift_test",
            "name": "漂移测试",
            "version": "1.0.0",
        },
        "cases": [
            {
                "id": "N-1",
                "question": "不存在的事实？",
                "query_type": "no_evidence",
                "expected": {"behavior": "refuse", "reason": "no_evidence"},
            }
        ],
    }
    validation = assets.validate_upload("cases.json", json.dumps(data).encode(), {})
    assert validation["valid"]
    identity = assets.create_import(validation, "user:qa")["import_id"]
    assets.write_once("reviews", identity, {"decision": "approved"})
    bid = "a" * 24
    assets.write_once("bindings", bid, {"import_id": identity, "kb_id": "policy_general"})
    calls = []

    def run(module, cases, **kwargs):
        calls.append((cases[0].id, kwargs["eval_scope"].kb_id))
        return [EvalResult(case_id=cases[0].id, module="rag", status="pass", expected={}, actual={})]

    monkeypatch.setattr(service, "_run_module", run)
    monkeypatch.setattr(service, "_attach_provenance", lambda config, report: report)
    monkeypatch.setattr(service, "_inject_token_totals", lambda *args: None)
    snapshots = iter([[{"doc_id": "old"}], [{"doc_id": "new"}]])
    monkeypatch.setattr(assets, "corpus_snapshot", lambda kb: next(snapshots))
    report = service.EvaluationService()._evaluate_cases(
        EvalConfig(module="rag", dataset=f"managed:{bid}", no_ragas=True), "run-test", 0
    )
    assert calls == [("N-1", "policy_general")]
    assert report.metadata["suite_governance"]["content_hash"] == assets.read("imports", identity)["content_hash"]
    assert report.metadata["run_validity"]["validity"] == "INVALID_DATASET"
    assert report.metadata["corpus_snapshot"] == [{"doc_id": "old"}]


def test_offline_api_executes_requested_suite(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.api.deps import OperatorIdentity, require_admin_user
    from backend.app.api.routes import evaluation as route
    from backend.evaluation import service, storage
    from backend.infra.llm import registry_store

    async def refresh():
        return None

    monkeypatch.setattr(registry_store, "refresh_registry", refresh)

    seen = []

    def evaluate(self, config):
        seen.append(config)
        return SimpleNamespace(
            summaries=[SimpleNamespace(module="rag", total=1, passed=1, failed=0, errors=0, pass_rate=1.0, metrics={})],
            timestamp="now",
            metadata={"run_validity": {"validity": "VALID"}},
        )

    monkeypatch.setattr(service.EvaluationService, "evaluate", evaluate)
    monkeypatch.setattr(storage, "persist_report", lambda report: Path("run-selected"))
    app = FastAPI()
    app.include_router(route.router, prefix="/api")
    app.dependency_overrides[require_admin_user] = lambda: OperatorIdentity(role="admin", actor="user:qa")
    response = TestClient(app).post("/api/evaluation/run", json={"evaluation_mode": "offline", "suite": "smoke"})
    assert response.json()["ok"] is True
    assert seen[0].selection == "smoke" and seen[0].live is False and seen[0].no_ragas is True
    assert seen[0].triggered_by == "user:qa"
