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
    ) -> tuple[list[list[str]], dict]:
        """生成模拟问题，并把 question_gen 独立记录为一个阶段。"""
        if not chunks_text:
            return [], {}
        from backend.rag.preprocessing import question_gen as _qg

        token = self._begin_lineage_stage(
            recorder, "question_gen", "question_gen", metadata={"chunk_count": len(chunks_text)}
        )
        try:
            def _generate_in_worker():
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
            return questions, usage
        except Exception as exc:
            self._finish_lineage_stage(
                recorder, token, status="failed",
                input_count=len(chunks_text), error_message=str(exc),
            )
            raise

    async def build(self, full_text: str, base_meta: dict,
                    parent_span_id: str = "",
                    chunks_text: list[str] | None = None,
                    processing_recorder=None) -> dict:
        """异步构建文档级元数据 — LLM Decision Router 评分决策。

        P2-2: LLM 计算异步批处理 —— 摘要、关键词、实体抽取并发执行
        """
        try:
            from backend.rag.preprocessing.metadata import (
                classify_with_confidence, analyze_complexity,
                extract_time_refs, detect_business_domain,
            )
            from backend.config.rag import (
                ENABLE_LLM_METADATA_EXTRACT,
                METADATA_SCHEMA_FINGERPRINT as _metadata_fp,
            )
            from backend.rag.preprocessing.entity import extract_entities, extract_person_names
        except ImportError:
            return {}

        try:
            import time as _time
            _t_build0 = _time.monotonic()
            _stage_ms: dict = {}
            # 统一（cascade）路径不经过规则 gather/near_dup 锚点，先置 None
            # 防御未定义（enrich/finalize 仅在对应锚点被执行时记录）
            _t_enrich0 = None
            _t_final0 = None
            fname = base_meta.get("source_file", "")
            fpath = base_meta.get("file_path", "")
            cls_detail: dict | None = None
            domain_detail: dict | None = None

            # P0-3（2026-10-19 治理）：级联自动分类（R0/R1/R2 DecisionEnvelope
            # + 影子对比）已退役——分类恒由规则分类器供给（classify_with_confidence），
            # doc_type/business_domain 不再有任何 LLM 前置；LLM 仅承担可选增强
            # （摘要/实体，ENABLE_LLM_METADATA_EXTRACT），见下方统一抽取路径。
            # 级联关闭时保留旧的统一 LLM 开关；失败后进入兼容规则路径。
            # 该路径已移除低置信复验、关键词 LLM，避免重复的 metadata 决策调用。
            unified: dict | None = None
            if ENABLE_LLM_METADATA_EXTRACT:
                decision_lineage = self._begin_lineage_stage(
                    processing_recorder,
                    "metadata_extract",
                    "metadata_extract",
                    metadata={"route": "legacy"},
                )
                try:
                    from backend.rag.preprocessing.metadata_llm import extract_metadata_llm_async
                    with processing_recorder.bind_stage(decision_lineage[0]) if processing_recorder and decision_lineage else nullcontext():
                        unified = await extract_metadata_llm_async(
                            full_text, fname, parent_span_id=parent_span_id)
                    self._finish_lineage_stage(
                        processing_recorder, decision_lineage,
                        status="success" if unified else "fallback",
                        model=self._role_model_identity(
                            "metadata_extract",
                            str((unified or {}).get("actual_model") or ""),
                        ),
                        input_count=1, output_count=1 if unified else 0,
                        usage=dict((unified or {}).get("llm_tokens") or {}) or None,
                        fallback_reason=None if unified else "llm_unavailable",
                        prompt_key="rag.preprocessing.metadata_extract",
                        prompt_version=str((unified or {}).get("prompt_version", "") or "") or None,
                        prompt_hash=str((unified or {}).get("prompt_hash") or "") or None,
                        schema_fingerprint=_metadata_fp,
                        metadata={
                            "llm_usage_status": str(
                                (unified or {}).get("llm_usage_status")
                                or (
                                    "reported"
                                    if (unified or {}).get("llm_tokens")
                                    else "unavailable"
                                )
                            ),
                            "prompt_source": str((unified or {}).get("prompt_source") or ""),
                            # STOP B4：非 DB 来源显式打标，不允许静默 fallback
                            "prompt_fallback": str(
                                (unified or {}).get("prompt_source") or ""
                            )
                            not in ("db", ""),
                        },
                    )
                except Exception as e:
                    self._finish_lineage_stage(
                        processing_recorder, decision_lineage,
                        status="failed", input_count=1, error_message=str(e),
                    )
                    logger.warning(f"[MetaLLM] 统一抽取异常（降级规则路径）: {e}")
                    unified = None
                if unified:
                    return await self.finalize_unified(
                        full_text, base_meta, unified,
                        parent_span_id=parent_span_id, chunks_text=chunks_text,
                        processing_recorder=processing_recorder)
                from backend.observability.metrics import metadata_route_total
                metadata_route_total.labels(level="legacy_rule", outcome="fallback").inc()

            # 质量门禁（P1）— span 收口到 stage_span（异常自动关闭）
            from backend.rag.preprocessing.metadata import assess_quality
            with stage_span(parent_span_id, 'quality', "Quality check",
                            SpanKind.INDEX_QUALITY_CHECK.value) as _q_span:
                quality = assess_quality(full_text)
                if _q_span is not None:
                    _q_span.metrics = {"score": quality.get("score", 0),
                                       "status": quality.get("status", "?"),
                                       "issues": quality.get("issues", [])}
                    _q_span.output = quality.get("dimensions", {})

            if not quality["passed"]:
                logger.warning(f"[Quality] 文档未通过质量门禁: {quality['issues']}")

            if parent_span_id:
                classify_span = trace_collector.start_span(
                    'classify', parent_id=parent_span_id, name="Classify",
                    type="llm", kind=SpanKind.INDEX_CLASSIFY.value,
                )
            try:
                doc_type, confidence, cls_detail = classify_with_confidence(full_text, filename=fname, file_path=fpath, return_detail=True)
            except Exception:
                # 异常时也要关闭 span，避免 classify 泄漏（P0-2）
                if parent_span_id:
                    trace_collector.end_span(classify_span,
                        metrics={"error": "classify_failed"}, status="error")
                raise
            if parent_span_id:
                trace_collector.end_span(classify_span, metrics={"doc_type": doc_type, "confidence": round(confidence, 3)},
                    output=locals().get("cls_detail", {}))


            # P2-1: MinHash 语义去重 — 检查同类型文档的近似内容（阈值热加载）
            from backend.config.indexing_rules import get_rules as _get_indexing_rules
            _idx_rules = _get_indexing_rules()
            from backend.rag.preprocessing.metadata import compute_minhash, minhash_similarity
            if parent_span_id:
                dedup_minhash_span = trace_collector.start_span(
                    'dedup_minhash', parent_id=parent_span_id, name="MinHash dedup",
                    type="llm", kind=SpanKind.INDEX_DEDUP_MINHASH.value,
                )
            minhash_sig = compute_minhash(full_text)
            if parent_span_id:
                trace_collector.end_span(dedup_minhash_span, metrics={"near_dup_id": "(see below)"})

            # 跨 doc_type 比对：分类漂移会使同一文档重索引时被判成不同类型，
            # 按类型过滤会漏检近重复（2026-09-17 副本误翻 active 事故根因之一）
            existing_rows = list(self._registry.list_all().values())
            near_dup_id = ""
            for existing in existing_rows:
                if existing.get("doc_id") == base_meta.get("doc_id", ""):
                    continue
                existing_sig = existing.get("minhash_sig", "")
                if existing_sig:
                    try:
                        existing_sig = json.loads(existing_sig) if isinstance(existing_sig, str) else existing_sig
                        sim = minhash_similarity(minhash_sig, existing_sig)
                        if sim > _idx_rules.near_dup_similarity_threshold:
                            near_dup_id = existing.get("doc_id", "")
                            logger.warning(f"[MinHash] 检测到近似文档: sim={sim:.2f}, existing={near_dup_id}")
                            break
                    except Exception as e:
                        # 单个已有文档签名损坏/格式异常 → 跳过该文档继续比对（软降级），留痕
                        logger.debug(f"[MinHash] 单文档签名比对失败，跳过: {e}", exc_info=True)

            if parent_span_id:
                rule_extract_span = trace_collector.start_span(
                    'rule_extract', parent_id=parent_span_id, name="Rule extract",
                    type="llm", kind=SpanKind.INDEX_KEYWORD_RULE.value,
                )
            time_refs = extract_time_refs(full_text)
            if parent_span_id:
                trace_collector.end_span(rule_extract_span, metrics={"time_refs_count": len(time_refs or []), "domain": "(computed below)"})

            if parent_span_id:
                domain_span = trace_collector.start_span(
                    'domain_classify', parent_id=parent_span_id, name="Domain classify",
                    type="llm", kind=SpanKind.INDEX_DOMAIN_CLASSIFY.value,
                )
            domain_result = detect_business_domain(full_text, return_detail=True)
            # P1: domain_result 返回 3 元组 (primary, alternatives, detail) 当 return_detail=True
            domain, _alternatives, domain_detail = domain_result
            domain_detail = domain_detail or {}
            if parent_span_id:
                trace_collector.end_span(domain_span, metrics={"domain": domain},
                    output=domain_detail or {})
            # 低置信样本不再在规则链内暗中追加 LLM 复验。
            # 需要升级时必须回到上方的统一 DecisionEnvelope 路径，保证
            # 每个文档决策最多一次 metadata LLM 调用且可被计量。

            # 规则关键词 + 复杂度（在最终 doc_type 确定之后）
            # extract_doc_keywords_typed（含 LLM 调用）与 extract_entities
            # 已移入下方 gather 并发执行——原先在主线程串行阻塞，P2-2 的
            # "并行"名不副实（关键词 LLM 调用先于 gather 发生）。
            from backend.rag.preprocessing.keyword import extract_rule_keywords
            rule_kws_preview = extract_rule_keywords(full_text, doc_type=doc_type)
            complexity = analyze_complexity(full_text, len(rule_kws_preview), confidence)
            person_names = extract_person_names(full_text)
        except Exception as e:
            # 规则六步失败 → 确定性兜底（合成最小 unified，走同一组包收口；
            # 元数据失败不得导致文档无法入库）
            logger.warning(f"[Metadata] 6步预处理失败，进入确定性兜底: {e}")
            fallback_unified = {
                "doc_type": "general",
                "confidence": 0.0,
                "business_domain": "general",
                "summary": "",
                "keywords": [],
                "entities": {},
                "risk": {"level": "none", "signals": []},
                "llm_tokens": {},
                "llm_usage_status": "unavailable",
                "prompt_source": "",
                "actual_model": "",
                "prompt_version": "",
                "prompt_hash": "",
            }
            return await self.finalize_unified(
                full_text,
                base_meta,
                fallback_unified,
                parent_span_id=parent_span_id,
                chunks_text=chunks_text,
                processing_recorder=processing_recorder,
            )

        # ⑧ 文档摘要 + 关键词 + 实体 — 三路并发（关键词 LLM 调用放线程池，
        # 与摘要 LLM 调用/实体抽取真正并行；总耗时 = max 而非 sum）
        summary = ""
        need_llm_summary = len(full_text) >= 1000  # <1KB 全文当摘要，不调 LLM

        llm_generate_span = None
        if parent_span_id:
            llm_generate_span = trace_collector.start_span(
                "llm_generate", parent_id=parent_span_id, name="LLM generate (keywords+summary+entities)",
                type="llm", kind=SpanKind.INDEX_LLM_GENERATE.value,
            )

        # LLM 未调用时为空列表（向后兼容；非 LLM 路径不生成问题）
        questions_by_chunk: list[list[str]] = []

        # P2-2: 并发执行四个重型任务（S0 修复：恢复 F1 重构漏迁的第四任务
        # 「模拟问题生成」——旧合并路径 enrich_metadata_llm 自失去调用方后，
        # questions_by_chunk 恒空，Document Expansion 前缀静默失效）
        from backend.rag.preprocessing.metadata import (
            _extract_first_sentences, build_llm_summary,
        )
        from backend.rag.preprocessing.keyword import KeywordResult as _KwResult
        sample = _sample_for_summary(full_text)

        async def task_summary():
            """LLM 摘要生成（<2KB 采样走抽取式，不调 LLM）"""
            from backend.config.rag import ENABLE_LLM_METADATA_EXTRACT
            if not ENABLE_LLM_METADATA_EXTRACT or len(sample) < 2000:
                return _extract_first_sentences(sample, 2), []
            summary_lineage = self._begin_lineage_stage(
                processing_recorder,
                "metadata_summary",
                "metadata_extract",
                metadata={"sample_chars": len(sample)},
            )
            try:
                with processing_recorder.bind_stage(summary_lineage[0]) if processing_recorder and summary_lineage else nullcontext():
                    result = await build_llm_summary(sample)
                self._finish_lineage_stage(
                    processing_recorder, summary_lineage, status="success",
                    model=self._role_model_identity("metadata_extract"),
                    input_count=1, output_count=1 if result and result[0] else 0,
                    prompt_key="rag.preprocessing.summary",
                    prompt_version="default",
                    schema_fingerprint=_metadata_fp,
                )
                return result
            except Exception as exc:
                self._finish_lineage_stage(
                    processing_recorder, summary_lineage, status="failed",
                    input_count=1, error_message=str(exc),
                )
                raise

        async def task_keywords():
            """P0-2（2026-10-19 去词表化）：doc 级普通关键词停发。

            词表/领域词/Jieba 排名提取已删（keyword.py），检索不再消费
            doc_keywords（Stage1 门控已移除）；空产出保持 KeywordResult
            契约与下游字段兼容（doc_keywords=[] / keywords_rule=[]）。
            复杂度特征改由 extract_rule_keywords 的标识符计数承担。
            不新增任何 LLM 关键词调用。
            """
            from backend.rag.preprocessing.keyword import KeywordResult as _KR
            return _KR(rule_keywords=[], llm_keywords=[])

        async def task_questions():
            """模拟问题生成（Document Expansion，S0 恢复）。

            走 question_gen（proxy 自动计量），tokens 随返回值回传
            （2026-09-16 起不再走模块级全局，避免并发上传互相覆盖），
            在下方与关键词路径 tokens 汇总。
            """
            if not chunks_text:
                return [], {}
            return await self._generate_questions_with_lineage(
                chunks_text, doc_type, processing_recorder,
            )

        # 并行执行：总耗时 = max(各任务耗时) 而非 sum；
        # 解包顺序与 gather 参数顺序一一对应
        summary_res, kw_res, entities_res, questions_res = await asyncio.gather(
            task_summary(),
            task_keywords(),
            asyncio.to_thread(extract_entities, full_text),
            task_questions(),
            return_exceptions=True,
        )

        if isinstance(summary_res, Exception):
            logger.warning(f"[Metadata] 并发任务失败 (task=summary): {summary_res}")
            summary_res = ("", [])
        if isinstance(kw_res, Exception):
            logger.warning(f"[Metadata] 并发任务失败 (task=keywords): {kw_res}")
            kw_res = _KwResult()
        if isinstance(entities_res, Exception):
            logger.warning(f"[Metadata] 并发任务失败 (task=entities): {entities_res}")
            entities_res = {}
        if isinstance(questions_res, Exception):
            logger.warning(f"[Metadata] 并发任务失败 (task=questions): {questions_res}")
            questions_res = ([], {})

        summary, persons = summary_res
        if summary and not person_names:
            person_names = persons
        kw_result = kw_res
        entities_nested = entities_res
        _stage_ms["decision_ms"] = round((_time.monotonic() - _t_build0) * 1000)
        _t_enrich0 = _time.monotonic()
        questions_by_chunk, question_gen_tokens = questions_res

        # 合并关键词（兼容旧字段，新字段已是对象数组）
        kws_rule_objs = kw_result.rule_keywords  # [{"word": ..., "source": "rule"}, ...]
        kws_llm_objs = kw_result.llm_keywords    # [{"word": ..., "source": "llm"}, ...]
        from backend.rag.preprocessing.keyword import dedupe_keywords_normalized
        kws_all_words = dedupe_keywords_normalized(
            [k["word"] for k in kws_rule_objs + kws_llm_objs])

        # LLM 决策信息
        llm_decision = kw_result.llm_decision if hasattr(kw_result, 'llm_decision') else {}
        need_llm_keywords = bool(kws_llm_objs)

        # 1.3b: tokens 汇总口径补全——keywords + questions 两路合并
        #（summary 抽取式路径无 tokens；build_llm_summary 的 LLM 用量
        #  已由 proxy 自动落 Store，此处只合并可回传的内存 tokens）
        merged_tokens = dict(kw_result.llm_tokens or {})
        if question_gen_tokens:
            for key in ("prompt_tokens", "completion_tokens"):
                merged_tokens[key] = int(merged_tokens.get(key, 0)) + int(
                    question_gen_tokens.get(key, 0) or 0)
            merged_tokens["cost_usd"] = round(
                float(merged_tokens.get("cost_usd", 0) or 0)
                + float(question_gen_tokens.get("cost_usd", 0) or 0), 6)
            kw_result.llm_tokens = merged_tokens

        if llm_generate_span:
            trace_collector.end_span(llm_generate_span, status="success",
                metrics={
                    "strategy": "parallel", "doc_size": len(full_text),
                    "need_llm_summary": need_llm_summary,
                    "need_llm_keywords": need_llm_keywords,
                    "parallel_execution": True,
                    "simulated_questions_chunks": len(questions_by_chunk),
                    "estimated_speedup": "4x (summary+keywords+entities+questions concurrent)",
                })

        # 兜底：<1KB 全文当摘要 / 没生成出来的剥 markdown 取前几句
        if not summary and len(full_text) <= 1000:
            summary = full_text.strip()
        elif not summary:
            from backend.rag.preprocessing.metadata import _extract_first_sentences
            summary = _extract_first_sentences(full_text, 3) or ""

        # ⑨ 章节提取（纯正则，零成本，所有文档都做）— span 收口到 stage_span
        sections = []
        try:
            from backend.rag.preprocessing.metadata import extract_sections
            with stage_span(parent_span_id, 'section', "Section extract",
                            SpanKind.INDEX_SECTION.value) as _sec_span:
                sections = extract_sections(full_text, max_sections=15)
                if _sec_span is not None:
                    _sec_span.metrics = {"sections_count": len(sections or [])}

        except Exception as e:
            # 章节提取失败 → 跳过 sections 元数据（软降级），留痕
            logger.debug(f"[Indexer] 章节提取失败，跳过: {e}", exc_info=True)

        if _t_final0 is not None:
            _stage_ms["finalize_ms"] = round((_time.monotonic() - _t_final0) * 1000)
        _stage_ms["total_ms"] = round((_time.monotonic() - _t_build0) * 1000)
        return {
            "stage_ms": dict(_stage_ms),
            "doc_type": doc_type,
            "confidence": confidence,
            "business_domain": domain,
            "domain_detail": domain_detail,
            "time_refs": time_refs,
            "complexity": complexity,
            "doc_keywords": kws_all_words,
            "keywords_rule": kws_rule_objs,
            "keywords_llm": kws_llm_objs,
            "llm_tokens": kw_result.llm_tokens,
            "llm_used": bool(kws_llm_objs),
            "llm_strategy": kw_result.llm_strategy,
            "llm_decision": llm_decision,
            # 存 list（落库经 _sanitize_metadata 成 JSON 数组串）：标量实体过滤靠
            # where→SQL 的数组包含臂命中。此前是 ", ".join 逗号串，多人文档
            # person_names 标量过滤必失配（2026-09-18 实证缺陷）。
            "person_names": list(person_names) if isinstance(person_names, (list, tuple))
                           else ([] if not person_names else [str(person_names)]),
            "entities": entities_nested,   # P1: 结构化实体 {person, org, regulation, ...}
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
        }

    # ---- 统一抽取路径的收口（纯规则部分照旧计算）----

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
        """统一 LLM 抽取成功后的收口：补齐纯规则产物并返回完整 metadata dict。

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

        # 质量门禁（与规则路径同源）
        quality = assess_quality(full_text)

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
        rule_kws_preview = extract_rule_keywords(full_text, doc_type=unified["doc_type"])
        complexity = analyze_complexity(
            full_text, len(rule_kws_preview), unified["confidence"])

        # 人名：优先 LLM 实体，空则正则兜底（与规则路径行为一致）
        person_names = list((unified.get("entities") or {}).get("person") or [])
        if not person_names:
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
                )
            elif processing_recorder is not None:
                question_lineage = self._begin_lineage_stage(
                    processing_recorder,
                    "question_gen",
                    "question_gen",
                    metadata={"chunk_count": len(chunks_text or [])},
                )
                self._finish_lineage_stage(
                    processing_recorder,
                    question_lineage,
                    status="skipped",
                    skip_reason=("route_no_llm" if not allow_llm_enrichment
                                 else "disabled_or_empty"),
                    prompt_key="rag.preprocessing.question_gen",
                    prompt_version=QUESTION_GEN_PROMPT_VERSION,
                    schema_fingerprint=_metadata_fp,
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

        # 关键词对象化（对齐 KeywordResult 输出口径：source=llm）
        kws_llm_objs = [{"word": w, "source": "llm"} for w in unified.get("keywords", [])]
        kws_rule_objs = [{"word": w, "source": "rule"} for w in rule_kws_preview]
        from backend.rag.preprocessing.keyword import dedupe_keywords_normalized
        kws_all_words = dedupe_keywords_normalized(
            [k["word"] for k in kws_rule_objs + kws_llm_objs])

        llm_tokens = dict(unified.get("llm_tokens") or {})
        if question_gen_tokens:
            for key in ("prompt_tokens", "completion_tokens"):
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
            "domain_detail": {"source": "metadata_llm"},
            "time_refs": time_refs,
            "complexity": complexity,
            "doc_keywords": kws_all_words,
            "keywords_rule": kws_rule_objs,
            "keywords_llm": kws_llm_objs,
            "llm_tokens": llm_tokens,
            "llm_used": not bool(route_level),
            "llm_strategy": f"cascade_{route_level}" if route_level else "unified_extract",
            "llm_decision": {
                "source": "cascade_router" if route_level else "metadata_llm",
                "fallback": False,
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

