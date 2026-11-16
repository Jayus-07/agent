"""知识库原文核对追问沿用知识域路由。"""

import pytest

from backend.orchestration.router.rule_router import RuleRouter
from backend.orchestration.router.domain_router import DomainRouter
from backend.orchestration.router.types import ExecutionMode


@pytest.mark.parametrize(
    "query",
    [
        "你刚才列出三个审核阈值，请核对原始依据",
        "请核对原始依据，并把刚才提到的金额门槛逐条对应到原文出处",
    ],
)
def test_knowledge_source_followup_routes_to_rag(query):
    decision = RuleRouter().route(
        query,
        routing_context={"active_domain": "knowledge"},
    )

    assert decision is not None
    assert decision.execution_mode is ExecutionMode.DIRECT
    assert decision.candidates[0].name == "rag.search"
    assert decision.confidence >= 0.9
    assert "继续检索知识库" in (decision.reason or "")


def test_knowledge_topic_followup_routes_to_rag_when_prior_domain_was_data():
    query = "你刚才列出三个审核阈值，请核对原始依据"

    decision = RuleRouter().route(
        query,
        routing_context={"active_domain": "data"},
    )

    assert decision is not None
    assert decision.execution_mode is ExecutionMode.DIRECT
    assert decision.candidates[0].name == "rag.search"
    assert "继续检索知识库" in (decision.reason or "")


def test_initial_refund_evidence_request_routes_to_rag_without_history():
    decision = RuleRouter().route(
        "请核对退款审核的金额门槛和审批角色，逐条列出对应原文出处，并说明金额规则是否存在冲突。",
    )

    assert decision is not None
    assert decision.candidates[0].name == "rag.search"
    assert decision.confidence >= 0.9


def test_generic_source_followup_does_not_override_non_knowledge_domain():
    decision = RuleRouter().route(
        "你刚才提到几个数，请核对原始依据",
        routing_context={"active_domain": "data"},
    )

    assert decision is None or "继续检索知识库" not in (decision.reason or "")


def test_agent_domain_router_uses_knowledge_for_explicit_source_followup():
    class _UnexpectedClassifier:
        def classify(self, *_args, **_kwargs):
            raise AssertionError("明确的知识主题追问不应再依赖模型猜域")

    decision = DomainRouter(classifier=_UnexpectedClassifier()).route(
        "你刚才列出三个审核阈值，请核对原始依据",
        {
            "request_context": {"product_id": "agent"},
            "routing_context": {"active_domain": "data"},
        },
    )

    assert decision["domain"] == "knowledge"
    assert decision["source"] == "rule"
    assert "继续走知识库域" in decision["reasoning"]


def test_agent_domain_router_uses_knowledge_for_initial_source_request():
    class _UnexpectedClassifier:
        def classify(self, *_args, **_kwargs):
            raise AssertionError("明确的业务原文请求不应依赖嵌入域分类")

    decision = DomainRouter(classifier=_UnexpectedClassifier()).route(
        "请核对退款审核的金额门槛和审批角色，逐条列出对应原文出处，并说明金额规则是否存在冲突。",
        {"request_context": {"product_id": "agent"}},
    )

    assert decision["domain"] == "knowledge"
    assert decision["source"] == "rule"


@pytest.mark.parametrize(
    ("product_id", "status", "expected_record_count"),
    [
        ("agent", "success", 1),
        ("agent", "failed", 0),
        ("customer_service", "success", 0),
    ],
)
def test_rag_step_records_knowledge_domain_only_after_agent_success(
    monkeypatch, product_id, status, expected_record_count,
):
    import asyncio

    from backend.orchestration.context import routing_context
    from backend.skills.rag.skill import RAGSkill, rag_skill_node

    recorded = []

    async def fake_execute(self, state, step_capability=""):
        return {
            "step_results": {
                state["current_step_id"]: {"status": status},
            },
        }

    def record_turn(*args, **kwargs):
        recorded.append((args, kwargs))

    monkeypatch.setattr(RAGSkill, "execute", fake_execute)
    monkeypatch.setattr(routing_context, "mark_domain_turn", record_turn)
    state = {
        "current_step_id": "step-1",
        "plan": {"nodes": {"step-1": {"capability": "rag.search"}}},
        "request_context": {"product_id": product_id},
        "tenant_id": "tenant-1",
        "user_id": "user-1",
        "product_session_id": "session-1",
    }

    result = asyncio.run(rag_skill_node(state))

    assert result["step_results"]["step-1"]["status"] == status
    assert len(recorded) == expected_record_count
    if expected_record_count:
        assert recorded[0][1]["domain"] == "knowledge"
        assert recorded[0][1]["action"] == "rag.search"


def test_explicit_data_analysis_can_switch_from_knowledge_context():
    decision = RuleRouter().route(
        "你刚才提到原始依据，请统计本月销售额",
        routing_context={"active_domain": "knowledge"},
    )

    assert decision is not None
    assert decision.candidates[0].name != "rag.search"
