"""test_pdf_heading.py — PDF 标题启发式（字号 + 编号模式）。"""
from backend.rag.preprocessing.parser.pdf_parser import (
    _body_font_size,
    _is_heading_line,
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


def test_decimal_clause_is_not_heading():
    """小数编号条款（2.1 / 3.2 / 6.2）是正文子条款，不是章节标题。

    回归：旧模式 \\d+(?:\\.\\d+)*[.、] 回溯后会匹配 "2." 前缀，把每个条款
    误判成标题，使「二、七天无理由退货」这类真标题变成孤立空 section，
    其正文 chunk 丢失标题上下文 → 检索按"七天无理由"查不到该 chunk。
    """
    for line in (
        "2.1 适用条件：商品不影响二次销售，包装配件齐全。",
        "2.2 时间窗口：客户签收后 7 天（含）内申请。",
        "3.1 商品存在质量问题（破损/错发/性能故障），全额退款。",
        "6.2 财务审核后 5 个工作日内付款。",
    ):
        assert _is_heading_line(line, 10.0, 10.0) is False, line


def test_integer_numbered_heading_still_detected():
    """整数编号标题（1. / 10、）仍应识别为标题，防止修复过度。"""
    assert _is_heading_line("1. 概述", 10.0, 10.0) is True
    assert _is_heading_line("10、附录", 10.0, 10.0) is True
    assert _is_heading_line("二、七天无理由退货", 10.0, 10.0) is True
    assert _is_heading_line("第三章 标准与限额", 10.0, 10.0) is True


def test_arabic_numbered_body_clause_excluded():
    """TD-02 回归（2026-10-02）：同字号阿拉伯编号条款行判回正文。

    实测（多部门知识库验收）：制度类 PDF 的 "1. xxx；" 条目行与标题
    同字号，无差别判标题会把整份文档条目吞进 section → leaf 层清空、
    节标题丢失、质量门禁 cleaned_chars 误杀（14 < 20）。
    行尾句读与长行是条款的强特征；短编号无标点（"1. 概述"）仍是标题。
    """
    # 行尾句读（中文制度条款强特征）→ 正文
    assert _is_heading_line("1. 办公账号密码长度不少于 12 位，须包含大小写字母；", 11.0, 11.0) is False
    assert _is_heading_line("2. 密码每 90 天强制更换，不得与历史密码重复。", 11.0, 11.0) is False
    # 长行（> 40 字符）无尾标点也判正文
    long_clause = "1. 办公账号密码长度不得少于 12 位且必须包含大小写字母数字与特殊符号共四类字符"
    assert len(long_clause) > 40
    assert _is_heading_line(long_clause, 11.0, 11.0) is False
    # 大字号编号行不受排除影响（字号分支先行命中）
    assert _is_heading_line("1. 办公账号密码长度不少于 12 位，须包含大小写字母；", 13.5, 11.0) is True
    # 中文编号与"第N章"不受排除影响（同字号标题语义保留）
    assert _is_heading_line("一、账号密码管理", 11.0, 11.0) is True
    assert _is_heading_line("第一章 总则", 11.0, 11.0) is True
