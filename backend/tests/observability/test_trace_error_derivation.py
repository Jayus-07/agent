"""Trace 错误诊断链路测试 — span 聚合派生 error + 脱敏覆盖 + 内容读取审计。

背景（2026-10-30 数据实证）：错误 trace 顶层 error 常为 {}、span.errors 为 []，
真正的错误码/消息在失败 span 的 metrics.error_code / metrics.error_class /
metrics.error 与 events[].message 中。本文件锁定：
  ① DTO 层从失败 span 聚合派生规范化 error（列表与详情共用）；
  ② 存量顶层 error 已有键优先（不改变既有采集口径）；
  ③ masked/summary 脱敏矩阵覆盖派生 error；
  ④ 详情读取在下发内容时写 ADMIN_ACTION 审计事件（软失败不影响读取）。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes import observability
from backend.app.api.routes._trace_dto import (
    _derive_trace_error,
    stored_dict_to_dto,
    to_trace_dto,
)


def _error_span(**overrides):
    """与 PG trace_store 实测数据同构的失败 tool span（admin_tool_failure_probe）。"""
    span = {
        "span_id": "tool:calculate_tool",
        "parent_id": None,
        "name": "calculate_tool",
        "type": "tool_call",
        "status": "error",
        "duration_ms": 2,
        "metrics": {
            "error_class": "timeout",
            "error_code": "SIMULATED_TIMEOUT",
            "error": "模拟上游响应超时",
            "retries": 0,
        },
        "input": {"tool": "calculate_tool"},
        "output": None,
        "events": [
            {
                "name": "tool.error",
                "level": "error",
                "message": "模拟上游响应超时",
                "attributes": {"error_class": "timeout", "error_code": "SIMULATED_TIMEOUT"},
            }
        ],
        "errors": [],
    }
    span.update(overrides)
    return span


class TestDeriveTraceError:
    def test_derives_from_failed_span_metrics(self):
        err = _derive_trace_error([_error_span()], {})
        assert err["code"] == "SIMULATED_TIMEOUT"
        assert err["class"] == "timeout"
        assert err["message"] == "模拟上游响应超时"
        assert err["error_node"] == "calculate_tool"
        assert err["retry_count"] == 0

    def test_events_message_fallback(self):
        span = _error_span(metrics={"error_class": "timeout"})
        err = _derive_trace_error([span], {})
        assert err["message"] == "模拟上游响应超时"
        assert err["code"] == ""

    def test_stored_error_keys_win(self):
        """存量顶层 error 已有键优先，不被派生值覆盖（向后兼容）。"""
        span = _error_span()
        err = _derive_trace_error([span], {"code": "LEGACY_CODE", "message": "存量消息"})
        assert err["code"] == "LEGACY_CODE"
        assert err["message"] == "存量消息"
        # 派生键补齐存量缺口
        assert err["class"] == "timeout"
        assert err["error_node"] == "calculate_tool"

    def test_no_error_span_returns_base(self):
        span = _error_span(status="success")
        base = {"code": "KEEP"}
        assert _derive_trace_error([span], base) == {"code": "KEEP"}
        assert _derive_trace_error([], base) == {"code": "KEEP"}
        assert _derive_trace_error(None, {}) == {}

    def test_message_truncated_to_200(self):
        span = _error_span(metrics={"error": "x" * 500})
        assert len(_derive_trace_error([span], {})["message"]) == 200


class TestDtoIntegration:
    def test_to_trace_dto_derives_error(self):
        record = {
            "id": "t1",
            "question": "q",
            "answer_preview": "a",
            "duration_ms": 2,
            "status": "",
            "spans": [
                {"span_id": "root", "name": "agent", "type": "agent", "status": "success"},
                _error_span(),
            ],
        }
        dto = to_trace_dto(record)
        assert dto["status"] == "error"
        assert dto["error"]["code"] == "SIMULATED_TIMEOUT"
        assert dto["error"]["message"] == "模拟上游响应超时"

    def test_stored_dict_to_dto_derives_error_from_raw_spans(self):
        """列表 DTO spans 被剥离，但 error 派生在剥离前读取原始 spans。"""
        stored = {
            "id": "t2",
            "question": "q",
            "answer_preview": "a",
            "status": "error",
            "spans": [_error_span()],
        }
        dto = stored_dict_to_dto(stored)
        assert dto["spans"] == []
        assert dto["error"]["code"] == "SIMULATED_TIMEOUT"

    def test_success_trace_error_untouched(self):
        record = {
            "id": "t3",
            "question": "q",
            "answer_preview": "a",
            "duration_ms": 100,
            "status": "success",
            "spans": [
                {"span_id": "root", "name": "agent", "type": "agent", "status": "success"}
            ],
        }
        assert to_trace_dto(record)["error"] == {}


class TestErrorRedactionMatrix:
    def _record(self):
        return {
            "id": "t4",
            "question": "q",
            "answer_preview": "a",
            "duration_ms": 2,
            "status": "error",
            "spans": [
                _error_span(
                    metrics={
                        "error_code": "SIMULATED_TIMEOUT",
                        "error_class": "timeout",
                        "error": "工具调用失败，联系电话13812345678",
                    }
                )
            ],
        }

    def test_masked_level_masks_error_message(self):
        dto = to_trace_dto(self._record(), detail_level="masked")
        assert "13812345678" not in dto["error"]["message"]
        assert dto["error"]["code"] == "SIMULATED_TIMEOUT"

    def test_summary_level_strips_message_keeps_metadata(self):
        dto = to_trace_dto(self._record(), detail_level="summary")
        assert dto["error"]["message"] == ""
        assert dto["error"]["code"] == "SIMULATED_TIMEOUT"
        assert dto["error"]["class"] == "timeout"

    def test_full_level_keeps_original(self):
        dto = to_trace_dto(self._record(), detail_level="full")
        assert dto["error"]["message"] == "工具调用失败，联系电话13812345678"


# ── 内容读取审计（HTTP 层，与 test_observability_trace_authz 同法）──────────


def _trace(trace_id, *, tenant="", question="合成问题", answer="合成答案"):
    tags = {"tenant_id": tenant} if tenant else {}
    return {
        "id": trace_id,
        "session_id": f"sess-{trace_id}",
        "question": question,
        "answer_preview": answer,
        "tags": tags,
        "spans": [],
        "children_ids": [],
    }


def _headers(*, user_id=None, tenant=None, roles=None):
    h = {}
    if user_id:
        h["X-Auth-Type"] = "jwt"
        h["X-User-Id"] = user_id
    if tenant:
        h["X-Tenant-Id"] = tenant
    if roles:
        h["X-User-Roles"] = roles
    return h


class _FakeStore:
    def get(self, trace_id):
        return None

    def list_children(self, parent_id, limit=50, *, tenant_id=None):
        return []


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(observability.router)
    return TestClient(app, raise_server_exceptions=False)


def _patch_store(monkeypatch, trace):
    monkeypatch.setattr(observability.trace_collector, "get", lambda _tid: trace)
    monkeypatch.setattr(observability, "get_trace_store", lambda: _FakeStore())


def test_detail_read_with_content_records_audit(client, monkeypatch):
    _patch_store(monkeypatch, _trace("t1", tenant="A"))
    recorded = []
    import backend.security.events as events_mod

    monkeypatch.setattr(
        events_mod, "record_security_event",
        lambda event_type, **kw: recorded.append((event_type, kw)) or True,
    )

    r = client.get("/observability/traces/t1",
                   headers=_headers(user_id="u1", tenant="A", roles="admin"))
    assert r.status_code == 200
    assert len(recorded) == 1
    event_type, kw = recorded[0]
    assert event_type == "ADMIN_ACTION"
    assert kw["category"] == "trace_content_view"
    assert kw["detail"] == {"trace_id": "t1"}
    # OperatorIdentity.actor 是审计留痕专用标识（JWT 通道为 user:<id>）
    assert kw["user_id"] == "user:u1"


def test_detail_read_without_content_skips_audit(client, monkeypatch):
    _patch_store(monkeypatch, _trace("t2", tenant="A", question="", answer=""))
    recorded = []
    import backend.security.events as events_mod

    monkeypatch.setattr(
        events_mod, "record_security_event",
        lambda event_type, **kw: recorded.append(kw) or True,
    )

    r = client.get("/observability/traces/t2",
                   headers=_headers(user_id="u1", tenant="A", roles="admin"))
    assert r.status_code == 200
    assert recorded == []


def test_detail_unauthorized_skips_audit(client, monkeypatch):
    _patch_store(monkeypatch, _trace("t3", tenant="B"))
    recorded = []
    import backend.security.events as events_mod

    monkeypatch.setattr(
        events_mod, "record_security_event",
        lambda event_type, **kw: recorded.append(kw) or True,
    )

    r = client.get("/observability/traces/t3",
                   headers=_headers(user_id="u1", tenant="A", roles="admin"))
    assert r.status_code == 404
    assert recorded == []


def test_audit_failure_does_not_break_read(client, monkeypatch):
    """审计旁路软失败：写入抛错时读取仍 200。"""
    _patch_store(monkeypatch, _trace("t4", tenant="A"))
    import backend.security.events as events_mod

    def _boom(event_type, **kw):
        raise RuntimeError("events table unavailable")

    monkeypatch.setattr(events_mod, "record_security_event", _boom)

    r = client.get("/observability/traces/t4",
                   headers=_headers(user_id="u1", tenant="A", roles="admin"))
    assert r.status_code == 200
    assert r.json()["question"] == "合成问题"


def test_audit_fires_at_masked_level_too(client, monkeypatch):
    """masked 级别仍下发 PII 打码后的问题/回答 → 属于内容读取，审计照记。

    （summary 级别同理；只有无内容或未授权才不触发，见上方用例。）
    """
    _patch_store(monkeypatch, _trace("t5", tenant="A"))
    recorded = []
    import backend.security.events as events_mod

    monkeypatch.setattr(
        events_mod, "record_security_event",
        lambda event_type, **kw: recorded.append(kw) or True,
    )
    monkeypatch.setattr(
        "backend.app.api.routes._trace_dto.TRACE_DETAIL_LEVEL", "masked",
    )

    r = client.get("/observability/traces/t5",
                   headers=_headers(user_id="u1", tenant="A", roles="admin"))
    assert r.status_code == 200
    assert len(recorded) == 1
