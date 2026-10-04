"""FAQ 双轨回归（C3/C4，2026-10-04）。

关键断言：
  - FAQ 命中路径 **不触达 RAG pipeline**（即不产生任何 LLM/embedding 调用）；
  - 未命中原样落回 RAG 链；
  - 过期（valid_until）与非 published 不参与匹配（fail-closed）；
  - C5 台账：命中与未命中都落 ai.cs_faq_query_log。
存储/PG 用注入的内存桩，只 mock 外部边界。
"""

import pytest

from backend.customer_service import faq as faq_mod
from backend.customer_service.faq import (
    FAQEntry,
    FAQStore,
    jaccard,
    normalize_question,
)


class FakeConn:
    def __init__(self, store):
        self._store = store

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def commit(self):
        pass

    def rollback(self):
        pass

    def cursor(self, cursor_factory=None):
        return FakeCursor(self._store)


class FakeCursor:
    def __init__(self, store):
        self._store = store
        self._result = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        s = self._store
        sql_l = " ".join(sql.split())
        if sql_l.startswith("CREATE TABLE"):
            return
        if sql_l.startswith("INSERT INTO ai.cs_faq"):
            key = params[0]
            s["rows"][key] = {
                "id": 100 + len(s["rows"]), "question": params[1],
                "variants": params[2], "answer": params[3],
                "status": params[4], "kb_refs": params[5], "valid_until": params[6],
            }
            self._result = [(s["rows"][key]["id"],)]
        elif sql_l.startswith("UPDATE ai.cs_faq SET hit_count"):
            for row in s["rows"].values():
                if row["id"] == params[0]:
                    row["hits"] = row.get("hits", 0) + 1
        elif sql_l.startswith("INSERT INTO ai.cs_faq_query_log"):
            s["log"].append({"question": params[0], "matched": params[1],
                             "faq_id": params[2], "latency_ms": params[3]})
        elif sql_l.startswith("SELECT count(*) FROM ai.cs_faq WHERE status"):
            self._result = [(sum(1 for r in s["rows"].values() if r["status"] == "published"),)]
        elif sql_l.startswith("SELECT count(*), count(*) FILTER"):
            total = len(s["log"])
            self._result = [(total, sum(1 for e in s["log"] if e["matched"]))]
        elif sql_l.startswith("SELECT id, question, variants"):
            import datetime
            today = str(datetime.date.today())
            self._result = [
                {"id": r["id"], "question": r["question"], "variants": r["variants"],
                 "answer": r["answer"], "kb_refs": r["kb_refs"],
                 "valid_until": r["valid_until"]}
                for r in s["rows"].values()
                if r["status"] == "published"
                and (not r["valid_until"] or r["valid_until"] >= today)
            ]

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return self._result


@pytest.fixture()
def store():
    s: dict = {"rows": {}, "log": []}
    return FAQStore(conn_factory=lambda: FakeConn(s)), s


class TestNormalizeAndScore:
    def test_normalize_strips_punct_and_case(self):
        assert normalize_question("  退货政策是什么？ ") == normalize_question("退货政策是什么")

    def test_reworded_query_scores_above_threshold(self):
        from backend.customer_service.faq import match_score
        score = match_score(_bigrams("退货的运费由谁承担呢"), _bigrams("退货商品运费谁承担"))
        assert score >= 0.42, f"改写问法得分不足: {score}"
        # 部分相关问题（仅共享「退货」）不得过线
        assert match_score(_bigrams("退货流程是什么"), _bigrams("退货运费谁承担")) < 0.42
        # 无关问法必须接近 0
        assert match_score(_bigrams("今天天气怎么样"), _bigrams("退款多久能到账")) == 0.0


def _bigrams(t: str):
    from backend.customer_service.faq import _bigrams as bg
    return bg(t)


class TestMatch:
    def test_exact_question_hit(self, store):
        st, s = store
        st.upsert_faq("七天无理由退货范围", "签收后 7 天内可申请。")
        m = st.match("七天无理由退货范围")
        assert m is not None and m.matched_by == "exact"
        assert "7 天" in m.answer

    def test_variant_hit(self, store):
        st, s = store
        st.upsert_faq("退款多久能到账", "5-7 个工作日。", variants=["退款几天到账"])
        m = st.match("退款几天到账")
        assert m is not None and m.matched_by == "exact"

    def test_reworded_query_jaccard_hit(self, store):
        st, s = store
        st.upsert_faq("退货运费由谁承担", "质量问题商家承担，无理由退货买家承担。")
        m = st.match("退货的运费谁来承担？")
        assert m is not None and m.score >= 0.42

    def test_unrelated_query_misses(self, store):
        st, s = store
        st.upsert_faq("退款多久能到账", "5-7 个工作日。")
        assert st.match("今天天气怎么样") is None

    def test_function_word_bigrams_not_dominant(self, store):
        """回归（2026-10-04 实测错答）：「密码忘了怎么办」不得命中「工牌丢了怎么办」。"""
        st, s = store
        st.upsert_faq("工牌丢了怎么办", "行政部挂失，3 个工作日补办。")
        st.upsert_faq("密码忘了怎么找回", "通过登录页重置密码。")
        m = st.match("密码忘了怎么办")
        assert m is not None, "应命中密码条目（共享实词「密码」「忘」）"
        assert "工牌" not in m.answer, f"错答: {m.question}"

    def test_expired_faq_excluded(self, store):
        st, s = store
        st.upsert_faq("限时政策是什么", "已过期答案。", valid_until="2020-01-01")
        assert st.match("限时政策是什么") is None

    def test_draft_faq_excluded(self, store):
        st, s = store
        st.upsert_faq("草稿问题是什么", "草稿答案。", status="draft")
        assert st.match("草稿问题是什么") is None


