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

from backend.orchestration.graph.event_schema import validate_frame
from backend.orchestration.graph.events import make_done_event
from backend.rag.citation import CitationFormatter
from backend.rag.evidence_gate import (
    GateDecision,
    RejectReason,
    build_rejection_response,
    resolve_answer_status,
    ANSWER_STATUS_HALLUCINATION,
    ANSWER_STATUS_NO_EVIDENCE,
    ANSWER_STATUS_PERMISSION_DENIED,
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


def test_build_rejection_response_carries_permission_filtered():
    decision = GateDecision(passed=False, reason=RejectReason.NO_EVIDENCE,
                            layer="retrieval", score=0.0)
    msg, info = build_rejection_response(decision, "retrieval", permission_filtered=2)
    assert info.permission_filtered == 2
    assert info.to_dict()["permission_filtered"] == 2
    assert msg == "知识库暂无相关资料。"


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


def test_make_done_event_falls_back_to_step_output():
    """LLM 重写路径 final_answer 无标记时，兜底扫 rag.search 步骤输出。"""
    sr = {"1": {"step_id": "1", "capability": "rag.search", "status": "success",
                "output": '<!--RAGMETA{"confidence":0.42}-->\n回答正文'}}
    evt = make_done_event("重写后的回答正文", sr, start_time=0.0)
    assert evt["data"]["confidence"] == 0.42
    assert "answer_status" not in evt["data"]


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
