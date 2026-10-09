"""旅游对话中的版本恢复指代必须走受控的版本账本路径。"""
from __future__ import annotations

import asyncio
from concurrent.futures import Future
from types import SimpleNamespace

import pytest

from backend.app.api.routes import travel
from backend.travel.core.plan_service import PlanVersionConflict


class _Control:
    def check(self):
        return None

    def cancel(self, *_args, **_kwargs):
        return None


class _VersionService:
    def __init__(self, versions: list[dict]):
        self.versions = versions
        self.restore_call: dict | None = None

    def latest_version(self, *_args, **_kwargs):
        return self.versions[0] if self.versions else None

    def active_version(self, *_args, **_kwargs):
        return next(
            (version for version in self.versions
             if version["plan_status"] == "confirmed"),
            None,
        )

    def list_versions(self, *_args, **_kwargs):
        return self.versions

    def restore(self, conversation_id, user_id, *, target_version,
                base_version, tenant_id):
        self.restore_call = {
            "conversation_id": conversation_id,
            "user_id": user_id,
            "target_version": target_version,
            "base_version": base_version,
            "tenant_id": tenant_id,
        }
        current_version = self.versions[0]["plan_version"] if self.versions else None
        if current_version != base_version:
            raise PlanVersionConflict(
                "行程已更新，请基于当前版本重试",
                current_version=current_version,
            )
        return {
            "status": "ok",
            "itinerary": {"plan_version": 3, "brief": {"destination": "福州"}},
            "plan_status": "waiting_confirmation",
            "change_record": {
                "parent_plan_version": base_version,
                "change": {"type": "rollback", "restored_version": target_version},
            },
        }


def _version(version: int, status: str) -> dict:
    return {
        "plan_version": version,
        "plan_status": status,
        "itinerary": {"plan_version": version, "brief": {"destination": "福州"}},
    }


def _request():
    return SimpleNamespace(
        identity=SimpleNamespace(user_id="user-1", tenant_id="tenant-1"),
    )


def _install_inline_worker(
    monkeypatch, *, message="还是换回刚才那个", mode="chat",
    base_plan_version=None,
):
    def submit(_identity, _conversation_id, worker):
        control = _Control()
        future = Future()
        future.set_result(worker(control))
        return SimpleNamespace(future=future, control=control)

    async def load_request(_request):
        return travel.TravelPlanRequest(
            message=message,
            session_id="conversation-1",
            conversation_id="conversation-1",
            mode=mode,
            base_plan_version=base_plan_version,
        )

    monkeypatch.setattr(travel, "_submit_travel_request", submit)
    monkeypatch.setattr(travel, "_load_travel_plan_request", load_request)
    monkeypatch.setattr(travel, "require_identity", lambda request: request.identity)
    monkeypatch.setattr(travel, "_build_travel_graph_input", lambda *_args: {})
    monkeypatch.setattr(travel, "_finish_travel_trace", lambda *_args: None)
    monkeypatch.setattr(travel, "_attach_trace_plan_projection", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "backend.observability.tracer.trace_collector.start",
        lambda *_args, **_kwargs: SimpleNamespace(tags={}, session_id="conversation-1"),
    )


@pytest.mark.asyncio
async def test_chat_restore_of_pending_draft_targets_active_version(monkeypatch):
    """草案待确认时，“换回刚才那个”应恢复 Active 而非被 pending 门拦截。"""
    service = _VersionService([
        _version(2, "waiting_confirmation"),
        _version(1, "confirmed"),
    ])
    monkeypatch.setattr(
        "backend.travel.core.plan_service.plan_version_service", service,
    )
    _install_inline_worker(monkeypatch)

    class Graph:
        def invoke(self, *_args, **_kwargs):
            raise AssertionError("版本恢复不得进入普通规划图")

    monkeypatch.setattr("backend.travel.graph_builder.get_travel_graph", lambda: Graph())

    result = await travel.travel_plan(_request())

    assert result["status"] == "success", result.get("final_answer")
    assert result["result_kind"] == "draft"
    assert result["active_plan_version"] == 1
    assert result["draft_plan_version"] == 3
    assert result["change_record"]["change"]["restored_version"] == 1
    assert service.restore_call == {
        "conversation_id": "conversation-1",
        "user_id": "user-1",
        "target_version": 1,
        "base_version": 2,
        "tenant_id": "tenant-1",
    }


