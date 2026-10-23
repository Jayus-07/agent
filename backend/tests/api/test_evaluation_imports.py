"""导入、不可变审核、绑定和执行契约的隔离验证。"""

from __future__ import annotations

import json
from copy import deepcopy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.evaluation.dataset import imports as assets


def package():
    return {
        "manifest": {
            "schema_version": "rag-eval/1.0",
            "dataset_key": "policy_golden",
            "name": "制度题集",
            "version": "1.0.0",
            "module": "rag",
        },
        "cases": [
            {
                "id": "P-001",
                "question": "请假需要几天？",
                "expected": {"behavior": "answer", "evidence": [{"doc_id": "doc-1", "quote": "提前三天"}]},
            }
        ],
    }


@pytest.fixture
def api(monkeypatch, tmp_path):
    from backend.app.api.deps import OperatorIdentity, require_user_actor
    from backend.app.api.routes import evaluation_imports as route

    monkeypatch.setenv("RAG_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(route, "require_kb", lambda kb: {"kb_id": kb})
    monkeypatch.setattr(
        assets,
        "corpus_snapshot",
        lambda kb: [{"doc_id": "doc-1", "file_name": "制度.pdf", "file_path": "", "version_id": "v1"}],
    )
    app = FastAPI()
    app.include_router(route.router, prefix="/api/evaluation")
    app.dependency_overrides[require_user_actor] = lambda: OperatorIdentity(role="super_admin", actor="user:qa")
    return TestClient(app), app, route


def upload(client, payload=None, endpoint="/dataset-imports"):
    return client.post(
        "/api/evaluation" + endpoint,
        files={
            "file": (
                "cases.json",
                json.dumps(payload or package(), ensure_ascii=False).encode("utf-8"),
                "application/json",
            )
        },
    )


def test_import_review_binding_and_normalized_execution(api, monkeypatch):
    client, _, route = api
    checked = upload(client, endpoint="/dataset-imports/validate")
    assert checked.json()["valid"] is True
    imported = upload(client).json()
    identity = imported["import_id"]
    assert imported["owner"] == "user:qa"
    # 未审核版本不能绑定执行。
    assert (
        client.post(
            "/api/evaluation/evaluation-bindings", json={"import_id": identity, "kb_id": "policy_general"}
        ).status_code
        == 404
    )
    reviewed = client.post(
        f"/api/evaluation/dataset-imports/{identity}/review", json={"decision": "approved", "reason": "已核对原文"}
    )
    assert reviewed.json()["reviewer"] == "user:qa"
    assert len(client.get(f"/api/evaluation/dataset-imports/{identity}").json()["cases"]) == 1
    linked = client.post("/api/evaluation/evaluation-bindings", json={"import_id": identity, "kb_id": "policy_general"})
    assert linked.status_code == 201
    binding = linked.json()["binding_id"]
    from backend.evaluation.config import EvalConfig
    from backend.evaluation.dataset.loader import load_dataset_file
    from backend.evaluation.service import _resolve_rag_scope

    cases = load_dataset_file(f"managed:{binding}")
    assert cases[0].expected["relevant_snippets"] == ["提前三天"]
    assert cases[0].metadata["kb_id"] == "policy_general"
    assert "annotation" not in cases[0].metadata
    scope, version = _resolve_rag_scope(cases, EvalConfig(dataset=f"managed:{binding}"))
    assert scope.kb_id == "policy_general" and scope.fixture_set == "" and version == "1.0.0"
    seen = []
    from backend.evaluation import managed_tasks

    monkeypatch.setattr(
        managed_tasks,
        "submit",
        lambda bid, actor, key: (
            seen.append((bid, actor, key)) or {"run_id": "run-1", "task_id": "run-1", "status": "PENDING"}
        ),
    )
    assert (
        client.post(
            f"/api/evaluation/evaluation-bindings/{binding}/run", json={"request_id": "request_123"}
        ).status_code
        == 202
    )
    assert seen == [(binding, "user:qa", "request_123")]
    # 不可通过审核端点覆盖决定。
    assert (
        client.post(
            f"/api/evaluation/dataset-imports/{identity}/review", json={"decision": "rejected", "reason": "改判"}
        ).status_code
        == 409
    )


def test_invalid_lines_and_privileged_fields_are_rejected(api):
    client, _, _ = api
    payload = package()
    row = payload["cases"][0]
    data = (json.dumps(row) + "\nBAD JSON\n" + json.dumps(row)).encode()
    result = client.post(
        "/api/evaluation/dataset-imports/validate",
        files={"file": ("cases.jsonl", data)},
        data={"manifest": json.dumps(payload["manifest"])},
    )
    errors = result.json()["errors"]
    assert {e["line"] for e in errors} == {2, 3}
    payload["cases"][0]["test_subject_ref"] = "super_admin"
    assert upload(client, payload).status_code == 422
    assert assets.list_records("imports") == []


def test_version_is_immutable_idempotent_and_content_verified(api):
    client, _, _ = api
    original = upload(client).json()
    assert upload(client).json()["import_id"] == original["import_id"]
    changed = deepcopy(package())
    changed["cases"][0]["question"] = "修订后的问题"
    assert upload(client, changed).status_code == 422
    identity = original["import_id"]
    client.post(f"/api/evaluation/dataset-imports/{identity}/review", json={"decision": "approved", "reason": "核验"})
    path = assets.root() / "imports" / f"{identity}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["cases"][0]["question"] = "被原地篡改"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="哈希"):
        assets.approved_import(identity)


def test_permissions_tenant_and_storage_failure_are_visible(api, monkeypatch):
    client, app, _ = api
    from backend.app.api.deps import OperatorIdentity, require_user_actor

    app.dependency_overrides[require_user_actor] = lambda: OperatorIdentity(role="admin", actor="user:admin")
    assert upload(client).status_code == 403
    app.dependency_overrides[require_user_actor] = lambda: OperatorIdentity(role="super_admin", actor="user:qa")
    assert client.get("/api/evaluation/dataset-imports", headers={"X-Tenant-Id": "other"}).status_code == 403
    monkeypatch.setattr(assets, "write_once", lambda *args: (_ for _ in ()).throw(OSError("disk full")))
    response = upload(client)
    assert response.status_code == 503
    assert "disk full" not in response.text


@pytest.mark.parametrize("change", ["empty", "unknown", "contradiction", "size"])
def test_schema_boundary_validation(api, change):
    client, _, _ = api
    value = package()
    if change == "empty":
        value["cases"] = []
    elif change == "unknown":
        value["cases"][0]["unexpected"] = True
    elif change == "contradiction":
        value["cases"][0]["expected"]["behavior"] = "refuse"
    else:
        value["cases"] *= 101
    assert upload(client, value).status_code == 422


def test_ambiguous_or_wrong_document_version_blocks_binding(api, monkeypatch):
    client, _, _ = api
    identity = upload(client).json()["import_id"]
    client.post(f"/api/evaluation/dataset-imports/{identity}/review", json={"decision": "approved", "reason": "核验"})
    monkeypatch.setattr(
        assets, "corpus_snapshot", lambda kb: [{"doc_id": "other", "file_name": "", "file_path": "", "version_id": ""}]
    )
    assert (
        client.post(
            "/api/evaluation/evaluation-bindings", json={"import_id": identity, "kb_id": "policy_general"}
        ).status_code
        == 422
    )


def test_oversize_body_is_rejected_before_multipart_parsing(api):
    client, _, _ = api
    response = client.post(
        "/api/evaluation/dataset-imports/validate",
        content=b"x" * (assets.MAX_BYTES + 65537),
        headers={"Content-Type": "multipart/form-data; boundary=bad"},
    )
    assert response.status_code == 413
