# =====================================================
# rag/retriever.py
# 只负责 Recall（粗召回）
# =====================================================

class CustomRetriever:

    def __init__(self, vectordb):
        self.vectordb = vectordb

    def retrieve(
            self,
            question,
            k=5,
            debug=False,
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
                例如: {"person_names": "吴浩", "doc_type": "resume"}
        """

        # ====================================
        # 1. 构建过滤条件
        # ====================================

        filter_dict = {}

        if doc_ids:
            filter_dict["doc_id"] = {"$in": list(doc_ids)}

        if metadata_filter:
            for key, value in metadata_filter.items():
                if isinstance(value, list):
                    filter_dict[key] = {"$in": value}
                else:
                    filter_dict[key] = value

        # ====================================
        # 2. 执行向量检索
        # ====================================

        if filter_dict:
            docs_with_scores = (
                self.vectordb
                .similarity_search_with_score(
                    question,
                    k=k * 2,
                    filter=filter_dict
                )
            )
            if debug:
                print(f"🔍 使用 Metadata Filter: {filter_dict}")
        else:
            docs_with_scores = (
                self.vectordb
                .similarity_search_with_score(
                    question,
                    k=k
                )
            )
            if debug:
                print("🔍 使用全局检索（无 Filter）")

        # ====================================
        # 3. Debug 输出
        # ====================================

        if debug:
            print("\n================ Recall Results ================\n")

            for i, (doc, score) in enumerate(docs_with_scores):
                print(f"[{i+1}] vector_score={score:.4f}")
                print(f"source_file: {doc.metadata.get('source_file')}")
                print(f"chunk_id: {doc.metadata.get('chunk_id')}")
                print(f"doc_type: {doc.metadata.get('doc_type')}")
                print(f"business_domain: {doc.metadata.get('business_domain')}")
                print(f"person_names: {doc.metadata.get('person_names', [])}")
                print(f"keywords: {doc.metadata.get('keywords', [])}")
                print(f"content: {doc.page_content[:50]}...")
                print("-" * 60)

        # ====================================
        # 4. 返回文档列表
        # ====================================

        docs = [doc for doc, score in docs_with_scores]

        return docs

    def retrieve_by_person(self, question: str, person_name: str, k=5, debug=False):
        """
        按人名检索（利用 person_names 元数据）
        
        参数:
            question: 查询问题
            person_name: 人名
            k: 返回数量
            debug: 是否调试模式
        """
        metadata_filter = {
            "person_names": person_name
        }
        
        return self.retrieve(
            question=question,
            k=k,
            debug=debug,
            metadata_filter=metadata_filter
        )

    def retrieve_by_domain(self, question: str, domain: str, k=5, debug=False):
        """
        按业务领域检索（利用 business_domain 元数据）
        
        参数:
            question: 查询问题
            domain: 业务领域 (finance/hr/ecommerce/operation/infrastructure)
            k: 返回数量
            debug: 是否调试模式
        """
        metadata_filter = {
            "business_domain": domain
        }
        
        return self.retrieve(
            question=question,
            k=k,
            debug=debug,
            metadata_filter=metadata_filter
        )

    def retrieve_by_doc_type(self, question: str, doc_type: str, k=5, debug=False):
        """
        按文档类型检索（利用 doc_type 元数据）
        
        参数:
            question: 查询问题
            doc_type: 文档类型 (resume/project/report/manual/policy/general)
            k: 返回数量
            debug: 是否调试模式
        """
        metadata_filter = {
            "doc_type": doc_type
        }
        
        return self.retrieve(
            question=question,
            k=k,
            debug=debug,
            metadata_filter=metadata_filter
        )