@pytest.mark.asyncio
async def test_chat_restore_without_unique_history_asks_instead_of_planning(monkeypatch):
    """没有历史目标时必须澄清，不能把恢复话语当成新行程规划。"""
    service = _VersionService([_version(1, "confirmed")])
    monkeypatch.setattr(
        "backend.travel.core.plan_service.plan_version_service", service,
    )
    _install_inline_worker(monkeypatch)
    graph_calls = []

    class Graph:
        def invoke(self, *_args, **_kwargs):
            graph_calls.append(True)
            return {"status": "success", "itinerary": {"plan_version": 2}}

    monkeypatch.setattr("backend.travel.graph_builder.get_travel_graph", lambda: Graph())

    result = await travel.travel_plan(_request())

    assert result["status"] == "needs_clarification"
    assert result["result_kind"] == "clarification"
    assert "版本" in result["final_answer"]
    assert graph_calls == []
    assert service.restore_call is None


@pytest.mark.asyncio
async def test_chat_restore_explicit_version_uses_exact_target(monkeypatch):
    """明确版本号必须精确恢复该版，不可按“上一版”猜测。"""
    service = _VersionService([
        _version(3, "waiting_confirmation"),
        _version(2, "confirmed"),
        _version(1, "confirmed"),
    ])
    monkeypatch.setattr(
        "backend.travel.core.plan_service.plan_version_service", service,
    )
    _install_inline_worker(monkeypatch, message="帮我恢复到第 1 版")

    class Graph:
        def invoke(self, *_args, **_kwargs):
            raise AssertionError("显式版本恢复不得进入普通规划图")

    monkeypatch.setattr("backend.travel.graph_builder.get_travel_graph", lambda: Graph())

    result = await travel.travel_plan(_request())

    assert result["status"] == "success"
    assert result["change_record"]["change"]["restored_version"] == 1
    assert service.restore_call["target_version"] == 1
    assert service.restore_call["base_version"] == 3


@pytest.mark.asyncio
async def test_how_to_question_about_restore_is_not_executed_as_rollback(monkeypatch):
    """询问“怎么恢复”是问答，不应静默创建恢复草案。"""
    service = _VersionService([
        _version(2, "confirmed"),
        _version(1, "confirmed"),
    ])
    monkeypatch.setattr(
        "backend.travel.core.plan_service.plan_version_service", service,
    )
    _install_inline_worker(monkeypatch, message="怎么恢复到上一版？")
    graph_calls = []

    class Graph:
        def invoke(self, *_args, **_kwargs):
            graph_calls.append(True)
            return {"status": "answered"}

    monkeypatch.setattr("backend.travel.graph_builder.get_travel_graph", lambda: Graph())
    monkeypatch.setattr(
        "backend.travel.models.graph_result.build_travel_graph_result",
        lambda _state: {
            "status": "answered", "final_answer": "可以从版本历史中选择恢复。",
            "itinerary": None,
        },
    )

    result = await travel.travel_plan(_request())

    assert result["status"] == "answered"
    assert graph_calls == [True]
    assert service.restore_call is None


