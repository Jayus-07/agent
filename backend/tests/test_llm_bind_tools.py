# -*- coding: utf-8 -*-
"""proxy bind_tools 包装（_BoundLLMProxy）单测。

背景: _LLMProxy.__getattr__ 只包装 _WRAP_METHODS 内的方法名，
bind_tools 原样透传会拿到裸 RunnableBinding——其 invoke/ainvoke 绕过
限流、韧性链（重试/熔断/fallback）与 token 记录。_BoundLLMProxy 把
绑定后的调用重新纳入同一条包装路径。
"""
import asyncio
from types import SimpleNamespace

import backend.infra.llm.proxy as proxy_mod
from backend.infra.llm.proxy import _BoundLLMProxy, _LLMProxy
from langchain_core.messages import AIMessage


class _RecordingBound:
    """模拟 RunnableBinding：记录 invoke/ainvoke 调用。"""

    def __init__(self, result="RAW"):
        self.sync_calls = []
        self.async_calls = []
        self._result = result

    def invoke(self, *args, **kwargs):
        self.sync_calls.append((args, kwargs))
        return self._result

    async def ainvoke(self, *args, **kwargs):
        self.async_calls.append((args, kwargs))
        return self._result


def _patch_chain(monkeypatch, recorded):
    """替换包装链各环节为记录桩，验证调用顺序与参数透传。"""
    monkeypatch.setattr(proxy_mod, "_enforce_rate_limit",
                        lambda uid: recorded.append(("rate_limit", uid)))
    monkeypatch.setattr(
        proxy_mod, "_call_with_resilience",
        lambda attr, *a, **k: recorded.append(("resilience",)) or attr(*a, **k))
    monkeypatch.setattr(
        proxy_mod, "_acall_with_resilience",
        lambda attr, *a, **k: recorded.append(("aresilience",)) or attr(*a, **k))
    monkeypatch.setattr(
        proxy_mod, "_record_tokens",
        lambda result, duration_ms=None: recorded.append(
            ("tokens", result, duration_ms is not None)))
    monkeypatch.setattr(
        proxy_mod, "_wrap_result",
        lambda r: recorded.append(("wrap", r)) or r)


class TestProxyBindTools:
    def test_llm_proxy_bind_tools_returns_wrapper(self, monkeypatch):
        """llm.bind_tools(...) 必须返回 _BoundLLMProxy，而非裸 RunnableBinding"""
        bound = _RecordingBound()
        target = SimpleNamespace(bind_tools=lambda tools: bound)
        monkeypatch.setattr(proxy_mod, "_resolve_active_llm", lambda: target)
        proxy = _LLMProxy()
        wrapped = proxy.bind_tools([{"type": "function", "function": {
            "name": "f", "description": "", "parameters": {}}}])
        assert isinstance(wrapped, _BoundLLMProxy)

    def test_bound_invoke_full_chain(self, monkeypatch):
        """invoke 走完整包装链: 限流 → 韧性 → token 记录 → think 剥离"""
        recorded = []
        _patch_chain(monkeypatch, recorded)
        bound = _RecordingBound()
        out = _BoundLLMProxy(bound).invoke([("human", "hi")], max_tokens=64)
        assert out == "RAW"
        kinds = [r[0] for r in recorded]
        assert kinds == ["rate_limit", "resilience", "tokens", "wrap"]
        # kwargs（max_tokens 等）原样透传给底层 binding
        assert bound.sync_calls[0][1] == {"max_tokens": 64}

    def test_bound_ainvoke_full_chain(self, monkeypatch):
        recorded = []
        _patch_chain(monkeypatch, recorded)
        bound = _RecordingBound()
        out = asyncio.run(
            _BoundLLMProxy(bound).ainvoke([("human", "hi")], max_tokens=32))
        assert out == "RAW"
        kinds = [r[0] for r in recorded]
        assert kinds == ["rate_limit", "aresilience", "tokens", "wrap"]
        assert bound.async_calls[0][1] == {"max_tokens": 32}

    def test_other_attrs_passthrough(self):
        bound = _RecordingBound()
        bound.marker = "underlying"
        proxy = _BoundLLMProxy(bound)
        # 未特殊处理的属性透传到底层 binding（RunnableBinding 语义保留）
        assert proxy.marker == "underlying"

    def test_chained_bind_tools_still_wrapped(self):
        """链式 bind（罕见）继续走包装，不裸透传"""
        inner = _RecordingBound()
        outer = SimpleNamespace(bind_tools=lambda tools: inner)
        base = SimpleNamespace(bind_tools=lambda tools: outer)
        proxy = _BoundLLMProxy(base)
        chained = proxy.bind_tools([])
        assert isinstance(chained, _BoundLLMProxy)


class TestWrapResultPreservesToolCalls:
    def test_think_stripped_tool_calls_kept(self):
        """_wrap_result 只剥 <think>，不得破坏 AIMessage.tool_calls（FC 依赖）"""
        msg = AIMessage(
            content="<think>推理过程</think>正文",
            tool_calls=[{"name": "report__generate",
                         "args": {"report_type": "daily_sales"}, "id": "c1"}],
        )
        out = proxy_mod._wrap_result(msg)
        assert out.content == "正文"
        assert out.tool_calls[0]["name"] == "report__generate"
        assert out.tool_calls[0]["args"] == {"report_type": "daily_sales"}
