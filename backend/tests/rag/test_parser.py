# backend/tests/rag/test_parser.py
import os

from backend.rag.preprocessing.ast import walk
from backend.rag.preprocessing.parser import parse_file

MD = """# 售后制度
## 退货流程
### 审核
客服审核退货原因。
### 验货
仓库检查商品。
## 差评处理
48小时内给出方案。
"""


def test_markdown_parser_builds_tree(tmp_path):
    p = tmp_path / "a.md"
    p.write_text(MD, encoding="utf-8")
    ast = parse_file(str(p))
    sections = {n.text: n.level for n in walk(ast.root) if n.type == "section" and n.level > 0}
    assert sections == {"售后制度": 1, "退货流程": 2, "审核": 3, "验货": 3, "差评处理": 2}
    # 叶子内容挂在对应 section 下
    leaves = [n.text for n in walk(ast.root) if n.type == "paragraph"]
    assert "客服审核退货原因。" in leaves and "仓库检查商品。" in leaves


def test_parse_file_dispatch_by_extension(tmp_path):
    t = tmp_path / "b.txt"
    t.write_text("一、退货流程\n提交申请。\n\n二、审核\n客服审核。\n", encoding="utf-8")
    ast = parse_file(str(t))
    titles = [n.text for n in walk(ast.root) if n.type == "section" and n.level > 0]
    assert titles == ["退货流程", "审核"]


def test_excel_parser_not_implemented(tmp_path):
    from backend.rag.preprocessing.parser.excel_parser import ExcelParser
    import pytest
    with pytest.raises(NotImplementedError):
        ExcelParser().parse(str(tmp_path / "c.xlsx"))
