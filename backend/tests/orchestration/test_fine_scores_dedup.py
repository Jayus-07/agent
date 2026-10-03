"""TD-17 同名 capability 分数覆盖回归（2026-10-03）。

实测：rag.search 有 14 条 examples（同 capability 多行），hierarchical
的 _fine_scores 用 dict 推导收分，后行覆盖前行——"发票认证时限"的
top1 分（0.640）被第 7 条"商品定价调价"（0.443）覆盖，fine_top1 翻转
到 web.search，问题被路由到公网搜索。此缺陷在旧 6 条 examples 时代
即存在（分数一直取"最后一条 example"），新增第 7 条后排序 unlucky
才暴露。

钉死：_fine_scores 与 VectorRouter.route 同名 capability 均取最优分。
"""
from types import SimpleNamespace

from backend.orchestration.router.hierarchical import HierarchicalRouter
from backend.orchestration.router.vector_router import VectorRouter


def _cs(name, score):
    return SimpleNamespace(name=name, score=score)


def test_fine_scores_takes_best_per_capability(monkeypatch):
    hr = HierarchicalRouter()
    # route 返回 8 行：rag.search 两行（首行 0.640 正确、第 7 行 0.443）
    decision = SimpleNamespace(candidates=[
        _cs("rag.search", 0.640),
        _cs("web.search", 0.453),
        _cs("data.collect", 0.451),
        _cs("email.send", 0.449),
        _cs("email.read", 0.447),
        _cs("data.collect", 0.443),
        _cs("rag.search", 0.443),
        _cs("data.collect", 0.442),
    ])
    monkeypatch.setattr(
        "backend.orchestration.router.router.get_router",
        lambda: SimpleNamespace(vector=SimpleNamespace(route=lambda q, top_k=8: decision)),
    )
    cands = [SimpleNamespace(name="rag.search"), SimpleNamespace(name="web.search")]
    scores = hr._fine_scores("发票认证时限是多久", cands)
    assert scores["rag.search"] == 0.640, "同名 capability 必须取最优分"
    assert scores["web.search"] == 0.453


def test_vector_route_dedupes_same_capability():
    """route 的 candidates 同名消重取最优（下游 dict 消费方安全）。"""
    fake_self = SimpleNamespace(_store=SimpleNamespace(
        similarity_search_with_score=lambda q, k=3: [
            (SimpleNamespace(metadata={"capability": "rag.search"}, page_content="a"), 0.563),
            (SimpleNamespace(metadata={"capability": "web.search"}, page_content="b"), 1.206),
            (SimpleNamespace(metadata={"capability": "rag.search"}, page_content="c"), 1.259),
        ],
        count=lambda: 3,
    ))
    decision = VectorRouter.route(fake_self, "发票认证时限是多久")
    assert decision.candidates[0].name == "rag.search"
    assert decision.candidates[0].score == 0.64
    names = [c.name for c in decision.candidates]
    assert len(names) == len(set(names)), "同名必须消重"


def test_fine_scores_all_below_keeps_floor(monkeypatch):
    """未进 top-K 的候选给保底分 0.3（既有语义保持）。"""
    hr = HierarchicalRouter()
    decision = SimpleNamespace(candidates=[_cs("web.search", 0.453)])
    monkeypatch.setattr(
        "backend.orchestration.router.router.get_router",
        lambda: SimpleNamespace(vector=SimpleNamespace(route=lambda q, top_k=8: decision)),
    )
    cands = [SimpleNamespace(name="rag.search"), SimpleNamespace(name="web.search")]
    scores = hr._fine_scores("任何问题", cands)
    assert scores["rag.search"] == 0.3
    assert scores["web.search"] == 0.453
