"""诊断 V2-BASIC-014 检索链路：逐阶段输出，定位 90000/玖万 丢失点。"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import json
from collections import Counter

def main():
    from backend.rag.context import RequestContext, set_context
    from backend.observability.tracer import SpanKind, trace_collector

    question = "采购合同总金额是多少？"
    kb_id = "rag_test_kb"

    # 设置 context（模拟 eval runner）
    ctx = RequestContext(
        metadata_filter={"kb_id": kb_id},
        intent_label="",
        query=question,
    )
    set_context(ctx)

    # 初始化 pipeline
    from backend.rag.pipeline import RAGPipeline
    pipeline = RAGPipeline()

    # === Stage 1: Doc 级检索 ===
    print("=" * 60)
    print("Stage 1: Doc 级检索 (doc_db similarity_search)")
    print("=" * 60)
    doc_filter = {"kb_id": kb_id}
    doc_results = pipeline.doc_db.similarity_search(question, k=15, filter=doc_filter)
    for i, d in enumerate(doc_results):
        doc_id = d.metadata.get("doc_id", "?")
        title = str(d.metadata.get("title", ""))[:50]
        kw = d.metadata.get("doc_keywords", "")
        print(f"  [{i}] doc_id={doc_id}, title={title}")
        if kw:
            try:
                kw_list = json.loads(kw) if kw.startswith("[") else kw.split(", ")
            except Exception:
                kw_list = [kw]
            print(f"       keywords={kw_list}")

    # 关键词重排
    from backend.rag.retrieval.retrievers import _score_by_keyword_overlap, _dedup_by_doc_id
    deduped = _dedup_by_doc_id(doc_results)
    reranked = _score_by_keyword_overlap(question, deduped)
    doc_ids = [d.metadata.get("doc_id") for d in reranked if d.metadata.get("doc_id")]
    print(f"\n  关键词重排后 doc_ids ({len(doc_ids)}): {doc_ids[:10]}")

    # === Stage 2: Chunk 级检索 (hybrid_retrieve) ===
    print("\n" + "=" * 60)
    print("Stage 2: Chunk 级检索 (hybrid_retrieve)")
    print("=" * 60)
    from backend.rag.retrieval.hybrid import hybrid_retrieve
    chunk_retriever = pipeline.lc_chain.chunk_retriever_base

    # 同义词扩展
    from backend.rag.preprocessing.synonyms import expand_query
    expanded_queries = expand_query(question) if doc_ids else None
    print(f"  同义词扩展: {expanded_queries}")

    hybrid_docs = hybrid_retrieve(
        question, chunk_retriever.chunk_retriever, chunk_retriever.bm25,
        k=chunk_retriever.k, doc_ids=doc_ids,
        metadata_filter={"kb_id": kb_id},
        expanded_queries=expanded_queries,
    )
    print(f"\n  hybrid_retrieve 返回 {len(hybrid_docs)} 个 chunks:")
    for i, d in enumerate(hybrid_docs):
        doc_id = d.metadata.get("doc_id", "?")
        chunk_idx = d.metadata.get("chunk_index", "?")
        rrf = d.metadata.get("rrf_score", "?")
        sim = d.metadata.get("similarity", "?")
        snippet = d.page_content[:80].replace("\n", " ")
        marker = " ★★★" if "90000" in d.page_content or "玖万" in d.page_content else ""
        print(f"  [{i}] doc={doc_id} chunk_idx={chunk_idx} rrf={rrf} sim={sim}{marker}")
        print(f"       {snippet}")

    # 检查目标 chunk 是否出现
    target_found = any(
        d.metadata.get("doc_id") == "14aa8a1c7a" and ("90000" in d.page_content or "玖万" in d.page_content)
        for d in hybrid_docs
    )
    print(f"\n  目标 chunk (14aa8a1c7a + 90000/玖万) 在 hybrid 结果中: {'YES' if target_found else 'NO'}")

    # === Stage 3: Rerank ===
    print("\n" + "=" * 60)
    print("Stage 3: Rerank (CrossEncoder 精排)")
    print("=" * 60)

    # 启动 trace
    trace = trace_collector.start(
        question=question,
        session_id="diag-014",
        workflow_name="rag_eval",
    )
    root_span = trace_collector.start_span(
        "diag_root", parent_id=None,
        name="diag", type="workflow", kind=SpanKind.RETRIEVAL.value,
        input={"question": question},
    )

    try:
        from backend.evaluation.runners.builtin import _get_full_retriever
        retriever = _get_full_retriever(pipeline)
        retrieved = retriever.invoke(question)
    finally:
        try:
            trace_collector.end_span(root_span)
        except Exception:
            pass

    print(f"\n  最终检索结果 ({len(retrieved)} 个 chunks):")
    for i, d in enumerate(retrieved):
        doc_id = d.metadata.get("doc_id", "?")
        chunk_idx = d.metadata.get("chunk_index", "?")
        rerank_score = d.metadata.get("rerank_score", "?")
        snippet = d.page_content[:80].replace("\n", " ")
        marker = " ★★★" if "90000" in d.page_content or "玖万" in d.page_content else ""
        print(f"  [{i}] doc={doc_id} chunk_idx={chunk_idx} rerank={rerank_score}{marker}")
        print(f"       {snippet}")

    target_in_final = any(
        d.metadata.get("doc_id") == "14aa8a1c7a" and ("90000" in d.page_content or "玖万" in d.page_content)
        for d in retrieved
    )
    print(f"\n  目标 chunk 在最终结果中: {'YES' if target_in_final else 'NO'}")

    # doc 分布
    doc_counter = Counter(d.metadata.get("doc_id", "?") for d in retrieved)
    print(f"\n  文档分布: {dict(doc_counter)}")


if __name__ == "__main__":
    main()
