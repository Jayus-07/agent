"""审核可见性公共出口回归（Batch 1A + GPT 复核返修，2026-10-12）。

契约（审计 P0-2/P1-3 + 复核返修 1-5）：
- pending_review/deprecated/过期 文档在全部普通检索出口不可见：
  hybrid_retrieve（ask 链）、retrieve_knowledge（/retrieve 及内部消费）、
  retrieve_documents（/rag/search、/retrieve_docs）、rag-server 端点、
  remote 代理（client）。
- 双层防线：黑名单（TTL+版本信号快路径，**非空集合同样过期**）+ 实时批量
  正向校验（Registry 当前状态，一条 SQL）——Redis 故障/TTL 窗口内新下线、
  发布即待审、删除残留由正向校验兜底。
- fail-closed：Registry 任何一层故障 → ReviewVisibilityUnavailableError；
  缺 doc_id（无法确认生命周期）对客一律剔除，内部 system_subject 存量兼容。
- 跨进程失效：invalidate 广播版本号，检索方节流轮询感知。
"""
from types import SimpleNamespace

import pytest
from langchain_core.documents import Document

import backend.rag.retrieval.review_filter as rf
from backend.rag.retrieval.hybrid import hybrid_retrieve
from backend.rag.retrieval.review_filter import (
    ReviewVisibilityUnavailableError,
    apply_review_visibility,
    get_review_blocked_doc_ids,
    invalidate_pending_review_cache,
    verify_visible_doc_ids,
)


def _doc(doc_id: str, content: str = "正文") -> Document:
    return Document(page_content=content, metadata={"doc_id": doc_id})


def _doc_id_of(d):
    return d if isinstance(d, str) else d.metadata.get("doc_id")


@pytest.fixture(autouse=True)
def _reset_cache():
    rf._block_cache = {"ids": frozenset(), "ts": 0.0}
    rf._version_cache = {"value": None, "ts": 0.0, "last_seen": None}
    FakeRegistry.instances = []
    yield
    rf._block_cache = {"ids": frozenset(), "ts": 0.0}
    rf._version_cache = {"value": None, "ts": 0.0, "last_seen": None}
    FakeRegistry.instances = []


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch):
    """单测默认无 Redis：版本信号退化为 TTL 兜底，不触发真实连接。"""

    def _fail():
        raise ConnectionError("no redis in unit test")

    monkeypatch.setattr("backend.infra.redis.client.get_redis", _fail)


class FakeRegistry:
    """可编程 Registry：黑名单行集 + 可见 doc_id 集 + 可注入故障。"""

    instances: list = []

    def __init__(self, harness):
        self.h = harness
        FakeRegistry.instances.append(self)

    def list_by_statuses(self, statuses):
        if self.h.error:
            raise self.h.error
        assert set(statuses) == {"pending_review", "deprecated"}
        return list(self.h.blocked_rows)

    def list_expired(self, now=None):
        if self.h.error:
            raise self.h.error
        return list(self.h.expired_rows)

    def list_visible_doc_ids(self, doc_ids, now=None):
        if self.h.verify_error:
            raise self.h.verify_error
        blocked = {
            r["doc_id"] for r in self.h.blocked_rows + self.h.expired_rows
        }
        return {d for d in (doc_ids or []) if d} - blocked - self.h.extra_blocked


class _Harness:
    def __init__(self):
        self.blocked_rows: list = []
        self.expired_rows: list = []
        self.extra_blocked: set = set()  # 正向校验视角额外不可见（如 deleted）
        self.error: Exception | None = None
        self.verify_error: Exception | None = None


def _patch_registry(monkeypatch, harness: _Harness):
    from backend.rag.indexing import doc_registry as dr

    monkeypatch.setattr(
        dr, "DocumentRegistry", lambda path: FakeRegistry(harness))
    return FakeRegistry


# ────────────────────── 修1：黑名单缓存 TTL ──────────────────────

