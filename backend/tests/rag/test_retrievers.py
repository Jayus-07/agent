"""RAG Retriever 核心链路测试（P1 整改新增）。

覆盖任务要求的 Retrieval fallback 可观测性与『系统失败不伪装成没有资料』：
  1. Vector 检索失败 → 异常向上传播（不静默返回空 context 伪装成无资料）
  2. BM25 无结果 → 纯 Vector 结果正常返回（Hybrid 融合不丢一侧）
  3. Stage 2 全空 → Neighbor Expansion fallback（有日志留痕）
  4. parent_lookup 失败 → 保留原检索结果（有日志留痕）
  5. AdaptiveRetriever Cluster 检测 → Context Expansion
"""
import pytest
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from types import SimpleNamespace


# =====================================================
# Hybrid 检索 fallback 行为
# =====================================================

class TestHybridFallback:
    @pytest.fixture(autouse=True)
    def _disable_enhanced_path(self, monkeypatch):
        """这些测试验证原始 hybrid fallback 逻辑，需绕过 enhanced 路由。"""
        monkeypatch.setattr("backend.config.rag.ADAPTIVE_THRESHOLD_ENABLED", False)
        monkeypatch.setattr("backend.config.rag.CONFIDENCE_AGGREGATOR_ENABLED", False)

    def test_vector_failure_falls_back_to_bm25(self):
        """Vector 崩溃 → 降级仅用 BM25，结果正常返回且 span 标记 fallback（可观测）。"""
        from backend.observability.tracer import trace_collector
        from backend.rag.retrieval.hybrid import hybrid_retrieve

        # mock 签名须与 CustomRetriever.retrieve 对齐:
        # 2026-08-20 hybrid_retrieve 新增 expanded_queries 透传,缺参会 TypeError
        def boom(q, k=5, doc_ids=None, metadata_filter=None, expanded_queries=None):
            raise RuntimeError("vector db down")
        v = SimpleNamespace(retrieve=boom)
        b = SimpleNamespace(invoke=lambda q: [
            Document(page_content="b1", metadata={"chunk_id": "b1", "doc_id": "d1"}),
        ])

        trace = trace_collector.start("hybrid-fallback", session_id="t1")
        try:
            trace_collector.start_span("root", parent_id=None, name="test", type="agent")
        except RuntimeError:
            pass  # root 已存在

        merged = hybrid_retrieve("q", v, b, k=5)
        assert len(merged) == 1
        assert merged[0].metadata["chunk_id"] == "b1"

        # fallback 可观测：retrieval span 的 metrics 带 fallback_side
        ret_span = next(
            (s for s in trace.spans if s.span_id == "hybrid_retrieval"), None
        )
        assert ret_span is not None
        assert ret_span.metrics.get("fallback_side") == "vector"
        assert "vector db down" in ret_span.metrics.get("fallback_reason", "")

    def test_both_fail_propagates(self):
        """两侧都失败 → 异常向上传播（真系统失败，不伪装成『没有资料』）。"""
        from backend.rag.retrieval.hybrid import hybrid_retrieve

        def boom_v(q, k=5, doc_ids=None, metadata_filter=None, expanded_queries=None):
            raise RuntimeError("vector db down")
        def boom_b(q):
            raise RuntimeError("bm25 db down")
        v = SimpleNamespace(retrieve=boom_v)
        b = SimpleNamespace(invoke=boom_b)
        with pytest.raises(RuntimeError, match="均失败"):
            hybrid_retrieve("q", v, b, k=5)

    def test_vector_only_when_bm25_empty(self):
        """BM25 无结果 → 保留 Vector 结果（Hybrid 融合不丢一侧）。"""
        from backend.rag.retrieval.hybrid import hybrid_retrieve
        v = SimpleNamespace(retrieve=lambda q, k=5, doc_ids=None, metadata_filter=None, expanded_queries=None: [
            Document(page_content="v1", metadata={"chunk_id": "v1", "doc_id": "d1"}),
        ])
        b = SimpleNamespace(invoke=lambda q: [])
        merged = hybrid_retrieve("q", v, b, k=5)
        assert len(merged) == 1
        assert merged[0].metadata["chunk_id"] == "v1"

    def test_bm25_only_when_vector_empty(self):
        """Vector 无结果 → 保留 BM25 结果。"""
        from backend.rag.retrieval.hybrid import hybrid_retrieve
        v = SimpleNamespace(retrieve=lambda q, k=5, doc_ids=None, metadata_filter=None, expanded_queries=None: [])
        b = SimpleNamespace(invoke=lambda q: [
            Document(page_content="b1", metadata={"chunk_id": "b1", "doc_id": "d1"}),
        ])
        merged = hybrid_retrieve("q", v, b, k=5)
        assert len(merged) == 1
        assert merged[0].metadata["chunk_id"] == "b1"

    def test_both_empty_returns_empty(self):
        """两侧都无结果 → 返回空列表（由上层 Gate 处理 NO_EVIDENCE 拒答）。"""
        from backend.rag.retrieval.hybrid import hybrid_retrieve
        v = SimpleNamespace(retrieve=lambda q, k=5, doc_ids=None, metadata_filter=None, expanded_queries=None: [])
        b = SimpleNamespace(invoke=lambda q: [])
        merged = hybrid_retrieve("q", v, b, k=5)
        assert merged == []


