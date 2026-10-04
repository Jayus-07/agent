"""test_cs_faq_gap_review.py — FAQ 缺口周检脚本单元测试（C6，2026-10-04）。

只 mock 外部边界（PG 连接/FAQStore 匹配）；断言三类判定状态：
  open（需人工处置）/ closed_now（已闭环）/ excluded（探活垃圾）。
"""
from __future__ import annotations

from backend.scripts import cs_faq_gap_review as gr


class StubCursor:
    def __init__(self, rows_by_prefix):
        self._rows_by_prefix = rows_by_prefix
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        self._rows = []
        if s.startswith("SELECT question, count(*)"):
            self._rows = self._rows_by_prefix["misses"]
        elif s.startswith("SELECT DISTINCT c.doc_id"):
            self._rows = self._rows_by_prefix["hints"]
        elif s.startswith("SELECT id, question, variants, answer, kb_refs"):
            self._rows = []
        elif s.startswith("SELECT id, question, variants, answer, kb_refs, valid_until"):
            self._rows = []
        elif s.startswith("CREATE TABLE"):
            self._rows = []

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return None


class StubConn:
    def __init__(self, rows_by_prefix):
        self._rows_by_prefix = rows_by_prefix

    def cursor(self, cursor_factory=None):
        return StubCursor(self._rows_by_prefix)

    def rollback(self):
        pass


class FakeStore:
    """替身 FAQStore：match 结果按问题前缀决定，_ensure_tables 空操作。"""

    hits = {"已修复的问题": 99}

    def __init__(self, conn_factory=None):
        pass

    def _ensure_tables(self):
        pass

    def match(self, q):
        from backend.customer_service.faq import FAQMatch

        if q in self.hits:
            return FAQMatch(self.hits[q], q, "答案", 1.0, "exact")
        return None


def _make_conn():
    return StubConn({
        "misses": [
            ("退款可以换微信收吗", 3, None, None),
            ("已修复的问题", 2, None, None),
            ("diag", 1, None, None),
        ],
        "hints": [("e94c3fb04d", "登录页点「忘记密码」…")],
    })


def test_collect_report_statuses(monkeypatch):
    monkeypatch.setattr(gr, "FAQStore", FakeStore)
    report = gr.collect_report(_make_conn(), days=7, limit=50)

    by_q = {g["question"]: g for g in report["gaps"]}
    # 真实 miss 且现在仍未命中 → open + 带 KB 线索
    assert by_q["退款可以换微信收吗"]["status"] == "open"
    assert by_q["退款可以换微信收吗"]["count"] == 3
    assert by_q["退款可以换微信收吗"]["kb_hints"][0]["doc_id"] == "e94c3fb04d"
    # 早期 miss、现在已能命中 → closed_now（闭环留证）
    assert by_q["已修复的问题"]["status"] == "closed_now"
    assert by_q["已修复的问题"]["faq_id"] == 99
    # 探活垃圾 → excluded，不进缺口面
    assert report["excluded"][0]["question"] == "diag"
    assert report["open_count"] == 1
    assert report["closed_now_count"] == 1
