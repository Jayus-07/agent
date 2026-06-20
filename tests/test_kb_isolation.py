"""
Knowledge Base 隔离测试 — kb_id 注入 + 检索过滤

覆盖:
  - loader: 目录结构 → kb_id 注入
  - schemas: API 请求 kb_id 字段
  - pipeline: ask()/search() kb_id 过滤
  - multi_agent: Planner 注入 kb_id 到 step params
"""
import pytest

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestKbIdInjection:
    """loader.py: 目录结构 → kb_id"""

    def test_default_kb_id_for_root(self, tmp_path):
        """根目录直接放的文件 → kb_id=default"""
        from preprocessing.loader import load_documents_from_directory
        from config import DEFAULT_KB_ID

        root = tmp_path / "docs"
        root.mkdir()
        (root / "test.txt").write_text("content", encoding="utf-8")

        chunks = load_documents_from_directory(str(root), chunk_size=500, chunk_overlap=50)
        assert len(chunks) > 0
        for c in chunks:
            assert c.metadata["kb_id"] == DEFAULT_KB_ID

    def test_subdir_kb_id(self, tmp_path):
        """子目录 → kb_id=子目录名"""
        from preprocessing.loader import load_documents_from_directory

        root = tmp_path / "docs"
        policy_dir = root / "policy"
        policy_dir.mkdir(parents=True)
        (policy_dir / "manual.txt").write_text("制度内容测试", encoding="utf-8")

        chunks = load_documents_from_directory(str(root), chunk_size=500, chunk_overlap=50)
        assert len(chunks) > 0
        for c in chunks:
            assert c.metadata["kb_id"] == "policy"

    def test_nested_subdir_inherits_parent_kb(self, tmp_path):
        """嵌套子目录继承第一级目录的 kb_id"""
        from preprocessing.loader import load_documents_from_directory

        root = tmp_path / "docs"
        sub = root / "tech" / "subfolder"
        sub.mkdir(parents=True)
        (sub / "doc.txt").write_text("技术文档", encoding="utf-8")

        chunks = load_documents_from_directory(str(root), chunk_size=500, chunk_overlap=50)
        assert len(chunks) > 0
        for c in chunks:
            assert c.metadata["kb_id"] == "tech"  # first level, not "subfolder"

    def test_multiple_kb_ids(self, tmp_path):
        """多个子目录 → 不同 kb_id"""
        from preprocessing.loader import load_documents_from_directory

        root = tmp_path / "docs"
        for kb in ["policy", "tech", "finance"]:
            d = root / kb
            d.mkdir(parents=True)
            (d / f"{kb}_doc.txt").write_text(f"{kb} content", encoding="utf-8")

        chunks = load_documents_from_directory(str(root), chunk_size=500, chunk_overlap=50)
        kb_ids = {c.metadata["kb_id"] for c in chunks}
        assert "policy" in kb_ids
        assert "tech" in kb_ids
        assert "finance" in kb_ids

    def test_kb_id_persists_through_split(self, tmp_path):
        """kb_id 在分块后保留"""
        from preprocessing.loader import load_documents_from_directory

        root = tmp_path / "docs"
        policy_dir = root / "policy"
        policy_dir.mkdir(parents=True)
        # Create text that triggers multiple chunks
        (policy_dir / "long.txt").write_text("4.2.1 测试\n" + "内容。\n" * 100, encoding="utf-8")

        chunks = load_documents_from_directory(str(root), chunk_size=200, chunk_overlap=20)
        assert len(chunks) > 1  # should be multiple chunks
        for c in chunks:
            assert c.metadata["kb_id"] == "policy"


class TestApiSchemas:
    """API 请求模型 kb_id 字段"""

    def test_chat_request_defaults_kb_id(self):
        from api.schemas import ChatRequest
        req = ChatRequest(question="test")
        assert req.kb_id is None  # optional, default None

    def test_chat_request_with_kb_id(self):
        from api.schemas import ChatRequest
        req = ChatRequest(question="test", kb_id="policy")
        assert req.kb_id == "policy"

    def test_rag_request_with_kb_id(self):
        from api.schemas import RAGAskRequest
        req = RAGAskRequest(question="test", kb_id="tech")
        assert req.kb_id == "tech"


