"""RAG memory 写入时序（2026-09-23 D1-5 回归）。

修复前 end_turn 挂在 _verify 尾部——ClaimVerifier / Faithfulness / META
拒答判定之前，被 Gate 拦截的答案已持久化进 L2/L3 并回注后续 prompt；
self-correction 二次 _verify 还会双写。锁定新契约：

- 只有全部 Gate 通过、最终 answer 确定后才写一次 memory；
- claim 拒答 / faithfulness 拒答 / META 自报拒答（含 self-correction
  仍失败）→ 不写；
- self-correction 成功 → 只写最终修正版一次。
"""
import pytest

from backend.rag.chain import RAGChain
from backend.rag.context import get_context


class _MemoryRecorder:
    def __init__(self):
        self.calls = []

    def end_turn(self, session_id, question, answer):
        self.calls.append((session_id, question, answer))


class _Decision:
    def __init__(self, passed=False):
        self.passed = passed
        self.reason = None
        self.layer = "test"
        self.score = 0.0
        self.diagnostics = {}


class _Gate:
    risk_level = "low"

    def build_decision_from_meta(self, meta):
        return _Decision()


class _Corrector:
    def __init__(self, can_retry=True):
        self._can_retry = can_retry
        self.retry_count = 1

    def can_retry(self):
        return self._can_retry


class _Faith:
    def __init__(self, score):
        self.score = score


def _make_chain(memory):
    chain = RAGChain.__new__(RAGChain)
    chain._memory = memory
    chain.gate = _Gate()
    chain.corrector = _Corrector()
    chain._last_sources = []
    chain._record_rag_metric = lambda status: None
    chain._finish = lambda trace, answer, t_total: None
    # 中间态经 rag.context 传递（与生产同路径）
    get_context().faithfulness = None
    get_context().meta = {"can_answer": True}
    return chain


def _success_flow(chain, memory):
    """桩好 _verify 之后的正常链路（Gate 全过）。"""
    chain._verify = lambda result, question, session_id="default": "ANSWER"
    chain._verify_claims = lambda answer, docs: answer
    chain._evaluate = lambda answer, docs: get_context().__setattr__(
        "faithfulness", _Faith(0.95)) or answer
    chain._respond({"answer": "raw", "context": []}, trace=None,
                   question="问题Q", session_id="s-1", t_total=0.1)
    return memory.calls


def test_all_gates_pass_writes_memory_once():
    memory = _MemoryRecorder()
    chain = _make_chain(memory)
    calls = _success_flow(chain, memory)
    assert calls == [("s-1", "问题Q", "ANSWER")]


def test_claim_reject_does_not_write_memory():
    memory = _MemoryRecorder()
    chain = _make_chain(memory)
    chain._verify = lambda result, question, session_id="default": "编造答案"
    chain._verify_claims = lambda answer, docs: None  # 数字编造被拦截
    chain._reject = lambda decision, layer, trace, t_total, **kw: "拒答文案"
    out = chain._respond({"answer": "raw", "context": []}, None,
                         "问题Q", "s-1", 0.1)
    assert out == "拒答文案"
    assert memory.calls == []


def test_faithfulness_reject_does_not_write_memory(monkeypatch):
    memory = _MemoryRecorder()
    chain = _make_chain(memory)
    chain._verify = lambda result, question, session_id="default": "低分答案"
    chain._verify_claims = lambda answer, docs: answer
    chain._evaluate = lambda answer, docs: get_context().__setattr__(
        "faithfulness", _Faith(0.05)) or answer
    chain._reject = lambda decision, layer, trace, t_total, **kw: "拒答文案"
    out = chain._respond({"answer": "raw", "context": []}, None,
                         "问题Q", "s-1", 0.1)
    assert out == "拒答文案"
    assert memory.calls == []


def test_meta_reject_and_selfcorrect_failure_do_not_write_memory():
    memory = _MemoryRecorder()
    chain = _make_chain(memory)
    chain._verify = lambda result, question, session_id="default": "资料未提及"
    chain._verify_claims = lambda answer, docs: answer
    chain._evaluate = lambda answer, docs: get_context().__setattr__(
        "faithfulness", _Faith(0.95)) or answer
    get_context().meta = {"can_answer": False}
    # self-correction 关闭 → 走统一拒答
    chain.corrector = _Corrector(can_retry=False)
    chain._reject = lambda decision, layer, trace, t_total, **kw: "拒答文案"
    out = chain._respond({"answer": "raw", "context": []}, None,
                         "问题Q", "s-1", 0.1)
    assert out == "拒答文案"
    assert memory.calls == []


def test_selfcorrect_success_writes_final_answer_once():
    memory = _MemoryRecorder()
    chain = _make_chain(memory)
    chain._verify = lambda result, question, session_id="default": "资料未提及"
    chain._verify_claims = lambda answer, docs: answer
    chain._evaluate = lambda answer, docs: get_context().__setattr__(
        "faithfulness", _Faith(0.95)) or answer
    get_context().meta = {"can_answer": False}
    # self-correction 成功 → 只写最终修正版一次
    chain._try_self_correct = lambda decision, trace, question, session_id, t_total: "修正后的回答"
    out = chain._respond({"answer": "raw", "context": []}, None,
                         "问题Q", "s-1", 0.1)
    assert out == "修正后的回答"
    assert memory.calls == [("s-1", "问题Q", "修正后的回答")]


def test_verify_no_longer_writes_memory_directly():
    """_verify 自身不再含写入调用（时序修复本体）。"""
    import inspect
    src = inspect.getsource(RAGChain._verify)
    assert ".end_turn(" not in src