# =====================================================
# ChunkLevelRetriever 降级链
# =====================================================

def _make_retriever(doc_db, chunk_retriever, bm25):
    from backend.rag.retrieval.retrievers import ChunkLevelRetriever
    return ChunkLevelRetriever(
        doc_db=doc_db, vectordb=None,
        chunk_retriever=chunk_retriever, bm25=bm25, person_index={},
    )


class TestChunkLevelFallback:
    def test_stage2_empty_falls_back_to_neighbor_expansion(self):
        """Stage 2 无结果 → Neighbor Expansion 用 doc 级检索拉全文（fallback 可观测）。"""
        doc_db = SimpleNamespace(
            similarity_search=lambda q, k=5, filter=None: [
                Document(page_content="doc", metadata={"doc_id": "d1"}),
            ],
            get=lambda where: {
                "documents": ["全文内容一", "全文内容二"],
                "metadatas": [
                    {"doc_id": "d1", "chunk_id": "d1c1"},
                    {"doc_id": "d1", "chunk_id": "d1c2"},
                ],
            },
        )
        chunk_retriever = SimpleNamespace(
            retrieve=lambda q, k=5, doc_ids=None, metadata_filter=None, expanded_queries=None: [],
        )
        bm25 = SimpleNamespace(invoke=lambda q: [])
        r = _make_retriever(doc_db, chunk_retriever, bm25)
        r.k = 5

        docs = r._get_relevant_documents("查不到的问题")
        assert len(docs) == 2
        assert {d.metadata["chunk_id"] for d in docs} == {"d1c1", "d1c2"}

    def test_stage2_empty_retries_with_synonym_expansion(self):
        """首查无扩展且空召回 → 同义词扩展重试命中（口语化 query 降级路径）。

        "发欧洲大概要多少天"类口语 query 首查被阈值全过滤时，重试带
        "几天/多久/时效"变体应能召回；首查与重试的 expanded_queries
        参数序列留痕断言（None → 变体列表）。
        """
        calls: list = []

        def chunk_retrieve(q, k=5, doc_ids=None, metadata_filter=None, expanded_queries=None):
            calls.append(expanded_queries)
            if expanded_queries:  # 仅带变体的重试命中（模拟首查阈值全过滤）
                return [Document(page_content="欧洲时效", metadata={
                    "chunk_id": "c1", "doc_id": "d1"})]
            return []

        doc_db = SimpleNamespace(similarity_search=lambda q, k=5, filter=None: [])
        bm25 = SimpleNamespace(invoke=lambda q: [])
        r = _make_retriever(doc_db, SimpleNamespace(retrieve=chunk_retrieve), bm25)
        r.k = 5

        docs = r._get_relevant_documents("发欧洲大概要多少天")
        assert len(docs) == 1
        assert docs[0].metadata["chunk_id"] == "c1"
        assert calls[0] is None  # 首查无扩展
        variant_calls = [c for c in calls if c]
        # 首查空召回后必须出现带同义词变体的重试调用（enhanced dense +
        # 内部 fallback 会多次触达 retriever，按行为断言而非调用序号）
        assert variant_calls, f"重试未携带同义词变体: {calls!r}"
        assert any("几天" in v for v in variant_calls[0])

    def test_stage2_first_hit_skips_synonym_retry(self):
        """首查有结果 → 不触发重试（正常请求零额外开销）。"""
        calls: list = []

        def chunk_retrieve(q, k=5, doc_ids=None, metadata_filter=None, expanded_queries=None):
            calls.append(expanded_queries)
            return [Document(page_content="正常召回", metadata={
                "chunk_id": "c1", "doc_id": "d1"})]

        doc_db = SimpleNamespace(similarity_search=lambda q, k=5, filter=None: [])
        bm25 = SimpleNamespace(invoke=lambda q: [])
        r = _make_retriever(doc_db, SimpleNamespace(retrieve=chunk_retrieve), bm25)
        r.k = 5

        docs = r._get_relevant_documents("退货政策是什么")
        assert len(docs) == 1
        assert len(calls) == 1  # 只有首查一次

    def test_parent_lookup_failure_keeps_original_docs(self):
        """parent_lookup 抛异常 → 返回原检索结果（降级不丢结果，有日志留痕）。"""
        from backend.rag.retrieval.retrievers import attach_parent_context
        leaf = Document(page_content="leaf", metadata={
            "chunk_id": "l1", "granularity": "leaf", "parent_chunk_id": "p1",
        })

        def boom(ids):
            raise RuntimeError("db down")
        result = attach_parent_context([leaf], boom)
        assert len(result) == 1
        assert result[0].metadata["chunk_id"] == "l1"