class TestBlockedIdsTTL:
    def test_reads_registry_and_dedup(self, monkeypatch):
        h = _Harness()
        h.blocked_rows = [{"doc_id": "d1"}, {"doc_id": "d2"}, {"doc_id": ""}]
        h.expired_rows = [{"doc_id": "d3"}]
        _patch_registry(monkeypatch, h)
        assert get_review_blocked_doc_ids() == frozenset({"d1", "d2", "d3"})

    def test_nonempty_cache_expires_after_ttl(self, monkeypatch):
        """返修缺陷1反例：非空 blocked 集合也必须按 TTL 过期重建。"""
        h = _Harness()
        h.blocked_rows = [{"doc_id": "old"}]
        reg_cls = _patch_registry(monkeypatch, h)
        get_review_blocked_doc_ids()
        assert len(reg_cls.instances) == 1

        # 时间推进 31s（> _COLLECT_TTL）：非空集合过期，必须重建
        h.blocked_rows = [{"doc_id": "old"}, {"doc_id": "fresh"}]
        real_time = rf.time
        clock = {"now": real_time.monotonic()}
        monkeypatch.setattr(
            rf, "time", SimpleNamespace(monotonic=lambda: clock["now"]))
        clock["now"] += rf._COLLECT_TTL + 1
        assert get_review_blocked_doc_ids() == frozenset({"old", "fresh"})
        assert len(reg_cls.instances) == 2, "非空缓存必须随 TTL 重建"

    def test_redis_down_ttl_still_rebuilds(self, monkeypatch):
        """返修缺陷1反例：Redis 故障期间非空缓存同样按 TTL 重建。"""
        h = _Harness()
        h.blocked_rows = [{"doc_id": "a"}]
        reg_cls = _patch_registry(monkeypatch, h)
        get_review_blocked_doc_ids()  # Redis 不可用，纯 TTL 模式

        real_time = rf.time
        clock = {"now": real_time.monotonic()}
        monkeypatch.setattr(
            rf, "time", SimpleNamespace(monotonic=lambda: clock["now"]))
        h.blocked_rows = [{"doc_id": "a"}, {"doc_id": "b"}]
        clock["now"] += rf._COLLECT_TTL + 1
        assert get_review_blocked_doc_ids() == frozenset({"a", "b"})

    def test_registry_down_fail_closed(self, monkeypatch):
        h = _Harness()
        h.error = RuntimeError("pg down")
        _patch_registry(monkeypatch, h)
        with pytest.raises(ReviewVisibilityUnavailableError):
            get_review_blocked_doc_ids()

    def test_version_signal_invalidates_cache(self, monkeypatch):
        """跨进程失效：版本号变化后集合缓存立即重建（不等 TTL）。"""
        rows = [{"doc_id": "old"}]
        h = _Harness()
        h.blocked_rows = rows
        reg_cls = _patch_registry(monkeypatch, h)
        assert get_review_blocked_doc_ids() == frozenset({"old"})

        rows.append({"doc_id": "new"})  # 模拟另一进程写入（原地改，闭包可见）
        rf._version_cache["ts"] = 0.0
        rf._version_cache["last_seen"] = 1
        monkeypatch.setattr(
            "backend.infra.redis.client.get_redis",
            lambda: type("R", (), {"get": lambda self, k: b"2"})(),
        )
        assert get_review_blocked_doc_ids() == frozenset({"old", "new"})
        assert len(reg_cls.instances) == 2

    def test_invalidate_broadcasts_and_clears(self, monkeypatch):
        incr = {"n": 0}

        class FakeRedis:
            def incr(self, key):
                incr["n"] += 1
                return 1

        monkeypatch.setattr(
            "backend.infra.redis.client.get_redis", lambda: FakeRedis())
        h = _Harness()
        h.blocked_rows = [{"doc_id": "d1"}]
        reg_cls = _patch_registry(monkeypatch, h)
        get_review_blocked_doc_ids()
        invalidate_pending_review_cache()
        assert incr["n"] == 1, "审批/下线方广播跨进程失效信号"
        get_review_blocked_doc_ids()
        assert len(reg_cls.instances) == 2, "invalidate 后本进程立即重建缓存"


# ────────────────── 修2+3：apply 收口（黑名单 + 正向校验） ──────────────────

