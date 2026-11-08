"""PDF 解析资源预算（2026-11-09 审计）。

正常 PDF 不受影响；页数、单页面积、总页面面积任一超限即拒绝。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.rag.preprocessing.parser import pdf_parser


class _FakeDoc:
    def __init__(self, areas: list[tuple[float, float]]):
        self._areas = areas

    def __len__(self) -> int:
        return len(self._areas)

    def __getitem__(self, index: int):
        width, height = self._areas[index]
        return SimpleNamespace(rect=SimpleNamespace(width=width, height=height))


def test_page_count_budget(monkeypatch):
    monkeypatch.setattr(pdf_parser, "RAG_PDF_MAX_PAGES", 2)
    doc = _FakeDoc([(595, 842)] * 3)
    assert "页数 3 超过上限 2" in (pdf_parser.pdf_budget_error(doc) or "")


def test_single_page_area_budget(monkeypatch):
    monkeypatch.setattr(pdf_parser, "RAG_PDF_MAX_PAGE_AREA", 1_000_000.0)
    doc = _FakeDoc([(595, 842), (5000, 5000)])
    error = pdf_parser.pdf_budget_error(doc)
    assert error and "第 2 页面积" in error


def test_total_area_budget(monkeypatch):
    monkeypatch.setattr(pdf_parser, "RAG_PDF_MAX_TOTAL_PAGE_AREA", 1_000_000.0)
    doc = _FakeDoc([(595, 842)] * 3)
    error = pdf_parser.pdf_budget_error(doc)
    assert error and "总页面面积超过上限" in error


def test_normal_pdf_passes():
    doc = _FakeDoc([(595, 842)] * 100)
    assert pdf_parser.pdf_budget_error(doc) is None
