"""P2 收官三项回归（审计第七轮，2026-11-03）。

钉死：
  1. OOXML 部件 XML 实体扩展防护：含 DTD/ENTITY 的部件 fail-closed 拒绝
     （合法 OOXML 部件全部是 namespace 化元素，零误伤）；同 xml_parser 口径。
  2. OCR 用量接入统一预算链路：存在请求级预算上下文时 token 计入结算，
     无上下文（后台索引）自然跳过；error 状态不计。
  3. /chat/messages DTO 契约：数量/长度/字段白名单 422 收口，不再裸 dict。
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.rag.preprocessing.parser.pptx_parser import _parse_ooxml_xml


# ── 1. OOXML 部件实体扩展防护 ──

def test_ooxml_parser_rejects_dtd_entity():
    evil = (b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa">]>'
            b"<r>&a;</r>")
    with pytest.raises(ValueError, match="DTD/ENTITY"):
        _parse_ooxml_xml(evil)


def test_ooxml_parser_rejects_billion_laughs_payload():
    """经典实体爆炸 payload：指数展开声明必须在解析前被拒。"""
    evil = (
        b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
        b'<!ENTITY lol1 "&lol;&lol;&lol;&lol;">]><lolz>&lol1;</lolz>'
    )
    with pytest.raises(ValueError, match="DTD/ENTITY"):
        _parse_ooxml_xml(evil)


def test_ooxml_parser_accepts_normal_ooxml_part():
    """合法 OOXML 部件（namespace 化元素）零误伤。"""
    ok = (b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          b'<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
          b'<p:cSld><p:spTree/></p:cSld></p:sld>')
    root = _parse_ooxml_xml(ok)
    assert isinstance(root, ET.Element)
    assert root.tag.endswith("sld")


# ── 2. OCR 预算结算接入 ──

def test_ocr_usage_settles_into_request_budget(monkeypatch):
    from backend.rag.preprocessing.parser import ocr as ocr_mod

    settled: list[dict] = []
    monkeypatch.setattr(
        "backend.infra.llm.budget.record_model_usage",
        lambda **kw: settled.append(kw) or "settled",
    )

    ocr_mod._record_ocr_usage("qwen-vl", 100, 20, 500, status="success")

    assert settled == [{"prompt_tokens": 100, "completion_tokens": 20,
                        "total_tokens": 120}]


def test_ocr_usage_error_status_skips_budget(monkeypatch):
    from backend.rag.preprocessing.parser import ocr as ocr_mod

    settled: list[dict] = []
    monkeypatch.setattr(
        "backend.infra.llm.budget.record_model_usage",
        lambda **kw: settled.append(kw) or "settled",
    )

    ocr_mod._record_ocr_usage("qwen-vl", 0, 0, 500, status="error")

    assert settled == []


# ── 3. /chat/messages DTO 契约 ──

def _client() -> TestClient:
    from backend.app.api.routes import chat as chat_route

    app = FastAPI()
    app.add_api_route(
        "/chat/agent/messages", chat_route.save_messages, methods=["POST"],
    )
    return TestClient(app, raise_server_exceptions=False)


def test_messages_rejects_oversized_batch():
    body = {"session_id": "s1",
            "messages": [{"role": "user", "content": "x"}] * 201}
    resp = _client().post("/chat/agent/messages", json=body)
    assert resp.status_code == 422


def test_messages_rejects_oversized_content_and_bad_fields():
    client = _client()
    long_content = {"session_id": "s1",
                    "messages": [{"role": "user", "content": "x" * 100_001}]}
    assert client.post("/chat/agent/messages", json=long_content).status_code == 422

    missing = {"session_id": "s1", "messages": [{"content": "no role"}]}
    assert client.post("/chat/agent/messages", json=missing).status_code == 422

    bad_session = {"session_id": "", "messages": [{"role": "user", "content": "x"}]}
    assert client.post("/chat/agent/messages", json=bad_session).status_code == 422


def test_messages_valid_payload_passes_validation():
    """合法 payload 到达身份校验（401 guest），证明 DTO 放行正常请求。"""
    body = {"session_id": "s1",
            "messages": [{"role": "user", "content": "hi"},
                         {"role": "assistant", "content": "你好"}]}
    resp = _client().post("/chat/agent/messages", json=body)
    assert resp.status_code == 401  # 未桩身份 → guest 401（DTO 已通过）
