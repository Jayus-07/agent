"""统一请求上下文重构（2026-09-14）回归：RAG 运行态的身份组合借读。

锁定三个契约：
  1. _prepare_context 以调用方声明回填身份（CS subject_type="customer" →
     pipeline.ask kwargs 链路保持，检索授权行为零变化）
  2. 图路径 bind() 注入的权威实例被 _prepare_context 复用（组合非复制，
     orchestration → RAG 转换点唯一）
  3. metadata_filter 为空时提前返回、不触碰身份——与旧实现
     "未 set 即默认空身份 → 授权未启用"语义一致
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
    import backend.rag.routing.kb_router as kb_router_mod

    class FakeRouter:
        def route(self, question):
            return {"candidates": [{"kb_id": k} for k in candidates]}

    monkeypatch.setattr(kb_router_mod, "KBRouter", FakeRouter)


def _stub_analyzer(monkeypatch, qf=None):
    import backend.rag.retrieval.query_analyzer as qa_mod

    class FakeParsed:
        intent = "fact"

        def to_metadata_filter(self):
            return dict(qf or {})

    class FakeAnalyzer:
        def analyze(self, question):
            return FakeParsed()

    monkeypatch.setattr(qa_mod, "QueryAnalyzer", FakeAnalyzer)


class TestPrepareContextIdentity:
    def test_kwargs_declare_subject_on_default_identity(self, monkeypatch):
        """直连路径（无图 bind）：kwargs 声明写入默认身份实例（CS 链路形状）。"""
        _stub_router(monkeypatch, ["cs_faq"])
        _stub_analyzer(monkeypatch)
        RAGPipeline._prepare_context(
            object(), "cs_faq", "退货政策",
            subject_type="customer", department="",
        )
        identity = rag_context.get_context().identity
        assert identity.subject_type == "customer"
        assert identity.department == ""
        # 身份是权威实例（组合借读），运行态只持引用
        from backend.core.request_context import RequestContext
        assert isinstance(identity, RequestContext)

    def test_binds_graph_instance_not_copy(self, monkeypatch):
        """图路径：bind() 注入的权威实例被复用（identity is 同一对象），
        kwargs 声明回填到同一实例——转换点唯一，无第二份拷贝。"""
        from backend.orchestration.request_context import RequestContext

        graph_ctx = RequestContext(
            session_id="s1", user_id="u1", department="hr")
        graph_ctx.bind()
        _stub_router(monkeypatch, ["policy_general"])
        _stub_analyzer(monkeypatch)
        RAGPipeline._prepare_context(
            object(), "*", "库存盘点",
            subject_type="employee", department="hr",
        )
        state = rag_context.get_context()
        assert state.identity is graph_ctx
        assert state.identity.subject_type == "employee"
        assert state.identity.department == "hr"

    def test_empty_filter_leaves_identity_untouched(self, monkeypatch):
        """mf 为空提前返回：身份保持默认空值（授权未启用，旧行为）。"""
        _stub_router(monkeypatch, [])  # 无候选 → mf 空
        _stub_analyzer(monkeypatch, {})
        RAGPipeline._prepare_context(
            object(), "*", "无法路由的问题",
            subject_type="customer", department="",
        )
        identity = rag_context.get_context().identity
        assert identity.subject_type == ""
        assert identity.department == ""