class TestApplyReviewVisibility:

    def test_active_visible(self, monkeypatch):
        h = _Harness()
        _patch_registry(monkeypatch, h)
        out = apply_review_visibility([_doc("ok")], context="t")
        assert [d.metadata["doc_id"] for d in out] == ["ok"]

    def test_pending_deprecated_expired_blocked(self, monkeypatch):
        """pending/deprecated/过期：黑名单快路径剔除。"""
        h = _Harness()
        h.blocked_rows = [{"doc_id": "pending1"}, {"doc_id": "depr1"}]
        h.expired_rows = [{"doc_id": "exp1"}]
        _patch_registry(monkeypatch, h)
        out = apply_review_visibility(
            [_doc("ok"), _doc("pending1"), _doc("depr1"), _doc("exp1")])
        assert [d.metadata["doc_id"] for d in out] == ["ok"]

    def test_deleted_dropped_by_positive_check(self, monkeypatch):
        """deleted 残留 chunk：黑名单不含（不在 pending/deprecated/expired），
        由正向校验（status != active）剔除。"""
        h = _Harness()
        h.blocked_rows = []
        h.extra_blocked = {"deleted1"}  # 模拟 registry 视角：该 doc 已删除
        _patch_registry(monkeypatch, h)
        out = apply_review_visibility([_doc("ok"), _doc("deleted1")])
        assert [d.metadata["doc_id"] for d in out] == ["ok"]

    def test_newly_deprecated_during_redis_outage_dropped(self, monkeypatch):
        """返修缺陷3核心反例：Redis 故障 + 黑名单缓存窗口内新下线的文档，
        正向校验（实时查 Registry）必须剔除，不得用过期安全状态放行。"""
        h = _Harness()
        _patch_registry(monkeypatch, h)
        get_review_blocked_doc_ids()  # 构建黑名单快照（此刻为空）

        # TTL 窗口内（不推进时钟、Redis 不可用无法收到失效信号）：
        # 文档 x 被下线（registry 视角不再可见）
        h.extra_blocked = {"x"}
        out = apply_review_visibility([_doc("ok"), _doc("x")])
        assert [d.metadata["doc_id"] for d in out] == ["ok"], \
            "黑名单过期的下线文档必须被实时正向校验拦下"

    def test_publish_pending_review_visible_immediately(self, monkeypatch):
        """返修4：发布提交即 pending_review（b 步）后，任何时刻检索都必须
        不可见——正向校验实时查 Registry，不受缓存/广播时序影响。"""
        h = _Harness()
        _patch_registry(monkeypatch, h)
        get_review_blocked_doc_ids()  # 发布前黑名单快照（不含新文档）

        # b 步提交：新文档进入 pending_review（尚未广播/缓存未过期）
        h.extra_blocked = {"near_dup_new"}
        out = apply_review_visibility([_doc("near_dup_new")])
        assert out == [], "发布即待审的文档在窗口期不得被检索返回"

    def test_missing_doc_id_strict_dropped(self, monkeypatch):
        """返修2：无法确认生命周期（缺 doc_id）对客主体一律剔除。"""
        h = _Harness()
        _patch_registry(monkeypatch, h)
        plain = "无 metadata 的存量对象"
        out = apply_review_visibility([_doc("ok"), plain],
                                      allow_missing_doc_id=False)
        assert [d.metadata["doc_id"] for d in out] == ["ok"]

    def test_missing_doc_id_internal_compat_kept(self, monkeypatch):
        """内部 system_subject 存量兼容：缺 doc_id 放行，有 doc_id 照常校验。"""
        h = _Harness()
        h.blocked_rows = [{"doc_id": "pending1"}]
        _patch_registry(monkeypatch, h)
        plain = "无 metadata 的存量对象"
        out = apply_review_visibility(
            [_doc("ok"), _doc("pending1"), plain], allow_missing_doc_id=True)
        assert [_doc_id_of(d) for d in out] == ["ok", plain]

    def test_registry_down_fail_closed(self, monkeypatch):
        """黑名单层故障：显式失败，不返回部分结果。"""
        h = _Harness()
        h.error = RuntimeError("pg down")
        _patch_registry(monkeypatch, h)
        with pytest.raises(ReviewVisibilityUnavailableError):
            apply_review_visibility([_doc("ok")])

    def test_verify_down_fail_closed(self, monkeypatch):
        """正向校验层故障：显式失败，不得退回黑名单结果继续放行。"""
        h = _Harness()
        h.verify_error = RuntimeError("pg down")
        _patch_registry(monkeypatch, h)
        with pytest.raises(ReviewVisibilityUnavailableError):
            apply_review_visibility([_doc("ok")])

    def test_verify_positive_api(self, monkeypatch):
        """verify_visible_doc_ids：只返回 active 且未过期的 doc_id。"""
        h = _Harness()
        h.blocked_rows = [{"doc_id": "p"}]
        h.expired_rows = [{"doc_id": "e"}]
        h.extra_blocked = {"d"}  # deleted
        _patch_registry(monkeypatch, h)
        got = verify_visible_doc_ids({"ok", "p", "e", "d", "", None})
        assert got == {"ok"}


