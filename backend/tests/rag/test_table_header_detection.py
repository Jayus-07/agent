"""tests/rag/test_table_header_detection.py — 表格多级表头检测回归

2026-10-28 修复：数值启发式对纯文本数据表（员工名册/文字台账，0% 数值
单元格）失效——扫描窗口内找不到数据行，前 N 行数据全被拍进多级表头
列摘要（`工号_E0001_E0002_E0003` 式），行语义全毁，模型合理拒答
（desktop_upload UFQ-023）。契约：
  1. 文本型数据表：窗口耗尽未见数值行 → 回退首行表头，数据行保留完整行语义；
  2. 数值型多级表头（财务表）：检测行为不变；
  3. 行级 kv 输出可被模型直接阅读。
"""
from __future__ import annotations

from backend.rag.preprocessing.parser._table_nl import (
    detect_multi_level_header,
    flatten_multi_level_header,
    row_to_kv,
)

EMPLOYEE_ROWS = [
    ["工号", "姓名", "部门", "入职日期"],
    ["E0001", "员工1", "客服", "2023-03-19"],
    ["E0002", "员工2", "研发", "2023-03-11"],
    ["E0003", "员工3", "财务", "2023-12-05"],
    ["E0004", "员工4", "销售", "2024-01-05"],
]


def test_text_table_falls_back_to_first_row_header():
    hc, header_rows, data_rows = detect_multi_level_header(EMPLOYEE_ROWS)
    assert hc == 1
    assert header_rows == [EMPLOYEE_ROWS[0]]
    assert len(data_rows) == 4
    flat = flatten_multi_level_header(header_rows)
    assert flat == ["工号", "姓名", "部门", "入职日期"]


def test_text_table_kv_preserves_row_semantics():
    hc, header_rows, data_rows = detect_multi_level_header(EMPLOYEE_ROWS)
    header = flatten_multi_level_header(header_rows)
    kv = row_to_kv(header, data_rows[0], "员工")
    assert "工号 E0001" in kv
    assert "部门 客服" in kv
    assert "入职日期 2023-03-19" in kv
    # 行语义不再被列摘要吞掉
    assert "E0002" not in kv and "员工2" not in kv


def test_numeric_multilevel_header_detection_unchanged():
    fin = [
        ["资产", "负债"],
        ["流动资产", "应付账款"],
        ["100", "200"],
        ["300", "400"],
    ]
    hc, header_rows, data_rows = detect_multi_level_header(fin)
    assert hc == 2
    flat = flatten_multi_level_header(header_rows)
    assert flat == ["资产_流动资产", "负债_应付账款"]
    assert len(data_rows) == 2


def test_numeric_first_data_row_still_breaks_at_row2():
    rows = [
        ["SKU", "名称", "价格"],
        ["000001", "商品1", "311"],
        ["000002", "商品2", "104"],
    ]
    hc, _, data_rows = detect_multi_level_header(rows)
    assert hc == 1
    assert len(data_rows) == 2
