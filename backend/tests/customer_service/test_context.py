"""test_context.py — CSContext 类型层测试

Phase 1: 测试 CSContext TypedDict 的 4 个辅助函数。
"""
from backend.customer_service.context import (
    CSContext,
    build_cs_context,
    build_reporter_snapshot,
    copy_cs_context,
    merge_cs_context,
)


class TestBuildCsContext:
    """build_cs_context() — Router 构建初始 cs_context"""

    def test_basic_construction(self):
        """基础构建：5 个核心字段"""
        ctx = build_cs_context(
            cs_route={"intent": "t_order_status", "domain": "trade"},
            cs_target="cs_business_query",
            authenticated_user_id="user-001",
            session_id="sess-001",
        )
        assert ctx["cs_route"]["intent"] == "t_order_status"
        assert ctx["cs_target"] == "cs_business_query"
        assert ctx["authenticated_user_id"] == "user-001"
        assert ctx["session_id"] == "sess-001"
        assert ctx["conversation_id"] == "sess-001"

    def test_explicit_conversation_id(self):
        """显式指定 conversation_id"""
        ctx = build_cs_context(
            cs_route={"intent": "k_faq"},
            cs_target="cs_knowledge",
            authenticated_user_id="user-002",
            session_id="sess-002",
            conversation_id="conv-002",
        )
        assert ctx["conversation_id"] == "conv-002"

    def test_empty_cs_route(self):
        """空 cs_route"""
        ctx = build_cs_context(
            cs_route={},
            cs_target="cs_knowledge",
            authenticated_user_id="user-003",
            session_id="sess-003",
        )
        assert ctx["cs_route"] == {}


class TestCopyCsContext:
    """copy_cs_context() — 节点 copy-on-write"""

    def test_shallow_copy(self):
        """浅拷贝：修改副本不影响原始"""
        original = build_cs_context(
            cs_route={"intent": "t_order_status"},
            cs_target="cs_business_query",
            authenticated_user_id="user-001",
            session_id="sess-001",
        )
        copied = copy_cs_context(original)
        copied["handoff_state"] = "handoff_requested"
        assert "handoff_state" not in original
        assert copied["handoff_state"] == "handoff_requested"

    def test_copy_none(self):
        """拷贝 None 返回空 dict"""
        copied = copy_cs_context(None)
        assert copied == {}

    def test_copy_empty(self):
        """拷贝空 dict 返回空 dict"""
        copied = copy_cs_context({})
        assert copied == {}

    def test_preserves_all_fields(self):
        """保留所有字段"""
        original = {
            "cs_route": {"intent": "k_faq"},
            "cs_target": "cs_knowledge",
            "authenticated_user_id": "user-001",
            "session_id": "sess-001",
            "conversation_id": "conv-001",
            "answer_meta": {"confidence": 0.9},
        }
        copied = copy_cs_context(original)
        assert copied == original
        assert copied is not original


