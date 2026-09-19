"""R0/R1/LLM/fallback 的统一元数据决策器。"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Mapping

from backend.observability.metrics import (
    metadata_route_latency_seconds,
    metadata_route_total,
)
from backend.rag.preprocessing.metadata_classifier import (
    ClassifierPrediction,
    MetadataClassifier,
)
from backend.rag.preprocessing.metadata_evidence import (
    EvidenceBundle,
    extract_evidence,
    r0_candidate,
)
from backend.rag.preprocessing.metadata_llm import extract_metadata_llm_async
from backend.rag.preprocessing.metadata_schema import (
    DecisionCandidate,
    DecisionEnvelope,
    EvidenceItem,
    UnifiedMetadata,
)
from backend.rag.preprocessing.taxonomy_spec import get_taxonomy
from backend.shared.logger import logger


_classifier_path: str = ""
_classifier: MetadataClassifier | None = None


def _metadata_model_version() -> str:
    """返回参与缓存版本的实际模型指针。"""
    from backend.config.llm import LLM_MODEL
    from backend.config.rag import METADATA_CLASSIFIER_MODEL_PATH

    classifier = METADATA_CLASSIFIER_MODEL_PATH or "off"
    return f"llm:{LLM_MODEL}|classifier:{classifier}"


def _metadata_prompt_version() -> str:
    """从 PromptService 当前快照读取版本；默认模板使用稳定标识。"""
    try:
        from backend.prompts.service import prompt_service

        version = prompt_service.get_version_for_cache_key(
            "rag.preprocessing.metadata_extract"
        )
        return f"v{version}" if version is not None else "default"
    except Exception as exc:
        logger.debug(f"[MetaDecision] prompt version unavailable: {exc}")
        return "default"


def _observe_route_latency(source: str, started: float) -> None:
    metadata_route_latency_seconds.labels(source=source).observe(
        max(time.monotonic() - started, 0.0)
    )


def _evidence_models(evidence: EvidenceBundle) -> list[EvidenceItem]:
    return [
        EvidenceItem(
            kind=signal.source,
            rule_id=signal.rule_id,
            value=signal.value,
            weight=signal.score,
            strength=signal.strength,
        )
        for signal in evidence.signals
    ]


def _candidate_models(
    candidates: list[tuple[str, float]] | tuple[str, ...],
) -> list[DecisionCandidate]:
    result: list[DecisionCandidate] = []
    for candidate in candidates:
        if isinstance(candidate, tuple):
            label, score = candidate
        else:
            label, score = candidate, 0.0
        result.append(
            DecisionCandidate(
                doc_type=label,
                confidence=max(0.0, min(float(score), 1.0)),
                source="classifier" if isinstance(candidate, tuple) else "evidence",
            )
        )
    return result


def _domain_for_text(text: str) -> str:
    try:
        from backend.rag.preprocessing.metadata import detect_business_domain

        return str(detect_business_domain(text)[0] or "general")
    except Exception as exc:
        logger.debug(f"[MetaDecision] 规则域识别失败，回落 general: {exc}")
        return "general"


def _envelope(
    *,
    decision: str,
    doc_type: str,
    business_domain: str,
    confidence: float,
    source: str,
    evidence: EvidenceBundle,
    candidates: list[DecisionCandidate] | None = None,
    conflicts: list[str] | None = None,
    fallback_reason: str = "",
    model_version: str = "",
    prompt_version: str = "",
    latency_ms: float = 0.0,
    llm_call_count: int = 0,
    metadata: dict | None = None,
) -> DecisionEnvelope:
    taxonomy = get_taxonomy()
    return DecisionEnvelope.model_validate(
        {
            "decision": decision,
            "doc_type": doc_type,
            "business_domain": business_domain,
            "confidence": confidence,
            "candidates": candidates or [],
            "source": source,
            "evidence": _evidence_models(evidence),
            "conflicts": conflicts or list(evidence.conflicts),
            "taxonomy_version": taxonomy.version,
            "rules_version": evidence.rules_version,
            "model_version": model_version,
            "prompt_version": prompt_version,
            "fallback_reason": fallback_reason,
            "latency_ms": latency_ms,
            "llm_call_count": llm_call_count,
            "metadata": metadata or {},
        }
    )


def build_deterministic_fallback(
    full_text: str,
    filename: str,
    file_path: str,
    evidence: EvidenceBundle,
    reason: str,
    *,
    llm_call_count: int = 0,
    latency_ms: float = 0.0,
) -> DecisionEnvelope:
    """构造完整 fallback；该函数自身绝不触发任何 LLM。"""
    del filename, file_path
    candidate = r0_candidate(evidence)
    doc_type = candidate or "general"
    return _envelope(
        decision="review" if evidence.conflicts else "fallback",
        doc_type=doc_type,
        business_domain=_domain_for_text(full_text),
        confidence=0.0,
        source="fallback",
        evidence=evidence,
        candidates=_candidate_models(tuple(evidence.candidates)),
        fallback_reason=reason,
        latency_ms=latency_ms,
        llm_call_count=llm_call_count,
    )


async def _load_classifier() -> MetadataClassifier | None:
    from backend.config.rag import (
        METADATA_CLASSIFIER_ENABLED,
        METADATA_CLASSIFIER_MODEL_PATH,
    )

    global _classifier, _classifier_path
    if not METADATA_CLASSIFIER_ENABLED or not METADATA_CLASSIFIER_MODEL_PATH:
        return None
    if _classifier_path == METADATA_CLASSIFIER_MODEL_PATH:
        return _classifier
    _classifier_path = METADATA_CLASSIFIER_MODEL_PATH
    _classifier = await asyncio.to_thread(
        MetadataClassifier.load, Path(METADATA_CLASSIFIER_MODEL_PATH)
    )
    return _classifier


async def _classifier_prediction(
    full_text: str,
    filename: str,
    file_path: str,
    embedding,
    evidence: EvidenceBundle,
) -> ClassifierPrediction | None:
    """加载 R1 并准备与训练卡一致的嵌入特征。"""
    del evidence
    classifier = await _load_classifier()
    if classifier is None:
        return None
    embedding_sims: Mapping[str, float] | None = None
    if bool(classifier.card.get("embedding_features_on", False)):
        if embedding is None:
            return classifier.predict(full_text, filename, file_path, None)
        from backend.config.rag import METADATA_CLASSIFIER_LOAD_TIMEOUT
        from backend.rag.preprocessing.metadata_router import _taxonomy_index

        ranked = await _taxonomy_index.classify(
            full_text, embedding, METADATA_CLASSIFIER_LOAD_TIMEOUT
        )
        if ranked is None:
            return classifier.predict(full_text, filename, file_path, None)
        embedding_sims = {label: score for label, score in ranked}
    return classifier.predict(full_text, filename, file_path, embedding_sims)


async def decide_metadata(
    full_text: str,
    filename: str,
    file_path: str = "",
    embedding=None,
    parent_span_id: str = "",
) -> DecisionEnvelope:
    """按固定顺序完成一次文档级元数据决策。"""
    started = time.monotonic()
    evidence = extract_evidence(full_text, filename, file_path)
    from backend.rag.preprocessing.metadata_runtime import (
        get_cached_decision_async,
        metadata_cache_key,
        put_cached_decision_async,
    )

    taxonomy = get_taxonomy()
    cache_model_version = _metadata_model_version()
    cache_prompt_version = _metadata_prompt_version()
    cache_key = metadata_cache_key(
        full_text,
        filename,
        file_path,
        taxonomy.version,
        cache_model_version,
        cache_prompt_version,
        evidence.rules_version,
    )
    cached = await get_cached_decision_async(cache_key)
    if cached is not None:
        _observe_route_latency(cached.source, started)
        return cached

    candidate = r0_candidate(evidence)
    if candidate is not None:
        metadata_route_total.labels(level="R0", outcome="hit").inc()
        envelope = _envelope(
            decision="accepted",
            doc_type=candidate,
            business_domain=_domain_for_text(full_text),
            confidence=0.99,
            source="r0",
            evidence=evidence,
            candidates=_candidate_models(tuple(evidence.candidates)),
            latency_ms=(time.monotonic() - started) * 1000,
        )
        _observe_route_latency(envelope.source, started)
        await put_cached_decision_async(cache_key, envelope)
        return envelope
    metadata_route_total.labels(level="R0", outcome="miss").inc()

    prediction = await _classifier_prediction(
        full_text, filename, file_path, embedding, evidence
    )
    if prediction is not None and prediction.accepted:
        metadata_route_total.labels(level="R1", outcome="hit").inc()
        envelope = _envelope(
            decision="accepted",
            doc_type=prediction.label,
            business_domain=_domain_for_text(full_text),
            confidence=prediction.confidence,
            source="r1",
            evidence=evidence,
            candidates=_candidate_models(prediction.candidates),
            model_version=prediction.model_version,
            latency_ms=(time.monotonic() - started) * 1000,
        )
        _observe_route_latency(envelope.source, started)
        await put_cached_decision_async(cache_key, envelope)
        return envelope
    metadata_route_total.labels(
        level="R1", outcome=(prediction.abstain_reason if prediction else "skip")
    ).inc()

    llm_calls = 1
    try:
        llm_result = await extract_metadata_llm_async(
            full_text, filename, parent_span_id=parent_span_id
        )
    except Exception as exc:
        logger.warning(f"[MetaDecision] LLM 抽取异常，进入确定性兜底: {exc}")
        llm_result = None
    if llm_result:
        try:
            unified = UnifiedMetadata.model_validate(llm_result)
            metadata_route_total.labels(level="R2", outcome="hit").inc()
            envelope = _envelope(
                decision="accepted",
                doc_type=unified.doc_type,
                business_domain=unified.business_domain,
                confidence=unified.confidence,
                source="llm",
                evidence=evidence,
                candidates=_candidate_models([(unified.doc_type, unified.confidence)]),
                model_version=cache_model_version,
                prompt_version=str(llm_result.get("prompt_version", "default")),
                latency_ms=(time.monotonic() - started) * 1000,
                llm_call_count=llm_calls,
                metadata=unified.to_extract_dict(),
            )
            _observe_route_latency(envelope.source, started)
            await put_cached_decision_async(cache_key, envelope)
            return envelope
        except Exception as exc:
            logger.warning(f"[MetaDecision] LLM 结果未通过统一 Schema: {exc}")
            reason = "llm_schema_invalid"
    else:
        reason = "llm_unavailable"
    metadata_route_total.labels(level="R2", outcome="fallback").inc()
    envelope = build_deterministic_fallback(
        full_text,
        filename,
        file_path,
        evidence,
        reason,
        llm_call_count=llm_calls,
        latency_ms=(time.monotonic() - started) * 1000,
    )
    _observe_route_latency(envelope.source, started)
    return envelope
