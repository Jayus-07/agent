from backend.shared.logger import logger


class CustomRetriever:

    def __init__(self, vectordb):
        self.vectordb = vectordb

    def retrieve(
            self,
            question,
            k=5,
            debug=1,
            doc_ids=None,
            metadata_filter=None,
            expanded_queries=None,  # 2026-08-20: 同义词扩展列表（默认 None = 不扩展）
    ):
        """
        检索文档

        参数:
            question: 查询问题
            k: 返回数量
            debug: 是否打印调试信息
            doc_ids: 文档ID列表过滤
            metadata_filter: 额外的元数据过滤条件字典
                例如: {"person_names": "MeridiHome", "doc_type": "listing"}
            expanded_queries: 同义词扩展后的额外 query 列表（None = 不扩展）
                RRF 融合所有 query 的检索结果，提升召回覆盖率
        """

        filter_dict = {}

        if doc_ids:
            filter_dict["doc_id"] = {"$in": list(doc_ids)}

        if metadata_filter:
            for key, value in metadata_filter.items():
                # ChromaDB 逻辑运算符（$and/$or/$not）的值已经是 where 表达式，
                # 不应再包 $in，否则产生无效语法：
                #   $or → {"$in": [{...}]}  # ❌
                if key.startswith("$"):
                    filter_dict[key] = value
                elif isinstance(value, list):
                    filter_dict[key] = {"$in": value}
                else:
                    filter_dict[key] = value

        if filter_dict:
            # ChromaDB requires exactly one top-level operator.
            # Multiple keys → wrap in $and.
            if len(filter_dict) > 1:
                filter_dict = {"$and": [{k: v} for k, v in filter_dict.items()]}
        else:
            filter_dict = None

        # 2026-08-20: 同义词扩展检索 — 对每个 expanded query 各调一次 + RRF 融合
        queries_to_search = [question]
        if expanded_queries:
            for q in expanded_queries:
                if q and q != question and q not in queries_to_search:
                    queries_to_search.append(q)

        # RRF 融合参数
        rrf_k = 60

        # 单 query 模式：直接调（保持向后兼容）
        if len(queries_to_search) == 1:
            if filter_dict:
                docs_with_scores = (
                    self.vectordb
                    .similarity_search_with_score(
                        question,
                        k=k * 2,
                        filter=filter_dict
                    )
                )
            else:
                docs_with_scores = (
                    self.vectordb
                    .similarity_search_with_score(
                        question,
                        k=k
                    )
                )
        else:
            # 多 query 模式：每个 query 单独检索 + RRF 融合
            from collections import defaultdict
            rrf_scores: dict[str, float] = defaultdict(float)
            all_docs: dict[str, tuple] = {}  # chunk_id -> (doc, score, query)
            for qi, q in enumerate(queries_to_search):
                try:
                    if filter_dict:
                        batch = self.vectordb.similarity_search_with_score(
                            q, k=k, filter=filter_dict,
                        )
                    else:
                        batch = self.vectordb.similarity_search_with_score(
                            q, k=k,
                        )
                except Exception as e:
                    logger.warning(f"expanded query '{q}' 检索失败: {e}")
                    continue
                # 原始 query 权重最高（rank bonus）
                rank_bonus = 1.0 if qi == 0 else 0.7  # 扩展 query 降权 30%
                for rank, (doc, score) in enumerate(batch, start=1):
                    cid = doc.metadata.get("chunk_id") or f'{doc.metadata.get("doc_id","?")}:{doc.metadata.get("chunk_index",0)}'
                    rrf_scores[cid] += rank_bonus * (1 / (rrf_k + rank))
                    if cid not in all_docs:
                        all_docs[cid] = (doc, score, q)
            # 按 RRF 排序取 top k*2
            sorted_cids = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
            docs_with_scores = [
                (all_docs[cid][0], all_docs[cid][1])
                for cid, _ in sorted_cids[:k * 2]
            ]
            if debug:
                logger.debug(
                    f"同义词扩展检索: 原 query + {len(queries_to_search)-1} 个变体 → {len(docs_with_scores)} chunks"
                )

        if debug:
            for i, (doc, score) in enumerate(docs_with_scores):
                logger.debug(
                    f"[{i+1}] vector_score={score:.4f} "
                    f"source={doc.metadata.get('source_file')} "
                    f"chunk={doc.metadata.get('chunk_id')} "
                    f"content={doc.page_content[:50]}..."
                )

        docs = []
        for doc, score in docs_with_scores:
            doc.metadata["similarity"] = round(score, 4)  # 保留向量相似度，供自适应检索用
            docs.append(doc)

        return docs
