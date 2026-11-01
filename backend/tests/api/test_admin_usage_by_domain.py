# -*- coding: utf-8 -*-
"""capability-governance G6：LLM 成本按域归因报表（llm_usage ⋈ trace_summary）。

真库集成（agent_memory@5433，p9g- 前缀行用后即清）：
- 数值：种构造行（唯一 workflow_name='p9g-wf'）→ 端点该域 usage_rows/
  tokens/cost 与构造一致，by_component 分解正确
- 覆盖率：coverage 段显式披露 join 覆盖（join_partner=trace_summary），
  不伪造 100%
- 门禁：require_editor_user（对齐 /admin/tools/stats 同层）
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes import admin_tools

_PREFIX = "p9g-"
_WF = "p9g-wf"


def _pg():
    env = {}
    for line in open(".env", encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k] = v
    return psycopg2_connect(env)


def psycopg2_connect(env):
    import psycopg2

    return psycopg2.connect(
        host=env.get("PGHOST", "localhost"),
        port=int(env.get("PGPORT", "5433")),
        dbname=env.get("PGDATABASE", "agent_memory"),
        user=env.get("PGUSER", "postgres"),
        password=env.get("PGPASSWORD", ""),
        connect_timeout=10,
    )


def _cleanup():
    with _pg() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM public.llm_usage WHERE trace_id LIKE %s",
                    (_PREFIX + "%",))
        cur.execute("DELETE FROM trace_summary WHERE trace_id LIKE %s",
                    (_PREFIX + "%",))
        conn.commit()


def _seed(trace_id: str, workflow_name: str, *, component: str = "llm",
          tokens: int = 100, cost: float = 0.5) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    with _pg() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO trace_summary (trace_id, ts, session_id, workflow_name,"
            "  created_at)"
            " VALUES (%s, %s, %s, %s, %s) ON CONFLICT (trace_id) DO NOTHING",
            (trace_id, now_iso, _PREFIX + "sess", workflow_name, now_iso),
        )
        cur.execute(
            "INSERT INTO public.llm_usage (ts, trace_id, session_id, tenant_id,"
            " component, model, total_tokens, cost_usd, created_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (now_iso, trace_id, _PREFIX + "sess", "tenant-p9g",
             component, "p9g-model", tokens, cost, now_iso),
        )
        conn.commit()


@pytest.fixture
def db():
    _cleanup()
    yield
    _cleanup()


def _editor_client(role: str = "editor") -> TestClient:
    app = FastAPI()
    app.include_router(admin_tools.router)
    # require_editor_user 是端点体内直接调用（非 Depends），须 monkeypatch 模块名
    from backend.app.api.deps import require_editor_user

    return TestClient(app, raise_server_exceptions=False)


def _allow_editor(monkeypatch, role: str = "editor") -> None:
    class _Op:
        user_id = "u-g6"
        tenant_id = "tenant-p9g"
        user_name = "g6-editor"
        roles = (role,)

    async def _ok(_request):
        return _Op()

    monkeypatch.setattr(admin_tools, "require_editor_user", _ok)


def _deny_editor(monkeypatch) -> None:
    from fastapi import HTTPException

    async def _deny(_request):
        raise HTTPException(403, "forbidden")

    monkeypatch.setattr(admin_tools, "require_editor_user", _deny)


class TestUsageByDomain:
    def test_viewer_forbidden(self, monkeypatch):
        _deny_editor(monkeypatch)
        client = _editor_client()
        r = client.get("/admin/tools/usage-by-domain")
        assert r.status_code == 403

    def test_domain_numbers_match_seeded(self, db, monkeypatch):
        _allow_editor(monkeypatch)
        _seed(_PREFIX + "t1", _WF, component="llm", tokens=100, cost=0.5)
        _seed(_PREFIX + "t1", _WF, component="embedding", tokens=30, cost=0.01)
        _seed(_PREFIX + "t2", _WF, component="llm", tokens=200, cost=1.0)
        # 无 trace 上下文的行不参与归因（进 coverage 未命中）
        with _pg() as conn, conn.cursor() as cur:
            now_iso = datetime.now(timezone.utc).isoformat()
            cur.execute(
                "INSERT INTO public.llm_usage (ts, trace_id, session_id,"
                " tenant_id, component, model, total_tokens, cost_usd, created_at)"
                " VALUES (%s, '', %s, 'tenant-p9g', 'embedding', 'm', 10, 0.001, %s)",
                (now_iso, _PREFIX + "sess", now_iso),
            )
            conn.commit()

        client = _editor_client()
        r = client.get("/admin/tools/usage-by-domain?days=7")
        assert r.status_code == 200
        body = r.json()

        assert body["scope"] == "db-join"
        cov = body["coverage"]
        assert cov["join_partner"].startswith("trace_summary")
        assert 0 < cov["coverage_ratio"] < 1, "覆盖率必须如实披露而非恒 1"

        wf_rows = [d for d in body["domains"] if d["domain_key"] == _WF]
        assert len(wf_rows) == 1, f"构造域行缺失: {[d['domain_key'] for d in body['domains']]}"
        row = wf_rows[0]
        assert row["usage_rows"] == 3
        assert row["total_tokens"] == 330
        assert abs(row["cost_usd"] - 1.51) < 1e-6
        assert row["by_component"] == {"llm": 2, "embedding": 1, "rerank": 0}
