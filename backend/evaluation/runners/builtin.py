"""内置 Runner 实现 — 对接当前项目的 multi_agent / retrieval / sql_agent。

此文件注册 4 个模块的 runner：
- planner: 对接 multi_agent.planner
- rag:     对接 retrieval.pipeline（ChromaDB 向量检索）
- sql:     对接 sql_agent.SQLAgent（6 层安全管线）
- e2e:     对接 multi_agent.graph.MultiAgentSystem（完整 Agent 链路）

复制评估框架到新项目时，替换此文件中的 runner 实现即可。
"""

import time
from backend.evaluation.models import TestCase, EvalResult
from backend.evaluation.registry import register_runner
from backend.evaluation.runner import evaluate_planner_offline
from backend.evaluation.metrics import recall_at_k, mrr, ndcg_at_k, result_set_match
from backend.evaluation.judge import judge_answer


# ==================== Planner ====================

def _run_planner(cases: list[TestCase], **kwargs) -> list[EvalResult]:
    """Planner runner — 调用 multi_agent.planner.planner_node。"""
    results: list[EvalResult] = []
    try:
        from backend.agents.planner import planner_node

        for case in cases:
            t0 = time.time()
            try:
                state = {
                    "question": case.question,
                    "kb_id": case.metadata.get("kb_id", "default"),
                }
                plan_state = planner_node(state)
                plan = plan_state.get("plan", {})
                nodes = plan.get("nodes", {})
                actual_caps = list(dict.fromkeys(
                    node.get("capability", "")
                    for node in nodes.values()
                    if node.get("capability")
                ))
                result = evaluate_planner_offline(case.id, case.expected, actual_caps)
                result.duration_ms = int((time.time() - t0) * 1000)
                results.append(result)
            except Exception as e:
                results.append(EvalResult(
                    case_id=case.id, module="planner", status="error",
                    expected=case.expected, actual={},
                    error_msg=str(e), duration_ms=int((time.time() - t0) * 1000),
                ))
    except ImportError:
        results = [
            EvalResult(
                case_id=c.id, module="planner", status="error",
                expected=c.expected, actual={},
                error_msg="Planner module not available",
            )
            for c in cases
        ]
    return results


# ==================== RAG ====================

_rag_pipeline = None
_rag_pipeline_error = None


def _init_rag_pipeline():
    """初始化 RAG 检索管线（模块级单例）。"""
    global _rag_pipeline, _rag_pipeline_error
    if _rag_pipeline is not None:
        return _rag_pipeline
    if _rag_pipeline_error is not None:
        return None
    try:
        from backend.rag.pipeline import RAGPipeline
    except ImportError:
        _rag_pipeline_error = "RAG pipeline import failed"
        return None
    try:
        _rag_pipeline = RAGPipeline()
        return _rag_pipeline
    except Exception as e:
        _rag_pipeline_error = str(e)
        return None


# 完整检索链路（模块级缓存，不含 MultiQuery/LLM）
_full_retriever = None


def _get_full_retriever(pipeline):
    """构建完整检索链路: ChunkLevelRetriever → Adaptive → CrossEncoder 精排。

    覆盖: Doc检索→关键词过滤→人名匹配→BM25+RRF混合→Adaptive补全→精排
    不含: MultiQuery(需LLM) / HistoryAware(需LLM) / LLM生成答案
    """
    global _full_retriever
    if _full_retriever is not None:
        return _full_retriever

    from backend.rag.retrieval.retrievers import AdaptiveRetriever
    from backend.rag.reranker import RerankCompressor
    from langchain_classic.retrievers import ContextualCompressionRetriever
    from backend.config import HYBRID_SEARCH_K

    # 修改 chunk_retriever_base 的 k 值用于评估
    base = pipeline.lc_chain.chunk_retriever_base
    base.k = HYBRID_SEARCH_K

    # Adaptive: 文档分布分析 → 集中则补全全文
    adaptive = AdaptiveRetriever(
        base_retriever=base,
        doc_db=pipeline.doc_db,
    )

    # Rerank: CrossEncoder 全局精排
    full_retriever = ContextualCompressionRetriever(
        base_compressor=RerankCompressor(),
        base_retriever=adaptive,
    )

    _full_retriever = full_retriever
    return _full_retriever