class TestNoLLMOnFaqHit:
    def test_answer_returns_faq_without_touching_pipeline(self, monkeypatch):
        """C4 核心断言：FAQ 命中 → 直接返回，RAG pipeline 一行都不碰。"""
        from backend.customer_service.faq import FAQMatch
        from backend.customer_service.knowledge import service as ks

        class FakeStore:
            def match(self, q):
                return FAQMatch(faq_id=42, question="退款多久能到账",
                                answer="1-3 个工作日。", score=1.0, matched_by="exact")

        called = {"pipeline": False}

        def _boom():
            called["pipeline"] = True
            raise AssertionError("FAQ 命中不得触达 RAG pipeline")

        monkeypatch.setattr(ks, "get_knowledge_service", ks.get_knowledge_service)
        import backend.customer_service.faq as faq_pkg
        monkeypatch.setattr(faq_pkg, "get_faq_store", lambda: FakeStore())
        monkeypatch.setattr("backend.rag.pipeline.get_rag_pipeline", _boom)

        svc = ks.CSKnowledgeService()
        result = svc.answer("退款多久能到账", kb_ids=["cs_faq"], session_id="s1")
        assert result.faq_hit is True
        assert result.faq_id == 42
        assert result.decision.value == "answer"
        assert "1-3" in result.answer
        assert called["pipeline"] is False

    def test_miss_falls_back_to_pipeline(self, monkeypatch):
        from backend.customer_service.knowledge import service as ks

        class FakeStore:
            def match(self, q):
                return None

        class FakeOutcome:
            answer = "RAG 答案"
            answer_meta = {"confidence": 0.9, "can_answer": True}

        class FakePipeline:
            def ask_result(self, **kwargs):
                return FakeOutcome()

        import backend.customer_service.faq as faq_pkg
        monkeypatch.setattr(faq_pkg, "get_faq_store", lambda: FakeStore())
        monkeypatch.setattr("backend.rag.pipeline.get_rag_pipeline", lambda: FakePipeline())

        svc = ks.CSKnowledgeService()
        result = svc.answer("完全不在FAQ的问题xyz", kb_ids=["cs_faq"], session_id="s1")
        assert result.faq_hit is False
        assert result.answer == "RAG 答案"

    def test_faq_layer_failure_degrades_to_rag(self, monkeypatch):
        from backend.customer_service.knowledge import service as ks

        class FakeStore:
            def match(self, q):
                raise RuntimeError("PG 不可用（测试桩）")

        class FakeOutcome:
            answer = "降级答案"
            answer_meta = {"confidence": 0.9, "can_answer": True}

        class FakePipeline:
            def ask_result(self, **kwargs):
                return FakeOutcome()

        import backend.customer_service.faq as faq_pkg
        monkeypatch.setattr(faq_pkg, "get_faq_store", lambda: FakeStore())
        monkeypatch.setattr("backend.rag.pipeline.get_rag_pipeline", lambda: FakePipeline())

        svc = ks.CSKnowledgeService()
        result = svc.answer("随便什么问题", kb_ids=["cs_faq"], session_id="s1")
        assert result.faq_hit is False and result.answer == "降级答案"

    def test_faq_layer_failure_counts_metric(self, monkeypatch):
        """G 告警数据源：FAQ 层故障必须落 cs_faq_layer_failures_total（告警规则消费）。"""
        from backend.customer_service.faq import cs_faq_layer_failures_total
        from backend.customer_service.knowledge import service as ks

        class FakeStore:
            def match(self, q):
                raise RuntimeError("PG 不可用（测试桩）")

        class FakeOutcome:
            answer = "降级答案"
            answer_meta = {"confidence": 0.9, "can_answer": True}

        class FakePipeline:
            def ask_result(self, **kwargs):
                return FakeOutcome()

        import backend.customer_service.faq as faq_pkg
        monkeypatch.setattr(faq_pkg, "get_faq_store", lambda: FakeStore())
        monkeypatch.setattr("backend.rag.pipeline.get_rag_pipeline", lambda: FakePipeline())

        before = cs_faq_layer_failures_total._value.get()
        svc = ks.CSKnowledgeService()
        svc.answer("随便什么问题", kb_ids=["cs_faq"], session_id="s1")
        assert cs_faq_layer_failures_total._value.get() == before + 1
