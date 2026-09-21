"""tests/test_tool_envelope.py — Tool 统一封套（成功侧）测试

与 test_error_protocol.py（失败侧协议）对称。
"""
import json

import pytest

from backend.shared.tool_envelope import (
    parse_tool_envelope,
    tool_error_result,
    tool_success_result,
)


class TestToolSuccessResult:
    def test_basic_shape(self):
        out = json.loads(tool_success_result({"rows": [1], "total": 1}))
        assert out["status"] == "success"
        assert out["data"] == {"rows": [1], "total": 1}

    def test_extra_top_level_fields(self):
        out = json.loads(tool_success_result({"a": 1}, row_count=1))
        assert out["row_count"] == 1
        assert out["data"] == {"a": 1}

    def test_chinese_not_escaped(self):
        raw = tool_success_result({"city": "福州"})
        assert "福州" in raw

    def test_non_serializable_falls_back_to_str(self):
        out = json.loads(tool_success_result({"obj": object()}))
        assert isinstance(out["data"]["obj"], str)


class TestToolErrorResult:
    def test_message_shape(self):
        out = json.loads(tool_error_result("未找到城市 POI 数据", known_cities="福州"))
        assert out["status"] == "failed"
        assert out["error"] == "未找到城市 POI 数据"
        assert out["known_cities"] == "福州"
        assert "error_protocol" not in out  # 消息字符串不走协议映射

    def test_exception_maps_to_protocol_envelope(self):
        out = json.loads(tool_error_result(TimeoutError("db slow"), source="tool"))
        assert out["status"] == "failed"
        assert out["error_protocol"]["code"] == "TIMEOUT"
        assert out["error_protocol"]["retryable"] is True
        # 异常 detail 不透传，只有安全 message
        assert "db slow" not in out["error"]

    def test_symmetry_with_success(self):
        ok = json.loads(tool_success_result({"x": 1}))
        bad = json.loads(tool_error_result("boom"))
        assert set(["status", "data"]).issubset(ok.keys())
        assert set(["status", "error"]).issubset(bad.keys())


class TestParseToolEnvelope:
    def test_roundtrip(self):
        raw = tool_success_result({"a": 1})
        assert parse_tool_envelope(raw)["data"] == {"a": 1}

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError):
            parse_tool_envelope("not json")
