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
            metadata_filter=None
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
            docs_with_scores = (
                self.vectordb
                .similarity_search_with_score(
                    question,
                    k=k * 2,
                    filter=filter_dict
                )
            )
            if debug:
                logger.debug(f"Metadata Filter: {filter_dict}")
        else:
            docs_with_scores = (
                self.vectordb
                .similarity_search_with_score(
                    question,
                    k=k
                )
            )
            if debug:
                logger.debug("Global search (no filter)")

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
