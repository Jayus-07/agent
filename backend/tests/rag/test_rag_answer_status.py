"""RAG 答案语义透传回归（2026-10-03 企业 RAG 前端语义改造）。

链路：chain._reject 写 answer_status → pipeline answer_meta → 工具
RAGMETA 文本标记 → reporter 剥离（用户可见文本）+ make_done_event 提取
（done 帧结构化字段）→ 前端拒答指引/低置信提示。

锁定契约：
- 拒答原因 → 稳定语义码映射 + 权限剔除升级（resolve_answer_status）；
- 工具出口标记构造（append_rag_answer_meta）；
- reporter 透传路径剥离标记（用户不可见机读注释）；
- done 帧提取 answer_status/confidence（无标记时字段缺省）；
- extract_sources 携带部门（多部门隔离来源卡）；
- done 帧契约校验器接受新字段。
"""
import types

import pytest

from backend.orchestration.graph.event_schema import validate_frame
from backend.orchestration.graph.events import (
    extract_answer_presentation_metadata,
    make_done_event,
)
from backend.rag.citation import CitationFormatter
from backend.rag.evidence_gate import (
    GateDecision,
    RejectReason,
    build_rejection_response,
    resolve_answer_status,
    ANSWER_STATUS_HALLUCINATION,
    ANSWER_STATUS_NO_EVIDENCE,
    ANSWER_STATUS_PERMISSION_DENIED,
    ANSWER_STATUS_RETRIEVAL_DEGRADED,
)
from backend.agents.reporter.reporter import (
    generate_final_answer,
    strip_rag_meta,
)
from backend.tools.rag import append_rag_answer_meta


# ── resolve_answer_status：映射 + 权限升级 ──────────────────

def test_resolve_answer_status_maps_all_reasons():
    assert resolve_answer_status(RejectReason.NO_EVIDENCE, 0) == ANSWER_STATUS_NO_EVIDENCE
    assert resolve_answer_status(RejectReason.LOW_RELEVANCE, 0) == ANSWER_STATUS_NO_EVIDENCE
    assert resolve_answer_status(RejectReason.DOC_TYPE_MISMATCH, 0) == ANSWER_STATUS_NO_EVIDENCE
    assert resolve_answer_status(RejectReason.INSUFFICIENT, 0) == ANSWER_STATUS_NO_EVIDENCE
    assert resolve_answer_status(RejectReason.HALLUCINATION, 0) == ANSWER_STATUS_HALLUCINATION
    # reason=None（无决策兜底）与未知值按缺省拒答文案语义落 no_evidence
    assert resolve_answer_status(None, 0) == ANSWER_STATUS_NO_EVIDENCE


def test_resolve_answer_status_permission_escalation():
    # 短缺类拒答 + 有越权剔除 → 升级权限语义（「有资料但你看不了」）
    assert resolve_answer_status(RejectReason.NO_EVIDENCE, 3) == ANSWER_STATUS_PERMISSION_DENIED
    assert resolve_answer_status(RejectReason.LOW_RELEVANCE, 1) == ANSWER_STATUS_PERMISSION_DENIED
    # 幻觉拦截不升级：检索已通过 Gate，剔除与拦截无因果
    assert resolve_answer_status(RejectReason.HALLUCINATION, 5) == ANSWER_STATUS_HALLUCINATION
    # 无剔除不升级
    assert resolve_answer_status(RejectReason.NO_EVIDENCE, 0) == ANSWER_STATUS_NO_EVIDENCE


def test_resolve_answer_status_distinguishes_rerank_outage_from_missing_evidence():
    assert resolve_answer_status(
        RejectReason.NO_EVIDENCE, 0, retrieval_degraded=True,
    ) == ANSWER_STATUS_RETRIEVAL_DEGRADED
    # 有资料但无权访问的提示优先，不能被基础设施故障覆盖。
    assert resolve_answer_status(
        RejectReason.NO_EVIDENCE, 2, retrieval_degraded=True,
    ) == ANSWER_STATUS_PERMISSION_DENIED
    # 内容事实校验拒绝仍保持原来的防幻觉语义。
    assert resolve_answer_status(
        RejectReason.HALLUCINATION, 0, retrieval_degraded=True,
    ) == ANSWER_STATUS_HALLUCINATION


