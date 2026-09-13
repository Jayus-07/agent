"""RAG Runner — 完整检索链路（无 LLM）。

链路: Doc检索 -> 关键词过滤 -> 人名匹配 -> BM25+RRF混合 -> Adaptive补全 -> CrossEncoder精排
不含 MultiQuery / HistoryAware / LLM 生成，needs_live=False 即可运行。

支持消融实验：kwargs["ablation_mode"] 可设为 vector_only / bm25_only /
hybrid / hybrid_rerank / hybrid_adaptive / full（默认）。
"""
from __future__ import annotations

import concurrent.futures
import json
import math
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from backend.evaluation.metrics import (
    answer_correctness_typed,
    context_noise_rate,
    faithfulness_claim_based,
    faithfulness_semantic,
    mrr,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    stage_rerank_metrics,
    stage_retrieval_metrics,
)
from backend.evaluation.models import EvalResult, TestCase
from backend.evaluation.registry import register_runner
from backend.evaluation.runners._common import (
    build_ablation_retriever,
    entities_all_present,
    extract_query_entities,
    gate_mode,
    get_full_retriever,
    init_rag_pipeline,
    match_by_snippet,
    normalize_snippet_text,
)
from backend.shared.jsonable import safe_jsonable as _safe_jsonable
from backend.shared.logger import logger

_ANSWERS_FILE: Path | None = None

# Stage1 doc 级探针检索的 top-k（诊断指标 S1 用，独立于主检索链路）
_STAGE1_PROBE_K = 5


def _doc_id_match(actual_id: str, expected_set: set[str], resolver=None, kb_id: str = "", department: str = "") -> bool:
    """规范化比较 — 通过 resolver 桥接 hash doc_id ↔ filename。"""
    if actual_id in expected_set:
        return True
    if resolver:
        for exp in expected_set:
            if resolver.matches(actual_id, exp, kb_id, department):
                return True
        return False
    actual_base = Path(actual_id.replace("\\", "/")).name
    for exp in expected_set:
        if actual_base == Path(exp.replace("\\", "/")).name:
            return True
    return False


def _normalize_fact_text(text: str) -> str:
    """归一化事实文本 — 统一空白、数字单位、标点，提高子串命中率。"""
    import re
    t = text.lower().strip()
    t = re.sub(r'\s+', '', t)
    t = t.replace('，', ',').replace('。', '.').replace('：', ':')
    t = t.replace('（', '(').replace('）', ')')
    # 中文季度只有一到四，多字符匹配（如"十一"）无意义且会漏归一化
    t = re.sub(r'第([一二三四])季度', lambda m: _cn_to_q(m.group(1)), t)
    t = t.replace('亿元', '亿').replace('万元', '万')
    return t


def _cn_to_q(cn: str) -> str:
    _map = {'一': '1', '二': '2', '三': '3', '四': '4'}
    return _map.get(cn, cn)


def _get_answers_path(run_id: str | None = None) -> Path:
    from backend.evaluation.storage import DATA_ROOT
    if run_id:
        return DATA_ROOT / run_id / "answers.jsonl"
    return DATA_ROOT / "answers.jsonl"


def write_answers_jsonl(
    entry: dict, path: Path | None = None,
) -> None:
    """追加一条 RAG 原始输出（四张答卷）到 answers.jsonl。"""
    target = path or _ANSWERS_FILE
    if target is None:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    entry["_timestamp"] = datetime.now(timezone.utc).isoformat()
    with target.open("a", encoding="utf-8") as f:
        f.write(json.dumps(_safe_jsonable(entry), ensure_ascii=False) + "\n")