# ────────────────────── 修5：三个代码出口 ──────────────────────

class TestHybridRetrieveWrapper:

    def _patch_impl(self, monkeypatch, docs):
        import backend.rag.retrieval.hybrid as hybrid_mod

        def fake_impl(query, vr, br, k=5, doc_ids=None, rrf_k=60,
                      metadata_filter=None, expanded_queries=None):
            return list(docs)

        monkeypatch.setattr(hybrid_mod, "_hybrid_retrieve_impl", fake_impl)

    def test_ask_chain_hides_all_blocked_states(self, monkeypatch):
        """ask/hybrid：pending/deprecated/expired/deleted/无 doc_id 全剔除。"""
        h = _Harness()
        h.blocked_rows = [{"doc_id": "pending1"}, {"doc_id": "depr1"}]
        h.expired_rows = [{"doc_id": "exp1"}]
        h.extra_blocked = {"deleted1"}
        _patch_registry(monkeypatch, h)
        docs = [_doc("ok"), _doc("pending1"), _doc("depr1"),
                _doc("exp1"), _doc("deleted1"), "无 doc_id 对客必须剔除"]
        self._patch_impl(monkeypatch, docs)
        out = hybrid_retrieve("q", None, None)
        assert [d.metadata.get("doc_id") for d in out] == ["ok"]

    def test_ask_chain_fail_closed(self, monkeypatch):
        h = _Harness()
        h.error = RuntimeError("pg down")
        _patch_registry(monkeypatch, h)
        self._patch_impl(monkeypatch, [_doc("any")])
        with pytest.raises(ReviewVisibilityUnavailableError):
            hybrid_retrieve("q", None, None)


class _FakeContextPipeline:
    """绕过 __init__ 的最小 pipeline 骨架（沿用 runtime_modes 测试模式）。"""

    def __init__(self, docs):
        self.bm25 = None  # BM25 腿软降级路径，专注向量腿可见性
        self.chunk_retriever = type(
            "R", (),
            {
                "_docs": docs,
                "retrieve": lambda self, q, k=5, metadata_filter=None:
                    list(self._docs),
                "vectordb": type(
                    "V", (),
                    {
                        "_docs": docs,
                        "similarity_search_with_score":
                            lambda self, q, k=10, filter=None:
                                [(d, 0.5) for d in self._docs],
                    },
                )(),
            },
        )()

    def ensure_retrieval_index_fresh(self):
        pass

    def _prepare_context(self, *a, **k):
        pass

    def _cleanup(self):
        pass

    def _doc_matches_filter(self, meta, filt):
        return True

    def _constrain_filter_to_authorized(self, mf, authorized):
        return dict(mf or {})


