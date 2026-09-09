"""test_prompt_version_trace.py — Prompt 版本追踪测试。

覆盖：
- record_prompt_version 写入 trace.metadata["prompt_versions"]
- 同一 key 重复调用更新而非追加
- 无 active trace 时静默跳过
- 异常时不抛出
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from backend.observability.prompt_trace import record_prompt_version


class TestRecordPromptVersion:
    def test_writes_to_trace_metadata(self):
        mock_trace = MagicMock()
        mock_trace.metadata = {}

        with patch("backend.observability.tracer.trace_collector") as mock_tc:
            mock_tc.current.return_value = mock_trace
            record_prompt_version("cs_routing", 3, "snapshot")

        versions = mock_trace.metadata["prompt_versions"]
        assert len(versions) == 1
        assert versions[0] == {"key": "cs_routing", "version": 3, "source": "snapshot"}

    def test_same_key_updates_not_duplicates(self):
        mock_trace = MagicMock()
        mock_trace.metadata = {}

        with patch("backend.observability.tracer.trace_collector") as mock_tc:
            mock_tc.current.return_value = mock_trace
            record_prompt_version("cs_routing", 3, "snapshot")
            record_prompt_version("cs_routing", 4, "db")

        versions = mock_trace.metadata["prompt_versions"]
        assert len(versions) == 1
        assert versions[0]["version"] == 4
        assert versions[0]["source"] == "db"

    def test_different_keys_append(self):
        mock_trace = MagicMock()
        mock_trace.metadata = {}

        with patch("backend.observability.tracer.trace_collector") as mock_tc:
            mock_tc.current.return_value = mock_trace
            record_prompt_version("cs_routing", 3, "snapshot")
            record_prompt_version("rag_generation", 1, "default")

        versions = mock_trace.metadata["prompt_versions"]
        assert len(versions) == 2

    def test_no_active_trace_is_silent(self):
        with patch("backend.observability.tracer.trace_collector") as mock_tc:
            mock_tc.current.return_value = None
            record_prompt_version("cs_routing", 3, "snapshot")

    def test_exception_is_swallowed(self):
        with patch("backend.observability.tracer.trace_collector") as mock_tc:
            mock_tc.current.side_effect = RuntimeError("boom")
            record_prompt_version("cs_routing", 3, "snapshot")

    def test_none_version_recorded(self):
        mock_trace = MagicMock()
        mock_trace.metadata = {}

        with patch("backend.observability.tracer.trace_collector") as mock_tc:
            mock_tc.current.return_value = mock_trace
            record_prompt_version("some_key", None, "default")

        versions = mock_trace.metadata["prompt_versions"]
        assert versions[0]["version"] is None
