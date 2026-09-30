"""发布记录端点测试（M8 / 台账 D8）

断言：
1. 迁移登记：063（061/062 已被并行会话 tool_contract_changes 占用）
2. 列表：行 → dict（gates/gate_details JSON 字符串与 dict 双形态）、result 过滤、分页透传
3. latest：空表骨架 / 有数据时 latest+last_pass（回滚判据）分离
"""
from __future__ import annotations

import json

import pytest


@pytest.fixture(autouse=True)
def _bypass_admin_auth(monkeypatch):
    from backend.app.api.routes import admin_releases as mod

    async def _allow(request):
        return None

    monkeypatch.setattr(mod, "require_admin_user", _allow)


def _row(rid: int, result: str = "PASS") -> tuple:
    return (
        rid, f"sha{rid}", "202609301200",
        json.dumps({f"Gate{i}": result == "PASS" for i in range(13)}),
        json.dumps({"g0": f"commit=sha{rid}"}),
        result, "ops", "2026-09-30 12:00:00+00",
        "2026-09-30 12:05:00+00", "2026-09-30 12:05:00+00",
    )


class _FakeRequest:
    pass


class TestListReleases:
    async def test_rows_mapped_and_json_parsed(self, monkeypatch):
        from backend.app.api.routes import admin_releases as mod

        captured: dict = {}

        def fake_query(sql, params):
            captured.setdefault("sqls", []).append(sql)
            captured.setdefault("params", []).append(params)
            return [_row(2), _row(1, "FAIL")]

        monkeypatch.setattr(mod, "_query", fake_query)
        # 直调路由函数不经过 FastAPI 依赖注入，Query 默认值需显式传
        resp = await mod.list_releases(_FakeRequest(), result="", limit=20, offset=0)
        assert resp["total"] == 2
        assert resp["releases"][0]["git_sha"] == "sha2"
        # JSON 字符串形态解析为 dict
        assert resp["releases"][0]["gates"]["Gate0"] is True
        assert resp["releases"][1]["result"] == "FAIL"
        # 无过滤时：列表查询带 LIMIT/OFFSET，COUNT 查询零参数
        assert captured["params"] == [(20, 0), ()]
        assert "ORDER BY id DESC" in captured["sqls"][0]

    async def test_dict_payload_passthrough_and_filter(self, monkeypatch):
        """psycopg2 返回 dict 形态 JSONB 时不二次解析；result 过滤进 where。"""
        from backend.app.api.routes import admin_releases as mod

        row = list(_row(3))
        row[3] = {"Gate0": True}   # dict 形态（psycopg2 实际行为）
        row[4] = {}
        row[5] = "FAIL"

        def fake_query(sql, params):
            assert "result = %s" in sql
            assert params[0] == "FAIL"
            return [tuple(row)]

        monkeypatch.setattr(mod, "_query", fake_query)
        resp = await mod.list_releases(_FakeRequest(), result="FAIL")
        assert resp["releases"][0]["gates"] == {"Gate0": True}

    async def test_total_reflects_filter(self, monkeypatch):
        from backend.app.api.routes import admin_releases as mod

        def fake_query(sql, params):
            if "COUNT" in sql:
                return [(7,)]
            return []

        monkeypatch.setattr(mod, "_query", fake_query)
        resp = await mod.list_releases(_FakeRequest(), result="PASS")
        assert resp["total"] == 7
        assert resp["releases"] == []


class TestLatestRelease:
    async def test_empty_table_skeleton(self, monkeypatch):
        from backend.app.api.routes import admin_releases as mod

        monkeypatch.setattr(mod, "_query", lambda sql, params: [])
        resp = await mod.latest_release(_FakeRequest())
        assert resp == {"latest": None, "last_pass": None}

    async def test_last_pass_differs_from_latest_fail(self, monkeypatch):
        """最新发布 FAIL 时，last_pass 指向更早的 PASS（回滚判据）。"""
        from backend.app.api.routes import admin_releases as mod

        def fake_query(sql, params):
            if "result='PASS'" in sql:
                return [_row(1)]
            return [_row(2, "FAIL")]

        monkeypatch.setattr(mod, "_query", fake_query)
        resp = await mod.latest_release(_FakeRequest())
        assert resp["latest"]["result"] == "FAIL"
        assert resp["last_pass"]["git_sha"] == "sha1"


class TestMigrationRegistration:
    def test_063_registered_in_migration_targets(self):
        """漏登记会被 verify_migration_state 的 unregistered 守卫拦截，此处快速失败。"""
        from scripts.init_db import MIGRATION_TARGETS

        assert MIGRATION_TARGETS.get("063_release_records.sql") == "memory"