def _run_rag(cases: list[TestCase], **kwargs) -> list[EvalResult]:
    """RAG runner — 完整检索链路（无 LLM）。

    链路: Doc检索 → 关键词过滤 → 人名匹配 → BM25+RRF混合 → Adaptive补全 → CrossEncoder精排
    不含 MultiQuery / HistoryAware / LLM 生成，needs_live=False 即可运行。
    """
    if not cases:
        return []

    pipeline = _init_rag_pipeline()
    if pipeline is None:
        return [
            EvalResult(
                case_id=c.id, module="rag", status="error",
                expected=c.expected, actual={},
                error_msg="RAG pipeline not available",
            )
            for c in cases
        ]

    retriever = _get_full_retriever(pipeline)
    # 获取底层检索器引用，用于采集管线各阶段数据
    chunk_base = pipeline.lc_chain.chunk_retriever_base

    results: list[EvalResult] = []
    for case in cases:
        t0 = time.time()
        try:
            kb_id = case.metadata.get("kb_id", "default")
            question = case.question

            # KB 隔离: 通过 contextvars 注入 metadata_filter
            if kb_id and kb_id != "*" and kb_id != "default":
                from backend.rag.context import RequestContext, set_context
                ctx = RequestContext(
                    metadata_filter={"kb_id": kb_id},
                    intent_label="",
                    query=question,
                )
                set_context(ctx)
            else:
                from backend.rag.context import RequestContext, set_context
                set_context(RequestContext())

            # === 采集 Doc 级检索结果（Stage 1）===
            doc_filter = {"kb_id": kb_id} if (kb_id and kb_id != "*" and kb_id != "default") else {}
            doc_results = pipeline.doc_db.similarity_search(question, k=5, filter=doc_filter) if doc_filter else pipeline.doc_db.similarity_search(question, k=5)
            stage1_docs = []
            for d in doc_results:
                stage1_docs.append({
                    "doc_id": d.metadata.get("doc_id", ""),
                    "title": str(d.metadata.get("title", ""))[:60],
                    "category": d.metadata.get("category_name", d.metadata.get("category", "")),
                })

            # === 完整检索链路 ===
            retrieved_docs = retriever.invoke(question)

            # === 组装详细检索轨迹 ===
            actual_doc_strs = []
            seen = set()
            details = []
            for doc in retrieved_docs:
                doc_id = doc.metadata.get("doc_id", "")
                source = doc.metadata.get("source", "").replace("\\", "/")
                identifier = doc_id if doc_id else source
                if identifier not in seen:
                    seen.add(identifier)
                    actual_doc_strs.append(identifier)

                details.append({
                    "doc_id": doc_id,
                    "title": str(doc.metadata.get("title", ""))[:80],
                    "chunk_id": doc.metadata.get("chunk_id", ""),
                    "rerank_score": doc.metadata.get("rerank_score"),
                    "source": source,
                    "snippet": doc.page_content[:200].replace("\n", " "),
                })

            # 检测自适应行为：chunks 集中在少数文档 vs 分散
            from collections import Counter
            doc_counter = Counter(d["doc_id"] for d in details if d["doc_id"])
            total = len(details)
            if total > 0:
                clustered = [did for did, cnt in doc_counter.items() if cnt / total >= 0.3]
                adaptive_info = f"集中({len(clustered)}个文档)" if len(clustered) <= 2 and clustered else f"分散({len(doc_counter)}个文档)"
            else:
                adaptive_info = "无结果"

            pipeline_info = {
                "stage1_docs": len(stage1_docs),
                "stage1_top_docs": stage1_docs,
                "stage2_chunks_recalled": total,
                "after_rerank": total,  # = stage2 数量（rerank 在 invoke 内完成）
                "adaptive": adaptive_info,
            }

            expected_docs = set(case.expected.get("relevant_docs", []))
            expected_doc_strs = list(expected_docs)

            r5 = recall_at_k(actual_doc_strs, expected_doc_strs, k=5)
            r10 = recall_at_k(actual_doc_strs, expected_doc_strs, k=10)
            mrr_val = mrr(actual_doc_strs, expected_doc_strs)
            ndcg_val = ndcg_at_k(actual_doc_strs, expected_doc_strs, k=10)

            min_expected = case.expected.get("min_relevant_chunks", 1)
            has_any_relevant = len(set(actual_doc_strs) & expected_docs) > 0
            passed = has_any_relevant if min_expected > 0 else not has_any_relevant

            results.append(EvalResult(
                case_id=case.id, module="rag",
                status="pass" if passed else "fail",
                expected=case.expected,
                actual={
                    "question": question,
                    "kb_id": kb_id,
                    "retrieved_docs": actual_doc_strs[:10],
                    "details": details,
                    "pipeline": pipeline_info,
                },
                metrics={
                    "recall@5": round(r5, 4),
                    "recall@10": round(r10, 4),
                    "mrr": round(mrr_val, 4),
                    "ndcg@10": round(ndcg_val, 4),
                },
                duration_ms=int((time.time() - t0) * 1000),
            ))
        except Exception as e:
            results.append(EvalResult(
                case_id=case.id, module="rag", status="error",
                expected=case.expected, actual={"question": case.question, "kb_id": case.metadata.get("kb_id", "default")},
                error_msg=str(e), duration_ms=int((time.time() - t0) * 1000),
            ))

    return results