class TestKbIdFallback:
    """fix f17：KBRouter 推断 kb_id 失配（路由到无文档的 KB）时，
    Stage 1 0 命中必须放宽 kb_id 重试，不得全量拒答。"""

    def test_wrong_kb_id_relaxed_to_cross_kb(self):
        """filter={kb_id: policy_finance, doc_type: financial} 但文档在
        policy_general → 放宽 kb_id 后 Stage 2 用剩余条件召回到结果。"""
        from backend.core.request_context import RequestContext
        from backend.rag.context import RagRequestState, set_context, clear_context

        set_context(RagRequestState(
            metadata_filter={"kb_id": "policy_finance", "doc_type": "financial"},
            query="员工出差报销需要提交哪些材料？",
        ))
        try:
            seen_filters = []

            def fake_retrieve(q, k=5, doc_ids=None, metadata_filter=None,
                              expanded_queries=None):
                seen_filters.append(metadata_filter)
                # 模拟：带 kb_id=policy_finance 时无命中，放宽后有命中
                if metadata_filter and metadata_filter.get("kb_id"):
                    return []
                return [Document(page_content="c1", metadata={
                    "chunk_id": "d1c1", "doc_id": "d1", "kb_id": "policy_general",
                })]

            doc_db = SimpleNamespace(
                similarity_search=lambda q, k=5, filter=None: [],  # Stage 1 0 命中
            )
            chunk_retriever = SimpleNamespace(retrieve=fake_retrieve)
            bm25 = SimpleNamespace(invoke=lambda q: [])
            r = _make_retriever(doc_db, chunk_retriever, bm25)
            r.k = 5

            docs = r._get_relevant_documents("员工出差报销需要提交哪些材料？")
            assert len(docs) == 1, "放宽 kb_id 后应召回到跨 KB 结果"
            assert docs[0].metadata["chunk_id"] == "d1c1"
            # Stage 2 实际用的 filter 不得再含 kb_id/$or，但保留 doc_type 收窄
            assert seen_filters, "Stage 2 未执行"
            last = seen_filters[-1] or {}
            assert "kb_id" not in last and "$or" not in last
            assert last.get("doc_type") == "financial"
        finally:
            clear_context()

    def test_cross_kb_fallback_excludes_test_kb(self):
        """跨库兜底禁入：放宽 kb_id 后召回到 rag_test_kb 文档 → 剔除不返回。

        f17"宁跨 KB 召回"不适用于测试/评测库——虚构内容经兜底对客输出
        等同泄漏；宁可空结果走拒答，不返回禁入库内容。
        """
        from backend.rag.context import RagRequestState, set_context, clear_context

        set_context(RagRequestState(
            metadata_filter={"kb_id": "cs_faq"},
            query="发欧洲大概要多少天？",
        ))
        try:
            def fake_retrieve(q, k=5, doc_ids=None, metadata_filter=None,
                              expanded_queries=None):
                if metadata_filter and metadata_filter.get("kb_id"):
                    return []
                return [Document(page_content="虚构测试内容", metadata={
                    "chunk_id": "t1", "doc_id": "d1", "kb_id": "rag_test_kb",
                })]

            doc_db = SimpleNamespace(
                similarity_search=lambda q, k=5, filter=None: [],
            )
            r = _make_retriever(
                doc_db, SimpleNamespace(retrieve=fake_retrieve),
                SimpleNamespace(invoke=lambda q: []),
            )
            r.k = 5

            docs = r._get_relevant_documents("发欧洲大概要多少天？")
            assert docs == [], "禁入库文档不得经跨库兜底返回"
        finally:
            clear_context()

    def test_cross_kb_fallback_exclusion_can_be_disabled(self, monkeypatch):
        """kill-switch 关闭 → 一键回滚旧行为（跨库兜底返回 rag_test_kb 文档）。"""
        monkeypatch.setenv("CROSS_KB_FALLBACK_EXCLUDE_TEST", "false")
        from backend.rag.context import RagRequestState, set_context, clear_context

        set_context(RagRequestState(
            metadata_filter={"kb_id": "cs_faq"},
            query="发欧洲大概要多少天？",
        ))
        try:
            def fake_retrieve(q, k=5, doc_ids=None, metadata_filter=None,
                              expanded_queries=None):
                if metadata_filter and metadata_filter.get("kb_id"):
                    return []
                return [Document(page_content="旧路径可回滚", metadata={
                    "chunk_id": "t1", "doc_id": "d1", "kb_id": "rag_test_kb",
                })]

            doc_db = SimpleNamespace(
                similarity_search=lambda q, k=5, filter=None: [],
            )
            r = _make_retriever(
                doc_db, SimpleNamespace(retrieve=fake_retrieve),
                SimpleNamespace(invoke=lambda q: []),
            )
            r.k = 5

            docs = r._get_relevant_documents("发欧洲大概要多少天？")
            assert len(docs) == 1, "名单置空后应恢复跨库兜底旧行为"
        finally:
            clear_context()

    def test_fallback_exclusion_is_attribute_driven(self, monkeypatch):
        """禁入名单由 audience 标签推导（单一来源）：新建 test 库自动被挡，
        未打标签的历史库不误伤；kill-switch 关闭时全部放行。"""
        from backend.config import knowledge_base as kbmod
        monkeypatch.setattr(kbmod, "KNOWLEDGE_BASES", {
            "cs_faq": {"audience": "customer"},
            "some_new_test_kb": {"audience": "test"},
            "legacy_untagged_kb": {},  # 未打标签：不误伤
        })
        assert kbmod.cross_kb_fallback_excluded() == ["some_new_test_kb"]
        monkeypatch.setenv("CROSS_KB_FALLBACK_EXCLUDE_TEST", "false")
        assert kbmod.cross_kb_fallback_excluded() == []


    # ── 主体属性 → 可见知识库集合（检索侧授权单一来源）──

    def test_customer_sees_only_customer_kbs(self):
        from backend.config.knowledge_base import authorized_kbs
        allowed = authorized_kbs("customer")
        assert "cs_faq" in allowed and "cs_policy" in allowed
        assert "policy_hr" not in allowed and "rag_test_kb" not in allowed
        assert "policy_general" not in allowed

    def test_employee_scoped_by_department_matrix(self):
        from backend.config.knowledge_base import authorized_kbs
        hr_view = authorized_kbs("employee", "hr")
        assert "policy_hr" in hr_view and "rag_test_kb" not in hr_view
        # biz_inventory 的 owner_depts 不含 hr → 不可见
        assert "biz_inventory" not in hr_view
        # "all" 库所有人可见
        assert "policy_general" in hr_view

    def test_employee_all_dept_sees_all_owner_kbs(self):
        from backend.config.knowledge_base import authorized_kbs
        admin_view = authorized_kbs("employee", "admin")
        # owner_depts=["all"] 的库对任何部门可见
        assert "policy_general" in admin_view
        assert "policy_hr" not in admin_view

    def test_employee_without_department_failsafe(self):
        """员工未带部门 → 仅见 owner_depts=["all"] 的库（fail-safe）。"""
        from backend.config.knowledge_base import authorized_kbs
        allowed = authorized_kbs("employee", "")
        assert allowed == ["policy_general"]

    def test_undeclared_subject_returns_none(self):
        """未声明主体 → None（授权未启用，调用方保持旧行为）。"""
        from backend.config.knowledge_base import authorized_kbs
        assert authorized_kbs("") is None
        assert authorized_kbs("unknown_type") is None

    def test_scope_kb_filter_strips_disallowed_kb(self):
        """filter 收敛：越界 kb 限定被剥离（保留其他条件），授权内原样。"""
        from backend.rag.retrieval.retrievers import _scope_kb_filter
        f = {"kb_id": "policy_hr", "doc_type": "faq"}
        assert _scope_kb_filter(f, {"cs_faq", "cs_policy"}) == {"doc_type": "faq"}
        f2 = {"kb_id": "cs_faq"}
        assert _scope_kb_filter(f2, {"cs_faq"}) is f2  # 全部授权 → 原样
        f3 = {"doc_type": "faq"}
        assert _scope_kb_filter(f3, {"cs_faq"}) is f3  # 无 kb 限定 → 原样

    def test_customer_subject_explicit_disallowed_kb_filtered(self):
        """customer 主体 + LLM 显式选了越界库（policy_hr）→ 越界内容被剔除。

        客服流量漏进主图时，LLM 可能用 search_knowledge_tool 选任意 kb；
        显式 kb 选择同样受主体授权约束（路由只提议，属性裁决）。
        """
        from backend.core.request_context import RequestContext
        from backend.rag.context import RagRequestState, set_context, clear_context

        set_context(RagRequestState(
            metadata_filter={"kb_id": "policy_hr"},
            query="薪资制度是什么",
            identity=RequestContext(subject_type="customer"),
        ))
        try:
            doc_db = SimpleNamespace(
                similarity_search=lambda q, k=15, filter=None: [
                    Document(page_content="薪资", metadata={
                        "doc_id": "d1", "kb_id": "policy_hr"}),
                    Document(page_content="faq", metadata={
                        "doc_id": "d2", "kb_id": "cs_faq"}),
                ],
            )

            def chunk_retrieve(q, k=5, doc_ids=None, metadata_filter=None, expanded_queries=None):
                # Stage 2 对 doc_ids 集合内的 doc 召回 chunk：模拟两库都有
                return [
                    Document(page_content="薪资chunk", metadata={
                        "chunk_id": "c1", "doc_id": "d1", "kb_id": "policy_hr"}),
                    Document(page_content="faqchunk", metadata={
                        "chunk_id": "c2", "doc_id": "d2", "kb_id": "cs_faq"}),
                ]

            r = _make_retriever(
                doc_db, SimpleNamespace(retrieve=chunk_retrieve),
                SimpleNamespace(invoke=lambda q: []),
            )
            r.k = 5

            docs = r._get_relevant_documents("薪资制度是什么")
            assert {d.metadata["kb_id"] for d in docs} == {"cs_faq"}, \
                "customer 主体不得拿到越界库内容"
        finally:
            clear_context()

    def test_employee_subject_department_matrix_at_retrieval(self):
        """employee(h dept=hr) + 宽搜 → owner_depts 矩阵外的库文档被剔除。"""
        from backend.core.request_context import RequestContext
        from backend.rag.context import RagRequestState, set_context, clear_context

        set_context(RagRequestState(
            metadata_filter={},
            query="库存怎么盘点",
            identity=RequestContext(subject_type="employee", department="hr"),
        ))
        try:
            doc_db = SimpleNamespace(
                similarity_search=lambda q, k=15, filter=None: [
                    Document(page_content="盘点", metadata={
                        "doc_id": "d1", "kb_id": "biz_inventory"}),
                    Document(page_content="通用", metadata={
                        "doc_id": "d2", "kb_id": "policy_general"}),
                ],
            )

            def chunk_retrieve(q, k=5, doc_ids=None, metadata_filter=None, expanded_queries=None):
                return [
                    Document(page_content="盘点chunk", metadata={
                        "chunk_id": "c1", "doc_id": "d1", "kb_id": "biz_inventory"}),
                    Document(page_content="通用chunk", metadata={
                        "chunk_id": "c2", "doc_id": "d2", "kb_id": "policy_general"}),
                ]

            r = _make_retriever(
                doc_db, SimpleNamespace(retrieve=chunk_retrieve),
                SimpleNamespace(invoke=lambda q: []),
            )
            r.k = 5

            docs = r._get_relevant_documents("库存怎么盘点")
            assert {d.metadata["kb_id"] for d in docs} == {"policy_general"}, \
                "hr 部门员工不得拿到 owner_depts 矩阵外的库内容"
        finally:
            clear_context()

    def test_retriever_filters_restricted_chunks_before_return(self):
        """KB 授权通过后仍须执行文档 permission_scope 收口。"""
        from backend.core.request_context import RequestContext
        from backend.rag.context import RagRequestState, set_context, clear_context

        set_context(RagRequestState(
            metadata_filter={},
            query="制度查询",
            identity=RequestContext(
                subject_type="employee",
                department="hr",
                permissions=None,
            ),
        ))
        try:
            general = Document(
                page_content="通用制度",
                metadata={
                    "chunk_id": "c-general",
                    "doc_id": "d-general",
                    "kb_id": "policy_general",
                    "permission_scope": "general",
                },
            )
            restricted = Document(
                page_content="财务制度",
                metadata={
                    "chunk_id": "c-finance",
                    "doc_id": "d-finance",
                    "kb_id": "policy_general",
                    "permission_scope": "finance_restricted",
                },
            )
            r = _make_retriever(
                SimpleNamespace(similarity_search=lambda q, k=15, filter=None: []),
                SimpleNamespace(
                    retrieve=lambda q, k=5, doc_ids=None, metadata_filter=None,
                    expanded_queries=None: [general, restricted],
                ),
                SimpleNamespace(invoke=lambda q: []),
            )
            r.k = 5

            docs = r._get_relevant_documents("制度查询")
            assert [doc.metadata["chunk_id"] for doc in docs] == ["c-general"]
        finally:
            clear_context()

    def test_or_scope_filter_also_relaxed(self):
        """$or 形式的多 KB 候选同样在 0 命中时被放宽。"""
        from backend.rag.context import RagRequestState, set_context, clear_context

        set_context(RagRequestState(
            metadata_filter={"$or": [{"kb_id": "policy_finance"},
                                     {"kb_id": "policy_general"}]},
        ))
        try:
            doc_db = SimpleNamespace(
                similarity_search=lambda q, k=5, filter=None: [],
            )
            hit = Document(page_content="c1", metadata={
                "chunk_id": "d1c1", "doc_id": "d1",
            })
            chunk_retriever = SimpleNamespace(
                retrieve=lambda q, k=5, doc_ids=None, metadata_filter=None,
                expanded_queries=None: [] if (metadata_filter and "$or" in metadata_filter)
                else [hit],
            )
            bm25 = SimpleNamespace(invoke=lambda q: [])
            r = _make_retriever(doc_db, chunk_retriever, bm25)
            r.k = 5

            docs = r._get_relevant_documents("报销流程")
            assert len(docs) == 1
        finally:
            clear_context()


