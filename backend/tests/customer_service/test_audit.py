"""test_audit.py — 审计日志构建器测试"""
from backend.customer_service.audit import append_audit, build_audit_entry


class TestBuildAuditEntry:
    def test_required_fields(self):
        entry = build_audit_entry(
            user_id="u1",
            action_type="refund_request",
            result="success",
        )
        assert entry["user_id"] == "u1"
        assert entry["action_type"] == "refund_request"
        assert entry["result"] == "success"
        assert "log_id" in entry
        assert "created_at" in entry

    def test_optional_fields(self):
        entry = build_audit_entry(
            user_id="u1",
            action_type="refund_request",
            result="failure",
            target_type="order",
            target_id="123",
            detail="something went wrong",
            conversation_id="conv-1",
        )
        assert entry["target_type"] == "order"
        assert entry["target_id"] == "123"
        assert entry["detail"] == "something went wrong"
        assert entry["conversation_id"] == "conv-1"

    def test_no_conversation_id_defaults_empty(self):
        entry = build_audit_entry(
            user_id="u1",
            action_type="test",
            result="success",
        )
        assert entry["conversation_id"] == ""

    def test_result_values(self):
        for result in ("success", "failure", "denied", "error"):
            entry = build_audit_entry(
                user_id="u1", action_type="test", result=result,
            )
            assert entry["result"] == result


class TestAppendAudit:
    def test_returns_new_list(self):
        original = [{"log_id": "a", "result": "success"}]
        new_entry = {"log_id": "b", "result": "failure"}
        result = append_audit(original, new_entry)

        assert len(result) == 2
        assert len(original) == 1
        assert result[0]["log_id"] == "a"
        assert result[1]["log_id"] == "b"

    def test_empty_list(self):
        entry = {"log_id": "x"}
        result = append_audit([], entry)
        assert result == [{"log_id": "x"}]
