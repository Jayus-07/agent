# -*- coding: utf-8 -*-
"""AI 失败分析 API（决策 #20）：聚类归因 + 优化建议 + 候选评测用例。

真实依赖：PostgreSQL（迁移 121 已落库，-m pg）。LLM 经
monkeypatch backend.infra.llm.llm 打桩（合法 JSON / 垃圾输出 / 抛异常三态）。
覆盖：
  - POST：合法 JSON → 聚类输入边界（prompt 只含 tool/error_class/计数/
    截断摘要，绝不含用户参数原文与 URL/凭据）、防幻觉过滤、落库、
    GET 列表回显；
  - 时间窗过滤（窗外失败不计入）；
  - 空聚类（时间窗内无失败）→ 跳过 LLM 直接落一条空分析；
  - 垃圾输出重试 1 次仍失败 → 502 且不落库；LLM 抛异常 → 503；
  - 非法 window/limit → 400；非 admin → 401/403。
Trace 数据打桩 trace_collector.list（构造 Span/TraceRecord，不污染全局
collector），span.input.params 故意携带敏感数据用于红线断言。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes import admin_tools
from backend.observability.tracer import Span, TraceRecord, trace_collector

pytestmark = pytest.mark.pg

TENANT = "e2e-failure-analysis-tenant"
ADMIN_HEADERS = {
    "X-Auth-Type": "jwt",
    "X-User-Id": "u-e2e-fa-admin",
    "X-User-Roles": "admin",
    "X-Tenant-Id": TENANT,
}
EDITOR_HEADERS = {
    "X-Auth-Type": "jwt",
    "X-User-Id": "u-e2e-fa-editor",
    "X-User-Roles": "editor",
    "X-Tenant-Id": TENANT,
}

# 敏感数据（仅测试内构造，断言它们绝不进入 LLM prompt）
USER_PHONE = "13800138000"
SECRET_KEY = "sk-should-never-leak"
SECRET_HOST = "internal.secret-host.example"

LONG_ERROR_MSG = (
    "上游响应超时 http://%s/v1?token=%s —— 重试 3 次仍未返回，"
    "用户查询城市=上海、门店=人民广场店，请联系管理员" % (SECRET_HOST, SECRET_KEY)
) + "补足三百字符。" * 20

VALID_JSON = json.dumps({"clusters": [{
    "tool": "map.lookup", "error_class": "timeout",
    "attribution": "上游超时预算不足，重试策略未覆盖慢查询",
    "suggestions": ["提高超时预算", "对慢查询增加降级路径"],
    "candidate_eval_cases": [{
        "question": "查询上海人民广场门店的营业时间",
        "expected_capability": "map.lookup",
        "expectation_note": "应在超时预算内返回结果",
    }],
}]}, ensure_ascii=False)


class _FakeLLM:
    """llm.invoke 打桩：记录收到的 messages，按 payload/异常脚本响应。"""

    def __init__(self, payloads: list):
        self.payloads = payloads
        self.calls: list = []

    def invoke(self, messages, **kwargs):
        self.calls.append(messages)
        item = self.payloads[min(len(self.calls) - 1, len(self.payloads) - 1)]
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(content=item)


def _db_exec(sql: str, params: dict | None = None) -> None:
    from backend.infra.db import get_memory_engine

    raw = get_memory_engine().raw_connection()
    try:
        with raw.cursor() as cur:
            cur.execute(sql, params or {})
        raw.commit()
    finally:
        raw.close()


def _db_one(sql: str, params: dict | None = None):
    from backend.infra.db import get_memory_engine

    raw = get_memory_engine().raw_connection()
    try:
        with raw.cursor() as cur:
            cur.execute(sql, params or {})
            row = cur.fetchone()
            return row[0] if row is not None else None
    finally:
        raw.close()


@pytest.fixture(autouse=True)
def _clean():
    _db_exec("DELETE FROM ai.tool_failure_analyses WHERE tenant_id = %(t)s",
             {"t": TENANT})
    yield
    _db_exec("DELETE FROM ai.tool_failure_analyses WHERE tenant_id = %(t)s",
             {"t": TENANT})


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(
        admin_tools, "run_governance_mutation",
        lambda request, operator, operation, payload, callback: callback())
    app = FastAPI()
    app.include_router(admin_tools.router)
    return TestClient(app, raise_server_exceptions=False)


def _mk_record(tool: str, error_class: str, error_msg: str, *,
               age_minutes: int = 0, params: dict | None = None) -> TraceRecord:
    """构造一条含失败 tool_call span 的 Trace（当前时刻回退 age_minutes）。"""
    now = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
    iso = now.isoformat()
    span = Span(
        span_id=f"sp-{uuid.uuid4().hex[:8]}", parent_id=None, name=tool,
        type="tool_call", status="error", start_time=iso, end_time=iso,
        duration_ms=1234,
        input={"tool": tool, "capability": tool, "agent": "test",
               "params": params or {}},
        metrics={"error_class": error_class, "error_code": error_class.upper(),
                 "error": error_msg},
    )
    return TraceRecord(id=f"tr-{uuid.uuid4().hex[:8]}", timestamp=iso,
                       session_id=f"sess-{uuid.uuid4().hex[:6]}",
                       question=f"触发 {tool} 失败的问题", spans=[span])


def _patch_traces(monkeypatch, records: list[TraceRecord]) -> None:
    monkeypatch.setattr(
        trace_collector, "list",
        lambda *args, **kwargs: list(records))


class TestRunAnalysis:
    def test_clusters_prompt_boundary_persist_and_list(self, client, monkeypatch):
        _patch_traces(monkeypatch, [
            # 同聚类 3 条（含敏感 params + 带凭据 URL 的长错误消息）
            _mk_record("map.lookup", "timeout", LONG_ERROR_MSG,
                       params={"phone": USER_PHONE, "api_key": SECRET_KEY}),
            _mk_record("map.lookup", "timeout", "上游响应超时"),
            _mk_record("map.lookup", "timeout", "上游响应超时"),
            # 另一聚类 1 条
            _mk_record("rag.search", "network_error", "网络不可用"),
        ])
        fake = _FakeLLM([VALID_JSON])
        monkeypatch.setattr("backend.infra.llm.llm", fake)

        r = client.post("/admin/tools/failure-analysis", headers=ADMIN_HEADERS,
                        json={"window_minutes": 60, "limit": 50})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["analysis_id"].startswith("tfa_")
        assert body["created_at"]
        assert len(body["clusters"]) == 1
        cluster = body["clusters"][0]
        assert cluster["tool"] == "map.lookup"
        assert cluster["error_class"] == "timeout"
        assert len(cluster["suggestions"]) == 2
        assert cluster["candidate_eval_cases"][0]["question"] == \
            "查询上海人民广场门店的营业时间"

        # prompt 仅一次调用；含聚类统计但绝不包含用户参数原文 / 凭据 / URL
        assert len(fake.calls) == 1
        system_text = fake.calls[0][0][1]
        assert "map.lookup" in system_text
        assert "timeout" in system_text
        assert "失败次数=3" in system_text
        assert "[URL已屏蔽]" in system_text  # URL 已清洗为占位符
        for forbidden in (USER_PHONE, SECRET_KEY, SECRET_HOST, "http://",
                          "https://", "params"):
            assert forbidden not in system_text, forbidden

        # 落库：input_summary 只存聚类计数与摘要，result 存完整 JSON
        row = _db_one(
            "SELECT window_minutes || '|' || (input_summary->'groups')::text "
            "|| '|' || (result->'clusters')::text || '|' || tenant_id "
            "FROM ai.tool_failure_analyses WHERE id = %(i)s",
            {"i": body["analysis_id"]})
        assert row is not None
        window, groups_text, clusters_text, row_tenant = row.split("|", 3)
        assert window == "60"
        assert row_tenant == TENANT
        assert "map.lookup" in groups_text and '"count": 3' in groups_text
        assert USER_PHONE not in groups_text  # input_summary 不含参数原文
        assert "map.lookup" in clusters_text
        assert "attribution" in clusters_text

        # GET 列表回显
        r = client.get("/admin/tools/failure-analysis?limit=10",
                       headers=ADMIN_HEADERS)
        assert r.status_code == 200, r.text
        listing = r.json()
        assert listing["count"] >= 1
        entry = next(a for a in listing["analyses"]
                     if a["id"] == body["analysis_id"])
        assert entry["window_minutes"] == 60
        assert entry["cluster_count"] == 1
        assert entry["result"]["clusters"][0]["attribution"] == \
            "上游超时预算不足，重试策略未覆盖慢查询"

    def test_window_filter_and_hallucination_drop(self, client, monkeypatch):
        _patch_traces(monkeypatch, [
            _mk_record("map.lookup", "timeout", "上游响应超时"),
            # 窗外（2 小时前）失败：window_minutes=60 时不计入
            _mk_record("map.lookup", "timeout", "上游响应超时", age_minutes=120),
        ])
        # LLM 幻觉一个输入中不存在的聚类 → 必须被丢弃
        hallucinated = json.dumps({"clusters": [
            {"tool": "map.lookup", "error_class": "timeout",
             "attribution": "超时", "suggestions": [], "candidate_eval_cases": []},
            {"tool": "ghost.tool", "error_class": "timeout",
             "attribution": "不存在于输入", "suggestions": [],
             "candidate_eval_cases": []},
        ]}, ensure_ascii=False)
        monkeypatch.setattr("backend.infra.llm.llm", _FakeLLM([hallucinated]))

        r = client.post("/admin/tools/failure-analysis", headers=ADMIN_HEADERS,
                        json={"window_minutes": 60, "limit": 50})
        assert r.status_code == 200, r.text
        clusters = r.json()["clusters"]
        assert [c["tool"] for c in clusters] == ["map.lookup"]
        # input_summary 的窗口内计数 = 1（窗外那条被时间窗过滤）
        summary = _db_one(
            "SELECT input_summary FROM ai.tool_failure_analyses "
            "WHERE id = %(i)s", {"i": r.json()["analysis_id"]})
        if isinstance(summary, str):
            summary = json.loads(summary)
        assert summary["groups"][0]["count"] == 1

    def test_empty_window_skips_llm_and_persists_empty(self, client,
                                                       monkeypatch):
        _patch_traces(monkeypatch, [])
        fake = _FakeLLM([])
        monkeypatch.setattr("backend.infra.llm.llm", fake)

        r = client.post("/admin/tools/failure-analysis", headers=ADMIN_HEADERS,
                        json={"window_minutes": 60, "limit": 50})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["clusters"] == []
        assert body["analysis_id"].startswith("tfa_")
        # 无失败数据不调用 LLM（无归因对象，不伪造分析）
        assert fake.calls == []
        # 落一条空分析（如实反映「时间窗内无失败」）
        assert _db_one(
            "SELECT jsonb_array_length(result->'clusters') "
            "FROM ai.tool_failure_analyses WHERE id = %(i)s",
            {"i": body["analysis_id"]}) == 0

    def test_garbage_twice_returns_502_and_no_persist(self, client,
                                                      monkeypatch):
        _patch_traces(monkeypatch, [
            _mk_record("map.lookup", "timeout", "上游响应超时")])
        fake = _FakeLLM(["这不是 JSON 输出", "完全无法解析的内容"])
        monkeypatch.setattr("backend.infra.llm.llm", fake)

        r = client.post("/admin/tools/failure-analysis", headers=ADMIN_HEADERS,
                        json={"window_minutes": 60, "limit": 50})
        assert r.status_code == 502
        assert "解析失败" in r.json()["detail"]
        # 重试恰好 1 次：共 2 次 LLM 调用；失败不落库（不伪造分析）
        assert len(fake.calls) == 2
        assert _db_one(
            "SELECT count(*) FROM ai.tool_failure_analyses "
            "WHERE tenant_id = %(t)s", {"t": TENANT}) == 0

    def test_llm_unavailable_returns_503(self, client, monkeypatch):
        _patch_traces(monkeypatch, [
            _mk_record("map.lookup", "timeout", "上游响应超时")])
        fake = _FakeLLM([RuntimeError("当前未配置可用模型")])
        monkeypatch.setattr("backend.infra.llm.llm", fake)

        r = client.post("/admin/tools/failure-analysis", headers=ADMIN_HEADERS,
                        json={"window_minutes": 60, "limit": 50})
        assert r.status_code == 503
        assert "未配置可用模型" in r.json()["detail"]
        assert len(fake.calls) == 1

    def test_invalid_window_and_limit_400(self, client, monkeypatch):
        _patch_traces(monkeypatch, [])
        fake = _FakeLLM([])
        monkeypatch.setattr("backend.infra.llm.llm", fake)
        for field, value in (("window_minutes", 4), ("window_minutes", 1441),
                             ("limit", 9), ("limit", 101)):
            payload = {"window_minutes": 60, "limit": 50}
            payload[field] = value
            r = client.post("/admin/tools/failure-analysis",
                            headers=ADMIN_HEADERS, json=payload)
            assert r.status_code == 400, (field, value, r.text)
            assert field in r.json()["detail"]
        assert fake.calls == []  # 校验失败不触发 LLM

    def test_run_requires_admin_and_list_allows_editor(self, client,
                                                       monkeypatch):
        _patch_traces(monkeypatch, [])
        r = client.post("/admin/tools/failure-analysis", headers=EDITOR_HEADERS,
                        json={"window_minutes": 60, "limit": 50})
        assert r.status_code in (401, 403)
        r = client.get("/admin/tools/failure-analysis?limit=10",
                       headers=EDITOR_HEADERS)
        assert r.status_code == 200
