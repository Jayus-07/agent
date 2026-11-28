"""RAG Chain 事实校验、Claim 比对与忠实度评估。"""
from __future__ import annotations

import re

from backend.rag.chain_modules.metrics_helper import _mark_gate_degraded
from backend.shared.logger import logger


def verify_answer(chain_instance, result: dict, question: str, session_id: str = "default") -> str:
    """验证阶段：剥离 think 块 + META 注释解析 + Citation 校验 + 格式化引用。

    P1 改造：
      - 解析 LLM 末尾 <!--META--> 注释，剥离出纯 Markdown
      - META 信息存到 chain_instance._last_meta，让 ask() 后续判定拒答/放行
    """
    from backend.observability.tracer import SpanName as _SpanName
    from backend.observability.tracer import trace_collector
    from backend.rag.evidence_gate import parse_meta_comment

    raw_answer = chain_instance.formatter.strip_think(result["answer"])
    context_docs = result.get("context", [])

    # ── P1: 解析 META 注释 ──
    meta_span = trace_collector.start_span("meta_parse", name=_SpanName.META_PARSE)
    cleaned_answer, meta = parse_meta_comment(raw_answer)
    # 分点归一化：句号+序号边界补换行（提示词约束不住的同行要点，此处确定性修复）
    cleaned_answer = chain_instance.formatter.normalize_point_layout(cleaned_answer)
    chain_instance._last_meta = meta
    trace_collector.end_span(meta_span,
                             metrics={"can_answer": meta.get("can_answer"),
                                      "citations_count": len(meta.get("citations", [])) if meta else 0,
                                      "confidence": meta.get("confidence")})

    # ── Citation 校验（基于已剥离 META 的 cleaned_answer）──
    citation_span = trace_collector.start_span(
        "citation", name=_SpanName.CITATION)
    if context_docs:
        answer, verified_docs = chain_instance.formatter.verify_support(cleaned_answer, context_docs, question)
    else:
        answer = cleaned_answer
        verified_docs = []
    # 多源同事实出现不同数值时，确定性标注「存在不同说法」并保留双方引用；
    answer = chain_instance.formatter.annotate_conflicts(answer, verified_docs, question)
    chain_instance._last_citation_evidence = chain_instance.formatter.extract_citation_evidence(
        verified_docs,
        answer,
    )
    trace_collector.end_span(citation_span,
                         metrics={"verified_citations": len(verified_docs),
                                  "total_citations": len(context_docs),
                                  "final_evidence_count": len(verified_docs)})
    references = chain_instance.formatter.format_references(verified_docs, answer)
    if references:
        answer = answer + references
    chain_instance._last_sources = chain_instance.formatter.extract_sources(verified_docs, answer)

    return answer


def verify_claims(answer: str, context_docs: list) -> str | None:
    """程序化 Claim 校验（非 LLM）。

    确定性事实（数字+单位/日期/金额/时效）与引用 chunk 原文比对。
    任一 claim 校验失败 → 返回 None（调用方拒答），LLM Judge 不能覆盖。

    Returns:
        answer（通过时原样返回）或 None（编造事实被拦截）。
    """
    from backend.observability.tracer import SpanName as _SpanName
    from backend.observability.tracer import trace_collector
    from backend.rag.evidence_gate.claim_verifier import verify_answer as raw_verify_answer

    claim_span = trace_collector.start_span(
        "claim_verify", name=_SpanName.CLAIM_VERIFY)
    try:
        verifier = raw_verify_answer(answer, context_docs)
        trace_collector.end_span(claim_span,
            metrics={"passed": verifier.passed,
                     "failed_claims": len(verifier.failed_claims),
                     "reason": verifier.reason})
        if not verifier.passed:
            logger.warning(
                f"[RAGChain] ClaimVerifier 拦截编造事实: {verifier.detail[:200]}"
            )
            return None
        return answer
    except Exception as e:
        logger.warning(f"[RAGChain] ClaimVerifier 异常跳过: {e}")
        trace_collector.end_span(claim_span, status="skipped",
                                 metrics={"error": str(e)[:100]})
        _mark_gate_degraded("claim_verify", "skip_check", e)
        return answer


def evaluate_faithfulness(chain_instance, answer: str, context_docs: list) -> str:
    """评估阶段：Faithfulness 忠实性检测 + 自动剔除不可信句子。"""
    from backend.observability.tracer import SpanName as _SpanName
    from backend.observability.tracer import trace_collector
    chain_instance._last_faithfulness = None
    faith_span = None

    try:
        from backend.rag.guardrails import check_faithfulness

        # 剥离 reference section
        ref_match = re.search(r'\n---\n\s*\n###\s*参考文献\s*\n', answer)
        answer_body = answer[:ref_match.start()] if ref_match else answer
        ref_section = answer[ref_match.start():] if ref_match else ""

        faith_span = trace_collector.start_span(
            "faithfulness", name=_SpanName.EVALUATE)
        chain_instance._last_faithfulness = check_faithfulness(answer_body, context_docs)
        trace_collector.end_span(faith_span,
                             metrics={
                                 "score": chain_instance._last_faithfulness.score,
                                 "claims": chain_instance._last_faithfulness.total_claims,
                                 "supported": chain_instance._last_faithfulness.supported_claims,
                                 "unsupported": chain_instance._last_faithfulness.unsupported_claims,
                             })

        if chain_instance._last_faithfulness.cleaned_answer and \
           chain_instance._last_faithfulness.cleaned_answer != answer_body:
            logger.warning(
                f"[RAGChain] 自动剔除 {chain_instance._last_faithfulness.unsupported_claims} 条不可信内容"
            )
            return chain_instance._last_faithfulness.cleaned_answer + ref_section
        return answer
    except Exception as e:
        logger.warning(f"[RAGChain] Faithfulness 检测跳过: {e}")
        if faith_span is not None:
            trace_collector.end_span(faith_span, status="skipped",
                                 metrics={"error": str(e)[:100]})
        _mark_gate_degraded("faithfulness", "skip_check", e)
        return answer
