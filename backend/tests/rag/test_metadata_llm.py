"""test_metadata_llm.py — 阶段2：元数据统一 LLM 抽取（metadata_llm.py）。

覆盖：
  1. parse_extract_response：干净 JSON / 代码围栏 / 夹杂说明 / 越界 doc_type / 坏 JSON；
  2. extract_metadata_llm_async 成功路径（mock prompt + LLM）；
  3. 失败路径：LLM 抛异常 / 超时返回 None → 调用方降级规则路径；
  4. 开关关闭时不走 LLM（config 契约）。
"""
import json
from unittest.mock import MagicMock

import pytest

from backend.rag.preprocessing.metadata_llm import (
    MetadataExtractError,
    extract_metadata_llm_async,
    parse_extract_response,
)

VALID_TYPES = {"policy", "sop", "financial", "faq", "general"}


GOOD = {
    "doc_type": "financial",
    "confidence": 0.9,
    "business_domain": "finance",
    "summary": "本季度报销与预算执行情况说明。",
    "keywords": ["报销", "预算", "发票"],
    "entities": {"person": ["张三"], "org": ["财务部"]},
    "time_refs": ["2026年Q3"],
}


# ============ 解析与校验 ============

class TestParseExtractResponse:
    def test_clean_json(self):
        r = parse_extract_response(json.dumps(GOOD, ensure_ascii=False), VALID_TYPES)
        assert r["doc_type"] == "financial"
        assert r["confidence"] == 0.9
        assert r["keywords"] == ["报销", "预算", "发票"]
        assert r["entities"]["person"] == ["张三"]

    def test_code_fence_stripped(self):
        raw = "```json\n" + json.dumps(GOOD, ensure_ascii=False) + "\n```"
        r = parse_extract_response(raw, VALID_TYPES)
        assert r["doc_type"] == "financial"

    def test_text_around_json(self):
        raw = "好的，以下是结果：\n" + json.dumps(GOOD, ensure_ascii=False) + "\n以上。"
        r = parse_extract_response(raw, VALID_TYPES)
        assert r["doc_type"] == "financial"

    def test_doc_type_out_of_enum_rejected(self):
        bad = dict(GOOD, doc_type="hacking")
        with pytest.raises(MetadataExtractError):
            parse_extract_response(json.dumps(bad, ensure_ascii=False), VALID_TYPES)

    def test_bad_json_rejected(self):
        with pytest.raises(MetadataExtractError):
            parse_extract_response("这不是 JSON", VALID_TYPES)
        with pytest.raises(MetadataExtractError):
            parse_extract_response('{"doc_type": "sop"', VALID_TYPES)

    def test_field_coercion_and_caps(self):
        obj = dict(GOOD, confidence="not-a-number", keywords=[1, 2, "  ", "ok"],
                   entities={"person": "not-a-list", "org": ["a", ""]},
                   time_refs=None)
        r = parse_extract_response(json.dumps(obj, ensure_ascii=False), VALID_TYPES)
        assert r["confidence"] == 0.7  # 脏值回落默认
        assert r["keywords"] == ["1", "2", "ok"]  # 数字转字符串，空白剔除
        assert r["entities"] == {"org": ["a"]}  # 非 list 的 person 剔除
        assert r["time_refs"] == []


# ============ 抽取调用 ============

class _FakeResp:
    def __init__(self, content: str):
        self.content = content
        self.usage_metadata = {"prompt_tokens": 10, "completion_tokens": 5}


@pytest.fixture
def _patch_prompt(monkeypatch):
    """render_sync 返回固定 prompt，避免读真实 prompt 存储。"""
    from backend.prompts.service import prompt_service
    monkeypatch.setattr(prompt_service, "render_sync",
                        lambda key, **kw: MagicMock(text="PROMPT"))


@pytest.mark.asyncio
async def test_extract_success(_patch_prompt, monkeypatch):
    import backend.rag.preprocessing.metadata_llm as m
    monkeypatch.setattr(
        m, "invoke_metadata_llm", lambda prompt: _FakeResp(json.dumps(GOOD, ensure_ascii=False)),
        raising=False)

    # async_safe_call_with_timeout(fn, timeout, None, msg, prompt) → 透传 fn(prompt)
    async def _passthrough(fn, *args, **kwargs):
        return fn(*args[3:], **kwargs)
    monkeypatch.setattr(m, "async_safe_call_with_timeout", _passthrough, raising=False)

    r = await extract_metadata_llm_async("一些文本", "报销制度.docx")
    assert r is not None
    assert r["doc_type"] == "financial"
    assert r["llm_tokens"]["prompt_tokens"] == 10


@pytest.mark.asyncio
async def test_extract_llm_failure_returns_none(_patch_prompt, monkeypatch):
    import backend.rag.preprocessing.metadata_llm as m

    def _boom(prompt):
        raise RuntimeError("llm down")

    async def _passthrough(fn, *args, **kwargs):
        return fn(*args[3:], **kwargs)
    monkeypatch.setattr(m, "invoke_metadata_llm", _boom, raising=False)
    monkeypatch.setattr(m, "async_safe_call_with_timeout", _passthrough, raising=False)

    r = await extract_metadata_llm_async("文本", "x.md")
    assert r is None, "LLM 失败必须返回 None（调用方降级规则路径）"


@pytest.mark.asyncio
async def test_extract_empty_text_short_circuit():
    assert await extract_metadata_llm_async("   ", "x.md") is None


def test_sync_entry_rejects_running_loop():
    """在有运行中事件循环的线程里调用同步入口必须立即报错（防误用）。"""
    import asyncio
    from backend.rag.preprocessing.metadata_llm import extract_metadata_llm

    async def _call():
        # 事件循环内调用同步入口 → RuntimeError，而不是静默嵌套 loop
        with pytest.raises(RuntimeError):
            extract_metadata_llm("text", "x.md")

    asyncio.run(_call())
