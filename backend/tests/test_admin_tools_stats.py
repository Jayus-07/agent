"""Tool 治理统计端点测试（M2 / 台账 D2）

断言：
1. 聚合逻辑：mock REGISTRY 样本 → totals/成功率/Top失败/Top错误类正确
2. 埋点：skill 失败出口打 skill_failure_total（error_type=M3 七分类）
3. 清单：34 Tool、契约字段来自 lock、runtime 合并
4. 读路径缓存：lock mtime 缓存 + stats TTL 缓存（治理优化批次）
5. changes 过滤参数校验；探针 ADMIN_ACTION 审计旁路
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _bypass_admin_auth(monkeypatch):
    from backend.app.api.routes import admin_tools as admin_tools_mod

    async def _allow(request):
        return None

    monkeypatch.setattr(admin_tools_mod, "require_admin_user", _allow)
    monkeypatch.setattr(admin_tools_mod, "require_editor_user", _allow)
    # 端点缓存隔离：避免跨测试残留 fake/真实样本
    monkeypatch.setattr(admin_tools_mod, "_STATS_CACHE", {"at": 0.0, "payload": None})
    monkeypatch.setattr(admin_tools_mod, "_LOCK_CACHE", {"mtime_ns": None, "raw": None})


class _FakeSample:
    def __init__(self, name, labels, value):
        self.name, self.labels, self.value = name, labels, value


class _FakeMetric:
    def __init__(self, name, samples):
        self.name, self.samples = name, samples


class TestAggregateToolStats:
    async def test_aggregation_math(self, monkeypatch):
        from backend.app.api.routes import admin_tools as mod

        def fake_collect():
            yield _FakeMetric("agent_tool_calls_total", [
                _FakeSample("agent_tool_calls_total", {"tool": "t1", "domain": "travel", "status": "success"}, 8),
                _FakeSample("agent_tool_calls_total", {"tool": "t1", "domain": "travel", "status": "timeout"}, 2),
                _FakeSample("agent_tool_calls_total", {"tool": "t2", "domain": "cs", "status": "success"}, 5),
                _FakeSample("agent_tool_calls_total", {"tool": "t2", "domain": "cs", "status": "failed"}, 5),
            ])
            yield _FakeMetric("agent_tool_error_class_total", [
                _FakeSample("agent_tool_error_class_total", {"tool": "t1", "domain": "travel", "error_class": "timeout"}, 2),
                _FakeSample("agent_tool_error_class_total", {"tool": "t2", "domain": "cs", "error_class": "business_error"}, 5),
                # _created 伴生序列必须被忽略
                _FakeSample("agent_tool_error_class_total_created", {}, 1),
            ])

        import prometheus_client
        monkeypatch.setattr(prometheus_client.REGISTRY, "collect", fake_collect)
        # 只测进程内 fake Prometheus 样本，避免默认 merged 模式把本机
        # Redis 历史日聚合混进断言。
        report = mod._aggregate_tool_stats(source="process")

        assert report["totals"]["tools_seen"] == 2
        assert report["totals"]["calls"] == 20
        assert report["totals"]["success"] == 13
        assert report["totals"]["failures"] == 7
        assert report["totals"]["success_rate"] == 0.65
        # Top 失败：t2(5) 并列 t1(2)？t2=5 失败在前
        assert report["top_failed_tools"][0]["tool"] == "t2"
        assert report["top_failed_tools"][0]["failures"] == 5
        # Top 错误类：business_error(5) > timeout(2)
        assert report["top_error_classes"][0] == {"error_class": "business_error", "count": 5}
        # 成功率 per-tool
        by_tool = {t["tool"]: t for t in report["tools"]}
        assert by_tool["t1"]["success_rate"] == 0.8
        assert by_tool["t1"]["error_classes"] == {"timeout": 2}

    async def test_empty_registry_is_not_error(self, monkeypatch):
        from backend.app.api.routes import admin_tools as mod

        def fake_collect():
            return iter([])

        import prometheus_client
        monkeypatch.setattr(prometheus_client.REGISTRY, "collect", fake_collect)
        report = mod._aggregate_tool_stats(source="process")
        assert report["totals"]["tools_seen"] == 0
        assert report["totals"]["success_rate"] is None
        assert report["top_failed_tools"] == []


class TestSkillFailureEmission:
    def test_required_failure_records_metric(self, monkeypatch):
        """skill 失败出口 → skill_failure_total{skill, error_type=七分类}。"""
        from backend.skills.base import _record_skill_failure
        from backend.core.tool_runtime.models import ToolStatus

        recorded: list[tuple] = []

        class _FakeCounter:
            def labels(self, **kw):
                recorded.append(tuple(sorted(kw.items())))
                return self

            def inc(self, v=1):
                pass

        import backend.observability.metrics as m
        monkeypatch.setattr(m, "skill_failure_total", _FakeCounter())
        _record_skill_failure("sql", ToolStatus.UNAVAILABLE, "CONN")
        assert recorded == [(("error_type", "network_error"), ("skill", "sql"))]

    def test_metric_emission_never_raises(self):
        from backend.skills.base import _record_skill_failure

        _record_skill_failure("x", None)  # 异常状态也软失败


class TestToolInventory:
    async def test_inventory_merges_lock_contract(self):
        from backend.app.api.routes import admin_tools as mod

        class _FakeRequest:
            pass

        resp = await mod.tool_inventory(_FakeRequest())
        # lock 是唯一事实源：以 lock 实际 Tool 数为准（memory_forget_tool
        # 落库后 39 → 40，此前硬编码断言过时）
        lock_path = Path(mod.__file__).resolve().parents[3] / "tool_contracts.lock.json"
        assert resp["count"] == len(json.load(open(lock_path, encoding="utf-8"))["tools"])
        assert resp["count"] >= 40
        assert not resp["lock_error"]
        # 归属已知限制：单 Tool Skill 主链（_tool_fn）可静态派生（10 个）；
        # SQLSkill(_tool_fn=NotImplementedError)/Competitor 多 Tool 分发不可静态求值
        rag = next(t for t in resp["tools"] if t["name"] == "search_knowledge_tool")
        assert rag["capabilities"] == ["rag.search"]
        assert rag["output_types"].get("rag.search") in ("text", "structured")
        sql = next(t for t in resp["tools"] if t["name"] == "execute_sql_tool")
        assert sql["module"].endswith("sql.py")
        assert len(sql["content_hash"]) == 16


class TestReadPathCaches:
    """治理优化：lock 每请求双读盘、stats 每请求全量 collect 都收敛到缓存。"""

    def test_lock_cache_hits_until_mtime_changes(self, monkeypatch, tmp_path):
        import os

        from backend.app.api.routes import admin_tools as mod

        lock_file = tmp_path / "tool_contracts.lock.json"
        lock_file.write_text('{"git_sha": "abc", "tools": {"t1": {}}}', encoding="utf-8")

        tools, sha, err = mod._load_tool_lock(lock_file)
        assert (tools, sha, err) == ({"t1": {}}, "abc", "")

        # mtime 未变时命中缓存：篡改缓存内容能透出（证明未重读盘）
        mod._LOCK_CACHE["raw"] = {"git_sha": "fake", "tools": {"cached": {}}}
        tools2, sha2, _ = mod._load_tool_lock(lock_file)
        assert (tools2, sha2) == ({"cached": {}}, "fake")

        # 文件变化（mtime 变）后缓存失效，重新读盘
        lock_file.write_text('{"git_sha": "new", "tools": {"t2": {}}}', encoding="utf-8")
        os.utime(lock_file, None)  # 确保 mtime 前移（同秒写入时 stat 精度不够）
        tools3, sha3, _ = mod._load_tool_lock(lock_file)
        assert (tools3, sha3) == ({"t2": {}}, "new")

    async def test_stats_ttl_cache_reuses_payload(self, monkeypatch):
        from backend.app.api.routes import admin_tools as mod

        calls = {"n": 0}

        def fake_aggregate(source=None):
            calls["n"] += 1
            return {"scope": "process", "totals": {"tools_seen": calls["n"]},
                    "tools": [{"tool": f"t{calls['n']}"}]}

        monkeypatch.setattr(mod, "_aggregate_tool_stats", fake_aggregate)
        monkeypatch.setattr(mod, "_STATS_CACHE", {"at": 0.0, "payload": None})

        class _FakeRequest:
            pass

        first = await mod.tool_stats(_FakeRequest())
        second = await mod.tool_stats(_FakeRequest())
        assert calls["n"] == 1  # TTL 内复用同一份聚合
        assert first is second
        assert first["totals"]["tools_seen"] == 1


class TestChangesFilters:
    async def test_invalid_classification_rejected(self):
        from backend.app.api.routes import admin_tools as mod
        from fastapi import HTTPException

        class _FakeRequest:
            pass

        with pytest.raises(HTTPException) as exc:
            await mod.tool_contract_changes(_FakeRequest(), classification="BOGUS")
        assert exc.value.status_code == 422

    async def test_invalid_since_format_rejected(self):
        from backend.app.api.routes import admin_tools as mod
        from fastapi import HTTPException

        class _FakeRequest:
            pass

        with pytest.raises(HTTPException) as exc:
            await mod.tool_contract_changes(_FakeRequest(), since="not-a-date")
        assert exc.value.status_code == 422


class TestFailureProbeAudit:
    async def test_probe_records_admin_action(self, monkeypatch):
        from backend.app.api.routes import admin_tools as mod

        recorded: list[tuple] = []

        class _FakeTrace:
            id = "probe-trace-1"
            session_id = "admin-probe-test"
            tags: dict = {}

        class _FakeCollector:
            def start(self, *a, **kw):
                return _FakeTrace()

            def finish(self, *a, **kw):
                return None

        monkeypatch.setattr(mod, "ENVIRONMENT", "development")
        monkeypatch.setattr(
            "backend.observability.tracer.trace_collector", _FakeCollector())
        monkeypatch.setattr(
            "backend.core.tool_runtime.tracing.start_tool_span", lambda *a, **kw: object())
        monkeypatch.setattr(
            "backend.core.tool_runtime.tracing.finish_tool_span", lambda *a, **kw: None)
        monkeypatch.setattr(
            "backend.core.tool_runtime.metrics.record_tool_result", lambda *a, **kw: None)

        def fake_event(event_type, *, category="", detail=None, **kw):
            recorded.append((event_type, category, detail))
            return True

        monkeypatch.setattr(
            "backend.security.events.record_security_event", fake_event)

        class _FakeRequest:
            pass

        body = mod.ToolFailureProbeRequest(tool="calculate_tool", error_class="timeout")
        resp = await mod.tool_failure_probe(_FakeRequest(), body)

        assert resp["simulated"] is True
        assert recorded and recorded[0][0] == "ADMIN_ACTION"
        assert recorded[0][1] == "tool_failure_probe"
        assert recorded[0][2]["tool"] == "calculate_tool"


def test_admin_action_in_event_whitelist():
    """迁移 113 + events.py 白名单同步：ADMIN_ACTION 可落 security_events。"""
    from backend.security.events import EVENT_TYPES

    assert "ADMIN_ACTION" in EVENT_TYPES
