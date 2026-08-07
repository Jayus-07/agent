"""测试 chat_stream 的中文 body 解析鲁棒性。

Why:
  防止 chat_stream 在中文 payload 下回归 422 "There was an error parsing the body"。
  当前实现用手动 await r.json() 兜底（避免 FastAPI 自动 body 解析 bug）。
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, AsyncMock, patch

from backend.app.api.routes.chat import router as chat_router


@pytest.fixture
def mock_multi_agent():
    """构造一个会触发 RAG 流的 mock agent。"""
    agent = MagicMock()
    agent.stream_events = MagicMock(return_value=iter([
        {"event": "meta", "data": {"node_labels": {}}},
        {"event": "error", "data": {"message": "test error"}},
    ]))
    return agent


@pytest.fixture
def client(mock_multi_agent):
    app = FastAPI()
    # chat_router 内部已 prefix="/chat"，这里不再加
    app.include_router(chat_router)

    with patch("backend.app.api.routes.chat.get_multi_agent", return_value=mock_multi_agent):
        with patch("backend.app.api.routes.chat.require_rate_limit", new_callable=AsyncMock):
            yield TestClient(app)


class TestChatStreamBody:
    """chat_stream body 解析鲁棒性。"""

    def test_chinese_question_accepted(self, client):
        """中文问题应该被正确解析，不能 422。"""
        with patch("backend.app.api.routes.chat._active_stops", {}):
            resp = client.post(
                "/chat/stream",
                json={"question": "退货政策是什么？", "session_id": "t1", "request_id": "r1"},
            )
        assert resp.status_code == 200, f"中文 payload 被拒: {resp.text}"
        # SSE 流应该包含至少一个 event
        body = resp.text
        assert "event: meta" in body or "event: error" in body

    def test_ascii_question_accepted(self, client):
        """ASCII payload 应该正常。"""
        with patch("backend.app.api.routes.chat._active_stops", {}):
            resp = client.post(
                "/chat/stream",
                json={"question": "test", "session_id": "t1", "request_id": "r1"},
            )
        assert resp.status_code == 200

    def test_empty_question_rejected(self, client):
        """空 question 应该 422（Pydantic min_length=1）。"""
        with patch("backend.app.api.routes.chat._active_stops", {}):
            resp = client.post(
                "/chat/stream",
                json={"question": "", "session_id": "t1", "request_id": "r1"},
            )
        assert resp.status_code == 422

    def test_missing_required_fields(self, client):
        """缺 question 必填字段应该 422。"""
        with patch("backend.app.api.routes.chat._active_stops", {}):
            resp = client.post(
                "/chat/stream",
                json={"session_id": "t1", "request_id": "r1"},
            )
        assert resp.status_code == 422

    def test_long_chinese_question(self, client):
        """超长中文问题应该 422（max_length=2000）。"""
        with patch("backend.app.api.routes.chat._active_stops", {}):
            resp = client.post(
                "/chat/stream",
                json={"question": "退货" * 1500, "session_id": "t1", "request_id": "r1"},
            )
        assert resp.status_code == 422
