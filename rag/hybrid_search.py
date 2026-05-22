# =====================================================
# rag/hybrid_search.py
# Hybrid Retrieval
# 实现 RRF 融合排序
# =====================================================

def hybrid_retrieve(query, vector_retriever, bm25_retriever, k=5, doc_ids=None, rrf_k=60):
    """
    混合检索函数：结合向量检索和BM25检索，使用RRF算法融合排序
    
    参数:
        query: 查询文本
        vector_retriever: 向量检索器（语义检索）
        bm25_retriever: BM25检索器（关键词检索）
        k: 最终返回的文档数量，默认5
        doc_ids: 可选的文档ID列表，用于限制检索范围
        rrf_k: RRF算法的平滑参数，默认60（值越大，排名差异影响越小）
    
    返回:
        合并排序后的前k个文档列表
    """
    
    # ==========================================
    # 第一步：执行双路召回
    # ==========================================
    
    # 向量检索：基于语义相似度召回相关文档
    # 如果指定了doc_ids，会在指定的文档范围内检索
    vector_docs = vector_retriever.retrieve(query, k=k, doc_ids=doc_ids)
    
    # BM25检索：基于关键词匹配召回相关文档
    # BM25是传统的稀疏检索方法，对精确关键词匹配效果好
    bm25_docs = bm25_retriever.invoke(query)
    
    # 如果指定了文档范围过滤，则过滤BM25结果
    if doc_ids:
        bm25_docs = [d for d in bm25_docs if d.metadata.get("doc_id") in doc_ids]
    
    # 限制BM25返回数量为k*2，避免过多候选文档影响后续排序
    bm25_docs = bm25_docs[:k*2]

    # ==========================================
    # 第二步：RRF (Reciprocal Rank Fusion) 融合排序
    # ==========================================
    # RRF公式: score = Σ(1 / (k + rank_i))
    # 其中rank_i是文档在第i个检索结果中的排名
    
    # 构建排名映射字典 {chunk_id: RRF分数}
    rank_map = {}
    
    # 计算向量检索的RRF分数
    # rank从1开始，排名越靠前分数越高
    for rank, doc in enumerate(vector_docs, start=1):
        cid = doc.metadata["chunk_id"]
        # 累加该chunk在所有检索结果中的RRF分数
        rank_map[cid] = rank_map.get(cid, 0) + 1 / (rrf_k + rank)
    
    # 计算BM25检索的RRF分数
    # 如果同一个chunk在两路检索中都出现，分数会累加，排名会提升
    for rank, doc in enumerate(bm25_docs, start=1):
        cid = doc.metadata["chunk_id"]
        rank_map[cid] = rank_map.get(cid, 0) + 1 / (rrf_k + rank)

    # ==========================================
    # 第三步：按RRF分数降序排序并返回结果
    # ==========================================
    
    # 按RRF分数从高到低排序，得到(chunk_id, 分数)的列表
    sorted_cids = sorted(rank_map.items(), key=lambda x: x[1], reverse=True)
    
    # 构建chunk_id到文档对象的映射字典
    # 注意：如果同一个文档在两路检索中都出现，后面的会覆盖前面的
    doc_dict = {doc.metadata["chunk_id"]: doc for doc in vector_docs + bm25_docs}
    
    # 按照排序后的chunk_id顺序，取出对应的文档对象，返回前k个
    merged = [doc_dict[cid] for cid, _ in sorted_cids[:k]]
    
    return merged


def rrf_fusion_docs(vector_docs, bm25_docs, k=5, rrf_k=60):
    """
    使用RRF算法融合向量检索和BM25检索结果

    参数:
        vector_docs: 向量检索结果列表
        bm25_docs: BM25检索结果列表
        k: 返回文档数量
        rrf_k: RRF平滑参数

    返回:
        融合排序后的文档列表
    """
    if not vector_docs and not bm25_docs:
        return []

    rank_map = {}

    for rank, doc in enumerate(vector_docs, 1):
        doc_id = doc.metadata["doc_id"]
        rank_map[doc_id] = rank_map.get(doc_id, 0) + 1 / (rrf_k + rank)

    for rank, doc in enumerate(bm25_docs, 1):
        doc_id = doc.metadata["doc_id"]
        rank_map[doc_id] = rank_map.get(doc_id, 0) + 1 / (rrf_k + rank)

    sorted_ids = sorted(rank_map, key=lambda x: rank_map[x], reverse=True)
    # bm25（chunk级）先入，vector_docs（doc级全文）后入覆盖，保证最终拿到完整文档
    doc_dict = {doc.metadata["doc_id"]: doc for doc in bm25_docs + vector_docs}

    return [doc_dict[doc_id] for doc_id in sorted_ids[:k]]