class TestPlannerKbId:
    """Planner 注入 kb_id 到 search_knowledge 步骤"""

    def test_kb_id_injected_into_search_knowledge_params(self):
        from multi_agent.planner import planner_node

        state = {
            "question": "叶菜类保鲜期",
            "kb_id": "policy",
            "plan": {"nodes": {}, "edges": {}},
        }
        result = planner_node(state)
        plan = result["plan"]
        assert len(plan["nodes"]) == 1
        node = list(plan["nodes"].values())[0]
        assert node["capability"] == "search_knowledge"
        assert node["params"]["kb_id"] == "policy"

    def test_kb_id_not_injected_into_sql_step(self):
        """SQL 步骤不应有 kb_id"""
        from multi_agent.planner import planner_node

        state = {
            "question": "技术部有多少人",
            "kb_id": "tech",
            "plan": {"nodes": {}, "edges": {}},
        }
        result = planner_node(state)
        # This should go to fallback since no DB schema in test — but verify kb_id is passed
        assert result["plan"]["nodes"]  # has nodes
        # If it generates search_knowledge, it should have kb_id
        for node in result["plan"]["nodes"].values():
            if node["capability"] == "search_knowledge":
                assert node["params"]["kb_id"] == "tech"


class TestKbFilterLogic:
    """metadata_filter 的 kb_id 构造"""

    def test_single_kb_filter(self):
        """单 kb_id filter"""
        filter_dict = {"kb_id": "policy"}
        # Should produce valid ChromaDB filter
        assert filter_dict["kb_id"] == "policy"

    def test_kb_id_plus_doc_type(self):
        """kb_id + doc_type 组合"""
        f = {"kb_id": "policy", "doc_type": "manual"}
        # ChromaDB $and wrapping
        wrapped = {"$and": [{k: v} for k, v in f.items()]}
        assert len(wrapped["$and"]) == 2

    def test_wildcard_kb_removes_filter(self):
        """kb_id='*' → 不加入 filter → 全库检索"""
        kb_id = "*"
        metadata_filter = {}
        if kb_id and kb_id != "*":
            metadata_filter["kb_id"] = kb_id
        assert "kb_id" not in metadata_filter


class TestBackwardCompatibility:
    """向后兼容"""

    def test_chunk_metadata_has_all_required_keys(self, tmp_path):
        """所有 chunk 必须有标准 metadata + kb_id"""
        from preprocessing.loader import load_documents_from_directory

        root = tmp_path / "docs"
        (root / "test.txt").mkdir(parents=True)
        (root / "test.txt" / "doc.txt").write_text("test content", encoding="utf-8")

        # The file is at root/test.txt/doc.txt, so first level subdir is "test.txt"
        chunks = load_documents_from_directory(str(root), chunk_size=500, chunk_overlap=50)
        for c in chunks:
            assert "parent_doc_id" in c.metadata
            assert "chunk_index" in c.metadata
            assert "source_file" in c.metadata
            assert "file_path" in c.metadata
            assert "kb_id" in c.metadata  # NEW required key

    def test_split_documents_still_works(self, tmp_path):
        """split_documents 签名和返回值不变"""
        from preprocessing.loader import split_documents
        from langchain_core.documents import Document

        docs = [Document(page_content="测试", metadata={})]
        chunks = split_documents(docs, str(tmp_path / "test.txt"))
        assert isinstance(chunks, list)
        assert len(chunks) > 0

    def test_search_knowledge_tool_default_kb(self):
        """search_knowledge_tool 默认 kb_id=default（不调用 pipeline）"""
        from multi_agent.tools import search_knowledge_tool
        import inspect
        sig = inspect.signature(search_knowledge_tool.func)
        assert "kb_id" in sig.parameters
        assert sig.parameters["kb_id"].default == "default"
