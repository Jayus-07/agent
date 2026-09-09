"""test_cs_trace_link.py — CS ↔ Trace linkage tests.

Covers:
- CS SpanKind enum values exist and have correct string representations
- TraceMiddleware _NODE_LABELS and _NODE_KINDS contain CS node mappings
- wrap_sync_node uses correct kind for CS nodes
"""
from __future__ import annotations


class TestCSSpanKinds:

    def test_cs_routing_kind_exists(self):
        from backend.observability.tracer import SpanKind
        assert SpanKind.CS_ROUTING == "cs_routing"

    def test_cs_knowledge_kind_exists(self):
        from backend.observability.tracer import SpanKind
        assert SpanKind.CS_KNOWLEDGE == "cs_knowledge"

    def test_cs_business_query_kind_exists(self):
        from backend.observability.tracer import SpanKind
        assert SpanKind.CS_BUSINESS_QUERY == "cs_business_query"

    def test_cs_business_action_kind_exists(self):
        from backend.observability.tracer import SpanKind
        assert SpanKind.CS_BUSINESS_ACTION == "cs_business_action"

    def test_cs_complaint_kind_exists(self):
        from backend.observability.tracer import SpanKind
        assert SpanKind.CS_COMPLAINT == "cs_complaint"

    def test_cs_handoff_kind_exists(self):
        from backend.observability.tracer import SpanKind
        assert SpanKind.CS_HANDOFF == "cs_handoff"

    def test_cs_confirmation_kind_exists(self):
        from backend.observability.tracer import SpanKind
        assert SpanKind.CS_CONFIRMATION == "cs_confirmation"

    def test_cs_guard_kind_exists(self):
        from backend.observability.tracer import SpanKind
        assert SpanKind.CS_GUARD == "cs_guard"

    def test_all_cs_kinds_are_string_enum(self):
        from backend.observability.tracer import SpanKind
        cs_kinds = [k for k in SpanKind if k.value.startswith("cs_")]
        assert len(cs_kinds) >= 8
        for k in cs_kinds:
            assert isinstance(k.value, str)


class TestMiddlewareCSMappings:

    def test_node_labels_contain_cs_nodes(self):
        from backend.observability.trace_middleware import _NODE_LABELS
        expected = {
            "cs_knowledge", "cs_business_query", "cs_business_action",
            "cs_complaint", "cs_handoff", "cs_handoff_intercept", "cs_pending",
        }
        assert expected.issubset(_NODE_LABELS.keys())

    def test_node_kinds_contain_cs_nodes(self):
        from backend.observability.trace_middleware import _NODE_KINDS
        expected = {
            "cs_knowledge", "cs_business_query", "cs_business_action",
            "cs_complaint", "cs_handoff", "cs_handoff_intercept", "cs_pending",
        }
        assert expected.issubset(_NODE_KINDS.keys())

    def test_cs_knowledge_maps_to_cs_knowledge_kind(self):
        from backend.observability.trace_middleware import _NODE_KINDS
        assert _NODE_KINDS["cs_knowledge"] == "cs_knowledge"

    def test_cs_handoff_intercept_maps_to_cs_handoff_kind(self):
        from backend.observability.trace_middleware import _NODE_KINDS
        assert _NODE_KINDS["cs_handoff_intercept"] == "cs_handoff"

    def test_cs_pending_maps_to_cs_confirmation_kind(self):
        from backend.observability.trace_middleware import _NODE_KINDS
        assert _NODE_KINDS["cs_pending"] == "cs_confirmation"

    def test_labels_are_chinese(self):
        from backend.observability.trace_middleware import _NODE_LABELS
        assert "客服" in _NODE_LABELS["cs_knowledge"]
        assert "人工" in _NODE_LABELS["cs_handoff"]


class TestMiddlewareWrapNodeCSKind:

    def test_wrap_sync_node_uses_cs_kind_for_cs_node(self):
        """wrap_sync_node should pass the CS-specific kind to start_span."""
        from unittest.mock import patch, MagicMock
        from backend.observability.trace_middleware import TraceMiddleware

        middleware = TraceMiddleware()
        mock_trace = MagicMock()
        mock_span = MagicMock()
        mock_span.span_id = "cs_knowledge:step1"

        with patch("backend.observability.trace_middleware.trace_collector") as mock_tc:
            mock_tc.current.return_value = mock_trace
            mock_tc.start_span.return_value = mock_span

            def dummy_node(state):
                return state

            wrapped = middleware.wrap_sync_node("cs_knowledge", dummy_node)
            wrapped({"current_step_id": "step1", "question": "test"})

            mock_tc.start_span.assert_called_once()
            call_kwargs = mock_tc.start_span.call_args
            assert call_kwargs.kwargs.get("kind") == "cs_knowledge" or \
                (len(call_kwargs.args) > 2 and call_kwargs.args[2] == "cs_knowledge") or \
                call_kwargs[1].get("kind") == "cs_knowledge"

    def test_wrap_sync_node_no_trace_passes_through(self):
        """When no active trace, wrap_sync_node just calls the node function."""
        from unittest.mock import patch
        from backend.observability.trace_middleware import TraceMiddleware

        middleware = TraceMiddleware()

        with patch("backend.observability.trace_middleware.trace_collector") as mock_tc:
            mock_tc.current.return_value = None

            called = False

            def dummy_node(state):
                nonlocal called
                called = True
                return state

            wrapped = middleware.wrap_sync_node("cs_knowledge", dummy_node)
            result = wrapped({"question": "test"})

            assert called
            assert result == {"question": "test"}
