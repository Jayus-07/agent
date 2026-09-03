"""Phase 3 测试 — PII 脱敏 / detail_level / 采样 / PG sink。"""
from __future__ import annotations

import copy
from unittest.mock import AsyncMock, patch

import pytest


# ═══════════════════════════════════════════════════
# Redaction
# ═══════════════════════════════════════════════════

class TestMaskText:
    def test_empty_and_none(self):
        from backend.observability.redaction import mask_text
        assert mask_text("") == ""
        assert mask_text(None) == ""

    def test_no_pii_passthrough(self):
        from backend.observability.redaction import mask_text
        text = "普通文本没有敏感信息"
        assert mask_text(text) == text

    def test_phone_masked(self):
        from backend.observability.redaction import mask_text
        text = "张三手机号13812345678"
        result = mask_text(text)
        assert "13812345678" not in result
        assert "[手机号]" in result

    def test_id_card_masked(self):
        from backend.observability.redaction import mask_text
        text = "身份证号110101199001011234"
        result = mask_text(text)
        assert "110101199001011234" not in result
        assert "[身份证号]" in result

    def test_email_masked(self):
        from backend.observability.redaction import mask_text
        text = "联系邮箱 test@example.com 谢谢"
        result = mask_text(text)
        assert "test@example.com" not in result
        assert "[邮箱]" in result


class TestRedactTraceDto:
    def _make_dto(self):
        return {
            "id": "trace-1",
            "question": "张三手机号13812345678",
            "answer_preview": "回复包含邮箱 a@b.com",
            "metadata": {"user_phone": "13900001111"},
            "tags": {"note": "车牌京A12345"},
            "spans": [
                {
                    "span_id": "s1",
                    "input": {"text": "身份证110101199001011234"},
                    "output": {"text": "正常输出"},
                    "events": [{"msg": "联系 test@mail.com"}],
                    "errors": [],
                    "llm_call": {
                        "prompt_text": "用户手机号13812345678",
                        "response_text": "好的",
                    },
                }
            ],
        }

    def test_full_level_no_change(self):
        from backend.observability.redaction import redact_trace_dto
        dto = self._make_dto()
        result = redact_trace_dto(copy.deepcopy(dto), level="full")
        assert result["question"] == dto["question"]
        assert result["spans"][0]["input"]["text"] == dto["spans"][0]["input"]["text"]

    def test_masked_level_masks_pii(self):
        from backend.observability.redaction import redact_trace_dto
        dto = self._make_dto()
        result = redact_trace_dto(dto, level="masked")
        assert "[手机号]" in result["question"]
        assert "13812345678" not in result["question"]
        assert "[邮箱]" in result["answer_preview"]
        assert "[手机号]" in result["metadata"]["user_phone"]
        assert "[身份证号]" in result["spans"][0]["input"]["text"]
        assert result["spans"][0]["output"]["text"] == "正常输出"
        assert "[手机号]" in result["spans"][0]["llm_call"]["prompt_text"]

    def test_masked_preserves_span_bodies(self):
        from backend.observability.redaction import redact_trace_dto
        dto = self._make_dto()
        result = redact_trace_dto(dto, level="masked")
        assert result["spans"][0]["input"] is not None
        assert result["spans"][0]["events"]

    def test_summary_strips_span_bodies(self):
        from backend.observability.redaction import redact_trace_dto
        dto = self._make_dto()
        result = redact_trace_dto(dto, level="summary")
        span = result["spans"][0]
        assert span["input"] is None
        assert span["output"] is None
        assert span["events"] == []
        assert span["llm_call"]["prompt_text"] == ""
        assert span["llm_call"]["response_text"] == ""
        assert "[手机号]" in result["question"]

    def test_original_not_mutated(self):
        from backend.observability.redaction import redact_trace_dto
        dto = self._make_dto()
        original_question = dto["question"]
        redact_trace_dto(dto, level="masked")
        assert dto["question"] == original_question


# ═══════════════════════════════════════════════════
# Sampling
# ═══════════════════════════════════════════════════

class TestSampling:
    def test_error_trace_always_kept_at_zero_rate(self):
        from backend.observability.trace_writer import _should_keep
        with patch("backend.observability.trace_writer.TRACE_SAMPLING_RATE", 0.0):
            record = {"id": "t1", "status": "error", "error": "boom"}
            assert _should_keep(record) is True

    def test_error_trace_kept_by_error_field(self):
        from backend.observability.trace_writer import _should_keep
        with patch("backend.observability.trace_writer.TRACE_SAMPLING_RATE", 0.0):
            record = {"id": "t2", "error": "some error"}
            assert _should_keep(record) is True

    def test_normal_trace_dropped_at_zero_rate(self):
        from backend.observability.trace_writer import _should_keep
        with patch("backend.observability.trace_writer.TRACE_SAMPLING_RATE", 0.0):
            record = {"id": "t3", "status": "success"}
            assert _should_keep(record) is False

    def test_normal_trace_kept_at_full_rate(self):
        from backend.observability.trace_writer import _should_keep
        with patch("backend.observability.trace_writer.TRACE_SAMPLING_RATE", 1.0):
            record = {"id": "t4", "status": "success"}
            assert _should_keep(record) is True

    def test_sampling_rate_half(self):
        from backend.observability.trace_writer import _should_keep
        with patch("backend.observability.trace_writer.TRACE_SAMPLING_RATE", 0.5):
            kept = sum(1 for _ in range(1000) if _should_keep({"id": "x", "status": "ok"}))
            assert 300 < kept < 700

    def test_object_record_supported(self):
        from backend.observability.trace_writer import _should_keep

        class FakeRecord:
            status = "error"
            error = "fail"
            id = "t5"

        with patch("backend.observability.trace_writer.TRACE_SAMPLING_RATE", 0.0):
            assert _should_keep(FakeRecord()) is True


# ═══════════════════════════════════════════════════
# PG Sink
# ═══════════════════════════════════════════════════

class TestPgTraceSink:
    def test_disabled_by_default(self):
        from backend.observability.pg_trace_sink import write_trace
        with patch("backend.observability.pg_trace_sink.TRACE_PG_MIRROR_ENABLED", False):
            write_trace({"id": "t1", "status": "ok"})

    def test_write_batch_disabled_returns_zero(self):
        from backend.observability.pg_trace_sink import write_batch
        with patch("backend.observability.pg_trace_sink.TRACE_PG_MIRROR_ENABLED", False):
            assert write_batch([{"id": "t1"}]) == 0

    @pytest.mark.asyncio
    async def test_write_one_executes_sql(self):
        from backend.observability.pg_trace_sink import _write_one

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()
        mock_session.commit = AsyncMock()

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("backend.memory.database.AsyncSessionLocal", return_value=mock_ctx):
            data = {
                "id": "trace-abc",
                "session_id": "s1",
                "conversation_id": "c1",
                "workflow_name": "rag",
                "status": "success",
                "duration_ms": 123,
            }
            await _write_one(data)
            mock_session.execute.assert_called_once()
            mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_write_one_skips_empty_id(self):
        from backend.observability.pg_trace_sink import _write_one

        mock_session = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("backend.memory.database.AsyncSessionLocal", return_value=mock_ctx):
            await _write_one({"id": "", "status": "ok"})
            mock_session.execute.assert_not_called()
