"""test_pdf_cross_page_table.py — PDF 跨页表格合并（保守规则）回归。

合并契约（2026-10-24）：
- 页 p 最后一个内容事件是表格 + 页 p+1 首个表格前仅页眉级短噪声 + 列数
  一致 → 数据行并入页 p 表格；重复表头丢弃后续页表头行；
- 正文在表格后（页尾非表格）/ 首表格前长正文 → 不合并（宁漏勿错）；
- 每次合并留痕（parse_issues: cross_page_table_merged, info）。
"""
from backend.rag.preprocessing.parser.pdf_parser import _merge_cross_page_tables


def _table_event(rows, page, y=500.0):
    return (y, "table", rows, page, (0, y, 500, y + 100))


def _text_event(text, page, y=50.0):
    return (y, "text", (text, None), page, (0, y, 500, y + 12))


HEADER = ["项目", "单价(元)", "数量"]


def test_repeated_header_cross_page_merged():
    """续页重复表头 → 丢弃表头行，数据行并入首页表格。"""
    page1 = [
        _text_event("技术服务合同", 1, y=50),
        _table_event([HEADER, ["基础运维", "6294", "1"]], 1),
    ]
    page2 = [
        _text_event("— 8 —", 2, y=30),  # 页眉页码级短噪声
        _table_event([HEADER, ["系统巡检", "28240", "2"]], 2),
        _text_event("以上费用按季度结算。", 2, y=600),
    ]
    page_events = [page1, page2]

    records = _merge_cross_page_tables(page_events)

    assert len(records) == 1
    assert records[0]["appended_rows"] == 1
    assert records[0]["header_repeated"] is True
    # 数据并入 T1，表头不重复
    assert page_events[0][1][2] == [HEADER, ["基础运维", "6294", "1"],
                                    ["系统巡检", "28240", "2"]]
    # T2 事件已从续页移除，页眉与正文保留
    remaining = [e[1] for e in page_events[1]]
    assert remaining == ["text", "text"]


def test_headerless_continuation_merged():
    """续页无表头（数据行直接开始）→ 直接并入。"""
    page_events = [
        [_table_event([HEADER, ["A", "1", "1"]], 1)],
        [_table_event([["B", "2", "2"]], 2)],
    ]
    records = _merge_cross_page_tables(page_events)

    assert len(records) == 1
    assert records[0]["header_repeated"] is False
    assert page_events[0][0][2] == [HEADER, ["A", "1", "1"], ["B", "2", "2"]]


def test_table_not_at_page_end_not_merged():
    """表格后还有正文（页尾非表格）→ 不合并（表格可能已完整结束）。"""
    page1 = [
        _table_event([HEADER, ["A", "1", "1"]], 1),
        _text_event("表格后说明文字。", 1, y=600),
    ]
    page2 = [_table_event([["B", "2", "2"]], 2)]
    assert _merge_cross_page_tables([page1, page2]) == []


def test_long_prose_before_next_table_not_merged():
    """续页首表格前有长正文（不是页眉噪声）→ 视为独立表格，不合并。"""
    page1 = [_table_event([HEADER, ["A", "1", "1"]], 1)]
    page2 = [
        _text_event("本节说明付款方式与结算周期，逾期处理与违约责任如下所述。", 2),
        _table_event([["B", "2", "2"]], 2),
    ]
    assert _merge_cross_page_tables([page1, page2]) == []


def test_column_count_mismatch_not_merged():
    """列数不一致 → 独立表格，不合并。"""
    other_header = ["序号", "名称", "规格", "单位"]
    page1 = [_table_event([HEADER, ["A", "1", "1"]], 1)]
    page2 = [_table_event([other_header, ["1", "B", "S", "件"]], 2)]
    assert _merge_cross_page_tables([page1, page2]) == []


def test_chain_merge_across_three_pages():
    """跨 3 页链式合并：p1+p2 合并后继续与 p3 合并。"""
    page_events = [
        [_table_event([HEADER, ["A", "1", "1"]], 1)],
        [_table_event([HEADER, ["B", "2", "2"]], 2)],
        [_table_event([["C", "3", "3"]], 3)],
    ]
    records = _merge_cross_page_tables(page_events)

    assert len(records) == 2
    # p1 表格最终含全部 3 行数据
    assert page_events[0][0][2] == [HEADER, ["A", "1", "1"], ["B", "2", "2"],
                                    ["C", "3", "3"]]
    assert page_events[1] == []
    assert page_events[2] == []
