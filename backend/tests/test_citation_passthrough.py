"""阶段 4：citation 结构化透出单测

锁定 sources 穿过三层的完整链路:
  chain._last_sources → pipeline.last_answer_meta → rag-server /ask 响应
  （远端消费方再经 RAGServiceProxy.last_answer_meta 拿到，已有测试覆盖）
"""
import pytest
from fastapi.testclient import TestClient

from backend.rag.pipeline import RAGPipeline

_SOURCES = [
    {"index": 1, "filename": "fba_sop.pdf", "doc_type": "policy",
     "type_label": "规范", "score": 0.82},
    {"index": 2, "filename": "faq_return.md", "doc_type": "faq",
     "type_label": "FAQ", "score": 0.76},
]


def _bare_pipeline(chain) -> RAGPipeline:
    """跳过重资源 __init__，只构造 _snapshot_answer_meta 所需的最小实例。"""
    p = RAGPipeline.__new__(RAGPipeline)
    p.lc_chain = chain
    return p


class TestPipelineMetaSnapshot:
    def test_sources_included_structured(self):
        class FakeChain:
            _last_meta = {"confidence": 0.9, "can_answer": True}
            _last_sources = _SOURCES

        p = _bare_pipeline(FakeChain())
        p._snapshot_answer_meta()
        assert p.last_answer_meta["source_count"] == 2
        assert p.last_answer_meta["sources"] == _SOURCES
        assert p.last_answer_meta["confidence"] == 0.9

    def test_sources_truncated_to_8(self):
        class FakeChain:
            _last_meta = {}
            _last_sources = [{"index": i, "filename": f"d{i}.pdf"} for i in range(20)]

        p = _bare_pipeline(FakeChain())
        p._snapshot_answer_meta()
        assert len(p.last_answer_meta["sources"]) == 8
        assert p.last_answer_meta["source_count"] == 20

    def test_no_sources_meta_still_works(self):
        class FakeChain:
            _last_meta = {"confidence": 0.3}
            _last_sources = []

        p = _bare_pipeline(FakeChain())
        p._snapshot_answer_meta()
        assert p.last_answer_meta["sources"] == []
        assert p.last_answer_meta["source_count"] == 0

    def test_chain_without_sources_attr(self):
        """旧 chain 无 _last_sources 属性时不炸（向后兼容）。"""
        class OldChain:
            _last_meta = {"confidence": 0.5}

        p = _bare_pipeline(OldChain())
        p._snapshot_answer_meta()
        assert "sources" not in p.last_answer_meta


class TestRagServerAskPassthrough:
    @pytest.fixture()
    def client(self, monkeypatch):
        import backend.services.rag_server as server

        class FakePipeline:
            last_answer_meta = {"confidence": 0.9, "sources": _SOURCES}

            def ask(self, question, session_id="default", kb_id="default",
                   kb_ids=None, subject_type="", department=""):
                return "答案"

        monkeypatch.setattr(server, "_kick_init", lambda: None)
        monkeypatch.setattr(server, "_get_pipeline", lambda: FakePipeline())
        with TestClient(server.app) as c:
            yield c

    def test_ask_response_contains_sources(self, client):
        resp = client.post("/ask", json={"question": "退货流程"})
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["answer"] == "答案"
        assert payload["meta"]["sources"] == _SOURCES
        assert payload["meta"]["confidence"] == 0.9
