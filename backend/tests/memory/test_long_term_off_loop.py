"""回归：long_term.py 的异步下放路径必须能解析 asyncio 名字。

2026-10-29 生产实测：long_term.py 使用 asyncio.to_thread 但未 import
asyncio，retrieve/store_with_resolution 在运行时抛 NameError，被
service.py 吞成 "[MemoryService] store 失败: name 'asyncio' is not
defined"（2.5 分钟内 84 次）。本测试守住两条 to_thread 路径。
"""
import asyncio

from backend.memory.long_term import LongTermMemory, MemoryFact


class _FakeRepo:
    async def search_hybrid(self, emb, user_id, top_k, tenant_id="", domain=None):
        return []


class _FakeEmbedding:
    def embed_query(self, query):
        return [float(len(query))]


def _make_ltm() -> LongTermMemory:
    ltm = LongTermMemory(_FakeRepo())
    ltm._embedding_model = _FakeEmbedding()
    return ltm


def test_retrieve_off_loop_runs_without_name_error():
    """retrieve 的 embedding 下放不因缺 import 抛 NameError。"""
    rows = asyncio.run(_make_ltm().retrieve("测试问题", user_id="u1"))
    assert rows == []


def test_store_with_resolution_off_loop_runs_without_name_error(monkeypatch):
    """store 路径的 embedding 下放不因缺 import 抛 NameError。"""
    import backend.memory.conflict as conflict_mod

    captured = {}

    async def fake_resolve_unkeyed(repo, fact, user_id, session_id, tenant_id, emb):
        captured["emb"] = emb
        return object()  # 形状无关：只验证 to_thread 下放与传参

    monkeypatch.setattr(conflict_mod, "resolve_unkeyed", fake_resolve_unkeyed)
    fact = MemoryFact(fact_type="preference", content="偏好中文回复")
    asyncio.run(_make_ltm().store_with_resolution(fact, user_id="u1", session_id="s1"))
    assert captured["emb"] == [6.0]
