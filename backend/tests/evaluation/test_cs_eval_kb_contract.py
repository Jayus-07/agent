"""客服 full_path test KB 与事实来源契约的离线验证。"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.customer_service.knowledge.evaluation_scope import (
    CS_EVAL_FIXTURE_SET,
    CS_EVAL_KB_ID,
    CS_EVAL_VERSION_ID,
    cs_knowledge_eval_scope,
    current_cs_knowledge_eval_scope,
)
from backend.evaluation.cs_fixture_catalog import (
    fact_ids_for_rag_source,
    load_cs_fixture_catalog,
)


def _scope_args() -> dict[str, str]:
    return {
        "kb_id": CS_EVAL_KB_ID,
        "fixture_set": CS_EVAL_FIXTURE_SET,
        "suite": "cs_q3_acceptance",
        "dataset_version": "2.2",
    }


def test_cs_fixture_manifest_locks_suite_facts_and_source_headings():
    catalog = load_cs_fixture_catalog()
    dataset_root = Path(__file__).resolve().parents[2] / "evaluation" / "datasets" / "cs"
    cases = {
        case["id"]: case
        for case in (
            json.loads(line)
            for line in (dataset_root / "cases.jsonl").read_text(
                encoding="utf-8",
            ).splitlines()
            if line.strip()
        )
    }
    suite = json.loads(
        (dataset_root / "suites" / "cs_q3_acceptance.json").read_text(
            encoding="utf-8",
        )
    )
    expected_fact_ids = {
        fact_id
        for case_id in suite["case_ids"]
        for fact_id in cases[case_id]["expected"].get("allowed_facts", [])
    }

    assert catalog.kb_id == CS_EVAL_KB_ID
    assert catalog.fixture_set == CS_EVAL_FIXTURE_SET
    assert catalog.version_id == CS_EVAL_VERSION_ID
    assert expected_fact_ids
    assert all(catalog.supports_fact_id(fact_id) for fact_id in expected_fact_ids)


def test_cs_fact_ids_require_test_kb_stable_doc_version_and_exact_section():
    source = {
        "kb_id": CS_EVAL_KB_ID,
        "doc_id": "cs-eval-policy-refund-timeline",
        "filename": "policy-refund-timeline.md",
        "version_id": CS_EVAL_VERSION_ID,
        "section": "处理时限",
    }

    assert fact_ids_for_rag_source(source, kb_id=CS_EVAL_KB_ID) == [
        "kb:policy-refund-timeline#时效",
    ]
    assert fact_ids_for_rag_source(source, kb_id="cs_policy") == []
    assert fact_ids_for_rag_source(
        {**source, "doc_id": "ordinary-doc-id"}, kb_id=CS_EVAL_KB_ID,
    ) == []
    assert fact_ids_for_rag_source(
        {**source, "filename": "other.md"}, kb_id=CS_EVAL_KB_ID,
    ) == []
    assert fact_ids_for_rag_source(
        {**source, "version_id": "old-version"}, kb_id=CS_EVAL_KB_ID,
    ) == []
    assert fact_ids_for_rag_source(
        {**source, "section": "未登记章节"}, kb_id=CS_EVAL_KB_ID,
    ) == []


def test_citation_evidence_keeps_same_document_sections_separate():
    from backend.rag.citation import CitationFormatter

    docs = [
        SimpleNamespace(metadata={
            "index": 1,
            "doc_id": "cs-eval-policy-return-us",
            "source_file": "policy-return-us.md",
            "section_title": "退货条件",
            "version_id": CS_EVAL_VERSION_ID,
        }),
        SimpleNamespace(metadata={
            "index": 2,
            "doc_id": "cs-eval-policy-return-us",
            "source_file": "policy-return-us.md",
            "section_title": "退款方式与时限",
            "version_id": CS_EVAL_VERSION_ID,
        }),
        SimpleNamespace(metadata={
            "index": 3,
            "doc_id": "cs-eval-policy-return-us",
            "source_file": "policy-return-us.md",
            "section_title": "特殊说明",
            "version_id": CS_EVAL_VERSION_ID,
        }),
    ]
    formatter = CitationFormatter()

    # SourceCard 按文件去重；事实证据保留回答实际引用的两个章节。
    assert len(formatter.extract_sources(docs, "退货资格[E1]，退款时效[E2]")) == 1
    evidence = formatter.extract_citation_evidence(
        docs,
        "退货资格[E1]，退款时效[E2]",
    )

    assert [item["section"] for item in evidence] == ["退货条件", "退款方式与时限"]
    assert formatter.extract_citation_evidence(docs, "没有引用标记") == []


def test_knowledge_expert_uses_all_cited_sections_for_fact_ids(monkeypatch):
    import backend.customer_service.knowledge as knowledge
    from backend.customer_service.experts.knowledge import execute_knowledge
    from backend.customer_service.knowledge.answer_decision import Decision
    from backend.customer_service.knowledge.service import CSKnowledgeResult

    cited_sections = [
        {
            "index": 1,
            "doc_id": "cs-eval-policy-return-us",
            "filename": "policy-return-us.md",
            "section": "退货条件",
            "version_id": CS_EVAL_VERSION_ID,
        },
        {
            "index": 2,
            "doc_id": "cs-eval-policy-return-us",
            "filename": "policy-return-us.md",
            "section": "退款方式与时限",
            "version_id": CS_EVAL_VERSION_ID,
        },
        {
            "index": 3,
            "doc_id": "cs-eval-policy-refund-timeline",
            "filename": "policy-refund-timeline.md",
            "section": "处理时限",
            "version_id": CS_EVAL_VERSION_ID,
        },
        {
            "index": 4,
            "doc_id": "cs-eval-policy-logistics-exception",
            "filename": "policy-logistics-exception.md",
            "section": "催件条件",
            "version_id": CS_EVAL_VERSION_ID,
        },
        {
            "index": 5,
            "doc_id": "cs-eval-policy-shipping-fee",
            "filename": "policy-shipping-fee.md",
            "section": "基本原则",
            "version_id": CS_EVAL_VERSION_ID,
        },
        {
            "index": 6,
            "doc_id": "cs-eval-product-catalog",
            "filename": "product-catalog.md",
            "section": "导购建议速查",
            "version_id": CS_EVAL_VERSION_ID,
        },
    ]
    service_result = CSKnowledgeResult(
        answer="可以申请退货，退款将按规则处理。",
        decision=Decision.ANSWER,
        confidence=0.97,
        kb_ids=[CS_EVAL_KB_ID],
        sources=[{"doc_id": cited_sections[0]["doc_id"]}],
        citation_evidence=cited_sections,
    )

    class StubKnowledgeService:
        def answer(self, **_kwargs):
            return service_result

    monkeypatch.setattr(
        knowledge,
        "get_knowledge_service",
        lambda: StubKnowledgeService(),
    )
    with cs_knowledge_eval_scope(**_scope_args()):
        result = execute_knowledge(
            "退货和退款规则是什么",
            {"intent": "k_faq", "kb_ids": ["cs_policy"]},
            "cs-eval-session",
            user_id="eval_bot",
        )

    expected_fact_ids = sorted({
        fact_id
        for source in cited_sections
        for fact_id in fact_ids_for_rag_source(source, kb_id=CS_EVAL_KB_ID)
    })
    assert len(cited_sections) == 6
    assert len(expected_fact_ids) > 5
    assert result["data"]["fact_ids"] == expected_fact_ids


def test_cs_eval_scope_is_allowlisted_and_restored():
    assert current_cs_knowledge_eval_scope() is None
    with cs_knowledge_eval_scope(**_scope_args()) as scope:
        assert current_cs_knowledge_eval_scope() == scope
        assert scope.kb_id == CS_EVAL_KB_ID
    assert current_cs_knowledge_eval_scope() is None

    for change in (
        {"kb_id": "cs_policy"},
        {"fixture_set": "expanded_100"},
        {"suite": "unregistered-suite"},
        {"dataset_version": "2.1"},
    ):
        args = {**_scope_args(), **change}
        with pytest.raises(ValueError):
            with cs_knowledge_eval_scope(**args):
                pytest.fail("非法客服评测范围不应启用")


def test_eval_knowledge_uses_only_test_kb_and_does_not_write_memory(monkeypatch):
    from backend.config import knowledge_base
    from backend.customer_service import faq
    from backend.customer_service.knowledge.service import CSKnowledgeService
    from backend.rag import pipeline

    monkeypatch.setattr(
        knowledge_base,
        "get_knowledge_bases",
        lambda: {CS_EVAL_KB_ID: {"audience": "test"}},
    )
    monkeypatch.setattr(
        faq,
        "get_faq_store",
        lambda: pytest.fail("评测不能命中生产 FAQ 快捷层"),
    )
    outcome = SimpleNamespace(
        answer="退款规则回答",
        answer_meta={"confidence": 0.95, "can_answer": True},
        sources=[{
            "doc_id": "cs-eval-policy-refund-timeline",
            "filename": "policy-refund-timeline.md",
            "version_id": CS_EVAL_VERSION_ID,
            "section": "处理时限",
        }],
        citation_evidence=[{
            "doc_id": "cs-eval-policy-refund-timeline",
            "filename": "policy-refund-timeline.md",
            "version_id": CS_EVAL_VERSION_ID,
            "section": "处理时限",
        }],
    )
    rag_pipeline = MagicMock()
    rag_pipeline.ask_result.return_value = outcome
    monkeypatch.setattr(pipeline, "get_rag_pipeline", lambda: rag_pipeline)

    with cs_knowledge_eval_scope(**_scope_args()):
        result = CSKnowledgeService().answer(
            "退款要多久",
            kb_ids=["cs_policy", "cs_faq"],
            user_id="eval_bot",
        )

    assert result.kb_ids == [CS_EVAL_KB_ID]
    assert result.answer == "退款规则回答"
    assert result.sources[0]["kb_id"] == CS_EVAL_KB_ID
    assert result.citation_evidence == outcome.citation_evidence
    call = rag_pipeline.ask_result.call_args.kwargs
    assert call["kb_id"] == CS_EVAL_KB_ID
    assert call["kb_ids"] == [CS_EVAL_KB_ID]
    assert call["subject_type"] == "employee"
    assert call["roles"] == ("super_admin",)
    assert call["persist_memory"] is False


def test_eval_knowledge_fails_closed_if_registry_loses_test_audience(monkeypatch):
    from backend.config import knowledge_base
    from backend.customer_service.knowledge.service import CSKnowledgeService

    monkeypatch.setattr(
        knowledge_base,
        "get_knowledge_bases",
        lambda: {CS_EVAL_KB_ID: {"audience": "customer"}},
    )
    with cs_knowledge_eval_scope(**_scope_args()):
        with pytest.raises(PermissionError, match="audience=test"):
            CSKnowledgeService().answer("退款规则", user_id="eval_bot")


def test_normal_knowledge_path_keeps_customer_contract(monkeypatch):
    from backend.customer_service import faq
    from backend.customer_service.knowledge.service import CSKnowledgeService
    from backend.rag import pipeline

    class NoFAQ:
        def match(self, _question):
            return None

    monkeypatch.setattr(faq, "get_faq_store", lambda: NoFAQ())
    rag_pipeline = MagicMock()
    rag_pipeline.ask_result.return_value = SimpleNamespace(
        answer="客服生产知识回答",
        answer_meta={"confidence": 0.95, "can_answer": True},
        sources=[],
    )
    monkeypatch.setattr(pipeline, "get_rag_pipeline", lambda: rag_pipeline)

    result = CSKnowledgeService().answer(
        "问题", kb_ids=["cs_faq"], user_id="customer-1",
    )

    assert result.answer == "客服生产知识回答"
    kwargs = rag_pipeline.ask_result.call_args.kwargs
    assert kwargs["subject_type"] == "customer"
    assert "roles" not in kwargs
    assert "persist_memory" not in kwargs


def test_query_fact_ids_only_come_from_sandbox_demo_order_fields():
    from backend.customer_service.experts.query import _attach_cs_eval_fact_ids

    sandbox_result = {
        "expert": "query",
        "status": "success",
        "data": {
            "service_facts": {
                "source": "sandbox_order_service",
                "orders": [
                    {
                        "order_no": "DEMO-1006",
                        "status": "shipped",
                        "total_amount": 89.5,
                        "created_at": "2026-10-01",
                    },
                    {"order_no": "DEMO-1012", "status": "paid"},
                ],
            },
        },
    }
    business_result = {
        "expert": "query",
        "status": "success",
        "data": {
            "service_facts": {
                "source": "business_order_service",
                "orders": [{"order_no": "DEMO-1006", "status": "paid"}],
            },
        },
    }

    with cs_knowledge_eval_scope(**_scope_args()):
        sandbox = _attach_cs_eval_fact_ids(sandbox_result)
        business = _attach_cs_eval_fact_ids(business_result)

    assert sandbox["data"]["fact_ids"] == [
        "db:demo_order#DEMO-1006.amount",
        "db:demo_order#DEMO-1006.date",
        "db:demo_order#DEMO-1006.status",
        "db:demo_order#DEMO-1012.status",
        "db:demo_order#列表语义",
    ]
    assert sandbox["data"]["fact_ids_observable"] is True
    assert business["data"]["fact_ids"] == []


def test_task_result_fact_ids_use_only_present_structured_fields():
    from backend.customer_service.experts.query import _sandbox_order_fact_ids

    assert _sandbox_order_fact_ids({
        "task_result": {
            "source": "sandbox_order_service",
            "facts": {"order_no": "DEMO-1012", "order_status": "paid"},
        },
    }) == ["db:demo_order#DEMO-1012.status"]
    assert _sandbox_order_fact_ids({
        "task_result": {
            "source": "sandbox_order_service",
            "facts": {"order_no": "BUS-1012", "order_status": "paid"},
        },
    }) == []
    assert _sandbox_order_fact_ids({
        "task_result": {
            "source": "business_order_service",
            "facts": {"order_no": "DEMO-1012", "order_status": "paid"},
        },
    }) == []


def test_eval_observer_collects_prior_sandbox_task_results_only():
    from backend.orchestration.graph.cs_graph_node import (
        _publish_cs_eval_result,
        capture_cs_graph_evaluation,
    )

    captured = []
    state = {
        "supervisor_decision": {"next_action": "finish"},
        "task_results": [
            {
                "source": "sandbox_order_service",
                "facts": {"order_no": "DEMO-1006", "order_status": "shipped"},
            },
            {
                "source": "business_order_service",
                "facts": {"order_no": "DEMO-1012", "order_status": "paid"},
            },
        ],
    }
    with cs_knowledge_eval_scope(**_scope_args()):
        with capture_cs_graph_evaluation(captured.append):
            _publish_cs_eval_result(state)

    assert captured[0]["fact_ids"] == ["db:demo_order#DEMO-1006.status"]
    assert captured[0]["fact_ids_observable"] is True


def test_full_path_binds_registered_suite_scope_but_keeps_main_graph_default(
    monkeypatch,
):
    from backend.evaluation.models import TestCase
    from backend.evaluation.runners import cs
    from backend.infra.llm import budget
    from backend.orchestration.graph import MultiAgentSystem
    from backend.orchestration.graph.cs_graph_node import _publish_cs_eval_result

    case = TestCase(
        id="cs-eval-scope",
        question="退款规则是什么",
        module="cs",
        expected={"cs_route": "hit", "allowed_facts": [
            "kb:policy-refund-timeline#时效",
        ]},
        metadata={
            **_scope_args(),
            "suite_content_hash": "locked-suite-hash",
        },
    )
    monkeypatch.setattr(MultiAgentSystem, "__init__", lambda self: None)
    monkeypatch.setattr(budget, "check_quota_blocked", lambda: None)
    monkeypatch.setattr(budget, "bind_request_budget", lambda *args, **kwargs: None)
    monkeypatch.setattr(budget, "clear_request_budget", lambda: None)

    def stream_events(self, _message, _session_id, **kwargs):
        assert kwargs["kb_id"] == "default"
        scope = current_cs_knowledge_eval_scope()
        assert scope is not None and scope.kb_id == CS_EVAL_KB_ID
        _publish_cs_eval_result({
            "supervisor_decision": {"next_action": "finish"},
            "last_expert_result": {
                "data": {
                    "fact_ids": ["kb:policy-refund-timeline#时效"],
                },
            },
        })
        yield {"event": "status", "data": {"node": "cs_graph_node"}}
        yield {"event": "done", "data": {"answer": "退款时限回答"}}

    monkeypatch.setattr(MultiAgentSystem, "stream_events", stream_events)
    result = cs._run_live_full_path([case])[0]

    assert result.status == "pass"
    assert result.metrics["allowed_facts_verified"] == 1.0
    assert current_cs_knowledge_eval_scope() is None


@pytest.mark.parametrize(
    "judge_violated,observable,expected_status,expected_metric",
    [
        (True, True, "fail", 0.0),
        (False, True, "pass", 1.0),
        (None, True, "skip", None),
        (False, False, "skip", None),
    ],
)
def test_full_path_forbidden_facts_judge_wiring(
    monkeypatch, judge_violated, observable, expected_status, expected_metric
):
    """forbidden_facts 接线四态：violated→fail / 合规→pass / 裁判不可用→skip /
    无可核验事实证据→不凭裁判计分（skip）。"""
    from backend.evaluation.judge_cs_forbidden import ForbiddenJudgeResult
    from backend.evaluation.models import TestCase
    from backend.evaluation.runners import cs
    from backend.infra.llm import budget
    from backend.orchestration.graph import MultiAgentSystem
    from backend.orchestration.graph.cs_graph_node import _publish_cs_eval_result

    case = TestCase(
        id="forbidden-wiring",
        question="订单 8765 的物流呢",
        module="cs",
        expected={"forbidden_facts": ["静默纠错订单号"]},
        metadata=_scope_args(),
    )
    monkeypatch.setattr(MultiAgentSystem, "__init__", lambda self: None)
    monkeypatch.setattr(budget, "check_quota_blocked", lambda: None)
    monkeypatch.setattr(budget, "bind_request_budget", lambda *a, **k: None)
    monkeypatch.setattr(budget, "clear_request_budget", lambda *a, **k: None)

    captured: dict = {}

    def fake_judge(answer, forbidden_facts, context=None):
        captured["answer"] = answer
        captured["facts"] = list(forbidden_facts)
        captured["context"] = context
        return ForbiddenJudgeResult(violated=judge_violated, reasoning="stub")

    # runner 在调用点动态 import——patch 源模块属性
    monkeypatch.setattr(
        "backend.evaluation.judge_cs_forbidden.judge_forbidden_facts", fake_judge
    )

    def stream_events(self, _message, _session_id, **kwargs):
        expert_data = (
            {"fact_ids": ["kb:policy-refund-timeline#时效"]}
            if observable else {}
        )
        _publish_cs_eval_result({
            "supervisor_decision": {"next_action": "finish"},
            "last_expert_result": {"data": expert_data},
        })
        yield {"event": "status", "data": {"node": "cs_graph_node"}}
        yield {"event": "done", "data": {"answer": "已为您查到订单 8756 的物流。"}}

    monkeypatch.setattr(MultiAgentSystem, "stream_events", stream_events)
    result = cs._run_live_full_path([case])[0]

    assert result.status == expected_status
    if observable and judge_violated is not None:
        # 有证据时裁判必须被调用且拿到声明清单与证据 context
        assert captured["facts"] == ["静默纠错订单号"]
        assert "订单 8765" in (captured["context"] or "")
        assert "运行时事实 ID" in (captured["context"] or "")
    if expected_metric is None:
        assert "forbidden_facts_respected" not in result.metrics
        assert result.actual["unverified_checks"]
    else:
        assert result.metrics["forbidden_facts_respected"] == expected_metric
        if judge_violated:
            assert "违反禁说事实" in (result.error_msg or "")


def test_full_path_rejects_non_cs_test_kb_suite_metadata():
    from backend.evaluation.models import TestCase
    from backend.evaluation.runners.cs import _cs_full_path_eval_scope_args

    case = TestCase(
        id="wrong-kb", question="问题", module="cs", expected={},
        metadata={
            "kb_id": "default",
            "fixture_set": "baseline",
            "suite": "cs_q3_acceptance",
            "dataset_version": "2.2",
        },
    )
    with pytest.raises(ValueError, match="范围不符"):
        _cs_full_path_eval_scope_args(case)


def test_full_path_non_suite_dataset_version_remains_compatible():
    from backend.evaluation.models import TestCase
    from backend.evaluation.runners.cs import _cs_full_path_eval_scope_args

    case = TestCase(
        id="legacy-dataset-case", question="问题", module="cs", expected={},
        metadata={"dataset_version": "2.2"},
    )

    assert _cs_full_path_eval_scope_args(case) is None


def test_full_path_aggregates_explicit_facts_across_user_turns(monkeypatch):
    from backend.evaluation.models import TestCase
    from backend.evaluation.runners import cs
    from backend.infra.llm import budget
    from backend.orchestration.graph import MultiAgentSystem
    from backend.orchestration.graph.cs_graph_node import _publish_cs_eval_result

    earlier_fact = "db:demo_order#DEMO-1006.status"
    case = TestCase(
        id="cs-eval-multiturn-facts",
        question="查一下订单 DEMO-1006 的状态",
        module="cs",
        expected={"cs_route": "hit", "allowed_facts": [earlier_fact]},
        metadata={
            **_scope_args(),
            "turns": [
                {"role": "user", "text": "查一下订单 DEMO-1006 的状态"},
                {"role": "user", "text": "那它可以退款吗"},
            ],
        },
    )
    monkeypatch.setattr(
        MultiAgentSystem,
        "__init__",
        lambda self: setattr(self, "turn", 0),
    )
    monkeypatch.setattr(budget, "check_quota_blocked", lambda: None)
    monkeypatch.setattr(budget, "bind_request_budget", lambda *args, **kwargs: None)
    monkeypatch.setattr(budget, "clear_request_budget", lambda: None)

    def stream_events(self, _message, _session_id, **kwargs):
        assert kwargs["kb_id"] == "default"
        assert current_cs_knowledge_eval_scope() is not None
        current_turn = self.turn
        self.turn += 1
        data = {"fact_ids": [earlier_fact]} if current_turn == 0 else {}
        _publish_cs_eval_result({
            "supervisor_decision": {"next_action": "finish"},
            "last_expert_result": {"data": data},
        })
        yield {"event": "status", "data": {"node": "cs_graph_node"}}
        yield {"event": "done", "data": {"answer": "订单退款答复"}}

    monkeypatch.setattr(MultiAgentSystem, "stream_events", stream_events)
    result = cs._run_live_full_path([case])[0]

    assert result.status == "pass"
    assert result.actual["fact_ids"] == [earlier_fact]
    assert current_cs_knowledge_eval_scope() is None


def test_cs_fixture_import_requires_explicit_apply_without_provider_call():
    from backend.evaluation.ingest_cs_fixtures import (
        ingest_cs_eval_fixtures,
    )

    with pytest.raises(ValueError, match="只支持"):
        ingest_cs_eval_fixtures(fixture_set="expanded_100", apply=True)
    with pytest.raises(ValueError, match="--apply"):
        ingest_cs_eval_fixtures(fixture_set=CS_EVAL_FIXTURE_SET, apply=False)


def test_cs_fixture_import_rejects_paths_outside_test_kb(tmp_path):
    from backend.evaluation.ingest_cs_fixtures import _resolve_test_kb_path

    test_kb_root = tmp_path / CS_EVAL_KB_ID
    valid_path = test_kb_root / "general" / "fixture.md"
    assert _resolve_test_kb_path(valid_path, test_kb_root) == valid_path.resolve()

    with pytest.raises(RuntimeError, match="根目录之外"):
        _resolve_test_kb_path(tmp_path / "production-kb" / "fixture.md", test_kb_root)

    with pytest.raises(RuntimeError, match="根目录之外"):
        _resolve_test_kb_path(test_kb_root, test_kb_root)


def test_backend_image_includes_cs_fixture_source_directory():
    repository_root = Path(__file__).resolve().parents[3]
    dockerfile = (repository_root / "Dockerfile").read_text(encoding="utf-8")

    assert (
        "COPY docs/customer-service/demo-kb/ ./docs/customer-service/demo-kb/"
        in dockerfile
    )
