"""向量路由基础设施故障语义测试。"""

import pytest


def test_index_mismatch_is_typed_and_never_rebuilds_in_request(monkeypatch):
    from backend.orchestration.router.vector_router import VectorRouter, VectorRouteError
    from backend.rag.vectorstore.pgvector_store import IndexEmbeddingMismatchError

    class Store:
        def count(self):
            return 1

        def similarity_search_with_score(self, query, k):
            raise IndexEmbeddingMismatchError("old-model", "new-model", "router_index")

        def mark_rebuild_required(self):
            calls["mark"] += 1

    calls = {"rebuild": 0, "mark": 0}
    router = object.__new__(VectorRouter)
    router._store = Store()
    monkeypatch.setattr(router, "_rebuild_index", lambda: calls.__setitem__("rebuild", 1))

    with pytest.raises(VectorRouteError) as exc_info:
        router.route("查库存")

    assert exc_info.value.code == "vector_index_mismatch"
    assert calls == {"rebuild": 0, "mark": 1}


def test_unavailable_index_is_typed():
    from backend.orchestration.router.vector_router import VectorRouter, VectorRouteError

    router = object.__new__(VectorRouter)
    router._store = None

    with pytest.raises(VectorRouteError) as exc_info:
        router.route("查库存")

    assert exc_info.value.code == "vector_unavailable"