class TestMergeCsContext:
    """merge_cs_context() — CS Graph 结果合并"""

    def test_merge_preserves_original_fields(self):
        """合并保留原始字段"""
        original = build_cs_context(
            cs_route={"intent": "t_order_status"},
            cs_target="cs_business_query",
            authenticated_user_id="user-001",
            session_id="sess-001",
        )
        result = {
            "conversation_id": "conv-002",
            "handoff_state": "handoff_requested",
            "confirmation_state": "pending",
        }
        merged = merge_cs_context(original, result)
        assert merged["authenticated_user_id"] == "user-001"
        assert merged["session_id"] == "sess-001"
        assert merged["cs_target"] == "cs_business_query"
        assert merged["cs_route"]["intent"] == "t_order_status"
        assert merged["conversation_id"] == "conv-002"
        assert merged["handoff_state"] == "handoff_requested"
        assert merged["confirmation_state"] == "pending"

    def test_merge_empty_result(self):
        """空 result 不覆盖任何字段"""
        original = build_cs_context(
            cs_route={"intent": "k_faq"},
            cs_target="cs_knowledge",
            authenticated_user_id="user-001",
            session_id="sess-001",
        )
        merged = merge_cs_context(original, {})
        assert merged["authenticated_user_id"] == "user-001"
        assert merged["conversation_id"] == "sess-001"

    def test_merge_empty_original(self):
        """空 original 只包含 result 字段"""
        result = {
            "conversation_id": "conv-003",
            "handoff_state": "ai_active",
        }
        merged = merge_cs_context({}, result)
        assert merged["conversation_id"] == "conv-003"
        assert merged["handoff_state"] == "ai_active"

    def test_merge_none_original(self):
        """None original 等同于空 dict"""
        result = {"conversation_id": "conv-004"}
        merged = merge_cs_context(None, result)
        assert merged["conversation_id"] == "conv-004"

    def test_merge_partial_result(self):
        """部分 result 字段只覆盖有值的"""
        original = {
            "authenticated_user_id": "user-001",
            "conversation_id": "conv-old",
            "handoff_state": "ai_active",
            "confirmation_state": "not_required",
        }
        result = {
            "conversation_id": "conv-new",
            "handoff_state": "",
        }
        merged = merge_cs_context(original, result)
        assert merged["conversation_id"] == "conv-new"
        assert merged["handoff_state"] == "ai_active"
        assert merged["confirmation_state"] == "not_required"


class TestBuildReporterSnapshot:
    """build_reporter_snapshot() — Reporter 输出快照"""

    def test_snapshot_from_state(self):
        """从 CSGraphState 构建快照"""
        state = {
            "conversation_id": "conv-001",
            "handoff_state": "handoff_requested",
            "confirmation_state": "pending",
            "cs_route": {"intent": "as_refund"},
            "expert_history": [{"expert": "action", "status": "success"}],
            "supervisor_decision": {"action": "route", "target": "cs_action_expert"},
        }
        snapshot = build_reporter_snapshot(state)
        assert snapshot["conversation_id"] == "conv-001"
        assert snapshot["handoff_state"] == "handoff_requested"
        assert snapshot["confirmation_state"] == "pending"
        assert snapshot["cs_route"]["intent"] == "as_refund"
        assert len(snapshot["expert_history"]) == 1
        assert snapshot["supervisor_decision"]["action"] == "route"

    def test_snapshot_empty_state(self):
        """空 state 返回默认值"""
        snapshot = build_reporter_snapshot({})
        assert snapshot["conversation_id"] == ""
        assert snapshot["handoff_state"] == ""
        assert snapshot["confirmation_state"] == ""
        assert snapshot["cs_route"] == {}
        assert snapshot["expert_history"] == []
        assert snapshot["supervisor_decision"] == {}

    def test_snapshot_partial_state(self):
        """部分 state 只填充有值字段"""
        state = {
            "conversation_id": "conv-002",
            "handoff_state": "ai_active",
        }
        snapshot = build_reporter_snapshot(state)
        assert snapshot["conversation_id"] == "conv-002"
        assert snapshot["handoff_state"] == "ai_active"
        assert snapshot["confirmation_state"] == ""
        assert snapshot["cs_route"] == {}


class TestCSContextType:
    """CSContext TypedDict 类型验证"""

    def test_typed_dict_is_dict_at_runtime(self):
        """TypedDict 运行时是 dict"""
        ctx = CSContext(
            cs_route={"intent": "k_faq"},
            cs_target="cs_knowledge",
            authenticated_user_id="user-001",
        )
        assert isinstance(ctx, dict)
        assert ctx["cs_target"] == "cs_knowledge"

    def test_total_false_allows_partial(self):
        """total=False 允许部分字段"""
        ctx = CSContext(cs_target="cs_knowledge")
        assert ctx["cs_target"] == "cs_knowledge"
        assert "cs_route" not in ctx

    def test_empty_construction(self):
        """空构造"""
        ctx = CSContext()
        assert ctx == {}
