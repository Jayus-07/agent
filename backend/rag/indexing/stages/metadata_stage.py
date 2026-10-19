"""Metadata Stage — 文档级元数据构建（规则路径 + 统一 LLM 抽取路径）。

从 indexer.py 原文迁移（行为零改动），IncrementalIndexer 上的
_build_doc_metadata / _finalize_unified_metadata / _detect_near_dup
保留为薄委托。返回 dict 契约不变（下游 chunk 注入 / doc_db 落库 /
registry.register 依赖的键集见 build() 返回值）。

span 收口：quality/section span 改用 stages.contracts.stage_span
（异常路径自动关 span，修复既有泄漏）；classify 等带自定义错误
语义的 span 保留原文写法。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from concurrent.futures import Future
from contextlib import nullcontext

from backend.observability.tracer import trace_collector, SpanKind
from backend.rag.indexing.stages.contracts import stage_span
from backend.shared.logger import logger
from backend.rag.indexing.processing_lineage import ModelIdentity


# 摘要采样：委托共享实现（P1 元数据治理 2026-10-13——超长文档不再只取
# 头尾 20k 声称代表全文，改为 头+章节目录+分层采样+尾 且标注覆盖范围）
def _sample_for_summary(text: str) -> str:
    from backend.rag.preprocessing.summary_sampling import sample_for_summary
    return sample_for_summary(text)


class MetadataStage:
    """元数据阶段：持有 registry / embedding / department 引用（只读）。"""

    def __init__(self, registry, embedding, department: str = ""):
        self._registry = registry
        self._embedding = embedding
        self._department = department
        self._shadow_tasks: set[Future] = set()

    @staticmethod
    def _embedding_model_name(embedding) -> str:
        """读取 Embedding 包装器中的实际模型名。"""
        for attribute in ("_model_name", "model_name", "model"):
            value = getattr(embedding, attribute, None)
            if isinstance(value, str) and value.strip():
                return os.path.basename(value.strip())
        return ""

    @staticmethod
    def _role_model_identity(role: str, actual_model: str | None = None) -> ModelIdentity:
        """读取本次入库实际生效的角色模型，不读取待提交配置。"""
        from backend.config import model_roles

        effective = model_roles.resolve_effective(role)
        model_name = str(actual_model or "").strip() or str(
            effective.get("value") or ""
        ).strip() or None
        # provider 必须走运行时模型注册表：管理端新增/覆盖的模型可能不在
        # 旧的 model_roles 快照里，否则血缘会留下 model_name 但 provider 为空。
        if model_name:
            from backend.infra.llm.models import resolve_provider

            provider = resolve_provider(model_name)
        else:
            provider = None
        return ModelIdentity(
            role=role,
            engine_type="llm",
            provider=provider,
            model_name=model_name,
            config_source=str(effective.get("source") or "") or None,
            config_revision=str(effective.get("updated_at") or "") or None,
        )

    @staticmethod
    def _begin_lineage_stage(recorder, stage: str, role: str | None,
                             engine_type: str = "llm", *,
                             metadata: dict | None = None):
        if recorder is None:
            return None
        return recorder.begin_stage(
            stage,
            role=role,
            engine_type=engine_type,
            metadata=metadata,
        )

    @staticmethod
    def _finish_lineage_stage(recorder, token, *, status: str,
                              model: ModelIdentity | None = None,
                              input_count: int = 0, output_count: int = 0,
                              usage: dict | None = None,
                              cache_status: str = "miss",
                              fallback_reason: str | None = None,
                              error_message: str | None = None,
                              skip_reason: str | None = None,
                              prompt_key: str | None = None,
                              prompt_version: str | None = None,
                              prompt_hash: str | None = None,
                              taxonomy_version: str | None = None,
                              rules_version: str | None = None,
                              schema_fingerprint: str | None = None,
                              metadata: dict | None = None) -> None:
        if recorder is None or token is None:
            return
        if model is not None:
            recorder.set_stage_model(token[0], model)
        recorder.finish_stage(
            token[0],
            status=status,
            started_at=token[1],
            input_count=input_count,
            output_count=output_count,
            cache_status=cache_status,
            usage=usage,
            fallback_reason=fallback_reason,
            error_message=error_message,
            skip_reason=skip_reason,
            prompt_key=prompt_key,
            prompt_version=prompt_version,
            prompt_hash=prompt_hash,
            taxonomy_version=taxonomy_version,
            rules_version=rules_version,
            schema_fingerprint=schema_fingerprint,
            metadata=metadata,
        )

    async def _generate_questions_with_lineage(
        self, chunks_text: list[str], doc_type: str, recorder=None,
        parent_span_id: str = "", skip_reason: str = "",
    ) -> tuple[list[list[str]], dict]:
        """生成模拟问题，并把 question_gen 独立记录为一个阶段。"""
        token = self._begin_lineage_stage(
            recorder, "question_gen", "question_gen", metadata={"chunk_count": len(chunks_text)}
        )
        q_span = (
            trace_collector.start_span(
                "question_gen_stage", parent_id=parent_span_id,
                name="Simulated question generation", type="question_stage",
                kind=SpanKind.INDEX_METADATA.value,
            ) if parent_span_id else None
        )
        if skip_reason or not chunks_text:
            self._finish_lineage_stage(
                recorder, token, status="skipped", input_count=len(chunks_text),
                skip_reason=skip_reason or "no_chunks",
            )
            if q_span is not None:
                trace_collector.end_span(
                    q_span, status="skipped",
                    metrics={"provider_call": False, "cache_status": "not_checked",
                             "skip_reason": skip_reason or "no_chunks"},
                )
            return [], {}
        from backend.rag.preprocessing import question_gen as _qg

        try:
            def _generate_in_worker():
                if q_span is not None:
                    generated = _qg.generate_chunk_questions(
                        chunks_text, doc_type, parent_span_id=q_span.span_id,
                    )
                else:
                    generated = _qg.generate_chunk_questions(chunks_text, doc_type)
                return generated[0], generated[1], _qg.get_last_generation_meta()

            with recorder.bind_stage(token[0]) if recorder and token else nullcontext():
                questions, usage, generation_meta = await asyncio.to_thread(
                    _generate_in_worker,
                )
            from backend.config.rag import (
                METADATA_SCHEMA_FINGERPRINT,
                QUESTION_GEN_PROMPT_VERSION,
            )
            stage_status = str(
                usage.get("stage_status")
                or generation_meta.get("status")
                or "success"
            )
            if stage_status not in {"success", "skipped", "cached", "fallback", "failed"}:
                stage_status = "success"
            actual_model = str(
                usage.get("model") or generation_meta.get("model") or ""
            )
            self._finish_lineage_stage(
                recorder, token, status=stage_status,
                model=(None if stage_status == "skipped" else
                       self._role_model_identity("question_gen", actual_model)),
                input_count=len(chunks_text),
                output_count=sum(len(item or []) for item in questions or []),
                usage=usage,
                cache_status=str(usage.get("cache_status") or generation_meta.get(
                    "cache_status"
                ) or (
                    "hit" if stage_status == "cached" else "miss"
                )),
                fallback_reason=str(
                    usage.get("fallback_reason")
                    or generation_meta.get("fallback_reason")
                    or ""
                ) or None,
                skip_reason=str(
                    usage.get("skip_reason")
                    or generation_meta.get("skip_reason")
                    or ""
                ) or None,
                prompt_key="rag.preprocessing.question_gen",
                prompt_version=QUESTION_GEN_PROMPT_VERSION,
                schema_fingerprint=METADATA_SCHEMA_FINGERPRINT,
            )
            if q_span is not None:
                trace_collector.end_span(
                    q_span,
                    status="error" if stage_status == "failed" else "success",
                    metrics={
                        "provider_call": bool(generation_meta.get("provider_call")),
                        "cache_status": generation_meta.get("cache_status", "miss"),
                        "provider_call_wall_ms": generation_meta.get(
                            "provider_call_wall_ms"
                        ),
                        "prompt_tokens": usage.get("prompt_tokens"),
                        "completion_tokens": usage.get("completion_tokens"),
                        "reasoning_tokens": usage.get("reasoning_tokens"),
                        "total_tokens": usage.get("total_tokens"),
                        "usage_status": generation_meta.get("usage_status", "unavailable"),
                        "fallback": stage_status == "fallback",
                        "fallback_reason": generation_meta.get("fallback_reason"),
                        "skip_reason": generation_meta.get("skip_reason"),
                    },
                )
            return questions, usage
        except Exception as exc:
            self._finish_lineage_stage(
                recorder, token, status="failed",
                input_count=len(chunks_text), error_message=str(exc),
            )
            if q_span is not None:
                trace_collector.end_span(
                    q_span, status="error",
                    metrics={"provider_call": False, "fallback": True,
                             "fallback_reason": "question_generation_error"},
                )
            raise

    async def build(self, full_text: str, base_meta: dict,
                    parent_span_id: str = "",
                    chunks_text: list[str] | None = None,
                    processing_recorder=None) -> dict:
        """规则先分类，再按内容需要选择性做一次摘要/实体增强。"""
        try:
            from backend.config.rag import (
                ENABLE_LLM_METADATA_EXTRACT,
                METADATA_SCHEMA_FINGERPRINT as schema_fingerprint,
            )
            from backend.rag.preprocessing.entity import extract_entities
            from backend.rag.preprocessing.metadata import (
                analyze_complexity, assess_quality, classify_with_confidence,
                detect_business_domain, extract_time_refs,
            )
            from backend.rag.preprocessing.keyword import extract_rule_keywords
        except ImportError:
            return {}

        started = time.monotonic()
        filename = base_meta.get("source_file", "")
        file_path = base_meta.get("file_path", "")
        try:
            doc_type, confidence, class_detail = classify_with_confidence(
                full_text, filename=filename, file_path=file_path,
                return_detail=True,
            )
        except Exception as exc:
            logger.warning(f"[Metadata] 规则文档分类失败，使用通用类型: {exc}")
            doc_type, confidence, class_detail = "general", 0.0, {}

        try:
            domain, _alternatives, domain_detail = detect_business_domain(
                full_text, return_detail=True,
            )
        except Exception as exc:
            logger.warning(f"[Metadata] 规则业务领域识别失败，使用 general: {exc}")
            domain, domain_detail = "general", {}

        if parent_span_id:
            class_span = trace_collector.start_span(
                "classify", parent_id=parent_span_id,
                name="Rule document classification", type="rule",
                kind=SpanKind.INDEX_CLASSIFY.value,
            )
            trace_collector.end_span(
                class_span,
                metrics={"doc_type": doc_type, "confidence": round(confidence, 3),
                         "source": "rule"},
                output=class_detail or {},
            )
            domain_span = trace_collector.start_span(
                "domain_classify", parent_id=parent_span_id,
                name="Rule business domain classification", type="rule",
                kind=SpanKind.INDEX_DOMAIN_CLASSIFY.value,
            )
            trace_collector.end_span(
                domain_span, metrics={"domain": domain, "source": "rule"},
                output=domain_detail or {},
            )

        quality = assess_quality(full_text)
        if parent_span_id:
            with stage_span(parent_span_id, "quality", "Quality check",
                            SpanKind.INDEX_QUALITY_CHECK.value) as quality_span:
                if quality_span is not None:
                    quality_span.metrics = {
                        "score": quality.get("score"),
                        "status": quality.get("status"),
                        "issues": quality.get("issues", []),
                        "source": "metadata_quality_check",
                    }
                    quality_span.output = quality.get("dimensions", {})

        from backend.rag.preprocessing.parser._qa_patterns import looks_like_qa_doc
        is_qa_document = looks_like_qa_doc(full_text)
        skip_reason = ""
        if not ENABLE_LLM_METADATA_EXTRACT:
            skip_reason = "feature_disabled"
        elif is_qa_document:
            skip_reason = "structured_qa"
        elif len(full_text.strip()) < 2000:
            skip_reason = "short_content_extractive_summary"

        unified: dict = {
            "doc_type": doc_type,
            "confidence": confidence,
            "business_domain": domain,
            "summary": "",
            "keywords": [],
            "entities": {},
            "time_refs": [],
            "risk": {"level": "none", "signals": []},
            "llm_tokens": {},
            "provider_call": False,
            "llm_usage_status": "unavailable",
            "metadata_source": "rules",
        }
        decision_stage = self._begin_lineage_stage(
            processing_recorder, "metadata_extract", "metadata_extract",
            metadata={"source": "rules", "skip_reason": skip_reason},
        )
        if not skip_reason:
            try:
                from backend.rag.preprocessing.metadata_llm import extract_metadata_llm_async
                binding = (
                    processing_recorder.bind_stage(decision_stage[0])
                    if processing_recorder and decision_stage else nullcontext()
                )
                with binding:
                    extracted = await extract_metadata_llm_async(
                        full_text, filename, parent_span_id=parent_span_id,
                    )
                if extracted:
                    unified.update(extracted)
                    # 分类事实只来自规则分类器；LLM 的兼容字段不得覆盖。
                    unified.update({
                        "doc_type": doc_type,
                        "confidence": confidence,
                        "business_domain": domain,
                        "provider_call": True,
                        "metadata_source": "llm_enhancement",
                    })
                else:
                    unified["metadata_source"] = "rules_fallback"
            except Exception as exc:
                logger.warning(f"[MetaLLM] 摘要/实体增强失败，使用规则降级: {exc}")
                unified["metadata_source"] = "rules_fallback"

        used = unified.get("metadata_source") == "llm_enhancement"
        if processing_recorder is not None and decision_stage is not None:
            self._finish_lineage_stage(
                processing_recorder, decision_stage,
                status="success" if used else ("skipped" if skip_reason else "fallback"),
                model=(self._role_model_identity(
                    "metadata_extract", str(unified.get("actual_model") or "")
                ) if used else None),
                input_count=1, output_count=1 if used else 0,
                usage=dict(unified.get("llm_tokens") or {}) or None,
                skip_reason=skip_reason or None,
                fallback_reason=(None if used or skip_reason else "llm_unavailable"),
                prompt_key="rag.preprocessing.metadata_extract",
                prompt_version=str(unified.get("prompt_version") or "") or None,
                prompt_hash=str(unified.get("prompt_hash") or "") or None,
                schema_fingerprint=schema_fingerprint,
                metadata={
                    "classification_source": "rule",
                    "llm_usage_status": unified.get("llm_usage_status", "unavailable"),
                    "prompt_source": unified.get("prompt_source", ""),
                },
            )

        try:
            rule_entities = await asyncio.to_thread(extract_entities, full_text)
        except Exception as exc:
            logger.debug(f"[Metadata] 规则实体抽取失败，按空值降级: {exc}")
            rule_entities = {}
        persons = []
        for entity_type in ("person", "organization", "platform"):
            persons.extend(rule_entities.get(entity_type) or [])
        merged_entities = {key: list(values or []) for key, values in rule_entities.items()}
        for key, values in (unified.get("entities") or {}).items():
            merged_entities[key] = list(dict.fromkeys(
                [*merged_entities.get(key, []), *(values or [])]
            ))
        unified["entities"] = merged_entities
        unified["person_names"] = persons
        unified["time_refs"] = unified.get("time_refs") or extract_time_refs(full_text)
        unified["rule_keywords"] = extract_rule_keywords(full_text, doc_type=doc_type)
        unified["summary"] = unified.get("summary") or self._extractive_summary(full_text)
        try:
            unified["complexity"] = analyze_complexity(
                full_text, len(unified["rule_keywords"]), confidence,
            )
        except Exception:
            unified["complexity"] = {}
        unified["quality"] = quality
        unified["domain_detail"] = domain_detail or {}
        unified["classification_source"] = "rule"
        unified["allow_question_generation"] = not is_qa_document
        if skip_reason:
            unified["metadata_skip_reason"] = skip_reason

        metadata = await self.finalize_unified(
            full_text, base_meta, unified,
            parent_span_id=parent_span_id, chunks_text=chunks_text,
            allow_llm_enrichment=not is_qa_document,
            processing_recorder=processing_recorder,
        )
        metadata["stage_ms"] = {
            "total_ms": round((time.monotonic() - started) * 1000),
        }
        return metadata

    @staticmethod
    def _extractive_summary(text: str) -> str:
        """以原文首句做摘要，避免降级时重新调用模型阅读全文。"""
        from backend.rag.preprocessing.llm_enrichment import _extract_first_sentences
        return _extract_first_sentences(text, 3) or ""

    @staticmethod
    def detect_near_dup(registry, minhash_sig: list[int], doc_type: str,
                        exclude_doc_id: str = "") -> str:
        """MinHash 近重复检测：与全部存量文档比对（跨 doc_type），返回 near_dup_id 或 ""。

        2026-09-17 起不再按 doc_type 过滤：LLM/规则分类存在漂移，同一文档重索引
        可能被判成不同 doc_type，按类型过滤会造成近重复漏检（副本被误翻 active
        的事故根因之一）。内容相似度本身与类型无关。
        """
        from backend.config.indexing_rules import get_rules
        from backend.rag.preprocessing.metadata import minhash_similarity
        rules = get_rules()
        existing_rows = list(registry.list_all().values())
        for existing in existing_rows:
            if existing.get("doc_id") == exclude_doc_id:
                continue
            existing_sig = existing.get("minhash_sig", "")
            if not existing_sig:
                continue
            try:
                existing_sig = json.loads(existing_sig) if isinstance(existing_sig, str) else existing_sig
                sim = minhash_similarity(minhash_sig, existing_sig)
                if sim > rules.near_dup_similarity_threshold:
                    logger.warning(
                        f"[MinHash] 检测到近似文档: sim={sim:.2f}, existing={existing.get('doc_id', '')}")
                    return existing.get("doc_id", "")
            except Exception as e:
                logger.debug(f"[MinHash] 单文档签名比对失败，跳过: {e}", exc_info=True)
        return ""

    async def finalize_unified(
        self, full_text: str, base_meta: dict, unified: dict,
        parent_span_id: str = "", chunks_text: list[str] | None = None,
        route_level: str = "", allow_llm_enrichment: bool = True,
        processing_recorder=None,
    ) -> dict:
        """收口规则元数据与可选增强，返回兼容的完整 metadata dict。

        与 build() 返回契约完全一致，保证下游（chunk 注入、
        doc_db 落库、registry.register）零感知路径差异。

        route_level 非空 = 级联路由 L0-L2 命中（分类零 LLM 成本，结构字段为
        规则产物）：llm_used=False、llm_strategy 标 cascade_L{N}。
        """
        import hashlib as _hashlib
        from backend.rag.preprocessing.metadata import (
            assess_quality, analyze_complexity, compute_minhash,
            extract_sections, extract_person_names, extract_time_refs,
        )
        from backend.config.rag import (
            METADATA_SCHEMA_FINGERPRINT as _metadata_fp,
            QUESTION_GEN_PROMPT_VERSION,
        )

        fname = base_meta.get("source_file", "")
        doc_id = base_meta.get("doc_id", "")

        # 与规则阶段共享同一评分结果，避免重复计算出两份元数据。
        quality = unified.get("quality") or assess_quality(full_text)

        # MinHash 近重复（规则产物，决定 pending_review，两条路径都必须算）
        minhash_sig = compute_minhash(full_text)
        # P0-3 收敛后统一路径自治计时（不含 build 阶段的 LLM 抽取耗时）
        import time as _time
        _stage_ms: dict = {}
        _t_final0 = _time.monotonic()
        near_dup_id = self.detect_near_dup(
            self._registry, minhash_sig, unified["doc_type"], exclude_doc_id=doc_id)

        # 规则关键词预览（复杂度入参；rule/llm 关键词合并对齐旧字段口径）
        from backend.rag.preprocessing.keyword import extract_rule_keywords
        rule_kws_preview = unified.get("rule_keywords")
        if rule_kws_preview is None:
            rule_kws_preview = extract_rule_keywords(
                full_text, doc_type=unified["doc_type"]
            )
        complexity = unified.get("complexity") or analyze_complexity(
            full_text, len(rule_kws_preview), unified["confidence"])

        # 人名：优先 LLM 实体，空则正则兜底（与规则路径行为一致）
        person_names = list(unified.get("person_names") or [])
        if not person_names:
            person_names = list((unified.get("entities") or {}).get("person") or [])
        if not person_names and "person_names" not in unified:
            person_names = extract_person_names(full_text) or []

        # 模拟问题（Document Expansion）——与规则路径共用同一开关与实现
        questions_by_chunk: list[list[str]] = []
        question_gen_tokens: dict = {}
        try:
            from backend.config.rag import ENABLE_SIMULATED_QUESTIONS
            if allow_llm_enrichment and ENABLE_SIMULATED_QUESTIONS and chunks_text:
                questions_by_chunk, question_gen_tokens = await self._generate_questions_with_lineage(
                    chunks_text,
                    unified["doc_type"],
                    processing_recorder,
                    parent_span_id=parent_span_id,
                )
            else:
                question_skip_reason = (
                    "structured_qa" if not allow_llm_enrichment
                    else "feature_disabled" if not ENABLE_SIMULATED_QUESTIONS
                    else "no_chunks"
                )
                await self._generate_questions_with_lineage(
                    chunks_text or [], unified["doc_type"], processing_recorder,
                    parent_span_id=parent_span_id,
                    skip_reason=question_skip_reason,
                )
        except Exception as e:
            logger.warning(f"[MetaLLM] 模拟问题生成失败（不影响元数据）: {e}")

        # 时间引用：LLM 结果优先，空则正则兜底
        time_refs = unified.get("time_refs") or extract_time_refs(full_text)

        # 摘要兜底：LLM 给了就用，异常空时取前几句
        summary = unified.get("summary") or ""
        if not summary:
            from backend.rag.preprocessing.metadata import _extract_first_sentences
            summary = _extract_first_sentences(full_text, 3) or ""

        sections = []
        try:
            sections = extract_sections(full_text, max_sections=15)
        except Exception as e:
            logger.debug(f"[Indexer] 章节提取失败，跳过: {e}", exc_info=True)

        # LLM 候选再次按原文精确匹配，保护历史调用方与测试替身。
        from backend.rag.preprocessing.keyword import filter_exact_llm_keywords
        exact_keywords = filter_exact_llm_keywords(
            full_text, unified.get("keywords") or [],
        )
        kws_llm_objs = [{"word": w, "source": "llm"} for w in exact_keywords]
        kws_rule_objs = [{"word": w, "source": "rule"} for w in rule_kws_preview]
        from backend.rag.preprocessing.keyword import dedupe_keywords_normalized
        kws_all_words = dedupe_keywords_normalized(
            [k["word"] for k in kws_rule_objs + kws_llm_objs])

        llm_tokens = dict(unified.get("llm_tokens") or {})
        if question_gen_tokens:
            for key in (
                "prompt_tokens", "completion_tokens", "reasoning_tokens",
                "total_tokens",
            ):
                llm_tokens[key] = int(llm_tokens.get(key, 0) or 0) + int(
                    question_gen_tokens.get(key, 0) or 0)
            llm_tokens["cost_usd"] = round(
                float(llm_tokens.get("cost_usd", 0) or 0)
                + float(question_gen_tokens.get("cost_usd", 0) or 0), 6)

        _stage_ms["finalize_ms"] = round((_time.monotonic() - _t_final0) * 1000)
        _stage_ms["total_ms"] = round((_time.monotonic() - _t_final0) * 1000)
        return {
            "stage_ms": dict(_stage_ms),
            "doc_type": unified["doc_type"],
            "confidence": unified["confidence"],
            "business_domain": unified["business_domain"],
            "domain_detail": unified.get("domain_detail") or {"source": "rule"},
            "time_refs": time_refs,
            "complexity": complexity,
            "doc_keywords": kws_all_words,
            "keywords_rule": kws_rule_objs,
            "keywords_llm": kws_llm_objs,
            "llm_tokens": llm_tokens,
            "llm_used": bool(unified.get("provider_call")),
            "llm_strategy": (
                "metadata_enrichment" if unified.get("provider_call")
                else "question_gen" if question_gen_tokens
                else "rules_extract"
            ),
            "llm_decision": {
                "source": unified.get("metadata_source", "rules"),
                "classification_source": "rule",
                "fallback": unified.get("metadata_source") == "rules_fallback",
                "route_level": route_level,
                # Schema v1 新增 risk 字段：LLM 未输出时缺省 none（级联路径无）
                **({"risk": unified["risk"]} if unified.get("risk") else {}),
            },
            # 同规则路径：存 list，过滤语义见上
            "person_names": list(person_names) if isinstance(person_names, (list, tuple))
                           else ([] if not person_names else [str(person_names)]),
            "entities": unified.get("entities") or {},
            "summary": summary,
            "sections": list(sections),
            "quality_score": quality.get("score", 0),
            "quality_issues": ", ".join(quality.get("issues", [])),
            "embedding_model": self._embedding_model_name(self._embedding),
            "minhash_sig": json.dumps(minhash_sig),
            "near_dup_id": near_dup_id,
            "metadata_fingerprint": _metadata_fp,
            "doc_version": 1,
            "kb_version": "v1",
            "department": base_meta.get("department") or self._department,
            "questions_by_chunk": questions_by_chunk,
            "risk": unified.get("risk") or {"level": "none", "signals": []},
        }