class TestPipelineExits:
    """pipeline 两个公共检索出口（/retrieve、/rag/search、remote 的实现层）。"""

    @pytest.fixture(autouse=True)
    def _no_kb_authz(self, monkeypatch):
        """屏蔽 KB 授权矩阵（1A 只验证审核可见性维度，授权有独立测试）。"""
        monkeypatch.setattr(
            "backend.rag.authz.retrieval_authorized_kbs",
            lambda *a, **k: None)

    def _pipeline(self, docs):
        return _FakeContextPipeline(docs)

    def test_retrieve_knowledge_hides_blocked_states(self, monkeypatch):
        from backend.rag import pipeline as module

        h = _Harness()
        h.blocked_rows = [{"doc_id": "pending1"}, {"doc_id": "depr1"}]
        h.expired_rows = [{"doc_id": "exp1"}]
        h.extra_blocked = {"deleted1"}
        _patch_registry(monkeypatch, h)
        p = self._pipeline([_doc("ok"), _doc("pending1"), _doc("depr1"),
                            _doc("exp1"), _doc("deleted1")])
        text = module.RAGPipeline.retrieve_knowledge(
            p, "q", kb_id="kb", subject_type="employee", department="general")
        assert text.count("正文") == 1, "四类不可见状态全部剔除"

    def test_internal_system_subject_missing_doc_id_also_dropped(
            self, monkeypatch):
        """终轮问题3：system_subject 来自请求体不可信，内部主体豁免已关闭——
        缺 doc_id（无法确认生命周期）即使带 system_subject 也一律剔除。
        存量数据修复路径 = 重索引（见审计报告 §10），不改检索放行语义。"""
        from backend.rag import pipeline as module

        h = _Harness()
        _patch_registry(monkeypatch, h)
        p = self._pipeline([_doc("ok"), "缺 doc_id 存量"])
        text = module.RAGPipeline.retrieve_knowledge(
            p, "q", kb_id="kb", subject_type="employee", department="general",
            system_subject="travel_domain")
        assert text.count("正文") == 1 and "缺 doc_id 存量" not in text

    def test_retrieve_knowledge_fail_closed(self, monkeypatch):
        from backend.rag import pipeline as module

        h = _Harness()
        h.error = RuntimeError("pg down")
        _patch_registry(monkeypatch, h)
        p = self._pipeline([_doc("ok")])
        with pytest.raises(ReviewVisibilityUnavailableError):
            module.RAGPipeline.retrieve_knowledge(
                p, "q", kb_id="kb", subject_type="employee",
                department="general")

    def test_retrieve_documents_hides_blocked_states(self, monkeypatch):
        from backend.rag import pipeline as module

        h = _Harness()
        h.blocked_rows = [{"doc_id": "pending1"}]
        h.extra_blocked = {"deleted1"}
        _patch_registry(monkeypatch, h)
        p = self._pipeline([_doc("ok"), _doc("pending1"), _doc("deleted1"),
                            "无 doc_id"])
        docs = module.RAGPipeline.retrieve_documents(
            p, "q", kb_id="kb", subject_type="employee", department="general")
        assert [d.metadata.get("doc_id") for d in docs] == ["ok"]

    def test_retrieve_documents_fail_closed(self, monkeypatch):
        from backend.rag import pipeline as module

        h = _Harness()
        h.verify_error = RuntimeError("pg down")
        _patch_registry(monkeypatch, h)
        p = self._pipeline([_doc("ok")])
        with pytest.raises(ReviewVisibilityUnavailableError):
            module.RAGPipeline.retrieve_documents(
                p, "q", kb_id="kb", subject_type="employee",
                department="general")


# ────────────────── 修5：rag-server 端点（/retrieve、/retrieve_docs） ──────────────────

class TestRagServerEndpoints:
    def _client(self, monkeypatch, pipeline_obj):
        from fastapi.testclient import TestClient
        import backend.services.rag_server as rs

        monkeypatch.setattr(rs, "_get_pipeline", lambda: pipeline_obj)
        return TestClient(rs.app)

    def test_retrieve_200(self, monkeypatch):
        import backend.services.rag_server as rs

        pipe = SimpleNamespace(
            retrieve_knowledge=lambda *a, **k: "内容",
            is_index_stale=False)
        resp = self._client(monkeypatch, pipe).post(
            "/retrieve", json={"question": "q", "kb_id": "kb",
                               "system_subject": "t"})
        assert resp.status_code == 200
        assert resp.json()["result"] == "内容"

    def test_retrieve_registry_down_503(self, monkeypatch):
        import backend.services.rag_server as rs

        def _boom(*a, **k):
            raise ReviewVisibilityUnavailableError("pg down")

        pipe = SimpleNamespace(retrieve_knowledge=_boom, is_index_stale=False)
        resp = self._client(monkeypatch, pipe).post(
            "/retrieve", json={"question": "q", "kb_id": "kb",
                               "system_subject": "t"})
        assert resp.status_code == 503, "审核事实源故障必须 503，不得 200 空结果"
        assert "拒绝" in resp.json()["detail"]

    def test_retrieve_docs_registry_down_503(self, monkeypatch):
        import backend.services.rag_server as rs

        def _boom(*a, **k):
            raise ReviewVisibilityUnavailableError("pg down")

        pipe = SimpleNamespace(retrieve_documents=_boom, is_index_stale=False)
        resp = self._client(monkeypatch, pipe).post(
            "/retrieve_docs", json={"question": "q", "kb_id": "kb"})
        assert resp.status_code == 503