def read_answers_jsonl(path: Path) -> list[dict]:
    """读取 answers.jsonl，返回所有条目。"""
    if not path.exists():
        return []
    entries = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def _append_result_checkpoint(path: Path, result: EvalResult) -> None:
    """追加单条 EvalResult 到 checkpoint 文件（断点续跑用）。

    只 checkpoint 已完成评估的结果（status != error），error 用例重跑时
    会重试。写失败不影响主流程（只损失该用例的续跑能力）。
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(result.model_dump(), ensure_ascii=False, default=str) + "\n")
    except Exception:
        logger.debug("[RAG eval] checkpoint 写入失败（忽略）", exc_info=True)


def _load_result_checkpoint(path: Path) -> dict[str, EvalResult]:
    """读取 checkpoint 中已完成用例的结果，按 case_id 索引（坏行跳过）。"""
    done: dict[str, EvalResult] = {}
    if not path.exists():
        return done
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = EvalResult.model_validate(json.loads(line))
                done[r.case_id] = r
            except Exception:
                continue
    return done


def _run_rag(cases: list[TestCase], **kwargs) -> list[EvalResult]:
    """RAG runner — 完整检索链路（无 LLM）。"""
    if not cases:
        return []

    pipeline = init_rag_pipeline()
    if pipeline is None:
        return [
            EvalResult(
                case_id=c.id, module="rag", status="error",
                expected=c.expected, actual={},
                error_msg="RAG pipeline not available",
            )
            for c in cases
        ]

    available_kbs = set()
    try:
        peek = pipeline.doc_db.get(where=None)
        for md in (peek.get("metadatas") or []):
            kid = md.get("kb_id")
            if kid:
                available_kbs.add(kid)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[RAG eval] 探测 doc_db KB 列表失败: {e}")

    ablation_mode = kwargs.get("ablation_mode", "full")

    global _ANSWERS_FILE
    _ANSWERS_FILE = _get_answers_path(kwargs.get("run_id"))
    _ANSWERS_FILE.parent.mkdir(parents=True, exist_ok=True)

    # ── 断点续跑（checkpoint）──
    # 原实现每轮清空 answers.jsonl 且结果只在内存累积，进程挂掉后
    # 100 条全量作废（一轮 30-90 分钟）。现在逐用例追加 checkpoint，
    # 同一 run 目录重跑时跳过已完成用例；--no-resume 强制全量重跑。
    # 已知限制：挂发生在 RAGAS 批量阶段之前时，续跑的用例不会补跑 RAGAS。
    checkpoint_file = _ANSWERS_FILE.parent / "results_checkpoint.jsonl"
    resume_enabled = kwargs.get("resume", True)
    done_results: dict[str, EvalResult] = {}
    if resume_enabled:
        done_results = _load_result_checkpoint(checkpoint_file)
        if done_results:
            logger.info(f"[RAG eval] 断点续跑：checkpoint 命中 {len(done_results)} 条已完成用例，将跳过")
    else:
        if checkpoint_file.exists():
            checkpoint_file.unlink()
        _ANSWERS_FILE.write_text("", encoding="utf-8")

    results: list[EvalResult] = []
    _deferred_ragas: list[tuple[int, TestCase, dict, dict]] = []
    from backend.evaluation.runners.doc_id_resolver import DocIdResolver
    resolver = DocIdResolver()
    def _eval_case(case: TestCase) -> tuple[EvalResult, dict | None]:
        """评估单条用例（原 _run_rag 循环体）。

        返回 (result, deferred_ragas_entry|None)。以单元素 for 包裹原循环体，
        逻辑与原实现逐行等价；workers>1 时在线程池中并发执行。
        """
        result_holder: list[EvalResult] = []
        deferred_holder: list = []
        for case in (case,):
            t0 = time.time()
            try:
                kb_id = case.metadata.get("kb_id", "default")
                department = case.metadata.get("department") or ""

                if ablation_mode != "full":
                    retriever = build_ablation_retriever(
                        pipeline, ablation_mode, kb_id, department,
                    )
                else:
                    # --multiquery：评测链套上生产链的 MultiQuery 层（口径对齐）
                    retriever = get_full_retriever(
                        pipeline, use_multiquery=bool(kwargs.get("multiquery")),
                    )
                if (
                    kb_id
                    and kb_id not in ("*", "default")
                    and available_kbs
                    and kb_id not in available_kbs
                ):
                    logger.warning(
                        f"[RAG eval] {case.id} 标注 KB='{kb_id}' 不在 doc_db 中 "
                        f"(available={sorted(available_kbs)}), fallback to default"
                    )
                    kb_id = "default"
                question = case.question

                if kb_id and kb_id != "*" and kb_id != "default":
                    from backend.rag.context import RagRequestState, set_context
                    mf = {"kb_id": kb_id}
                    if department:
                        mf["department"] = department
                    ctx = RagRequestState(
                        metadata_filter=mf,
                        intent_label="",
                        query=question,
                    )
                    set_context(ctx)
                else:
                    from backend.rag.context import RagRequestState, set_context
                    set_context(RagRequestState())

                # === Stage 1: Doc 级检索 ===
                doc_filter = {}
                if kb_id and kb_id != "*" and kb_id != "default":
                    doc_filter["kb_id"] = kb_id
                if department:
                    doc_filter["department"] = department
                doc_results = (
                    pipeline.doc_db.similarity_search(question, k=_STAGE1_PROBE_K, filter=doc_filter)
                    if doc_filter
                    else pipeline.doc_db.similarity_search(question, k=_STAGE1_PROBE_K)
                )
                stage1_docs = []
                stage1_doc_ids = []
                for d in doc_results:
                    doc_id = d.metadata.get("doc_id", "")
                    src = (d.metadata.get("source_file")
                           or d.metadata.get("file_path", "").replace("\\", "/").split("/")[-1])
                    stage1_docs.append({
                        "doc_id": doc_id,
                        "title": str(d.metadata.get("title", ""))[:60],
                        "category": d.metadata.get("category_name", d.metadata.get("category", "")),
                    })
                    if doc_id:
                        stage1_doc_ids.append(resolver.canonical(src or doc_id, kb_id, department))
                stage1_fallback_suspected = bool(
                    doc_filter and not stage1_doc_ids
                )

                # === 完整检索链路 ===
                from backend.observability.tracer import SpanKind, trace_collector
                trace = trace_collector.start(
                    question=question,
                    session_id=f"eval-{case.id}",
                    workflow_name="rag_eval",
                )
                root_span = trace_collector.start_span(
                    "rag_eval_root", parent_id=None,
                    name=f"RAG eval {case.id}",
                    type="workflow", kind=SpanKind.RETRIEVAL.value,
                    input={"question": question, "kb_id": kb_id},
                )
                try:
                    retrieved_docs = retriever.invoke(question)
                finally:
                    try:
                        trace_collector.end_span(root_span)
                    except Exception:
                        logger.debug("trace root_span end failed for %s", case.id, exc_info=True)

                # === 捕获 trace spans ===
                trace_spans = []
                for sp in (trace.spans or []):
                    trace_spans.append({
                        "span_id": sp.span_id,
                        "parent_id": sp.parent_id,
                        "name": sp.name,
                        "type": sp.type,
                        "kind": sp.kind,
                        "status": sp.status,
                        "duration_ms": sp.duration_ms,
                        "sequence": sp.sequence,
                        "metrics": dict(sp.metrics or {}),
                        "input": _safe_jsonable(sp.input),
                        "output": _safe_jsonable(sp.output),
                        "events": list(sp.events or []),
                        "errors": list(sp.errors or []),
                    })
                total_trace_ms = sum(s["duration_ms"] for s in trace_spans)

                # === 组装检索轨迹 ===
                actual_doc_strs = []
                seen = set()
                details = []
                for doc in retrieved_docs:
                    doc_id = doc.metadata.get("doc_id", "")
                    source = (doc.metadata.get("source_file")
                              or doc.metadata.get("file_path", "").replace("\\", "/").split("/")[-1])
                    canon = resolver.canonical(source or doc_id, kb_id, department)
                    identifier = canon if canon else (source if source else doc_id)
                    if identifier not in seen:
                        seen.add(identifier)
                        actual_doc_strs.append(identifier)

                    details.append({
                        "doc_id": doc_id,
                        "canonical_id": canon,
                        "title": str(doc.metadata.get("title", ""))[:80],
                        "chunk_id": doc.metadata.get("chunk_id", ""),
                        "department": doc.metadata.get("department", ""),
                        "rerank_score": doc.metadata.get("rerank_score"),
                        "source": source,
                        "snippet": doc.page_content[:200].replace("\n", " "),
                        "page_content": doc.page_content,
                    })

                doc_counter = Counter(d["doc_id"] for d in details if d["doc_id"])
                total = len(details)
                if total > 0:
                    clustered = [did for did, cnt in doc_counter.items() if cnt / total >= 0.3]
                    adaptive_info = f"集中({len(clustered)}个文档)" if len(clustered) <= 2 and clustered else f"分散({len(doc_counter)}个文档)"
                else:
                    adaptive_info = "无结果"

                pipeline_info = {
                    "ablation_mode": ablation_mode,
                    "stage1_docs": len(stage1_docs),
                    "stage1_top_docs": stage1_docs,
                    "stage1_fallback_suspected": stage1_fallback_suspected,
                    "stage2_chunks_recalled": total,
                    "after_rerank": total,
                    "adaptive": adaptive_info,
                    "id_resolution": {
                        "backend": resolver.backend,
                        "unresolved": list(set(resolver.unresolved)),
                    },
                }

                raw_expected = case.expected.get("relevant_docs", []) or case.expected.get("required_docs", []) or []
                expected_docs: set[str] = set()
                for entry in raw_expected:
                    if isinstance(entry, dict):
                        src_file = entry.get("source_file", "")
                        doc_id_val = entry.get("doc_id", "")
                        if src_file and doc_id_val:
                            resolver.seed_alias(doc_id_val, src_file)
                        token = src_file or doc_id_val
                    else:
                        token = str(entry)
                    if token:
                        expected_docs.add(token)
                expected_canonical = {resolver.canonical(x, kb_id, department) for x in expected_docs}
                expected_doc_strs = list(expected_canonical)

                # ── ID 归一化：resolver.matches() 桥接 hash doc_id ↔ filename ──
                # canonical() 无法解析的 hash（如 "a1e6a1c29a"）通过 matches() 与
                # 预期文档比对，命中则替换为 canonical 文件名，使 recall/mrr/ndcg 可正确匹配。
                expected_doc_set = set(expected_doc_strs)
                for i, ident in enumerate(actual_doc_strs):
                    if ident in expected_doc_set:
                        continue
                    for exp in expected_doc_strs:
                        if resolver.matches(ident, exp, kb_id, department):
                            actual_doc_strs[i] = exp
                            break
                for i, sid in enumerate(stage1_doc_ids):
                    if sid in expected_doc_set:
                        continue
                    for exp in expected_doc_strs:
                        if resolver.matches(sid, exp, kb_id, department):
                            stage1_doc_ids[i] = exp
                            break

                expected_chunks = set(case.expected.get("relevant_chunks", []) or [])
                expected_snippets = case.expected.get("relevant_snippets", []) or []
                match_type = case.expected.get("match_type", "chunk_id")
                min_expected = case.expected.get("min_relevant_chunks", 1)

                # === 分阶段诊断指标 ===
                stage_metrics: dict[str, float | dict] = {}

                if expected_doc_strs:
                    s1 = stage_retrieval_metrics(
                        stage1_doc_ids, expected_doc_strs,
                    )
                    stage_metrics["S1_doc_retrieval"] = s1

                rerank_scores = [
                    d.get("rerank_score") or 0.0 for d in details
                ]
                if expected_docs:
                    relevant_mask = [
                        any(
                            resolver.matches(d.get("doc_id", ""), exp, kb_id, department)
                            or resolver.matches(d.get("source", ""), exp, kb_id, department)
                            or (d.get("canonical_id", "") and d["canonical_id"] == resolver.canonical(exp, kb_id, department))
                            for exp in expected_docs
                        )
                        for d in details
                    ]
                elif gate_mode() in ("shadow", "semantic") and case.expected.get("ground_truth_context"):
                    from backend.evaluation.runners.rag_semantic import _extract_gt_texts, _get_scorer
                    gt_texts = _extract_gt_texts(case.expected)
                    if gt_texts and details:
                        scorer = _get_scorer()
                        retrieved_texts = [d.get("page_content", "") for d in details]
                        relevant_mask = []
                        for rt in retrieved_texts:
                            if not rt:
                                relevant_mask.append(False)
                                continue
                            queries = [rt] * len(gt_texts)
                            scores = scorer.score_pairs(queries, gt_texts)
                            relevant_mask.append(max(scores) >= 0.50)
                    else:
                        relevant_mask = [False] * len(details)
                else:
                    relevant_mask = [False] * len(details)
                if rerank_scores and any(relevant_mask):
                    stage_metrics["S4_rerank"] = stage_rerank_metrics(
                        rerank_scores, relevant_mask,
                    )

                # === 答案生成（仅 ENV_MODE=local 走本地 Ollama；cloud 模式内部直接跳过）===
                gate = gate_mode()

                from backend.evaluation.generation import get_token_usage as _get_token_usage
                _tok_before = _get_token_usage()

                _generated_answer = ""
                retrieved_texts_for_gen = [
                    d.get("page_content", "") for d in details if d.get("page_content")
                ]
                if retrieved_texts_for_gen:
                    try:
                        from backend.evaluation.generation import generate_answer_ollama
                        _generated_answer = generate_answer_ollama(question, retrieved_texts_for_gen)
                    except Exception as e:
                        logger.warning(f"[GenAnswer] {case.id} 答案生成失败: {e}")

                # === 组装 RAGAS 输入（四张答卷）===
                expected_answer = case.expected.get("expected_answer") or case.metadata.get("expected_answer", "")
                gt_context_texts = []
                for gt in case.expected.get("ground_truth_context", []):
                    if isinstance(gt, dict):
                        t = gt.get("text", "")
                    elif isinstance(gt, str):
                        t = gt
                    else:
                        t = str(gt)
                    if t.strip():
                        gt_context_texts.append(t.strip())

                ragas_input = {
                    "question": question,
                    "answer": _generated_answer,
                    "contexts": retrieved_texts_for_gen,
                    "ground_truth": expected_answer or None,
                    "reference_contexts": gt_context_texts or None,
                }

                # 写入 answers.jsonl（供 CI 缓存 + 双轨读取）
                write_answers_jsonl({
                    "case_id": case.id,
                    **ragas_input,
                    "metadata": {
                        "domain": case.metadata.get("domain", ""),
                        "difficulty": case.metadata.get("difficulty", ""),
                        "ragas_level": kwargs.get("ragas_level", "standard"),
                    },
                    "_ragas_results": None,
                })

                # === 生成质量评估（P0 修复：用 generated_answer 而非 retrieved_text）===
                generation_metrics: dict[str, float] = {}
                required_facts = case.expected.get("required_facts", [])
                required_docs = case.expected.get("required_docs", [])
                requires_citation = case.metadata.get("requires_citation", True)

                if case.metadata.get("generation_eval") or _generated_answer:
                    must_contain = case.metadata.get("must_contain", [])
                    must_not_contain = case.metadata.get("must_not_contain", [])
                    answer_type = case.metadata.get("answer_type", "factual")

                    # 只用真实生成回答计算指标。无生成答案（离线模式/生成失败）时
                    # 不再用检索原文拼接冒充——检索文本天然含 ground truth 措辞，
                    # 会把 S6/S7 系统性虚高；此时答案类指标直接跳过（聚合记缺失）
                    answer_for_eval = (_generated_answer or "").strip()

                    if expected_answer and answer_for_eval:
                        correctness = answer_correctness_typed(
                            actual_answer=answer_for_eval,
                            expected_answer=expected_answer,
                            answer_type=answer_type,
                            must_contain=must_contain,
                            must_not_contain=must_not_contain,
                        )
                        generation_metrics["S6_answer_correctness"] = correctness["correctness"]
                        generation_metrics["must_contain_hit"] = correctness["must_contain_hit"]
                        generation_metrics["must_not_contain_violation"] = correctness["must_not_contain_violation"]

                    if expected_answer and details and answer_for_eval:
                        context_list = [d.get("page_content", "") for d in details]
                        try:
                            from backend.evaluation.runners.rag_semantic import _get_scorer
                            scorer = _get_scorer()
                            faithfulness = faithfulness_semantic(
                                answer=answer_for_eval,
                                context=context_list,
                                scorer=scorer,
                            )
                        except Exception:
                            faithfulness = faithfulness_claim_based(
                                answer=answer_for_eval,
                                context=context_list,
                            )
                        # 空答案/无可验证 claim → skipped（None），不写入指标
                        if faithfulness.get("faithfulness") is not None:
                            generation_metrics["S7_faithfulness"] = faithfulness["faithfulness"]
                            generation_metrics["claim_count"] = float(faithfulness["claim_count"])
                            generation_metrics["supported_claim_count"] = float(faithfulness["supported_count"])

                # === LLM-as-Judge（--judge 时启用）===
                # 原实现 judge.py 从未被任何 runner 调用（死代码），现接线：
                # 对真实生成答案做 4 维质量评分，分数进 judge_* 指标
                if kwargs.get("judge") and _generated_answer and expected_answer:
                    try:
                        from backend.evaluation.judge import judge_answer
                        _rubric = {
                            "completeness": f"参考答案: {expected_answer}",
                            "faithfulness": "所有数字/事实必须能追溯到检索证据，不得编造",
                            "conciseness": "表述精炼，无冗余重复",
                            "citation_quality": "引用标注准确且与证据一致",
                        }
                        if required_facts:
                            _rubric["completeness"] += f"；必须覆盖的事实: {', '.join(required_facts)}"
                        _jr = judge_answer(question, _rubric, _generated_answer)
                        if _jr.total > 0:  # total=0.0 表示评估失败，不写入（避免污染均值）
                            generation_metrics["judge_total"] = round(_jr.total / 5, 4)
                            for _k, _v in _jr.scores.items():
                                generation_metrics[f"judge_{_k}"] = round(_v / 5, 4)
                    except Exception as judge_err:
                        logger.debug(f"[Judge] {case.id} 评分失败（跳过）: {judge_err}")

                    if generation_metrics:
                        stage_metrics["generation"] = generation_metrics

                # === Evaluator Provider 调用 ===
                should_reject = case.expected.get("should_reject", False)

                # 构建 Evaluator 上下文
                eval_ctx: dict = {
                    "gate_mode": gate,
                    "details": details,
                    "generated_answer": _generated_answer,
                    "semantic_thresholds": kwargs.get("semantic_thresholds"),
                    "actual_doc_strs": actual_doc_strs,
                    "expected_doc_strs": expected_doc_strs,
                    "expected_docs": expected_docs,
                    "should_reject": should_reject,
                    "dept_leak": False,  # 下方计算后回填
                    "confidence": "none",  # 下方计算后回填
                    "ragas_input": ragas_input,
                    "no_ragas": kwargs.get("no_ragas", False),
                    "ragas_level": kwargs.get("ragas_level", "standard"),
                    "ragas": kwargs.get("ragas", False),
                }

                # Semantic Evaluator
                semantic_metrics: dict[str, float] = {}
                from backend.evaluation.evaluators.semantic import SemanticEvaluator
                sem_eval = SemanticEvaluator(thresholds=kwargs.get("semantic_thresholds"))
                if sem_eval.should_run(case, eval_ctx):
                    semantic_metrics = sem_eval.evaluate(case, eval_ctx)
                eval_ctx["semantic_metrics"] = semantic_metrics

                # RAGAS Evaluator（延迟到循环外并行执行）
                ragas_metrics: dict[str, float] = {}
                if not kwargs.get("no_ragas", False):
                    deferred_holder.append((case, eval_ctx, ragas_metrics))

                # doc-level metrics
                r5 = recall_at_k(actual_doc_strs, expected_doc_strs, k=5)
                r10 = recall_at_k(actual_doc_strs, expected_doc_strs, k=10)
                mrr_val = mrr(actual_doc_strs, expected_doc_strs)
                ndcg_val = ndcg_at_k(actual_doc_strs, expected_doc_strs, k=10)

                # === confidence 判定（阈值从配置读取）===
                sem_thresh = kwargs.get("semantic_thresholds") or {}
                thresh_low = sem_thresh.get("confidence_low", 0.5)
                thresh_high = sem_thresh.get("confidence_high", 0.6)
                thresh_gap = sem_thresh.get("confidence_gap", 0.15)

                if not details:
                    confidence = "none"
                    reject_gate = "retrieval"
                    reject_reason = "no_evidence"
                elif not any(d.get("rerank_score") is not None for d in details):
                    # 检索路径未跑 reranker（如 ablation 的 vector_only/bm25_only）：
                    # 无 rerank 分数无法做置信度分级。若按 0.0 处理，should_reject
                    # 用例会白送 reject_accuracy=1.0、正常用例一律 fail，指标完全失真
                    confidence = "unknown"
                    reject_gate = None
                    reject_reason = None
                else:
                    top1_score = float(details[0]["rerank_score"])
                    top2_score = float(details[1]["rerank_score"]) if len(details) > 1 and details[1].get("rerank_score") is not None else 0.0
                    score_gap = top1_score - top2_score
                    if top1_score < thresh_low:
                        confidence = "none"
                        reject_gate = "retrieval"
                        reject_reason = "low_relevance"
                    elif top1_score < thresh_high:
                        confidence = "low"
                        reject_gate = None
                        reject_reason = None
                    elif score_gap < thresh_gap:
                        confidence = "medium"
                        reject_gate = None
                        reject_reason = None
                    else:
                        confidence = "high"
                        reject_gate = None
                        reject_reason = None

                # === V1.3 拒答校准 ===
                query_entities: list[str] = []
                entity_absent = False
                if should_reject and details:
                    query_entities = extract_query_entities(question)
                    if query_entities and not entities_all_present(query_entities, details):
                        entity_absent = True
                        confidence = "low"
                        reject_gate = "entity_check"
                        reject_reason = "entity_absent"

                # === Top-1 准确率 ===
                would_reject = confidence in ("none", "low")
                top1_accuracy = None
                reject_top1_accuracy = None
                if should_reject:
                    # 拒答语义单独记录（"是否拒答"与检索 top-1 命中不是同一量纲，
                    # 混入同一均值会稀释 top1_accuracy）；confidence unknown 时不参与统计
                    if confidence != "unknown":
                        reject_top1_accuracy = 1.0 if would_reject else 0.0
                elif confidence == "unknown":
                    pass
                elif confidence == "none":
                    top1_accuracy = 0.0
                elif semantic_metrics:
                    sem_recall = semantic_metrics.get("sem_context_recall")
                    if sem_recall is not None and not math.isnan(sem_recall):
                        top1_accuracy = 1.0 if sem_recall >= 0.50 else 0.0
                    elif expected_docs and actual_doc_strs:
                        top1_accuracy = 1.0 if _doc_id_match(actual_doc_strs[0], expected_docs, resolver, kb_id, department) else 0.0
                elif expected_docs and actual_doc_strs:
                    top1_accuracy = 1.0 if _doc_id_match(actual_doc_strs[0], expected_docs, resolver, kb_id, department) else 0.0
                elif confidence in ("medium", "high") and details:
                    top1_accuracy = 1.0

                # === reject_accuracy ===
                if should_reject:
                    # confidence unknown（无 rerank 分数）时跳过，不计入均值
                    reject_accuracy = None if confidence == "unknown" else (1.0 if would_reject else 0.0)
                else:
                    reject_accuracy = None

                # === 新增业务指标 ===
                # Required Fact Coverage: 归一化子串 + 语义二次判断
                required_fact_coverage = None
                if required_facts and _generated_answer:
                    answer_norm = _normalize_fact_text(_generated_answer)
                    substring_hits = [f for f in required_facts if _normalize_fact_text(f) in answer_norm]
                    missed_facts = [f for f in required_facts if f not in substring_hits]

                    semantic_hits = 0
                    if missed_facts and details:
                        try:
                            from backend.evaluation.runners.rag_semantic import _get_scorer
                            scorer = _get_scorer()
                            pairs = [(f, _generated_answer) for f in missed_facts]
                            scores = scorer.score_pairs(
                                [p[0] for p in pairs], [p[1] for p in pairs],
                            )
                            semantic_hits = sum(1 for s in scores if s >= 0.75)
                        except Exception:
                            pass

                    fact_hits_count = len(substring_hits) + semantic_hits
                    required_fact_coverage = fact_hits_count / len(required_facts)

                # False Answer Rate: 该拒答却未拒答的比例
                false_answer_rate = None
                if should_reject and confidence == "unknown":
                    false_answer_rate = None
                elif should_reject and confidence in ("medium", "high"):
                    false_answer_rate = 1.0
                elif should_reject:
                    false_answer_rate = 0.0

                # Citation 指标：引用来源的精确度和完整度
                citation_accuracy = None
                citation_completeness = None
                if requires_citation and _generated_answer and details:
                    import re
                    # 提取 [1][2] 引用标记
                    cited_indices = set()
                    for m in re.finditer(r'\[(?:E)?(\d+)\]', _generated_answer, re.IGNORECASE):
                        cited_indices.add(int(m.group(1)) - 1)  # 转为 0-indexed

                    # 期望来源文档（canonical 化）
                    gt_sources = set()
                    for gt in case.expected.get("ground_truth_context", []):
                        if isinstance(gt, dict) and gt.get("source_doc"):
                            gt_sources.add(resolver.canonical(gt["source_doc"], kb_id, department))

                    # 实际引用文档（通过 index 映射到 details，canonical 化）
                    cited_sources = set()
                    for idx in cited_indices:
                        if 0 <= idx < len(details):
                            canon = details[idx].get("canonical_id", "")
                            src = details[idx].get("source", "")
                            token = canon or src
                            if token:
                                cited_sources.add(resolver.canonical(token, kb_id, department))

                    if cited_sources:
                        citation_accuracy = len(cited_sources & gt_sources) / len(cited_sources)
                    if gt_sources:
                        citation_completeness = len(cited_sources & gt_sources) / len(gt_sources)

                # Multi-hop Success: 多文档/推理类问题的检索成功率
                multi_hop_success = None
                query_type = case.metadata.get("query_type", "")
                is_multi_hop_type = query_type in ("multi_doc", "reasoning")
                if len(required_docs) >= 2 or (is_multi_hop_type and required_docs):
                    retrieved_doc_set = set()
                    for d in details:
                        canon = d.get("canonical_id", "")
                        if canon:
                            retrieved_doc_set.add(canon)
                        src = d.get("source", "")
                        if src:
                            retrieved_doc_set.add(resolver.canonical(src, kb_id, department))
                    required_set = {resolver.canonical(x, kb_id, department) for x in required_docs}
                    multi_hop_success = len(retrieved_doc_set & required_set) / len(required_set)

                # === 多策略命中判定 ===
                actual_chunk_ids = {d["chunk_id"] for d in details if d.get("chunk_id")}

                if match_type == "snippet":
                    chunk_hit, chunk_recall = match_by_snippet(details, expected_snippets)
                elif expected_chunks:
                    matched_chunks = actual_chunk_ids & expected_chunks
                    chunk_recall = len(matched_chunks) / len(expected_chunks)
                    chunk_hit = len(matched_chunks) >= min_expected
                else:
                    chunk_recall = 1.0 if (set(actual_doc_strs) & expected_canonical) else 0.0
                    chunk_hit = bool(set(actual_doc_strs) & expected_canonical) if min_expected > 0 \
                        else not bool(set(actual_doc_strs) & expected_canonical)

                # === 部门隔离泄漏判定 ===
                retrieved_depts = {d.get("department", "") for d in details if d.get("department")}
                forbidden_depts = case.expected.get("forbidden_departments") or []
                allowed_depts = case.expected.get("allowed_departments") or []
                dept_leak = False
                if forbidden_depts:
                    dept_leak = bool(retrieved_depts & set(forbidden_depts))
                if allowed_depts:
                    dept_leak = dept_leak or bool(retrieved_depts - set(allowed_depts))

                # === pass 判定（shadow 模式不决定 pass/fail，仅记录语义指标）===
                if should_reject and confidence in ("none", "low"):
                    passed = not dept_leak
                elif gate == "semantic" and semantic_metrics:
                    recall_min = sem_thresh.get("sem_context_recall_min", sem_thresh.get("context_recall_min", 0.50))
                    sem_recall = semantic_metrics.get("sem_context_recall")
                    if sem_recall is not None:
                        passed = sem_recall >= recall_min and not dept_leak
                        if passed and case.metadata.get("generation_eval"):
                            faith_min = sem_thresh.get("sem_faithfulness_min", sem_thresh.get("faithfulness_min", 0.50))
                            corr_min = sem_thresh.get("sem_answer_correctness_min", sem_thresh.get("answer_similarity_min", 0.40))
                            sem_faith = semantic_metrics.get("sem_faithfulness")
                            sem_corr = semantic_metrics.get("sem_answer_correctness")
                            if sem_faith is not None and sem_faith < faith_min:
                                passed = False
                            if sem_corr is not None and sem_corr < corr_min:
                                passed = False
                    else:
                        passed = chunk_hit and not dept_leak
                else:
                    # v4.0 cases: no relevant_docs → use semantic metrics
                    if not expected_doc_strs and semantic_metrics:
                        sem_recall = semantic_metrics.get("sem_context_recall")
                        recall_min = sem_thresh.get("sem_context_recall_min", 0.50)
                        if sem_recall is not None:
                            passed = sem_recall >= recall_min and not dept_leak
                        else:
                            passed = not dept_leak and bool(details)
                    else:
                        passed = chunk_hit and not dept_leak

                _tok_after = _get_token_usage()
                _case_prompt_tokens = _tok_after["prompt_tokens"] - _tok_before["prompt_tokens"]
                _case_completion_tokens = _tok_after["completion_tokens"] - _tok_before["completion_tokens"]

                # ── 结果瘦身（--full-trace 保留全量）──
                # per_case JSON 原样携带完整 page_content + 全部 span 的
                # input/output，报告体积随检索量线性膨胀。默认只保留
                # snippet（前 200 字）与 span 的 metrics 摘要。
                # 注意：瘦身在全部打分完成之后执行，不影响指标计算。
                if not kwargs.get("full_trace", False):
                    details = [
                        {k: v for k, v in d.items() if k != "page_content"}
                        for d in details
                    ]
                    trace_spans = [
                        {k: v for k, v in sp.items()
                         if k not in ("input", "output", "events", "errors")}
                        for sp in trace_spans
                    ]

                result_holder.append(EvalResult(
                    case_id=case.id, module="rag",
                    status="pass" if passed else "fail",
                    expected=case.expected,
                    actual={
                        "question": question,
                        "kb_id": kb_id,
                        "department": department,
                        "retrieved_docs": actual_doc_strs[:10],
                        "retrieved_departments": sorted(retrieved_depts),
                        "dept_leak": dept_leak,
                        "details": details,
                        "pipeline": {**pipeline_info, "gate_mode": gate, "query_type": case.metadata.get("query_type", "")},
                        "stage_metrics": stage_metrics,
                        "generated_answer": _generated_answer or None,
                        "rejection": {
                            "confidence": confidence,
                            "reject_gate": reject_gate,
                            "reject_reason": reject_reason,
                            "top1_rerank_score": details[0].get("rerank_score") if details else None,
                            "query_entities": query_entities or None,
                            "entity_absent": entity_absent,
                        },
                        "trace": {
                            "trace_id": trace.id,
                            "total_spans": len(trace_spans),
                            "total_trace_ms": total_trace_ms,
                            "prompt_tokens": _case_prompt_tokens,
                            "completion_tokens": _case_completion_tokens,
                            "spans": trace_spans,
                        },
                    },
                    metrics={
                        "recall@5": round(r5, 4),
                        "recall@10": round(r10, 4),
                        "precision@5": round(precision_at_k(actual_doc_strs, expected_doc_strs, 5), 4),
                        "context_noise@10": round(context_noise_rate(actual_doc_strs, expected_doc_strs, 10), 4),
                        "mrr": round(mrr_val, 4),
                        "ndcg@10": round(ndcg_val, 4),
                        "chunk_recall": round(chunk_recall, 4),
                        **({"top1_accuracy": round(top1_accuracy, 4)} if top1_accuracy is not None else {}),
                        **({"reject_top1_accuracy": round(reject_top1_accuracy, 4)} if reject_top1_accuracy is not None else {}),
                        "dept_leak": int(dept_leak),
                        **({"reject_accuracy": round(reject_accuracy, 4)} if reject_accuracy is not None else {}),
                        **({f"gen_{k}": round(v, 4) if v is not None else None for k, v in generation_metrics.items()} if generation_metrics else {}),
                        **({k: round(v, 4) if v is not None else None for k, v in semantic_metrics.items()} if semantic_metrics else {}),
                        **({k: (v if k == "ragas_reason" else (round(v, 4) if v is not None else None)) for k, v in ragas_metrics.items()} if ragas_metrics else {}),
                        **({"required_fact_coverage": round(required_fact_coverage, 4)} if required_fact_coverage is not None else {}),
                        **({"false_answer_rate": round(false_answer_rate, 4)} if false_answer_rate is not None else {}),
                        **({"citation_accuracy": round(citation_accuracy, 4)} if citation_accuracy is not None else {}),
                        **({"citation_completeness": round(citation_completeness, 4)} if citation_completeness is not None else {}),
                        **({"multi_hop_success": round(multi_hop_success, 4)} if multi_hop_success is not None else {}),
                    },
                    duration_ms=int((time.time() - t0) * 1000),
                ))
                if results[-1].status != "error":
                    _append_result_checkpoint(checkpoint_file, results[-1])
            except Exception as e:
                result_holder.append(EvalResult(
                    case_id=case.id, module="rag", status="error",
                    expected=case.expected, actual={"question": case.question, "kb_id": case.metadata.get("kb_id", "default")},
                    error_msg=str(e), duration_ms=int((time.time() - t0) * 1000),
                ))
        return result_holder[0], (deferred_holder[0] if deferred_holder else None)

    # ── 用例执行（workers=1 串行；>1 线程池并发，结果按用例顺序组装）──
    # 检索器/模型为可复用单例，case 间无共享可变状态；本地 Ollama 生成
    # 仍会在服务端排队，并发收益主要来自检索/打分/云端调用场景
    workers = max(1, int(kwargs.get("workers") or 1))
    pending = [c for c in cases if c.id not in done_results]
    evaluated: dict[str, tuple[EvalResult, dict | None]] = {}
    if workers > 1 and pending:
        logger.info(f"[RAG eval] 并发执行 {len(pending)} 条用例 (workers={workers})")
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as _pool:
            _futures = {_pool.submit(_eval_case, c): c for c in pending}
            for _fut in concurrent.futures.as_completed(_futures):
                _c = _futures[_fut]
                evaluated[_c.id] = _fut.result()
    else:
        for c in pending:
            evaluated[c.id] = _eval_case(c)
    for c in cases:
        if c.id in done_results:
            results.append(done_results[c.id])
            continue
        _result, _deferred = evaluated[c.id]
        results.append(_result)
        if _deferred is not None:
            _deferred_ragas.append((len(results) - 1, *_deferred))
        if _result.status != "error":
            _append_result_checkpoint(checkpoint_file, _result)


    # === RAGAS 并行评估（所有 case 批量并发）===
    # 整段包 try/except：RAGAS 阶段失败不应吞掉前面几十条已完成用例的结果
    if _deferred_ragas:
        _ragas_workers = int(kwargs.get("ragas_workers", 4))
        logger.info(f"[RAGAS] 并行评估 {len(_deferred_ragas)} 个 case（workers={_ragas_workers}）")
        try:
            from backend.evaluation.evaluators.ragas_provider import RagasEvaluator
            from backend.evaluation.ragas_bridge import _get_llm, _get_embeddings
            _get_llm()
            _get_embeddings()

            def _run_ragas(job):
                result_idx, case, eval_ctx, ragas_metrics_dict = job
                ragas_eval = RagasEvaluator()
                if ragas_eval.should_run(case, eval_ctx):
                    m = ragas_eval.evaluate(case, eval_ctx)
                    ragas_metrics_dict.update(m)

            _ragas_pool = concurrent.futures.ThreadPoolExecutor(max_workers=_ragas_workers)
            try:
                _ragas_futures = [_ragas_pool.submit(_run_ragas, j) for j in _deferred_ragas]
                for i, fut in enumerate(concurrent.futures.as_completed(_ragas_futures)):
                    try:
                        fut.result()
                    except Exception as e:
                        logger.warning(f"[RAGAS] 并行评估失败 ({i}): {e}")
            finally:
                _ragas_pool.shutdown(wait=False, cancel_futures=True)

            # 更新 results 中的 RAGAS 指标
            for result_idx, case, eval_ctx, ragas_metrics_dict in _deferred_ragas:
                if ragas_metrics_dict and result_idx < len(results):
                    r = results[result_idx]
                    r.metrics.update(
                        {k: (v if k == "ragas_reason" else (round(v, 4) if v is not None else None))
                         for k, v in ragas_metrics_dict.items()}
                    )
                    # checkpoint 重新追加（读取时同 case_id 后行覆盖前行）
                    _append_result_checkpoint(checkpoint_file, r)
        except Exception as e:
            logger.error(f"[RAGAS] 批量评估阶段失败（保留已完成用例结果）: {e}", exc_info=True)

    return results


register_runner("rag", _run_rag, needs_live=False)
