"""请求级 Prompt 版本绑定测试（治理 #5 / 台账 M4 尾项）

三层断言：
1. current_versions()：进程内快照批量版本（纯内存零 IO）
2. AgentState.prompt_versions 已入 schema——LangGraph updates 流不剥离
   （selection_blocked/_clarify 同款坑的回归防线）
3. trace.tags["prompt_versions"] 格式（k=v 逗号串，排序稳定截断 12）
"""
from __future__ import annotations


class TestCurrentVersions:
    def test_returns_snapshot_versions(self):
        from backend.prompts.service import PromptService, _SnapshotEntry

        svc = PromptService()
        svc._snapshot = {
            "a.system": _SnapshotEntry(template="t1", version=3, variables=[]),
            "b.router": _SnapshotEntry(template="t2", version=7, variables=[]),
        }
        assert svc.current_versions() == {"a.system": 3, "b.router": 7}

    def test_empty_snapshot(self):
        from backend.prompts.service import PromptService

        svc = PromptService()
        svc._snapshot = {}
        assert svc.current_versions() == {}

    def test_never_raises_on_locked(self):
        """并发读（_snapshot_lock 竞争）不抛错——pin 是旁路观测。"""
        from backend.prompts.service import prompt_service

        assert isinstance(prompt_service.current_versions(), dict)


class TestStateSchemaRegistration:
    def test_prompt_versions_in_agent_state_annotations(self):
        """必须入 schema：LangGraph updates 剥离 schema 外键（既有三次事故）。"""
        from backend.orchestration.state import AgentState

        assert "prompt_versions" in AgentState.__annotations__

    def test_initial_state_accepts_prompt_versions(self):
        from backend.orchestration.state import OrchestratorState

        assert "prompt_versions" in OrchestratorState.__annotations__


class TestTraceTagFormat:
    def test_tag_string_format(self):
        """trace.tags 是扁平 dict（PG JSONB），版本串格式 k=v 逗号连接。"""
        pv = {"a.system": 3, "b.router": 7}
        tag = ",".join(f"{k}={v}" for k, v in sorted(pv.items())[:12]) or "none"
        assert tag == "a.system=3,b.router=7"

    def test_tag_truncates_to_12_keys(self):
        pv = {f"k{i:02d}": i for i in range(20)}
        tag = ",".join(f"{k}={v}" for k, v in sorted(pv.items())[:12]) or "none"
        assert tag.count(",") == 11

    def test_empty_versions_tag_none(self):
        pv = {}
        tag = ",".join(f"{k}={v}" for k, v in sorted(pv.items())[:12]) or "none"
        assert tag == "none"
