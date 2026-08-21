"""显式 kb_id 与 KBRouter 推断 filter 冲突 — 回归测试。

背景（e2e 发现）：pipeline.ask(kb_id="rag_test_kb") 时 _prepare_context 把
KBRouter 关键词推断的 {"$or": [{"kb_id": "policy_finance"}, ...]} 与显式
mf["kb_id"]="rag_test_kb" 同时放进 metadata_filter。Chroma 顶层键为隐式
AND —— 两条件互斥，召回必为空 → 全量拒答。

修复行为：
  1. 显式 kb_id（非 */default）时不再合并 Router 的 $or 候选
  2. 显式 kb_id 覆盖任何同名残留键，顶层仅保留该 kb_id（kb 维度）
  3. 未显式指定时保留 Router $or 行为不变
  4. QueryAnalyzer 的 doc_type/business_domain 收窄不受影响
"""
import pytest

from backend.rag import context as rag_context
from backend.rag.pipeline import RAGPipeline


@pytest.fixture(autouse=True)
def _clear_context():
    rag_context.clear_context()
    yield
    rag_context.clear_context()


def _stub_router(monkeypatch, candidates):
    """让 KBRouter().route() 返回指定候选 KB。"""
    import backend.rag.routing.kb_router as kb_router_mod

    class FakeRouter:
        def route(self, question):
            return {"candidates": [{"kb_id": k} for k in candidates]}

    monkeypatch.setattr(kb_router_mod, "KBRouter", FakeRouter)


def _stub_analyzer(monkeypatch, qf):
    """让 QueryAnalyzer().analyze() 返回指定 metadata filter。"""
    import backend.rag.retrieval.query_analyzer as qa_mod

    class FakeParsed:
        intent = "fact"

        def to_metadata_filter(self):
            return dict(qf)

    class FakeAnalyzer:
        def analyze(self, question):
            return FakeParsed()

    monkeypatch.setattr(qa_mod, "QueryAnalyzer", FakeAnalyzer)


def _get_filter():
    ctx = rag_context.get_context()
    return (ctx.metadata_filter if ctx else None) or {}


class TestExplicitKbIdOverridesRouter:
    def test_explicit_kb_drops_router_or(self, monkeypatch):
        """显式 kb_id 时 Router 的 $or 不得出现在 filter 中。"""
        _stub_router(monkeypatch, ["policy_finance", "policy_general"])
        _stub_analyzer(monkeypatch, {})
        RAGPipeline._prepare_context(object(), "rag_test_kb", "出差餐补标准")
        mf = _get_filter()
        assert mf.get("kb_id") == "rag_test_kb"
        assert "$or" not in mf, f"显式 kb_id 与 $or 并存会互斥清空召回: {mf}"

    def test_explicit_kb_keeps_query_analyzer_narrowing(self, monkeypatch):
        """doc_type/business_domain 收窄与显式 kb_id 兼容共存。"""
        _stub_router(monkeypatch, ["policy_finance"])
        _stub_analyzer(monkeypatch, {"doc_type": "financial"})
        RAGPipeline._prepare_context(object(), "rag_test_kb", "报销标准")
        mf = _get_filter()
        assert mf.get("kb_id") == "rag_test_kb"
        assert "$or" not in mf
        assert mf.get("doc_type") == "financial"

    def test_wildcard_kb_keeps_router_or(self, monkeypatch):
        """kb_id='*' 时维持 Router $or 行为。"""
        _stub_router(monkeypatch, ["biz_inventory", "policy_general"])
        _stub_analyzer(monkeypatch, {})
        RAGPipeline._prepare_context(object(), "*", "库存盘点")
        mf = _get_filter()
        assert mf.get("$or") == [
            {"kb_id": "biz_inventory"}, {"kb_id": "policy_general"},
        ]
        assert "kb_id" not in mf

    def test_default_kb_keeps_router_or(self, monkeypatch):
        _stub_router(monkeypatch, ["policy_general"])
        _stub_analyzer(monkeypatch, {})
        RAGPipeline._prepare_context(object(), "default", "退货政策")
        mf = _get_filter()
        assert mf.get("$or") is not None or mf.get("kb_id") == "policy_general"
