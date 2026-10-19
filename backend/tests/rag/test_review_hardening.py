"""本轮审查缺陷的真实本地行为验证；不调用 Provider。"""
import json
import pickle

import pytest
from langchain_core.documents import Document
from langchain_core.messages import HumanMessage


def test_contextualize_uses_configured_system_human_and_history(monkeypatch):
    from backend.prompts.service import _SnapshotEntry, prompt_service
    from backend.rag.chain import _build_contextualize_prompt

    for version in (2, 3):
        monkeypatch.setitem(prompt_service._snapshot, "rag.contextualize", _SnapshotEntry(
            template=f"配置系统版本{version}\n---\n独立查询：{{input}}", version=version,
            variables=["input"],
        ))
        messages = _build_contextualize_prompt().format_messages(
            input="它的流程", chat_history=[HumanMessage(content="采购审批")],
        )
        assert messages[0].content == f"配置系统版本{version}"
        assert messages[1].content == "采购审批"
        assert messages[2].content == "独立查询：它的流程"


@pytest.mark.parametrize("invalid", ["无分隔符", "---\n{input}", "系统\n---"])
def test_contextualize_invalid_template_falls_back(monkeypatch, invalid):
    from backend.prompts.service import prompt_service
    from backend.rag.chain import DEFAULT_CONTEXTUALIZE_SYSTEM, _build_contextualize_prompt

    monkeypatch.setattr(prompt_service, "get_template_sync", lambda key: invalid)
    messages = _build_contextualize_prompt().format_messages(input="原问题", chat_history=[])
    assert messages[0].content == DEFAULT_CONTEXTUALIZE_SYSTEM
    assert messages[-1].content == "原问题"


def test_bm25_missing_checksum_never_deserializes(tmp_path, monkeypatch):
    from backend.rag.retrieval.bm25_store import BM25Store

    store = BM25Store(str(tmp_path))
    data_path = tmp_path / "docs.pkl"
    data_path.write_bytes(pickle.dumps({"probe": "受信任数据"}))
    monkeypatch.setattr(pickle, "loads", lambda data: pytest.fail("缺校验不应反序列化"))
    assert store._safe_load_pickle(data_path) is None
    store._checksum_path(data_path).write_text("mismatch", encoding="utf-8")
    assert store._safe_load_pickle(data_path) is None


def test_chunk_cache_json_roundtrip_and_invalid_data_are_safe(tmp_path, monkeypatch):
    from backend.rag.preprocessing import chunk_cache as cache

    monkeypatch.setattr(cache, "_CACHE_DIR", tmp_path / "cache")
    source = tmp_path / "document.txt"
    source.write_text("完整源文", encoding="utf-8")
    doc = Document(page_content="有效内容", metadata={"section_path": ["章", "节"]}, id="chunk1")
    cache.save_cached_chunks(str(source), [doc])
    loaded = cache.load_cached_chunks(str(source))
    assert loaded == [doc]
    path = cache._cache_path(str(source))
    assert path.suffix == ".json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["chunks"] = [{"page_content": "x", "metadata": "非法结构"}]
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert cache.load_cached_chunks(str(source)) is None
    path.write_bytes(pickle.dumps({"chunks": [doc]}))
    assert cache.load_cached_chunks(str(source)) is None
    cache.save_cached_chunks(str(source), [doc])
    source.write_text("修改源文", encoding="utf-8")
    assert cache.load_cached_chunks(str(source)) is None


def test_cleaner_preserves_straight_and_curly_quotes():
    from backend.rag.preprocessing.cleaner import DocumentCleaner

    text = '中文"开头内容"中文与中文\'单引内容\'中文，“中文”及‘中文’。'
    result = DocumentCleaner().clean(text)
    assert '"开头内容"' in result.text
    assert "'单引内容'" in result.text
    assert '“中文”' in result.text and '‘中文’' in result.text
