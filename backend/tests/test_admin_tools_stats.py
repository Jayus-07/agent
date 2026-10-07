"""Tool 治理统计端点测试（M2 / 台账 D2）

断言：
1. 聚合逻辑：mock REGISTRY 样本 → totals/成功率/Top失败/Top错误类正确
2. 埋点：skill 失败出口打 skill_failure_total（error_type=M3 七分类）
3. 清单：34 Tool、契约字段来自 lock、runtime 合并
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _bypass_admin_auth(monkeypatch):
    from backend.app.api.routes import admin_tools as admin_tools_mod

    async def _allow(request):
        return None

    monkeypatch.setattr(admin_tools_mod, "require_admin_user", _allow)


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
        assert resp["count"] == 39
        assert not resp["lock_error"]
        # 归属已知限制：单 Tool Skill 主链（_tool_fn）可静态派生（10 个）；
        # SQLSkill(_tool_fn=NotImplementedError)/Competitor 多 Tool 分发不可静态求值
        rag = next(t for t in resp["tools"] if t["name"] == "search_knowledge_tool")
        assert rag["capabilities"] == ["rag.search"]
        assert rag["output_types"].get("rag.search") in ("text", "structured")
        sql = next(t for t in resp["tools"] if t["name"] == "execute_sql_tool")
        assert sql["module"].endswith("sql.py")
        assert len(sql["content_hash"]) == 16
