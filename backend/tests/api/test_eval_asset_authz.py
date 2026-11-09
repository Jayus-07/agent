"""评测资产读端点鉴权回归（审计 #10 / #32，2026-11-09）。

修复前：
  - GET /evaluation/cases、/runs、/prompt-version-runs 无任何身份依赖；
  - GET /evaluation/datasets、/datasets/{id}/versions/{v}、/dataset-candidates、/suites 无身份依赖，
    且 dataset_id/version 直接拼路径；
  - POST /evaluation-v2/datasets/evidence-search 只校验 admin 角色，不核对快照 KB 授权。

修复后：读端点统一挂 require_editor_user；版本路径做标识符白名单与根目录收容；
证据检索按 RagAuthorization.can_search_kb 复核快照 KB。
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from backend.app.api.routes import evaluation as evaluation_route
from backend.app.api.routes import evaluation_datasets as datasets_route


def _guards(router) -> dict:
    out = {}
    for r in router.routes:
        if not isinstance(r, APIRoute):
            continue
        out[r.path] = {
            d.call.__name__ for d in r.dependant.dependencies
            if getattr(d.call, "__name__", "").startswith("require_")
        }
    return out


def test_evaluation_reads_require_editor():
    guards = _guards(evaluation_route.router)
    for path in ("/evaluation/cases", "/evaluation/runs", "/evaluation/prompt-version-runs"):
        assert path in guards, f"路由缺失: {path}"
        assert "require_editor_user" in guards[path], f"{path} 未挂 editor 闸: {guards[path]}"


def test_dataset_reads_require_editor():
    guards = _guards(datasets_route.router)
    for path in (
        "/evaluation/datasets",
        "/evaluation/datasets/{dataset_id}/versions/{version}",
        "/evaluation/dataset-candidates",
        "/evaluation/suites",
    ):
        assert path in guards, f"路由缺失: {path}"
        assert "require_editor_user" in guards[path], f"{path} 未挂 editor 闸: {guards[path]}"


@pytest.mark.parametrize("dataset_id,version", [
    ("../etc", "1"),
    ("good", "../../1"),
    ("a/b", "1"),
])
def test_dataset_version_rejects_unsafe_identifiers(dataset_id, version):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(datasets_route.get_dataset_version(dataset_id, version))
    assert exc.value.status_code == 422


def test_evidence_search_denies_snapshot_outside_kb_scope(monkeypatch):
    from backend.app.api.routes import evaluation_v2 as route
    from backend.security.principal import Principal
    from backend.rag import authz as rag_authz

    class _Body:
        snapshot_id = "a" * 24
        query = "x"
        top_k = 3

    monkeypatch.setattr(
        "backend.app.api.routes.evaluation_v2.load", lambda _sid: {"kb_id": "test_kb", "fulltext": []},
        raising=False,
    )
    # load 在函数体内 import，patch 真实模块
    import backend.evaluation.corpus_snapshots as corpus
    monkeypatch.setattr(corpus, "load", lambda _sid: {"kb_id": "test_kb", "fulltext": []})

    class _Denied:
        @classmethod
        def build(cls, principal):
            return type("A", (), {"can_search_kb": staticmethod(lambda kb: False)})()

    monkeypatch.setattr(rag_authz, "RagAuthorization", _Denied)

    principal = Principal(user_id="1", tenant_id="default", authenticated=True)
    from backend.app.api.deps import OperatorIdentity
    with pytest.raises(HTTPException) as exc:
        asyncio.run(route.search_dataset_evidence(
            _Body(), identity=type("I", (), {"tenant_id": "default"})(),
            actor=OperatorIdentity(role="admin", actor="user:1"),
            principal=principal,
        ))
    assert exc.value.status_code == 403