def test_rag_chain_reject_explains_rerank_timeout_without_claiming_no_documents(
    monkeypatch,
):
    from backend.rag import chain as chain_module
    from backend.rag.chain import RAGChain
    from backend.rag.context import RagRequestState

    request_state = RagRequestState(rerank_unavailable={"local|http://rerank|model"})
    monkeypatch.setattr(chain_module, "get_context", lambda: request_state)
    monkeypatch.setattr(
        RAGChain, "_end_root_span",
        staticmethod(lambda *args, **kwargs: None),
    )
    monkeypatch.setattr(RAGChain, "_finish", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "backend.security.events.record_security_event",
        lambda *args, **kwargs: None,
    )

    chain = object.__new__(RAGChain)
    trace = types.SimpleNamespace(metadata={})
    decision = GateDecision(
        passed=False, reason=RejectReason.NO_EVIDENCE,
        layer="retrieval", score=0.0,
    )

    message = chain._reject(decision, "retrieval", trace, 0.0)

    assert "暂时无法判断知识库里是否有相关资料" in message
    assert request_state.meta["answer_status"] == ANSWER_STATUS_RETRIEVAL_DEGRADED


@pytest.mark.parametrize(
    "state_kwargs",
    [
        {"rerank_unavailable": {"local|http://rerank|model"}},
        {"vector_degraded": True},
    ],
    ids=["rerank-outage", "embedding-outage"],
)
def test_rag_chain_retrieval_outage_short_circuits_generation_with_string(
    monkeypatch, state_kwargs,
):
    from backend.rag import chain as chain_module
    from backend.rag.chain import RAGChain
    from backend.rag.context import RagRequestState, clear_context, set_context
    from langchain_core.documents import Document
    from langchain_core.retrievers import BaseRetriever

    class UnusedRetriever(BaseRetriever):
        def _get_relevant_documents(self, query, *, run_manager=None):
            raise AssertionError("降级短路不应调用底层 Retriever")

    class UnexpectedModelCall:
        def stream(self, *_args, **_kwargs):
            raise AssertionError("重排不可用时不应调用生成模型")

    chain = object.__new__(RAGChain)
    chain.doc_db = object()
    chain.chunk_retriever_base = UnusedRetriever()
    chain._runtime_prompt_keys = []
    chain._gate_wrap_retrieve = lambda _payload: [
        Document(
            page_content="测试检索文档",
            metadata={"source_file": "test.md", "permission_scope": "general"},
        )
    ]
    request_state = RagRequestState(**state_kwargs)
    set_context(request_state)
    monkeypatch.setattr(chain_module, "llm", UnexpectedModelCall())
    monkeypatch.setattr(chain_module, "ENABLE_HISTORY_AWARE_RETRIEVAL", False)

    try:
        chain._build_chains()
        result = chain.chain_standalone.invoke(
            {"input": "退款审批", "chat_history": []},
        )
    finally:
        clear_context()

    assert isinstance(result["answer"], str)
    assert '"reason":"no_evidence"' in result["answer"]


def test_build_rejection_response_carries_permission_filtered():
    decision = GateDecision(passed=False, reason=RejectReason.NO_EVIDENCE,
                            layer="retrieval", score=0.0)
    msg, info = build_rejection_response(decision, "retrieval", permission_filtered=2)
    assert info.permission_filtered == 2
    assert info.to_dict()["permission_filtered"] == 2
    assert msg == "我在当前可访问的知识库中，没有找到能回答这个问题的资料。"


@pytest.mark.parametrize(
    "message",
    [
        "我在当前可访问的知识库中，没有找到能回答这个问题的资料。",
        "我找到了一些资料，但它们和问题不够相关，暂时不能据此回答。",
        "我找到了相关资料，但无法确认它们能支持这个答案，所以没有下结论。",
        "我找到了相关资料，但你当前没有查看权限。",
        "这次查询没有完成，我暂时无法判断知识库里是否有相关资料。",
    ],
)
def test_user_facing_rag_refusals_are_not_cacheable(message):
    from backend.rag.pipeline import RAGPipeline

    assert RAGPipeline._is_rejection(message)


# ── 工具出口 RAGMETA 标记 ───────────────────────────────────

