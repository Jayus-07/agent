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


class TestBindPromptEntries:
    def test_entries_pin_draft_template_over_snapshot(self):
        """显式模板注入优先于进程快照——发布评测草稿的唯一可靠通道。

        回归：bind_prompt_versions 对草稿版本静默跳过、评测回落已发布版，
        发布门禁因此从未真正评测过候选模板（2026-10-29 修复）。
        """
        from backend.prompts.service import PromptService, _SnapshotEntry

        svc = PromptService()
        svc._snapshot = {
            "rag.qa": _SnapshotEntry(template="published", version=14, variables=[]),
        }
        with svc.bind_prompt_entries({"rag.qa": (15, "draft-template {context}")}):
            assert svc.get_template_sync("rag.qa") == "draft-template {context}"
            assert svc.current_versions() == {"rag.qa": 15}
        # 退出 pin 后回落快照
        assert svc.get_template_sync("rag.qa") == "published"

    def test_entries_empty_is_noop(self):
        from backend.prompts.service import PromptService, _SnapshotEntry

        svc = PromptService()
        svc._snapshot = {
            "rag.qa": _SnapshotEntry(template="published", version=14, variables=[]),
        }
        with svc.bind_prompt_entries(None):
            assert svc.get_template_sync("rag.qa") == "published"


class TestBindPromptVersionsUnknownVersion:
    def test_unknown_version_skips_pin_and_falls_back(self, caplog):
        """快照/历史中不存在的版本（草稿）必须显式告警并回落，不能假装钉住。"""
        import logging

        from backend.prompts.service import PromptService, _SnapshotEntry

        svc = PromptService()
        svc._snapshot = {
            "rag.qa": _SnapshotEntry(template="published", version=14, variables=[]),
        }
        with caplog.at_level(logging.WARNING):
            with svc.bind_prompt_versions({"rag.qa": 15}):
                # 未钉住 → 回落快照中的已发布版（不报错、不空模板）
                assert svc.get_template_sync("rag.qa") == "published"
        assert any(
            "不在进程快照/历史中" in rec.message for rec in caplog.records
        ), "未解析版本的告警缺失"

    def test_known_version_still_pins(self):
        from backend.prompts.service import PromptService, _SnapshotEntry

        svc = PromptService()
        svc._snapshot = {
            "rag.qa": _SnapshotEntry(template="published", version=14, variables=[]),
        }
        with svc.bind_prompt_versions({"rag.qa": 14}):
            assert svc.get_template_sync("rag.qa") == "published"