# =====================================================
# AdaptiveRetriever Context Expansion
# =====================================================

class _FakeBaseRetriever(BaseRetriever):
    """最小 BaseRetriever 实现（pydantic 校验要求真实子类）。"""

    docs: list = []

    def _get_relevant_documents(self, query: str, *, run_manager=None):
        return list(self.docs)


class TestAdaptiveRetriever:
    def test_cluster_triggers_context_expansion(self):
        """命中集中在少数文档 → Context Expansion 拉全文（替换 chunk，不前置）。"""
        from backend.rag.retrieval.retrievers import AdaptiveRetriever
        base = _FakeBaseRetriever(docs=[
            Document(page_content="c1", metadata={"doc_id": "d1"}),
            Document(page_content="c2", metadata={"doc_id": "d1"}),
        ])
        doc_db = SimpleNamespace(get=lambda where: {
            "documents": ["文档全文"],
            "metadatas": [{"doc_id": "d1"}],
        })
        ar = AdaptiveRetriever(base_retriever=base, doc_db=doc_db)
        docs = ar._get_relevant_documents("q")
        # 全文替换同 doc 的所有 chunk（不再前置导致数量膨胀）
        assert len(docs) == 1
        assert docs[0].page_content == "文档全文"

    def test_dispersed_keeps_chunks_only(self):
        """命中分散在多个文档 → 跳过 Expansion（避免上下文污染）。"""
        from backend.rag.retrieval.retrievers import AdaptiveRetriever
        base = _FakeBaseRetriever(docs=[
            Document(page_content="c1", metadata={"doc_id": "d1"}),
            Document(page_content="c2", metadata={"doc_id": "d2"}),
        ])
        doc_db = SimpleNamespace(get=lambda where: {
            "documents": [], "metadatas": [],
        })
        ar = AdaptiveRetriever(base_retriever=base, doc_db=doc_db)
        docs = ar._get_relevant_documents("q")
        assert len(docs) == 2
        assert all("全文" not in d.page_content for d in docs)