def test_append_rag_answer_meta_with_status_and_confidence():
    out = append_rag_answer_meta("知识库暂无相关资料。", {"answer_status": "rag_no_evidence"})
    assert out.startswith("<!--RAGMETA")
    assert '"answer_status":"rag_no_evidence"' in out.replace(" ", "")
    assert out.endswith("知识库暂无相关资料。")


def test_append_rag_answer_meta_confidence_only():
    out = append_rag_answer_meta("正常回答", {"confidence": 0.85, "can_answer": True})
    assert "<!--RAGMETA" in out and '"confidence":0.85' in out.replace(" ", "")


def test_append_rag_answer_meta_no_payload_unchanged():
    assert append_rag_answer_meta("回答", {}) == "回答"
    assert append_rag_answer_meta("回答", {"can_answer": True}) == "回答"
    # 空答案不包标记
    assert append_rag_answer_meta("", {"answer_status": "rag_no_evidence"}) == ""


# ── reporter 剥离 ──────────────────────────────────────────

def test_strip_rag_meta_removes_marker():
    text = '<!--RAGMETA{"answer_status":"rag_no_evidence"}-->\n知识库暂无相关资料。'
    assert strip_rag_meta(text) == "知识库暂无相关资料。"
    assert strip_rag_meta("无标记文本") == "无标记文本"


def test_reporter_passthrough_strips_rag_meta():
    """单 RAG 步骤透传路径（direct 主链路）：final_answer 不含机读标记。"""
    output = ("<!--RAGMETA{\"answer_status\":\"rag_no_evidence\",\"confidence\":0.1}-->\n"
              "知识库暂无相关资料。依据不足时请换个问法，或补充更多背景信息再试。")
    sr = {"1": {"step_id": "1", "capability": "rag.search", "status": "success",
                "description": "知识库检索", "output": output}}
    final = generate_final_answer("测试问题", sr)
    assert "<!--RAGMETA" not in final
    assert "知识库暂无相关资料" in final


# ── done 帧提取 ────────────────────────────────────────────

def test_make_done_event_extracts_status_from_final_answer():
    answer = ('<!--RAGMETA{"answer_status":"rag_permission_denied"}-->\n'
              "知识库中存在相关资料，但当前账号没有查看权限。")
    evt = make_done_event(answer, {}, start_time=0.0)
    assert evt["event"] == "done"
    assert evt["data"]["answer_status"] == "rag_permission_denied"
    assert "confidence" not in evt["data"]

    degraded = (
        '<!--RAGMETA{"answer_status":"rag_retrieval_degraded"}-->\n'
        "这次查询没有完成，我暂时无法判断知识库里是否有相关资料。"
    )
    degraded_evt = make_done_event(degraded, {}, start_time=0.0)
    assert degraded_evt["data"]["answer_status"] == "rag_retrieval_degraded"


def test_make_done_event_falls_back_to_step_output():
    """LLM 重写路径 final_answer 无标记时，兜底扫 rag.search 步骤输出。"""
    sr = {"1": {"step_id": "1", "capability": "rag.search", "status": "success",
                "output": '<!--RAGMETA{"confidence":0.42}-->\n回答正文'}}
    evt = make_done_event("重写后的回答正文", sr, start_time=0.0)
    assert evt["data"]["confidence"] == 0.42
    assert "answer_status" not in evt["data"]


def test_make_done_event_keeps_source_indexes_for_citation_cards():
    """RAG 步骤的参考文献编号应原样进入 SSE done.sources。"""
    rag_output = (
        "回答正文见 [2] 和 [1]。\n\n---\n\n### 参考文献\n\n"
        "2. **第二份.pdf** (制度规范) — 第 4 页 <!--doc:doc-2-->\n"
        "1. **第一份.pdf** (制度规范) — 第 2 页 <!--doc:doc-1-->\n"
    )
    sr = {"1": {
        "step_id": "1", "capability": "rag.search", "status": "success",
        "output": rag_output,
    }}

    event = make_done_event("重写后的回答正文", sr, start_time=0.0)

    assert [source["index"] for source in event["data"]["sources"]] == [1, 2]
    assert [source["filename"] for source in event["data"]["sources"]] == [
        "第一份.pdf", "第二份.pdf",
    ]
    assert [source["doc_id"] for source in event["data"]["sources"]] == [
        "doc-1", "doc-2",
    ]


