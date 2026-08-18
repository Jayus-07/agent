"""P1 并发隔离测试：请求级中间态（RequestContext / proxy ContextVar）互不串扰。

覆盖：
  1. proxy token 元数据：并发线程各自写入/读取，互不覆盖（模块级 dict 的串扰已被消除）
  2. RequestContext 决策中间态（meta）：并发线程各自 set/读，互不干扰
  3. get_context 惰性 set：未 set 时读写同一稳定实例（不丢写）
  4. ask 每请求新建 gate/corrector：并发/串行请求间 retry_count 等状态不串
  5. 同一 chain 实例并发 ask：各请求的 _last_meta 不串扰
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from langchain_core.documents import Document

from backend.rag.context import RequestContext, get_context, set_context


def _doc():
    return Document(page_content="x", metadata={
        "chunk_id": "c1", "doc_id": "d1", "doc_type": "general",
        "source_file": "d.md", "index": 1, "rerank_score": 0.8,
    })


def _gate_ok_docs():
    docs = [_doc()]
    docs[0].metadata["__evidence_gate_decision__"] = {
        "gate_passed": True, "gate_layer": "retrieval",
        "gate_score": 0.8, "gate_reason": "",
    }
    return docs


# =====================================================
# 1. proxy token 元数据隔离
# =====================================================

class TestProxyTokenIsolation:
    def test_token_meta_isolated_between_threads(self):
        """两线程各 set 不同 token，各读各的（ContextVar 按上下文隔离）。"""
        from backend.infra.llm import proxy as proxy_mod

        def worker(val):
            proxy_mod._last_call_meta_var.set({
                "prompt_tokens": val, "finish_reason": "stop",
            })
            time.sleep(0.05)  # 制造交叠窗口：若仍共享同一 dict 会被覆盖
            return proxy_mod._last_call_meta_var.get().get("prompt_tokens")

        with ThreadPoolExecutor(max_workers=2) as ex:
            f1 = ex.submit(worker, 100)
            f2 = ex.submit(worker, 200)
        assert f1.result() == 100
        assert f2.result() == 200


# =====================================================
# 2. RequestContext 决策中间态隔离
# =====================================================

class TestRequestContextIsolation:
    def test_meta_isolated_between_threads(self):
        """两线程各自 set_context 不同 meta，get_context 读到各自的。"""

        def worker(tag):
            set_context(RequestContext(meta={"tag": tag}))
            time.sleep(0.05)
            return get_context().meta["tag"]

        with ThreadPoolExecutor(max_workers=2) as ex:
            f1 = ex.submit(worker, "A")
            f2 = ex.submit(worker, "B")
        assert f1.result() == "A"
        assert f2.result() == "B"

    def test_get_context_lazy_set_is_stable(self):
        """未 set 时 get_context 惰性 set：读写同一实例（写不丢失）。"""
        ctx = get_context()
        ctx.meta = {"k": "v"}
        assert get_context().meta["k"] == "v"


# =====================================================
# 3. ask 每请求新建 gate/corrector
# =====================================================

def _stub_chain():
    from backend.rag.chain import RAGChain
    from backend.rag.citation import CitationFormatter
    from backend.rag.evidence_gate import EvidenceGateController
    from backend.rag.evidence_gate.self_correction import SelfCorrectionStrategy
    chain = RAGChain.__new__(RAGChain)
    chain.doc_db = None
    chain.vectordb = None
    chain.chunk_retriever = None
    chain.bm25 = None
    chain.person_index = {}
    chain._memory = None
    chain._last_sources = []
    chain.gate = EvidenceGateController()
    chain.corrector = SelfCorrectionStrategy()
    chain.formatter = CitationFormatter()
    return chain


class TestPerRequestStrategyInstances:
    def test_ask_creates_fresh_gate_and_corrector(self, monkeypatch):
        """ask 每请求新建 gate/corrector：复用实例的状态（如 retry_count）不跨请求。"""
        from types import SimpleNamespace
        from backend.observability.tracer import trace_collector
        import time as _time

        chain = _stub_chain()
        chain.chain = SimpleNamespace(invoke=lambda inp: {
            "input": inp, "context": _gate_ok_docs(),
            "answer": "答案。<!--META{\"can_answer\":true,\"citations\":[],\"confidence\":0.9}-->",
        })
        monkeypatch.setattr(chain, "_evaluate", lambda answer, ctx: answer)

        trace = trace_collector.start("iso-strategy", session_id="t1")
        try:
            trace_collector.start_span("root", parent_id=None, name="test", type="agent")
        except RuntimeError:
            pass
        monkeypatch.setattr(chain, "_start", lambda q, sid: (trace, _time.time()))

        old_gate, old_corrector = chain.gate, chain.corrector
        old_corrector.record_attempt(success=False)  # 模拟上一请求脏状态
        chain.ask("问题", session_id="t1")

        assert chain.gate is not old_gate          # gate 已换新实例
        assert chain.corrector is not old_corrector  # corrector 已换新实例
        assert chain.corrector.retry_count == 0    # 新实例无脏状态


# =====================================================
# 4. 同一 chain 实例并发 ask：决策中间态不串
# =====================================================

class TestConcurrentAskIsolation:
    def test_concurrent_ask_same_chain_meta_isolated(self, monkeypatch):
        """同一 chain 实例并发 ask：各请求解析出的 _last_meta（confidence）不串。"""
        from types import SimpleNamespace
        import time as _time

        chain = _stub_chain()
        tl = threading.local()

        def fake_invoke(inp):
            conf = tl.conf
            return {"input": inp, "context": _gate_ok_docs(),
                    "answer": (f"答案。<!--META{{\"can_answer\":true,"
                               f"\"citations\":[],\"confidence\":{conf}}}-->")}
        chain.chain = SimpleNamespace(invoke=fake_invoke)
        monkeypatch.setattr(chain, "_evaluate", lambda answer, ctx: answer)

        def worker(conf, q):
            tl.conf = conf
            chain.ask(q, session_id="t1")
            time.sleep(0.02)
            return get_context().meta.get("confidence")

        with ThreadPoolExecutor(max_workers=2) as ex:
            f1 = ex.submit(worker, 0.85, "并发问题A")
            f2 = ex.submit(worker, 0.60, "并发问题B")
        assert f1.result() == 0.85
        assert f2.result() == 0.60
