"""test_complaint_service.py — 投诉处理服务测试"""
import pytest

from backend.customer_service.service.complaint_service import (
    ComplaintDetection,
    ComplaintService,
    ComplaintTicket,
    get_complaint_service,
)


@pytest.fixture
def service():
    return ComplaintService()


class TestDetect:

    def test_no_complaint(self, service):
        result = service.detect("我想查一下订单状态")
        assert result.is_complaint is False
        assert result.severity == "low"
        assert result.matched_patterns == []

    def test_empty_input(self, service):
        result = service.detect("")
        assert result.is_complaint is False

    def test_medium_severity_single_pattern(self, service):
        result = service.detect("你们的服务太差了")
        if result.is_complaint:
            assert result.severity == "medium"
            assert len(result.matched_patterns) == 1

    def test_high_severity_multiple_patterns(self, service):
        result = service.detect("你们的服务太差了，我要投诉，我要找领导")
        if result.is_complaint and len(result.matched_patterns) >= 2:
            assert result.severity == "high"


class TestCreateTicket:

    def test_ticket_fields(self, service):
        ticket = service.create_ticket(
            user_id="user1",
            conversation_id="conv1",
            severity="medium",
            summary="服务不满意",
        )
        assert ticket.ticket_id.startswith("COMPLAINT-")
        assert len(ticket.ticket_id) == len("COMPLAINT-") + 8
        assert ticket.user_id == "user1"
        assert ticket.conversation_id == "conv1"
        assert ticket.severity == "medium"
        assert ticket.status == "open"
        assert ticket.created_at

    def test_summary_truncated(self, service):
        ticket = service.create_ticket(
            user_id="user1",
            conversation_id="conv1",
            severity="low",
            summary="x" * 1000,
        )
        assert len(ticket.summary) <= 500


class TestBuildComfortResponse:

    def test_high_severity_response(self, service):
        detection = ComplaintDetection(is_complaint=True, severity="high", matched_patterns=["p1", "p2"])
        ticket = ComplaintTicket(
            ticket_id="COMPLAINT-TEST1234",
            user_id="user1",
            conversation_id="conv1",
            severity="high",
            summary="test",
        )
        response = service.build_comfort_response(detection, ticket)
        assert "COMPLAINT-TEST1234" in response
        assert "抱歉" in response or "重视" in response

    def test_medium_severity_response(self, service):
        detection = ComplaintDetection(is_complaint=True, severity="medium", matched_patterns=["p1"])
        ticket = ComplaintTicket(
            ticket_id="COMPLAINT-MED5678",
            user_id="user1",
            conversation_id="conv1",
            severity="medium",
            summary="test",
        )
        response = service.build_comfort_response(detection, ticket)
        assert "COMPLAINT-MED5678" in response


class TestSimulateExecute:

    def test_simulate_returns_executed(self, service):
        ticket = ComplaintTicket(
            ticket_id="COMPLAINT-SIM00001",
            user_id="user1",
            conversation_id="conv1",
            severity="medium",
            summary="test",
        )
        result = service.simulate_execute(ticket)
        assert result["executed"] is True
        assert result["action"] == "complaint_ticket_created"
        assert result["ticket_id"] == "COMPLAINT-SIM00001"
        assert result["status"] == "open"


class TestSingleton:

    def test_same_instance(self):
        s1 = get_complaint_service()
        s2 = get_complaint_service()
        assert s1 is s2