@pytest.mark.asyncio
async def test_chat_restore_sse_returns_draft_result_without_graph_run(monkeypatch):
    """SSE 入口复用同一恢复路径，并只发真实开始/结束/完成事件。"""
    service = _VersionService([
        _version(2, "waiting_confirmation"),
        _version(1, "confirmed"),
    ])
    monkeypatch.setattr(
        "backend.travel.core.plan_service.plan_version_service", service,
    )
    _install_inline_worker(monkeypatch)

    class Graph:
        def invoke(self, *_args, **_kwargs):
            raise AssertionError("SSE 版本恢复不得进入普通规划图")

    monkeypatch.setattr("backend.travel.graph_builder.get_travel_graph", lambda: Graph())

    response = await travel.travel_plan_stream(_request())
    frames = [frame async for frame in response.body_iterator]
    payload = "".join(frames)

    assert "event: run.started" in payload
    assert "event: run.finished" in payload
    assert '"event": "done"' in payload
    assert '"result_kind": "draft"' in payload
    assert '"restored_version": 1' in payload
    assert service.restore_call["target_version"] == 1


@pytest.mark.asyncio
async def test_chat_restore_unknown_explicit_version_asks_without_planning(monkeypatch):
    """账本不存在的显式目标只澄清，不能猜相邻版本或新建行程。"""
    service = _VersionService([
        _version(2, "confirmed"),
        _version(1, "confirmed"),
    ])
    monkeypatch.setattr(
        "backend.travel.core.plan_service.plan_version_service", service,
    )
    _install_inline_worker(monkeypatch, message="恢复到第 9 版")
    graph_calls = []

    class Graph:
        def invoke(self, *_args, **_kwargs):
            graph_calls.append(True)
            return {}

    monkeypatch.setattr("backend.travel.graph_builder.get_travel_graph", lambda: Graph())

    result = await travel.travel_plan(_request())

    assert result["status"] == "needs_clarification"
    assert "v1" in result["final_answer"]
    assert graph_calls == []
    assert service.restore_call is None


@pytest.mark.asyncio
async def test_chat_restore_preserves_stale_base_version_conflict(monkeypatch):
    """语言恢复仍受原有 base_version CAS 约束，不覆盖并发更新。"""
    service = _VersionService([
        _version(2, "waiting_confirmation"),
        _version(1, "confirmed"),
    ])
    monkeypatch.setattr(
        "backend.travel.core.plan_service.plan_version_service", service,
    )
    _install_inline_worker(monkeypatch, base_plan_version=1)

    class Graph:
        def invoke(self, *_args, **_kwargs):
            raise AssertionError("检测到恢复意图时不得进入规划图")

    monkeypatch.setattr("backend.travel.graph_builder.get_travel_graph", lambda: Graph())

    result = await travel.travel_plan(_request())

    assert result["status"] == "failed"
    assert result["error_type"] == "plan_version_conflict"
    assert service.restore_call["base_version"] == 1


@pytest.mark.asyncio
async def test_read_only_mode_does_not_execute_natural_language_restore(monkeypatch):
    """只读 QA 模式仍禁止版本写入，恢复指令不能绕过 P1-02。"""
    service = _VersionService([
        _version(2, "waiting_confirmation"),
        _version(1, "confirmed"),
    ])
    monkeypatch.setattr(
        "backend.travel.core.plan_service.plan_version_service", service,
    )
    _install_inline_worker(monkeypatch, mode="read_only")
    graph_calls = []
    monkeypatch.setattr(
        travel, "_load_read_only_plan_context",
        lambda *_args: {
            "active_plan_version": 1, "draft_plan_version": 2,
            "reference_itinerary": {"plan_version": 2},
        },
    )

    class ReadOnlyGraph:
        def invoke(self, *_args, **_kwargs):
            graph_calls.append(True)
            return {"status": "answered"}

    monkeypatch.setattr(
        "backend.travel.graph_builder.get_travel_read_only_graph",
        lambda: ReadOnlyGraph(),
    )
    monkeypatch.setattr(
        "backend.travel.models.graph_result.build_travel_graph_result",
        lambda _state: {
            "status": "needs_clarification",
            "final_answer": "当前只读请求不能恢复行程。",
            "clarification": "请在可修改模式下操作。",
            "itinerary": None,
        },
    )

    result = await travel.travel_plan(_request())

    assert result["status"] == "needs_clarification"
    assert graph_calls == [True]
    assert service.restore_call is None