# ==================== SQL ====================

def _run_sql(cases: list[TestCase], **kwargs) -> list[EvalResult]:
    """SQL runner — 通过 SQLAgent.ask() 走完整的 6 层安全管线。"""
    if not cases:
        return []

    results: list[EvalResult] = []
    try:
        from backend.config import DB_CONFIG

        for case in cases:
            t0 = time.time()
            try:
                from backend.sql.sql_agent import SQLAgent
                agent = SQLAgent(db_config=DB_CONFIG)
                outcome = agent.ask(case.question)

                expected_security = case.expected.get("security_checks", [])
                is_security_test = (
                    "sensitive_column_blocked" in expected_security
                    or "write_blocked" in expected_security
                )

                if is_security_test:
                    is_blocked = (
                        "错误" in outcome or "拦截" in outcome
                        or "不允许" in outcome or "访问控制" in outcome
                    )
                    results.append(EvalResult(
                        case_id=case.id, module="sql",
                        status="pass" if is_blocked else "fail",
                        expected=case.expected,
                        actual={"output": outcome[:200]},
                        metrics={
                            "syntax_valid": 0.0,
                            "result_match": 0.0,
                            "security_pass": 1.0 if is_blocked else 0.0,
                        },
                        duration_ms=int((time.time() - t0) * 1000),
                    ))
                else:
                    is_error = "失败" in outcome or "错误" in outcome or "访问控制" in outcome
                    if is_error:
                        actual_result = []
                        syntax_ok = 0.0
                        security_pass = 0.0
                    else:
                        actual_result = _parse_markdown_table(outcome)
                        syntax_ok = 1.0
                        security_pass = 1.0

                    expected_result = case.expected.get("expected_result", [])
                    if expected_result:
                        result_ok = result_set_match(actual_result, expected_result)
                    else:
                        result_ok = float(len(actual_result) > 0) if actual_result else 0.0

                    passed = security_pass == 1.0 and (syntax_ok > 0 or result_ok > 0)

                    results.append(EvalResult(
                        case_id=case.id, module="sql",
                        status="pass" if passed else "fail",
                        expected=case.expected,
                        actual={"sql": "routed via SQLAgent.ask()", "result": actual_result},
                        metrics={
                            "syntax_valid": syntax_ok,
                            "result_match": result_ok,
                            "security_pass": security_pass,
                        },
                        duration_ms=int((time.time() - t0) * 1000),
                    ))
            except Exception as e:
                results.append(EvalResult(
                    case_id=case.id, module="sql", status="error",
                    expected=case.expected, actual={},
                    error_msg=str(e), duration_ms=int((time.time() - t0) * 1000),
                ))
    except ImportError:
        results = [
            EvalResult(
                case_id=c.id, module="sql", status="error",
                expected=c.expected, actual={},
                error_msg="SQL agent not available",
            )
            for c in cases
        ]
    return results


