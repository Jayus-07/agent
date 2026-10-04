"""city-guide ①级文档摘要回归（2026-10-04，验收清单 K2）。

此前 `_doc_summary` 导入不存在的符号 `DocRegistryPG`（实际类名
`PostgresDocumentRegistry`），ImportError 被兜底 except 吞掉 → ①级摘要
恒降级为空。回归两层：
1. 符号存在性：从 doc_registry_pg 能导入 PostgresDocumentRegistry（真正的断点）；
2. 行为：匹配目的地的 travel_guide 文档返回非空 summary，不匹配返回 None。
"""

from __future__ import annotations

import backend.rag.indexing.doc_registry_pg as registry_module
from backend.travel.services import city_guide_service


def test_registry_class_symbol_exists():
    """K2 断点回归：city_guide_service 函数内导入的类名必须真实存在。"""
    assert hasattr(registry_module, "PostgresDocumentRegistry")


class StubRegistry:
    def __init__(self, docs):
        self._docs = docs

    def list_by_kb(self, kb_id):
        return list(self._docs)


def _doc(destination: str, fname: str, summary: str) -> dict:
    return {
        "doc_id": "d1",
        "doc_type": "travel_guide",
        "file_name": fname,
        "metadata": {"destination": destination},
        "summary": summary,
    }


def test_doc_summary_returns_summary_for_matching_destination(monkeypatch):
    stub = StubRegistry([
        _doc("福州", "福州-景点-三坊七巷.md", "三坊七巷是福州历史文化街区。"),
    ])
    monkeypatch.setattr(registry_module, "PostgresDocumentRegistry", lambda: stub)

    out = city_guide_service._doc_summary("福州")

    assert out is not None
    assert out["summary"] == "三坊七巷是福州历史文化街区。"
    assert out["doc_id"] == "d1"


def test_doc_summary_returns_none_for_other_destination(monkeypatch):
    stub = StubRegistry([
        _doc("福州", "福州-景点-三坊七巷.md", "三坊七巷是福州历史文化街区。"),
    ])
    monkeypatch.setattr(registry_module, "PostgresDocumentRegistry", lambda: stub)

    assert city_guide_service._doc_summary("厦门") is None