def test_make_done_event_keeps_same_file_citations_for_different_pages():
    rag_output = (
        "回答正文分别引用 [E1] 和 [E2]。\n\n---\n\n### 参考文献\n\n"
        "1. **制度.pdf** (制度规范) — 第 1 页 · 适用范围 "
        "<!--doc:policy-v1-->\n"
        "2. **制度.pdf** (制度规范) — 第 4 页 · 适用范围 "
        "<!--doc:policy-v1-->\n"
    )
    steps = {"1": {
        "step_id": "1", "capability": "rag.search", "status": "success",
        "output": rag_output,
    }}

    event = make_done_event("回答正文分别引用 [E1] 和 [E2]。", steps, start_time=0.0)

    assert [(source["index"], source["filename"], source["pages"])
            for source in event["data"]["sources"]] == [
        (1, "制度.pdf", [1]), (2, "制度.pdf", [4]),
    ]


def test_answer_presentation_metadata_matches_sse_for_history_restore():
    rag_output = (
        '<!--RAGMETA{"answer_status":"rag_retrieval_degraded",'
        '"confidence":0.42}-->\n回答正文依据有限。\n\n'
        "### 参考文献\n\n"
        "1. **员工手册.pdf** (制度规范) — 第 4 页 · 请假制度 "
        "<!--doc:policy-doc--><!--source_meta:version_id=v2;"
        "effective_from=2026-01-01;effective_to=2026-12-31-->\n"
    )
    steps = {"1": {
        "step_id": "1", "capability": "rag.search", "status": "success",
        "output": rag_output,
    }}

    history_metadata = extract_answer_presentation_metadata("回答正文依据有限。", steps)
    done_data = make_done_event(
        "回答正文依据有限。", steps, start_time=0.0,
    )["data"]

    assert history_metadata["answer_status"] == "rag_retrieval_degraded"
    assert history_metadata["confidence"] == 0.42
    assert history_metadata["reply_source"] == "knowledge_base"
    assert history_metadata["sources"][0]["doc_id"] == "policy-doc"
    assert history_metadata["sources"][0]["version_id"] == "v2"
    assert history_metadata["sources"][0]["effective_from"] == "2026-01-01"
    assert history_metadata["sources"][0]["effective_to"] == "2026-12-31"
    assert {key: done_data[key] for key in history_metadata} == history_metadata

    from datetime import datetime, timezone
    from backend.memory.service import _history_message_payload

    row = types.SimpleNamespace(
        id=1,
        role="assistant",
        content="回答正文依据有限。",
        created_at=datetime(2026, 11, 15, tzinfo=timezone.utc),
        message_metadata={"response_metadata": history_metadata},
    )
    history_payload = _history_message_payload(row)

    assert history_payload["sources"] == done_data["sources"]
    assert history_payload["answer_status"] == done_data["answer_status"]
    assert history_payload["confidence"] == done_data["confidence"]


def test_make_done_event_without_marker_has_no_status_keys():
    evt = make_done_event("普通回答", {}, start_time=0.0)
    assert "answer_status" not in evt["data"]
    assert "confidence" not in evt["data"]


# ── sources 部门字段 ───────────────────────────────────────

def test_extract_sources_carries_department():
    formatter = CitationFormatter()
    docs = [
        types.SimpleNamespace(metadata={
            "index": 1, "source_file": "报销制度.pdf", "doc_type": "policy",
            "score": 0.83, "department": "finance",
        }),
        types.SimpleNamespace(metadata={
            "index": 2, "source_file": "通用FAQ.md", "doc_type": "general",
            "score": 0.71, "department": "general",
        }),
    ]
    sources = formatter.extract_sources(docs, "见 [E1] [E2]")
    by_name = {s["filename"]: s for s in sources}
    assert by_name["报销制度.pdf"]["department"] == "finance"
    # general（无部门归属）不下发，保持展示不变
    assert "department" not in by_name["通用FAQ.md"]


# ── done 帧契约 ────────────────────────────────────────────

def test_done_frame_contract_accepts_new_fields():
    frame = {"event": "done", "data": {
        "elapsed": 1.2, "sources": [],
        "answer_status": "rag_no_evidence", "confidence": 0.42,
    }}
    validate_frame(frame)  # 不抛即通过
