"""Diagnose V2-BASIC-007 and V2-BASIC-014 chunk retrieval failures."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import json


def diagnose_case(question, kb_id, target_doc_id, target_snippets, label):
    print(f"\n{'='*70}")
    print(f"  DIAGNOSING: {label}")
    print(f"  Question: {question}")
    print(f"  Target doc: {target_doc_id}, snippets: {target_snippets}")
    print(f"{'='*70}")

    from backend.rag.context import RequestContext, set_context
    ctx = RequestContext(
        metadata_filter={"kb_id": kb_id},
        intent_label="",
        query=question,
    )
    set_context(ctx)

    from backend.rag.pipeline import RAGPipeline
    pipeline = RAGPipeline()

    # === Stage 1: Doc retrieval ===
    print("\n--- Stage 1: Doc retrieval (k=15) ---")
    doc_filter = {"kb_id": kb_id}
    doc_results = pipeline.doc_db.similarity_search(question, k=15, filter=doc_filter)

    from backend.rag.retrieval.retrievers import _score_by_keyword_overlap, _dedup_by_doc_id
    deduped = _dedup_by_doc_id(doc_results)
    reranked = _score_by_keyword_overlap(question, deduped)
    doc_ids = [d.metadata.get("doc_id") for d in reranked if d.metadata.get("doc_id")]
    print(f"  Top-5 doc_ids after keyword rerank: {doc_ids[:5]}")
    print(f"  Target doc rank: {doc_ids.index(target_doc_id) + 1 if target_doc_id in doc_ids else 'NOT FOUND'}")

    # === Stage 2: Chunk retrieval (hybrid) ===
    print("\n--- Stage 2: hybrid_retrieve ---")
    from backend.rag.retrieval.hybrid import hybrid_retrieve
    chunk_retriever = pipeline.lc_chain.chunk_retriever_base

    from backend.rag.preprocessing.synonyms import expand_query
    expanded_queries = expand_query(question) if doc_ids else None
    print(f"  Expanded queries: {expanded_queries}")

    hybrid_docs = hybrid_retrieve(
        question, chunk_retriever.chunk_retriever, chunk_retriever.bm25,
        k=chunk_retriever.k, doc_ids=doc_ids,
        metadata_filter={"kb_id": kb_id},
        expanded_queries=expanded_queries,
    )
    print(f"\n  hybrid_retrieve returned {len(hybrid_docs)} chunks:")
    target_in_hybrid = False
    for i, d in enumerate(hybrid_docs):
        did = d.metadata.get("doc_id", "?")
        cidx = d.metadata.get("chunk_index", "?")
        cid = d.metadata.get("chunk_id", "?")[:12]
        rrf = d.metadata.get("rrf_score", "?")
        sim = d.metadata.get("similarity", "?")
        snippet = d.page_content[:100].replace("\n", " ")
        has_target = any(s in d.page_content for s in target_snippets)
        marker = " <<<TARGET>>>" if has_target else ""
        if did == target_doc_id and has_target:
            target_in_hybrid = True
        print(f"  [{i}] doc={did} chunk_idx={cidx} rrf={rrf} sim={sim}{marker}")
        print(f"       {snippet}")

    print(f"\n  Target snippets in hybrid results: {target_in_hybrid}")

    # Check per-doc limit
    from collections import Counter
    doc_counter = Counter(d.metadata.get("doc_id") for d in hybrid_docs)
    print(f"  Doc distribution in hybrid: {dict(doc_counter)}")
    target_doc_chunks = [d for d in hybrid_docs if d.metadata.get("doc_id") == target_doc_id]
    print(f"  Target doc chunks in hybrid: {len(target_doc_chunks)}")
    for c in target_doc_chunks:
        cidx = c.metadata.get("chunk_index", "?")
        has = [s for s in target_snippets if s in c.page_content]
        print(f"    chunk_idx={cidx}, has_snippets={has}, content={c.page_content[:80].replace(chr(10), ' ')}")

    # === Stage 3: Full retriever (with rerank) ===
    print("\n--- Stage 3: Full retriever (with rerank) ---")
    from backend.evaluation.runners.builtin import _get_full_retriever
    retriever = _get_full_retriever(pipeline)
    final_docs = retriever.invoke(question)

    print(f"\n  Final results after rerank: {len(final_docs)} chunks:")
    target_in_final = False
    for i, d in enumerate(final_docs):
        did = d.metadata.get("doc_id", "?")
        cidx = d.metadata.get("chunk_index", "?")
        rerank = d.metadata.get("rerank_score", "?")
        snippet = d.page_content[:100].replace("\n", " ")
        has_target_snip = any(s in d.page_content for s in target_snippets)
        marker = " <<<TARGET>>>" if has_target_snip else ""
        if did == target_doc_id and has_target_snip:
            target_in_final = True
        print(f"  [{i}] doc={did} chunk_idx={cidx} rerank={rerank}{marker}")
        print(f"       {snippet}")

    print(f"\n  Target snippets in final results: {target_in_final}")

    # Summary
    print(f"\n--- SUMMARY for {label} ---")
    print(f"  Target doc in top-5: {'YES' if target_doc_id in doc_ids[:5] else 'NO'}")
    print(f"  Target snippets in hybrid: {'YES' if target_in_hybrid else 'NO'}")
    print(f"  Target snippets in final: {'YES' if target_in_final else 'NO'}")
    if target_in_hybrid and not target_in_final:
        print(f"  >>> ISSUE: Reranker is dropping target chunks!")
    elif not target_in_hybrid:
        print(f"  >>> ISSUE: Target chunks not reaching hybrid_retrieve!")


def main():
    # V2-BASIC-007: "盘点分为哪几类？"
    diagnose_case(
        question="盘点分为哪几类？",
        kb_id="rag_test_kb",
        target_doc_id="99a80089e4",
        target_snippets=["盘点", "年度盘点"],
        label="V2-BASIC-007",
    )

    # V2-BASIC-014: "采购合同总金额是多少？"
    diagnose_case(
        question="采购合同总金额是多少？",
        kb_id="rag_test_kb",
        target_doc_id="14aa8a1c7a",
        target_snippets=["90000", "玖万"],
        label="V2-BASIC-014",
    )


if __name__ == "__main__":
    main()