def _parse_markdown_table(md: str) -> list[dict]:
    """从 markdown 表格字符串解析行数据。"""
    if not md or "(无结果)" in md:
        return []
    lines = md.strip().split("\n")
    if len(lines) < 3:
        return []
    header_line = lines[0]
    headers = [h.strip() for h in header_line.split("|")[1:-1]]
    rows = []
    for line in lines[2:]:
        if not line.strip():
            continue
        cols = [c.strip() for c in line.split("|")[1:-1]]
        if len(cols) == len(headers):
            row = {}
            for h, c in zip(headers, cols):
                try:
                    row[h] = int(c)
                except ValueError:
                    try:
                        row[h] = float(c)
                    except ValueError:
                        row[h] = c
            rows.append(row)
    return rows


# ==================== E2E ====================

def _infer_routing_from_answer(answer: str, expected_routing: set) -> set:
    """从回答内容推断使用了哪些能力。"""
    actual_caps = set()
    if not answer:
        return actual_caps
    if "|" in answer and "---" in answer:
        actual_caps.add("query_database")
    if "参考文献" in answer or "来源" in answer:
        actual_caps.add("search_knowledge")
    # 如果没有任何特征：actual_caps 保持为空，routing_accuracy 会正确反映失败
    return actual_caps


def _run_e2e(cases: list[TestCase], **kwargs) -> list[EvalResult]:
    """E2E runner — 完整 MultiAgentSystem 链路 + 可选的 LLM-as-Judge 评分。"""
    if not cases:
        return []

    judge = kwargs.get("judge", False)
    results: list[EvalResult] = []

    try:
        from backend.orchestration.graph import MultiAgentSystem
        mas = MultiAgentSystem()

        for case in cases:
            t0 = time.time()
            try:
                kb_id = case.metadata.get("kb_id", "default")
                answer = mas.ask(case.question, kb_id=kb_id)

                expected_routing = set(case.expected.get("expected_routing", []))
                actual_caps = _infer_routing_from_answer(answer, expected_routing)
                routing_ok = (
                    expected_routing.issubset(actual_caps)
                    if expected_routing else True
                )

                metrics = {"routing_accuracy": 1.0 if routing_ok else 0.0}

                if judge and answer:
                    rubric = case.expected.get("rubric", {})
                    jr = judge_answer(case.question, rubric, answer)
                    metrics["judge_completeness"] = float(jr.scores.get("completeness", 0))
                    metrics["judge_faithfulness"] = float(jr.scores.get("faithfulness", 0))
                    metrics["judge_conciseness"] = float(jr.scores.get("conciseness", 0))
                    metrics["judge_citation"] = float(jr.scores.get("citation_quality", 0))
                    metrics["judge_total"] = jr.total
                    metrics["judge_confidence"] = {
                        "low": 0.0, "medium": 0.5, "high": 1.0
                    }.get(jr.confidence, 0.5)

                passed = routing_ok and (
                    not judge or metrics.get("judge_total", 5) >= 3.0
                )

                results.append(EvalResult(
                    case_id=case.id, module="e2e",
                    status="pass" if passed else "fail",
                    expected=case.expected,
                    actual={"answer": answer[:500], "routing": list(actual_caps)},
                    metrics={
                        k: round(v, 4) if isinstance(v, float) else v
                        for k, v in metrics.items()
                    },
                    duration_ms=int((time.time() - t0) * 1000),
                ))
            except Exception as e:
                results.append(EvalResult(
                    case_id=case.id, module="e2e", status="error",
                    expected=case.expected, actual={},
                    error_msg=str(e),
                    duration_ms=int((time.time() - t0) * 1000),
                ))
    except ImportError:
        results = [
            EvalResult(
                case_id=c.id, module="e2e", status="error",
                expected=c.expected, actual={},
                error_msg="MultiAgentSystem not available",
            )
            for c in cases
        ]
    return results


# ==================== 注册所有 Runner ====================

register_runner("planner", _run_planner, needs_live=True)
register_runner("rag", _run_rag, needs_live=False)       # 不依赖 LLM
register_runner("sql", _run_sql, needs_live=True)
register_runner("e2e", _run_e2e, needs_live=True)
