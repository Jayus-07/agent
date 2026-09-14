"""2.2 C1 三策略虚拟 parent 补齐 — Step/Legal/QA 的 parent-child 检索修复。

契约：
- Legal：每 LEGAL_CLAUSES_PER_PARENT(默认 5) 条款挂一个虚拟 parent
- Step / QA：文档级单 parent
- leaf.parent_chunk_id 非空且指向真实存在的 parent chunk_id
- chunk_id 与既有 leaf id 无撞车（anchor 隔离）
"""
from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.chunking import (
    LegalChunkStrategy,
    QAChunkStrategy,
    StepChunkStrategy,
)


def _legal_ast(n_clauses: int) -> DocumentAST:
    clauses = [DocumentNode(type="paragraph", text=f"第{i}条 本条规定第{i}项的具体内容与执行标准。")
               for i in range(1, n_clauses + 1)]
    return DocumentAST(root=DocumentNode(type="section", text="", children=clauses))


def _step_ast() -> DocumentAST:
    secs = [DocumentNode(type="section", text=f"{cn}、准备阶段",
                         level=1,
                         children=[DocumentNode(type="paragraph", text=f"第{cn}阶段要做的事情说明。")])
            for cn in ("一", "二", "三")]
    return DocumentAST(root=DocumentNode(type="section", text="", children=secs))


def _qa_ast() -> DocumentAST:
    nodes = []
    for q in ("报销流程是什么？", "年假有几天？"):
        nodes.append(DocumentNode(type="qa_question", text=q))
        nodes.append(DocumentNode(type="qa_answer", text="按公司制度执行相关流程。"))
    return DocumentAST(root=DocumentNode(type="section", text="", children=nodes))


def _parent_ids(chunks):
    return {c.metadata["chunk_id"] for c in chunks if c.metadata["granularity"] == "parent"}


class TestLegalParents:

    def test_twelve_clauses_three_parents(self):
        chunks = LegalChunkStrategy().split(_legal_ast(12), "/tmp/contract.md")
        leaves = [c for c in chunks if c.metadata["granularity"] == "leaf"]
        parents = [c for c in chunks if c.metadata["granularity"] == "parent"]
        assert len(leaves) == 12
        assert len(parents) == 3  # ceil(12/5)

    def test_leaf_parent_links_resolve(self):
        chunks = LegalChunkStrategy().split(_legal_ast(7), "/tmp/contract.md")
        pids = _parent_ids(chunks)
        for c in chunks:
            if c.metadata["granularity"] == "leaf":
                assert c.metadata["parent_chunk_id"] in pids, "leaf 的 parent 必须真实存在"

    def test_parent_text_lists_clause_titles(self):
        chunks = LegalChunkStrategy().split(_legal_ast(5), "/tmp/contract.md")
        parent = next(c for c in chunks if c.metadata["granularity"] == "parent")
        assert "第1条" in parent.page_content and "第5条" in parent.page_content


class TestStepParents:

    def test_single_doc_level_parent(self):
        chunks = StepChunkStrategy().split(_step_ast(), "/tmp/sop.md")
        parents = [c for c in chunks if c.metadata["granularity"] == "parent"]
        assert len(parents) == 1
        leaves = [c for c in chunks if c.metadata["granularity"] == "leaf"]
        assert len(leaves) == 3
        for c in leaves:
            assert c.metadata["parent_chunk_id"] == parents[0].metadata["chunk_id"]
        # parent 文本汇总了各章节标题
        assert "一、准备阶段" in parents[0].page_content


class TestQAParents:

    def test_single_doc_level_parent(self):
        chunks = QAChunkStrategy().split(_qa_ast(), "/tmp/faq.md")
        parents = [c for c in chunks if c.metadata["granularity"] == "parent"]
        assert len(parents) == 1
        leaves = [c for c in chunks if c.metadata["granularity"] == "leaf"]
        assert len(leaves) == 2
        for c in leaves:
            assert c.metadata["parent_chunk_id"] == parents[0].metadata["chunk_id"]
        # parent 文本 = 问题列表
        assert "报销流程" in parents[0].page_content
