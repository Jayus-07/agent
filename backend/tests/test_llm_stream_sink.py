"""proxy 流式通道单测（P1 真 token 级流式）

覆盖:
- sync stream 包装：逐 chunk 透传 + 用量记录（sink 转发由调用方显式负责——
  RAG 链要过滤 META、reporter 聚合后转发，proxy 自动转发会双发 + 泄漏 META）
- 首 chunk 前瞬时错误：整体重试后成功
- 首 chunk 后失败：不重试，异常上抛（避免重复文本）
- emit_stream_delta / extract_chunk_text 边界行为
"""
import pytest

from backend.infra.llm import proxy as proxy_mod


class _Chunk:
    """最小 AIMessageChunk 替身。"""

    def __init__(self, content: str, usage_metadata: dict | None = None):
        self.content = content
        self.usage_metadata = usage_metadata


class _FakeStreamLLM:
    """可编排的假 LLM：每次 .stream() 调用按序执行 scenarios 中的行为。"""

    def __init__(self, *scenarios):
        # scenario: list[str]（正常输出）或 Exception 实例（抛错）
        self.scenarios = list(scenarios)
        self.calls = 0

    def stream(self, *args, **kwargs):
        scenario = self.scenarios.pop(0) if self.scenarios else []
        self.calls += 1
        for item in scenario:
            if isinstance(item, Exception):
                raise item
            yield item if isinstance(item, _Chunk) else _Chunk(item)


@pytest.fixture(autouse=True)
def reset_sink():
    proxy_mod.reset_stream_sink()
    yield
    proxy_mod.reset_stream_sink()


def test_stream_does_not_auto_forward_to_sink(monkeypatch):
    """proxy 不自动转发 sink：emit 责任在调用方（防双发/META 泄漏），透传不受影响。"""
    received = []
    proxy_mod.set_stream_sink(received.append)
    fake = _FakeStreamLLM(["你", "好", "！"])
    monkeypatch.setattr(proxy_mod, "_resolve_active_llm", lambda: fake)

    chunks = list(proxy_mod.llm.stream("问题"))

    assert received == []
    assert [c.content for c in chunks] == ["你", "好", "！"]


def test_stream_no_sink_still_yields(monkeypatch):
    """无 sink（非 SSE 场景）时流式照常产出，不报错。"""
    fake = _FakeStreamLLM(["a", "b"])
    monkeypatch.setattr(proxy_mod, "_resolve_active_llm", lambda: fake)
    chunks = list(proxy_mod.llm.stream("q"))
    assert [c.content for c in chunks] == ["a", "b"]


def test_stream_retry_before_first_content(monkeypatch):
    """首 chunk 前瞬时错误 → 整体重试成功，透传内容完整无重复。"""
    monkeypatch.setattr(proxy_mod, "LLM_MAX_RETRIES", 1)
    monkeypatch.setattr(proxy_mod, "LLM_RETRY_BACKOFF_BASE", 0.0)
    received = []
    proxy_mod.set_stream_sink(received.append)

    class FakeTimeoutError(Exception):
        pass

    fake = _FakeStreamLLM(
        [FakeTimeoutError("connect timeout")],  # 第一次：首 chunk 前失败
        ["回", "答"],                            # 第二次：成功
    )
    monkeypatch.setattr(proxy_mod, "_resolve_active_llm", lambda: fake)

    chunks = list(proxy_mod.llm.stream("q"))

    assert fake.calls == 2
    assert received == []
    assert [c.content for c in chunks] == ["回", "答"]


def test_stream_no_retry_after_first_content(monkeypatch):
    """已输出内容后失败 → 不重试（避免重复文本），异常上抛。"""
    monkeypatch.setattr(proxy_mod, "LLM_MAX_RETRIES", 2)
    monkeypatch.setattr(proxy_mod, "LLM_RETRY_BACKOFF_BASE", 0.0)
    received = []
    proxy_mod.set_stream_sink(received.append)

    class FakeTimeoutError(Exception):
        pass

    fake = _FakeStreamLLM(
        ["开", FakeTimeoutError("mid-stream down")],  # 同一次调用:输出后失败
    )
    monkeypatch.setattr(proxy_mod, "_resolve_active_llm", lambda: fake)

    with pytest.raises(FakeTimeoutError):
        list(proxy_mod.llm.stream("q"))

    assert fake.calls == 1
    assert received == []


def test_stream_records_usage_from_usage_chunk(monkeypatch):
    """带 usage_metadata 的 chunk 触发 _record_tokens（turn 用量可见）。"""
    proxy_mod.reset_turn_usage()
    fake = _FakeStreamLLM(["答案"])
    monkeypatch.setattr(proxy_mod, "_resolve_active_llm", lambda: fake)
    # 最后一个 chunk 携带 usage_metadata
    fake.scenarios = [[_Chunk("答案", usage_metadata={
        "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
    })]]

    list(proxy_mod.llm.stream("q"))

    usage = proxy_mod.get_turn_usage()
    assert usage, "流式结束后应有 turn 用量记录"
    entry = next(iter(usage.values()))
    assert entry["total_tokens"] == 15


def test_emit_stream_delta_edge_cases():
    """无 sink / 空 text → False 且不抛异常；sink 失败静默。"""
    proxy_mod.reset_stream_sink()
    assert proxy_mod.emit_stream_delta("x") is False
    assert proxy_mod.emit_stream_delta("") is False

    def bad_sink(_):
        raise RuntimeError("sink down")

    proxy_mod.set_stream_sink(bad_sink)
    assert proxy_mod.emit_stream_delta("x") is False


def test_extract_chunk_text_variants():
    assert proxy_mod.extract_chunk_text("str chunk") == "str chunk"
    assert proxy_mod.extract_chunk_text(_Chunk("abc")) == "abc"
    assert proxy_mod.extract_chunk_text(_Chunk("")) == ""
    multimodal = _Chunk("")
    multimodal.content = [{"type": "text", "text": "多模态"}]
    assert proxy_mod.extract_chunk_text(multimodal) == "多模态"
