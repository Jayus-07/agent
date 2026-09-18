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

from backend.observability.tracer import trace_collector, SpanKind
from backend.rag.indexing.stages.contracts import stage_span
from backend.shared.logger import logger


# 摘要采样：按文档长度自适应，保证头尾关键信息不丢
def _sample_for_summary(text: str) -> str:
    n = len(text)
    if n <= 2000:
        return text           # 短文档全文
    if n <= 8000:
        cut = int(n * 0.6)
        return text[:cut] + "\n...(中略)...\n" + text[-int(n * 0.4):]  # 头60%+尾40%
    MAX_SAFE = 50000
    if n > MAX_SAFE:
        return text[:20000] + "\n...(中间大量细则略)...\n" + text[-20000:]  # 极端超长安全绳
    return text               # 8KB~50KB 全文（DeepSeek 1M context 完全够）


class MetadataStage:
    """元数据阶段：持有 registry / embedding / department 引用（只读）。"""

    def __init__(self, registry, embedding, department: str = ""):
        self._registry = registry
        self._embedding = embedding
        self._department = department

    async def build(self, full_text: str, base_meta: dict,
                    parent_span_id: str = "",
                    chunks_text: list[str] | None = None) -> dict:
        """异步构建文档级元数据 — LLM Decision Router 评分决策。

        P2-2: LLM 计算异步批处理 —— 摘要、关键词、实体抽取并发执行
        """
        try:
            from backend.rag.preprocessing.metadata import (
                classify_with_confidence, analyze_complexity,
                extract_time_refs, detect_business_domain,
            )
            from backend.config.rag import METADATA_SCHEMA_FINGERPRINT as _metadata_fp
            from backend.rag.preprocessing.keyword import extract_doc_keywords_typed
            from backend.rag.preprocessing.entity import extract_entities, extract_person_names
        except ImportError:
            return {}

        try:
            fname = base_meta.get("source_file", "")
            fpath = base_meta.get("file_path", "")
            cls_detail: dict | None = None
            domain_detail: dict | None = None

            # ── 阶段2：统一 LLM 元数据抽取（主流化）──
            # ENABLE_LLM_METADATA_EXTRACT=true 时优先单次 LLM JSON 抽取
            # （doc_type/domain/summary/keywords/entities/time_refs 一次抽齐），
            # 任何失败自动降级到下方原规则路径。MinHash/质量门禁/复杂度等
            # 纯规则产物两条路径都照常计算。
            # METADATA_CASCADE_ENABLED=true（规划阶段 2.2）时先走级联路由：
            # L0 文件名/路径 → L1 嵌入检索 → L2 词表复核，命中零 LLM 成本；
            # 全部未命中才落 L3（即原单次 LLM 抽取）。
            from backend.config.rag import ENABLE_LLM_METADATA_EXTRACT
            unified: dict | None = None
            if ENABLE_LLM_METADATA_EXTRACT:
                try:
                    from backend.config.rag import METADATA_CASCADE_ENABLED
                    if METADATA_CASCADE_ENABLED:
                        from backend.rag.preprocessing.metadata_router import cascade_route
                        decision = await cascade_route(
                            full_text, fname, fpath, embedding=self._embedding,
                            parent_span_id=parent_span_id)
                        if decision.level in ("L0", "L1", "L2"):
                            return await self.finalize_cascade(
                                full_text, base_meta, decision,
                                parent_span_id=parent_span_id, chunks_text=chunks_text)
                        unified = decision.llm_result
                    else:
                        from backend.rag.preprocessing.metadata_llm import extract_metadata_llm_async
                        unified = await extract_metadata_llm_async(
                            full_text, fname, parent_span_id=parent_span_id)
                except Exception as e:
                    logger.warning(f"[MetaLLM] 统一抽取异常（降级规则路径）: {e}")
                    unified = None
                if unified:
                    return await self.finalize_unified(
                        full_text, base_meta, unified,
                        parent_span_id=parent_span_id, chunks_text=chunks_text)
                # L3 失败/未命中 → 规则链 fallback（规划阶段 4.2），打点观测触发率
                from backend.observability.metrics import metadata_route_total
                metadata_route_total.labels(level="rule_fallback", outcome="hit").inc()

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
            # 低置信 LLM 复验（前置：必须在关键词/复杂度之前确定最终 doc_type）
            # 1.3c 计量约束：本处仅允许本地模型（ChatOllama，无 usage/无成本，
            # 无需计量）；若未来切到 cloud 模型，必须改走 invoke_metadata_llm
            # （proxy 层自动落 llm_usage_store），禁止直连云 SDK——否则漏记。
            if confidence < _idx_rules.llm_reverify_conf_below and doc_type == "general":
                try:
                    from backend.config.llm import OLLAMA_ENABLED
                    from backend.config.rag import DOC_LLM_MODEL
                    from backend.prompts.service import prompt_service
                    doc_type_prompt = prompt_service.render_sync(
                        "rag.indexing.doc_type", full_text=full_text[:1500],
                    ).text
                    if DOC_LLM_MODEL and OLLAMA_ENABLED:
                        from langchain_ollama import ChatOllama
                        llm_l = ChatOllama(model=DOC_LLM_MODEL, temperature=0.0, num_ctx=2048, request_timeout=20)
                        llm_type = llm_l.invoke(doc_type_prompt).content.strip()
                    else:
                        from backend.rag.preprocessing.llm_enrichment import invoke_metadata_llm
                        llm_type = invoke_metadata_llm(doc_type_prompt).content.strip()
                    valid_types = {"policy", "sop", "ad_policy", "compliance", "legal",
                                   "contract_template", "security", "financial", "customer_data",
                                   "product_spec", "listing", "faq", "training", "general"}
                    if llm_type and llm_type.lower() in valid_types:
                        doc_type = llm_type.lower()
                        confidence = _idx_rules.llm_reverify_confidence
                        logger.info(f"[Classify] LLM 复验: {doc_type}")
                except Exception as e:
                    logger.warning(f"[Classify] LLM 复验失败: {e}")

            # 规则关键词 + 复杂度（在最终 doc_type 确定之后）
            # extract_doc_keywords_typed（含 LLM 调用）与 extract_entities
            # 已移入下方 gather 并发执行——原先在主线程串行阻塞，P2-2 的
            # "并行"名不副实（关键词 LLM 调用先于 gather 发生）。
            from backend.rag.preprocessing.keyword import extract_rule_keywords
            rule_kws_preview = extract_rule_keywords(full_text, doc_type=doc_type)
            complexity = analyze_complexity(full_text, len(rule_kws_preview), confidence)
            person_names = extract_person_names(full_text)
        except Exception as e:
            logger.warning(f"[Metadata] 6步预处理失败,fallback general: {e}")
            return {"doc_type": "general"}

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
            return await build_llm_summary(sample) if len(sample) >= 2000 else (_extract_first_sentences(sample, 2), [])

        async def task_keywords():
            """规则+LLM 关键词提取（LLM 调用放线程池，不阻塞事件循环）"""
            return await asyncio.to_thread(
                extract_doc_keywords_typed, full_text,
                doc_type=doc_type, confidence=confidence, complexity=complexity,
            )

        async def task_questions():
            """模拟问题生成（Document Expansion，S0 恢复）。

            走 question_gen（proxy 自动计量），tokens 随返回值回传
            （2026-09-16 起不再走模块级全局，避免并发上传互相覆盖），
            在下方与关键词路径 tokens 汇总。
            """
            if not chunks_text:
                return [], {}
            from backend.rag.preprocessing import question_gen as _qg
            return await asyncio.to_thread(
                _qg.generate_chunk_questions, chunks_text, doc_type,
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
        questions_by_chunk, question_gen_tokens = questions_res

        # 合并关键词（兼容旧字段，新字段已是对象数组）
        kws_rule_objs = kw_result.rule_keywords  # [{"word": ..., "source": "rule"}, ...]
        kws_llm_objs = kw_result.llm_keywords    # [{"word": ..., "source": "llm"}, ...]
        kws_all_words = [k["word"] for k in kws_rule_objs + kws_llm_objs]

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

        return {
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
            "embedding_model": os.path.basename(getattr(self._embedding, "model_name", "") or
                                                 str(getattr(self._embedding, "model", ""))) or "",
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
        route_level: str = "",
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
        from backend.config.rag import METADATA_SCHEMA_FINGERPRINT as _metadata_fp

        fname = base_meta.get("source_file", "")
        doc_id = base_meta.get("doc_id", "")

        # 质量门禁（与规则路径同源）
        quality = assess_quality(full_text)

        # MinHash 近重复（规则产物，决定 pending_review，两条路径都必须算）
        minhash_sig = compute_minhash(full_text)
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
            if ENABLE_SIMULATED_QUESTIONS and chunks_text:
                from backend.rag.preprocessing import question_gen as _qg
                questions_by_chunk, question_gen_tokens = await asyncio.to_thread(
                    _qg.generate_chunk_questions, chunks_text, unified["doc_type"])
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
        kws_all_words = [k["word"] for k in kws_rule_objs + kws_llm_objs]

        llm_tokens = dict(unified.get("llm_tokens") or {})
        if question_gen_tokens:
            for key in ("prompt_tokens", "completion_tokens"):
                llm_tokens[key] = int(llm_tokens.get(key, 0) or 0) + int(
                    question_gen_tokens.get(key, 0) or 0)
            llm_tokens["cost_usd"] = round(
                float(llm_tokens.get("cost_usd", 0) or 0)
                + float(question_gen_tokens.get("cost_usd", 0) or 0), 6)

        return {
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
            "embedding_model": os.path.basename(getattr(self._embedding, "model_name", "") or
                                                 str(getattr(self._embedding, "model", ""))) or "",
            "minhash_sig": json.dumps(minhash_sig),
            "near_dup_id": near_dup_id,
            "metadata_fingerprint": _metadata_fp,
            "doc_version": 1,
            "kb_version": "v1",
            "department": base_meta.get("department") or self._department,
            "questions_by_chunk": questions_by_chunk,
        }

    async def finalize_cascade(
        self, full_text: str, base_meta: dict, decision,
        parent_span_id: str = "", chunks_text: list[str] | None = None,
    ) -> dict:
        """级联路由 L0-L2 命中的收口（规划阶段 2.2）：分类用路由结果，
        结构字段（summary/keywords/entities/time_refs）走规则提取——零 LLM
        成本。委托 finalize_unified(route_level=...) 保证返回契约与统一
        抽取路径完全一致，下游零感知。

        keywords 传空：finalize_unified 内部的规则关键词预览会生成
        source=rule 的关键词；unified["keywords"] 的语义是 LLM 产物，
        级联路径没有 LLM 关键词。
        """
        from backend.rag.preprocessing.metadata import (
            _extract_first_sentences, extract_time_refs,
        )
        from backend.rag.preprocessing.entity import extract_entities

        entities: dict = {}
        try:
            entities = extract_entities(full_text) or {}
        except Exception as e:
            logger.warning(f"[MetaRouter] 级联实体提取失败（空实体兜底）: {e}")

        unified = {
            "doc_type": decision.doc_type,
            "confidence": decision.confidence,
            "business_domain": (decision.evidence or {}).get("domain") or "general",
            "summary": _extract_first_sentences(full_text, 3) or "",
            "keywords": [],
            "entities": entities,
            "time_refs": extract_time_refs(full_text) or [],
        }
        return await self.finalize_unified(
            full_text, base_meta, unified,
            parent_span_id=parent_span_id, chunks_text=chunks_text,
            route_level=decision.level)
