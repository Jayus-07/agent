"""test_chat_input_limit.py — 聊天输入限制（Chat/RAG 收口 2026-09-22）

覆盖：
- Schema 边界：< limit 通过 / = limit 通过 / > limit ValidationError
- /chat/stream 超长 → 422 + 业务语义（details.reason=CHAT_INPUT_TOO_LARGE +
  limit_chars），不再泄露 Pydantic 实现细节
- 请求体字节上限（content-length > CHAT_INPUT_MAX_BYTES）→ 同业务错误
- Guard 对齐：GUARD_MAX_INPUT_CHARS/TOKENS 默认值跟随 CHAT_INPUT 配置
- env override 生效
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import backend.config as config
from backend.config.chat_input import (
    CHAT_INPUT_MAX_BYTES,
    CHAT_INPUT_MAX_CHARS,
)


# ── Schema 边界 ─────────────────────────────────────────────────

class TestChatRequestSchema:
    def _validate(self, question: str) -> None:
        from backend.app.api.schemas import ChatRequest
        ChatRequest(question=question, session_id="s")

    def test_under_limit_passes(self):
        self._validate("a" * (CHAT_INPUT_MAX_CHARS - 1))

    def test_exact_limit_passes(self):
        self._validate("a" * CHAT_INPUT_MAX_CHARS)

    def test_over_limit_rejected(self):
        with pytest.raises(ValidationError):
            self._validate("a" * (CHAT_INPUT_MAX_CHARS + 1))

    def test_limit_follows_config(self, monkeypatch):
        monkeypatch.setenv("CHAT_INPUT_MAX_CHARS", "123")
        import importlib
        import backend.config.chat_input as ci
        import backend.app.api.schemas as schemas
        importlib.reload(ci)
        try:
            importlib.reload(schemas)
            with pytest.raises(ValidationError):
                schemas.ChatRequest(question="a" * 124, session_id="s")
            assert schemas.ChatRequest(
                question="a" * 123, session_id="s").question == "a" * 123
        finally:
            # 先恢复 env 再 reload（顺序不能反，否则污染后续测试的 schema）
            monkeypatch.delenv("CHAT_INPUT_MAX_CHARS", raising=False)
            importlib.reload(ci)
            importlib.reload(schemas)


# ── Guard 对齐 ──────────────────────────────────────────────────

class TestGuardAlignment:
    def test_guard_defaults_follow_chat_input(self):
        from backend.config import guard as guard_cfg
        assert guard_cfg.GUARD_MAX_INPUT_CHARS == CHAT_INPUT_MAX_CHARS
        from backend.config.chat_input import CHAT_INPUT_MAX_TOKENS
        assert guard_cfg.GUARD_MAX_INPUT_TOKENS == CHAT_INPUT_MAX_TOKENS


# ── /chat/stream 业务错误 ───────────────────────────────────────

class _EchoAgent:
    def stream_events(self, question, session_id, **kwargs):
        yield {"event": "delta", "data": {"content": "ok"}}
        yield {"event": "done", "data": {"elapsed": 0.0, "sources": []}}


@pytest.fixture()
def client(monkeypatch):
    import backend.app.api.middleware.auth as auth_mw
    import backend.app.api.routes.chat as chat_mod

    monkeypatch.setattr(chat_mod, "get_multi_agent", lambda: _EchoAgent())
    monkeypatch.setattr(auth_mw, "API_KEY", "test")
    from backend.app.server import app
    return TestClient(app)


class TestChatStreamBusinessError:
    def test_over_limit_returns_business_error(self, client):
        resp = client.post(
            "/chat/stream",
            json={"question": "a" * (CHAT_INPUT_MAX_CHARS + 1),
                  "session_id": "s"},
            headers={"X-API-Key": "test"},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["code"] == "INVALID_PARAM"
        assert body["details"]["reason"] == "CHAT_INPUT_TOO_LARGE"
        assert body["details"]["limit_chars"] == CHAT_INPUT_MAX_CHARS
        assert "知识库文件上传" in body["message"]
        # 实现细节不外泄
        assert "pydantic" not in str(body).lower()
        assert "validation" not in str(body).lower()

    def test_over_bytes_returns_business_error(self, client):
        resp = client.post(
            "/chat/stream",
            json={"question": "hi", "session_id": "s"},
            headers={"X-API-Key": "test",
                     "Content-Length": str(CHAT_INPUT_MAX_BYTES + 1)},
        )
        # content-length 头与实际 body 不符时 httpx 会修正；
        # 这里直接断言超限分支被触发（header 被原样上送的场景）
        assert resp.status_code == 422
        body = resp.json()
        assert body["details"]["reason"] == "CHAT_INPUT_TOO_LARGE"

    def test_normal_short_input_still_works(self, client):
        with client.stream(
            "POST", "/chat/stream",
            json={"question": "hi", "session_id": "s"},
            headers={"X-API-Key": "test"},
        ) as resp:
            assert resp.status_code == 200

    def test_unparseable_body_no_internal_leak(self, client):
        resp = client.post(
            "/chat/stream",
            content=b"{bad json",
            headers={"X-API-Key": "test",
                     "Content-Type": "application/json"},
        )
        assert resp.status_code in (400, 422)
        assert "ChatRequest 解析失败" not in resp.text

    def test_wrong_type_question_generic_invalid_param(self, client):
        """非长度类错误走通用 INVALID_PARAM，不带 TOO_LARGE 语义。"""
        resp = client.post(
            "/chat/stream",
            json={"question": 12345, "session_id": "s"},
            headers={"X-API-Key": "test"},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["code"] == "INVALID_PARAM"
        assert (body.get("details") or {}).get("reason") != "CHAT_INPUT_TOO_LARGE"


# ── 配置正式化 ──────────────────────────────────────────────────

class TestChatInputConfig:
    def test_exported_from_backend_config(self):
        assert hasattr(config, "CHAT_INPUT_MAX_CHARS")
        assert hasattr(config, "CHAT_INPUT_MAX_BYTES")
        assert hasattr(config, "CHAT_INPUT_MAX_TOKENS")

    def test_defaults(self, monkeypatch, tmp_path):
        monkeypatch.delenv("CHAT_INPUT_MAX_CHARS", raising=False)
        monkeypatch.delenv("CHAT_INPUT_MAX_BYTES", raising=False)
        monkeypatch.delenv("CHAT_INPUT_MAX_TOKENS", raising=False)
        monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
        import importlib
        import backend.config.chat_input as ci
        importlib.reload(ci)
        try:
            assert ci.CHAT_INPUT_MAX_CHARS == 20000
            assert ci.CHAT_INPUT_MAX_BYTES == 65536
            assert ci.CHAT_INPUT_MAX_TOKENS == 8000
        finally:
            importlib.reload(ci)