# ────────────────── 修5：remote 代理（client 503 → 结构化异常） ──────────────────

class TestRemoteProxy:

    def _proxy(self, monkeypatch, status_code):
        import httpx

        from backend.rag.client import RAGServiceProxy

        def handler(request):
            return httpx.Response(status_code, json={"detail": "拒绝检索"})

        transport = httpx.MockTransport(handler)
        proxy = RAGServiceProxy.__new__(RAGServiceProxy)
        proxy._base_url = "http://test"
        proxy._client = httpx.Client(transport=transport, base_url="http://test")
        return proxy

    def test_retrieve_knowledge_503_converted(self, monkeypatch):
        proxy = self._proxy(monkeypatch, 503)
        with pytest.raises(ReviewVisibilityUnavailableError):
            proxy.retrieve_knowledge("q", "kb", system_subject="t")

    def test_retrieve_documents_503_converted(self, monkeypatch):
        proxy = self._proxy(monkeypatch, 503)
        with pytest.raises(ReviewVisibilityUnavailableError):
            proxy.retrieve_documents("q", "kb")

    def test_other_errors_keep_runtime_error(self, monkeypatch):
        """非 503 的服务错误维持既有 RuntimeError 语义（不扩大变更面）。"""
        proxy = self._proxy(monkeypatch, 500)
        with pytest.raises(RuntimeError) as ei:
            proxy.retrieve_knowledge("q", "kb", system_subject="t")
        assert not isinstance(ei.value, ReviewVisibilityUnavailableError)


# ────────────────── 修5：app /rag/search 端点（本地分支 503 语义） ──────────────────

class TestAppSearchEndpoint:

    def _client(self, monkeypatch, pipeline_obj):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        import backend.app.api.routes.rag_search as mod

        principal = SimpleNamespace(
            authenticated=True, subject_type="employee",
            department="general", roles=(), permissions=("general",),
            user_id="u1", tenant_id="default",
        )
        monkeypatch.setattr(mod, "require_principal", lambda request: principal)
        monkeypatch.setattr(mod, "get_rag_pipeline", lambda: pipeline_obj)
        app = FastAPI()
        app.include_router(mod.router)
        return TestClient(app)

    def test_search_ok(self, monkeypatch):
        import backend.app.api.routes.rag_search as mod

        # doc 带 kb_id/permission_scope：端点出口还有一层 can_read_row 双保险
        ok = Document(page_content="正文", metadata={
            "doc_id": "ok", "kb_id": "policy_general",
            "permission_scope": "general"})
        pipe = SimpleNamespace(retrieve_documents=lambda *a, **k: [ok],
                               is_index_stale=False)
        resp = self._client(monkeypatch, pipe).post(
            "/search", json={"query": "制度", "kb_id": "policy_general"})
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    def test_search_registry_down_503_not_empty_200(self, monkeypatch):
        """返修2/3 验收：/rag/search 事实源故障必须 503，
        不得落入通用 except 伪装成「200 + 空结果」。"""
        import backend.app.api.routes.rag_search as mod

        def _boom(*a, **k):
            raise ReviewVisibilityUnavailableError("pg down")

        pipe = SimpleNamespace(retrieve_documents=_boom, is_index_stale=False)
        resp = self._client(monkeypatch, pipe).post(
            "/search", json={"query": "制度", "kb_id": "policy_general"})
        assert resp.status_code == 503
        body = resp.json()
        assert "拒绝" in body.get("detail", "")


# ────────── 终轮返修：审核/下线/删除后答案缓存失效 + system_subject 豁免关闭 ──────────

