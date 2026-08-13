"""test_pdf_heading.py — PDF 标题启发式（字号 + 编号模式）。"""
from backend.rag.preprocessing.parser.pdf_parser import (
    _body_font_size, _is_heading_line,
)


def test_body_font_size_is_mode():
    """正文字号 = 众数（正文占多数，标题字号是少数）。"""
    sizes = [10.5, 10.5, 10.5, 10.5, 10.5, 14.0, 14.0]
    assert _body_font_size(sizes) == 10.5


def test_body_font_size_empty():
    assert _body_font_size([]) == 0.0


def test_is_heading_by_larger_font():
    """字号明显大于正文 → 标题。"""
    assert _is_heading_line("第一章 总则", 14.0, 10.5) is True


def test_is_heading_by_number_pattern():
    """编号模式（第 N 章 / 一、）即使字号不明显也算标题。"""
    assert _is_heading_line("一、适用范围", 10.5, 10.5) is True
    assert _is_heading_line("第三章 安全库存", 10.5, 10.5) is True


def test_is_not_heading_for_body_text():
    """普通正文 → 非标题。"""
    assert _is_heading_line("为规范库存管理，保障账实相符。", 10.5, 10.5) is False
