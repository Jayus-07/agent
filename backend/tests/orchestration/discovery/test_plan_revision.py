# -*- coding: utf-8 -*-
"""运行中任务多轮计划修订（决策 #29）——服务层 + API（pg + LLM monkeypatch）。

真实依赖：PostgreSQL（迁移 124 ai.plan_revisions + tasks 表，-m pg）。
Mock 边界（只 mock 外部/重依赖，不扩大 mock）：
  - LLM：monkeypatch backend.infra.llm.llm（修订起草的模型调用）；
  - checkpoint 读写：_load_graph_state / _patch_task_plan（真实链路依赖
    PostgresSaver + 完整任务图，语义等同执行器既有 resume 注入通道，
    本文件不重复搭建整图）；
  - 重新入图入口：backend.tasks.task_manager.resume_task（执行链入口）。
覆盖：
  - 并发版本冲突 409（draft 版本不匹配 / 双 draft 同 base 先应用者胜）
  - success 步骤被 LLM 试图改动 → 服务端拒绝（不信任模型输出）
  - 新增步骤 / 新增写操作 capability → 拒绝
  - 修订含 travel.* → agent 产品授权过滤剔除（越权能力拒绝）
  - pending/failed 修订成功落 draft + changes 准确（含 skipped 级联重发）
  - apply 后 new_version 递增 + resume 执行入口被调 + 步骤状态复核
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.models.task import TaskStatus
from backend.orchestration.discovery import plan_revision as pr
from backend.services import task_service

pytestmark = pytest.mark.pg

TENANT = "e2e-planrev-tenant"
USER_ID = "u-e2e-planrev"
OTHER_USER = "u-e2e-planrev-other"
HEADERS = {
    "X-Auth-Type": "jwt",
    "X-User-Id": USER_ID,
    "X-Tenant-Id": TENANT,
}
OTHER_HEADERS = {
    "X-Auth-Type": "jwt",
    "X-User-Id": OTHER_USER,
    "X-Tenant-Id": TENANT,
}

# 步骤布局：1 success / 2 failed / 3 skipped(依赖1,2) / 4 skipped(依赖3) /
# 5 pending（独立，可作废）
PLAN = {
    "nodes": {
        "1": {"step_id": "1", "capability": "rag.search",
              "description": "检索退货制度", "params": {"question": "退货政策"}},
        "2": {"step_id": "2", "capability": "sql.query",
              "description": "查询销售额", "params": {"question": "上季度销售额"}},
        "3": {"step_id": "3", "capability": "business.analyze",
              "description": "分析业务风险", "params": {}},
        "4": {"step_id": "4", "capability": "report.generate",
              "description": "生成报告", "params": {"report_type": "daily_sales"}},
        "5": {"step_id": "5", "capability": "rag.search",
              "description": "补充检索", "params": {"question": "物流时效"}},
    },
    "edges": {"3": ["1", "2"], "4": ["3"]},
}
STEP_RESULTS = {
    "1": {"step_id": "1", "capability": "rag.search", "status": "success",
          "output": {"summary": "找到3条退货相关制度"}},
    "2": {"step_id": "2", "capability": "sql.query", "status": "failed",
          "error": "SQL 语法错误"},
    "3": {"step_id": "3", "capability": "business.analyze", "status": "skipped",
          "error": "前置步骤执行失败"},
    "4": {"step_id": "4", "capability": "report.generate", "status": "skipped",
          "error": "前置步骤执行失败"},
    # 5 无条目 → pending（supervisor 同口径）
}


class _StubResponse:
    def __init__(self, payload: dict):
        self.content = json.dumps(payload, ensure_ascii=False)


class _StubLLM:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls: list[dict] = []

    def invoke(self, messages, **kwargs):
        self.calls.append({"messages": messages})
        return _StubResponse(self.payload)


def _db_exec(sql: str, params: dict | None = None) -> None:
    from backend.infra.db import get_memory_engine

    raw = get_memory_engine().raw_connection()
    try:
        with raw.cursor() as cur:
            cur.execute(sql, params or {})
        raw.commit()
    finally:
        raw.close()


@pytest.fixture()
def task_row():
    """真实 tasks 行（thread_id = task-<task_id> 惯例）。"""
    record = task_service.create_task(
        USER_ID, "查销售额并分析风险", tenant_id=TENANT)
    yield record
    _db_exec("DELETE FROM ai.plan_revisions WHERE thread_id = %(t)s",
             {"t": record.thread_id})
    _db_exec("DELETE FROM tasks WHERE id = %(i)s", {"i": record.id})


@pytest.fixture()
def env(monkeypatch, task_row):
    """固定 state 读取 + 可编程 LLM stub；返回 (thread_id, stub 控制器)。"""
    holder: dict = {"llm_payload": {"revisions": [], "discard_step_ids": []}}
    state = {"plan": json.loads(json.dumps(PLAN)),
             "step_results": json.loads(json.dumps(STEP_RESULTS)),
             "request_context": {"product_id": "agent"}}
    patched: dict = {"calls": []}

    monkeypatch.setattr(pr, "_load_graph_state", lambda tid: state)
    monkeypatch.setattr(
        pr, "_patch_task_plan",
        lambda tid, plan, sr_patch: patched["calls"].append(
            {"thread_id": tid, "plan": plan, "step_results_patch": sr_patch}))
    monkeypatch.setattr(
        "backend.infra.llm.llm", _StubLLM(holder["llm_payload"]))
    holder["patched"] = patched
    holder["state"] = state
    return task_row.thread_id, holder


def _set_llm_payload(holder: dict, payload: dict) -> None:
    from backend.infra.llm import llm as llm_pkg

    llm_pkg.payload = payload


def _task_id(thread_id: str) -> str:
    return thread_id.removeprefix("task-")


def _prepare_draft(env, payload: dict | None = None) -> dict:
    """起草一份标准修订（改 failed 步骤 2 + 作废 pending 步骤 5）。"""
    thread_id, holder = env
    _set_llm_payload(holder, payload or {
        "revisions": [{"step_id": "2", "params": {"question": "按月"},
                       "note": "按月"}],
        "discard_step_ids": ["5"],
    })
    return pr.draft_revision(thread_id, TENANT, USER_ID, "按月统计",
                             base_plan_version=0)


@pytest.fixture()
def client(monkeypatch):
    app = FastAPI()
    from backend.app.api.routes import plan_revisions as routes

    app.include_router(routes.router)
    return TestClient(app, raise_server_exceptions=False)


class TestDraftRevision:
    def test_revises_failed_step_with_cascade_and_changes(self, env):
        thread_id, holder = env
        _set_llm_payload(holder, {
            "revisions": [{"step_id": "2", "capability": "sql.query",
                           "description": "按月查询销售额",
                           "params": {"question": "按月聚合的销售额"},
                           "note": "改为按月聚合"}],
            "discard_step_ids": ["5"],
        })
        draft = pr.draft_revision(thread_id, TENANT, USER_ID,
                                  "销售额改成按月统计，补充检索不要了",
                                  base_plan_version=0)
        assert draft["status"] == "draft"
        assert draft["base_plan_version"] == 0
        assert draft["new_version"] == 1
        assert draft["id"].startswith("planrev_")

        plan = draft["revised_plan"]
        # failed 步骤以新 id 重发（reducer 不可降级，degradation 同款先例）
        assert "2" not in plan["nodes"] and "2r1" in plan["nodes"]
        assert plan["nodes"]["2r1"]["params"] == {"question": "按月聚合的销售额"}
        # skipped 下游机械级联重发
        assert "3r1" in plan["nodes"] and "4r1" in plan["nodes"]
        assert plan["edges"]["3r1"] == ["1", "2r1"]
        assert plan["edges"]["4r1"] == ["3r1"]
        # 作废引用移除 + 保留步骤原样
        assert "5" not in plan["nodes"]
        assert plan["nodes"]["1"]["capability"] == "rag.search"

        actions = {c["step_id"]: c["action"] for c in draft["changes"]}
        assert actions["2"] == "modified"
        assert actions["3"] == "modified"  # 级联
        assert actions["4"] == "modified"  # 级联
        assert actions["5"] == "discarded"
        assert actions["1"] == "kept"
        discarded = next(c for c in draft["changes"] if c["step_id"] == "5")
        assert "不会被撤销" in discarded["note"]
        modified = next(c for c in draft["changes"] if c["step_id"] == "2")
        assert modified["note"] == "改为按月聚合"

        # 落库校验
        rows = pr._query_all(
            "SELECT * FROM ai.plan_revisions WHERE id = %(i)s",
            {"i": draft["id"]})
        assert rows and rows[0]["status"] == "draft"
        assert rows[0]["thread_id"] == thread_id

    def test_rejects_modifying_success_step(self, env):
        thread_id, holder = env
        _set_llm_payload(holder, {
            "revisions": [{"step_id": "1", "description": "试图改成功步骤"}],
            "discard_step_ids": [],
        })
        with pytest.raises(pr.RevisionInvalid) as ei:
            pr.draft_revision(thread_id, TENANT, USER_ID, "改一下检索",
                              base_plan_version=0)
        assert "已成功执行" in ei.value.message
        assert "不会被撤销" in ei.value.message

    def test_rejects_new_step_and_unknown_step(self, env):
        thread_id, holder = env
        _set_llm_payload(holder, {
            "revisions": [{"step_id": "9", "capability": "rag.search",
                           "description": "凭空新增"}],
            "discard_step_ids": [],
        })
        with pytest.raises(pr.RevisionInvalid) as ei:
            pr.draft_revision(thread_id, TENANT, USER_ID, "加一步",
                              base_plan_version=0)
        assert "不支持新增步骤" in ei.value.message

    def test_rejects_write_capability(self, env):
        thread_id, holder = env
        # email.send 治理声明 operation=send（写操作）
        _set_llm_payload(holder, {
            "revisions": [{"step_id": "2", "capability": "email.send",
                           "params": {"content": "群发"}}],
            "discard_step_ids": [],
        })
        with pytest.raises(pr.RevisionInvalid) as ei:
            pr.draft_revision(thread_id, TENANT, USER_ID, "顺便发个邮件",
                              base_plan_version=0)
        assert "写操作" in ei.value.message

    def test_rejects_travel_capability_under_agent_product(self, env):
        thread_id, holder = env
        # agent 产品禁行 travel.*：修订为 travel.poi_search 被授权过滤拒绝
        _set_llm_payload(holder, {
            "revisions": [{"step_id": "2", "capability": "travel.poi_search"}],
            "discard_step_ids": [],
        })
        with pytest.raises(pr.RevisionInvalid) as ei:
            pr.draft_revision(thread_id, TENANT, USER_ID, "改查景点",
                              base_plan_version=0)
        assert "授权" in ei.value.message

    def test_version_conflict_409(self, env):
        thread_id, holder = env
        _set_llm_payload(holder, {"revisions": [], "discard_step_ids": []})
        with pytest.raises(pr.RevisionConflict):
            pr.draft_revision(thread_id, TENANT, USER_ID, "乱写的指令",
                              base_plan_version=5)

    def test_no_revisable_steps(self, env):
        thread_id, holder = env
        # 全部步骤 success → 无可修订对象
        holder["state"]["step_results"] = {
            sid: {"status": "success"} for sid in PLAN["nodes"]}
        _set_llm_payload(holder, {"revisions": [], "discard_step_ids": []})
        with pytest.raises(pr.RevisionInvalid) as ei:
            pr.draft_revision(thread_id, TENANT, USER_ID, "随便改改",
                              base_plan_version=0)
        assert "没有 pending/failed 步骤" in ei.value.message

    def test_empty_instruction_rejected(self, env):
        thread_id, _ = env
        with pytest.raises(pr.RevisionInvalid):
            pr.draft_revision(thread_id, TENANT, USER_ID, "  ",
                              base_plan_version=0)


class TestApplyRevision:
    def test_apply_marks_applied_patches_plan_and_calls_resume(self, env, monkeypatch):
        thread_id, holder = env
        draft = _prepare_draft(env)

        # 任务置 PAUSED（apply 仅在任务静止时可用）
        task_service.update_status(_task_id(thread_id), TaskStatus.PAUSED)

        resume_calls: list[dict] = []

        def _fake_resume(task_id, user_input="", *, allow_failed=False,
                         dispatch_type="resume"):
            resume_calls.append({"task_id": task_id, "allow_failed": allow_failed,
                                 "dispatch_type": dispatch_type})
            return task_service.get_task(task_id)

        monkeypatch.setattr("backend.tasks.task_manager.resume_task", _fake_resume)

        result = pr.apply_revision(draft["id"], TENANT, USER_ID,
                                   base_plan_version=0)
        assert result["status"] == "applied"
        assert result["new_version"] == 1

        # checkpoint 写回：revised plan 原样 + pending 条目标记 skipped
        assert len(holder["patched"]["calls"]) == 1
        call = holder["patched"]["calls"][0]
        assert call["thread_id"] == thread_id
        assert call["plan"]["nodes"]["2r1"]["params"] == {"question": "按月"}
        assert call["step_results_patch"]["5"]["status"] == "skipped"
        # failed 条目不可降级 → 不在补丁里（历史事实不改写）
        assert "2" not in call["step_results_patch"]

        # 既有执行链入口被调
        assert len(resume_calls) == 1
        assert resume_calls[0]["task_id"] == _task_id(thread_id)
        assert resume_calls[0]["dispatch_type"] == "plan_revision"
        assert resume_calls[0]["allow_failed"] is False

        # 版本账本推进
        assert pr._current_plan_version(thread_id) == 1

    def test_apply_twice_conflict(self, env, monkeypatch):
        thread_id, holder = env
        draft = _prepare_draft(env)
        task_service.update_status(_task_id(thread_id), TaskStatus.PAUSED)
        monkeypatch.setattr(
            "backend.tasks.task_manager.resume_task",
            lambda task_id, **kw: task_service.get_task(task_id))
        pr.apply_revision(draft["id"], TENANT, USER_ID, base_plan_version=0)
        with pytest.raises(pr.RevisionConflict) as ei:
            pr.apply_revision(draft["id"], TENANT, USER_ID, base_plan_version=0)
        assert "仅 draft 可应用" in ei.value.message

    def test_double_draft_same_base_first_apply_wins(self, env, monkeypatch):
        thread_id, holder = env
        d1 = _prepare_draft(env)
        d2 = _prepare_draft(env)  # 同 base 第二份 draft（同 new_version 共存）
        assert d1["id"] != d2["id"]
        task_service.update_status(_task_id(thread_id), TaskStatus.PAUSED)
        monkeypatch.setattr(
            "backend.tasks.task_manager.resume_task",
            lambda task_id, **kw: task_service.get_task(task_id))
        pr.apply_revision(d1["id"], TENANT, USER_ID, base_plan_version=0)
        with pytest.raises(pr.RevisionConflict) as ei:
            pr.apply_revision(d2["id"], TENANT, USER_ID, base_plan_version=0)
        assert "版本已变化" in ei.value.message

    def test_apply_rejects_running_task(self, env):
        thread_id, holder = env
        draft = _prepare_draft(env)
        task_service.update_status(_task_id(thread_id), TaskStatus.RUNNING)
        with pytest.raises(pr.RevisionConflict) as ei:
            pr.apply_revision(draft["id"], TENANT, USER_ID, base_plan_version=0)
        assert "暂停" in ei.value.message

    def test_apply_rejects_when_step_status_drifted(self, env):
        thread_id, holder = env
        draft = _prepare_draft(env)
        task_service.update_status(_task_id(thread_id), TaskStatus.PAUSED)
        # 起草后步骤状态漂移：failed → success（并发执行推进）
        holder["state"]["step_results"]["2"] = {
            "step_id": "2", "status": "success", "output": "补跑成功"}
        with pytest.raises(pr.RevisionConflict) as ei:
            pr.apply_revision(draft["id"], TENANT, USER_ID, base_plan_version=0)
        assert "状态已变为" in ei.value.message


class TestPlanRevisionAPI:
    def test_requires_identity(self, client):
        r = client.post("/plan-revisions", json={
            "thread_id": "task-x", "instruction": "改", "base_plan_version": 0})
        assert r.status_code == 401

    def test_draft_and_list_roundtrip(self, client, env, monkeypatch):
        thread_id, holder = env
        _set_llm_payload(holder, {
            "revisions": [{"step_id": "2", "params": {"question": "按月"}}],
            "discard_step_ids": [],
        })
        r = client.post("/plan-revisions", headers=HEADERS, json={
            "thread_id": thread_id, "instruction": "按月统计",
            "base_plan_version": 0})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["id"].startswith("planrev_")
        assert body["changes"][0]["action"] == "modified"

        r = client.get("/plan-revisions", headers=HEADERS,
                       params={"thread_id": thread_id})
        assert r.status_code == 200
        items = r.json()["revisions"]
        assert r.json()["count"] == 1
        assert items[0]["id"] == body["id"]
        assert "revised_plan" not in items[0]  # 列表不回传完整计划

    def test_ownership_isolation(self, client, env):
        thread_id, holder = env
        _set_llm_payload(holder, {"revisions": [], "discard_step_ids": []})
        # 他人 thread → 404（不泄露存在性）
        r = client.post("/plan-revisions", headers=OTHER_HEADERS, json={
            "thread_id": thread_id, "instruction": "改",
            "base_plan_version": 0})
        assert r.status_code == 404
        # 他人列表 → 空
        r = client.get("/plan-revisions", headers=OTHER_HEADERS,
                       params={"thread_id": thread_id})
        assert r.status_code == 200
        assert r.json()["revisions"] == []

    def test_apply_conflict_maps_409(self, client, env, monkeypatch):
        thread_id, holder = env
        draft = _prepare_draft(env)
        task_service.update_status(_task_id(thread_id), TaskStatus.PAUSED)
        # base 版本不匹配 → 409
        r = client.post(f"/plan-revisions/{draft['id']}/apply", headers=HEADERS,
                        json={"base_plan_version": 9})
        assert r.status_code == 409
        monkeypatch.setattr(
            "backend.tasks.task_manager.resume_task",
            lambda task_id, **kw: task_service.get_task(task_id))
        r = client.post(f"/plan-revisions/{draft['id']}/apply", headers=HEADERS,
                        json={"base_plan_version": 0})
        assert r.status_code == 200, r.text
        assert r.json()["new_version"] == 1