class _ReviewStub:
    """review_service 的最小 stub 集（只 mock 外部边界）。"""

    def __init__(self, status="pending_review", kb_id="kb_a"):
        self.status = status
        self.kb_id = kb_id

    def registry(self):
        st = self

        class R:
            def get_by_doc_id(self, doc_id):
                return {"doc_id": doc_id, "status": st.status,
                        "kb_id": st.kb_id, "file_path": "t://x.md"}

            def update_status_by_doc_id(self, doc_id, new_status):
                return 1

            def mark_deleted_by_doc_id(self, doc_id):
                return 1

        return R()

    def pipeline(self):
        class Store:
            def delete_all_collections_by_doc_id(self, doc_id):
                pass

            def delete(self, where=None):
                pass

            def get(self, where=None):
                return {"ids": []}

        class ChunkStore:
            def delete_by_doc_id(self, doc_id):
                pass

            def count_by_doc_id(self, doc_id):
                return 0

        class P:
            vectordb = Store()
            doc_db = Store()
            chunk_store = ChunkStore()
            bm25_store = None

            def remove_documents_from_bm25(self, ids, file_paths=None):
                pass

        return P()


class TestAnswerCacheInvalidatedOnReview:
    """终轮问题2：pending 审批/拒绝、删除后，同 KB 答案缓存必须整体失效，
    否则已缓存答案会绕过审核过滤继续返回不可见内容。"""

    def _patch_cache(self, monkeypatch):
        calls = []

        class FakeCache:
            def invalidate_kb(self, kb_id):
                calls.append(kb_id)

        monkeypatch.setattr(
            "backend.rag.answer_cache.get_answer_cache", lambda: FakeCache())
        return calls

    def test_approve_invalidates_kb_cache(self, monkeypatch):
        from backend.rag.indexing.review_service import review_pending_doc

        calls = self._patch_cache(monkeypatch)
        result = review_pending_doc(
            "d1", "approve", registry=_ReviewStub().registry(),
            pipeline=_ReviewStub().pipeline())
        assert result.get("ok"), result
        assert calls == ["kb_a"], "approve 后必须失效该 KB 答案缓存"

    def test_reject_invalidates_kb_cache(self, monkeypatch):
        from backend.rag.indexing import review_service as rs

        calls = self._patch_cache(monkeypatch)
        monkeypatch.setattr(rs, "_get_chunk_store",
                            lambda: _ReviewStub().pipeline().chunk_store)
        result = rs.review_pending_doc(
            "d1", "reject", registry=_ReviewStub().registry(),
            pipeline=_ReviewStub().pipeline())
        assert result.get("ok"), result
        assert calls == ["kb_a"], "reject 后必须失效该 KB 答案缓存"

    def test_delete_cascade_invalidates_kb_cache(self, monkeypatch):
        from backend.rag.indexing import review_service as rs

        calls = self._patch_cache(monkeypatch)
        monkeypatch.setattr(rs, "_get_chunk_store",
                            lambda: _ReviewStub().pipeline().chunk_store)
        result = rs.delete_document_cascade(
            "d1", registry=_ReviewStub().registry(),
            pipeline=_ReviewStub().pipeline())
        assert result.get("ok"), result
        assert calls == ["kb_a"], "删除后必须失效该 KB 答案缓存"


class TestSystemSubjectExemptClosed:
    """终轮问题3：system_subject 来自请求体、不可作为可信来源，
    缺 doc_id 的存量豁免必须对内部主体同样关闭。"""

    @pytest.fixture(autouse=True)
    def _no_kb_authz(self, monkeypatch):
        """屏蔽 KB 授权矩阵（本类只验证审核可见性维度）。"""
        monkeypatch.setattr(
            "backend.rag.authz.retrieval_authorized_kbs",
            lambda *a, **k: None)

    def test_internal_system_subject_no_missing_doc_id_exempt(
            self, monkeypatch):
        from backend.rag import pipeline as module

        h = _Harness()
        _patch_registry(monkeypatch, h)
        p = _FakeContextPipeline([_doc("ok"), "缺 doc_id 存量"])
        text = module.RAGPipeline.retrieve_knowledge(
            p, "q", kb_id="kb", subject_type="employee", department="general",
            system_subject="travel_domain")
        assert "缺 doc_id 存量" not in text, (
            "system_subject 不可信：缺 doc_id 文档不得放行")
        assert text.count("正文") == 1
