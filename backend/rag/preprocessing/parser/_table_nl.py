"""表格 → 自然语言转换工具。

背景：原表格 chunk 的 text 是 CSV 格式（如 "异常类型, 处理时效, 责任人\n丢件, 48 小时内核查, 物流客服"），
CrossEncoder rerank 不擅长"列名+数据"格式，给表格 chunk 打分偏低，导致表格内容被叙述段 chunk 顶替。

修复：把 CSV 风格转换成自然语言描述（含行数、列名、每行数据），让 CrossEncoder 能识别语义。

保留原始 rows 用于表格展示（前端可渲染为 HTML <table>）。
"""
from __future__ import annotations

from typing import List, Optional


def table_to_natural_language(
    rows: List[List[str]],
    *,
    section_title: str = "",
    max_rows: int = 50,
) -> str:
    """把表格 rows（二维数组）转成自然语言描述。

    格式：
        [section_title] 共 N 行数据，列：col1, col2, col3。
        - row1_col1 是 row1_col2，row1_col3
        - row2_col1 是 row2_col2，row2_col3
        ...

    Args:
        rows: 二维数组（第一行通常是表头）
        section_title: 所属章节标题，作为语义 hint
        max_rows: 最多展开多少行（防止超大表格把 chunk 撑爆）

    Returns:
        str: 自然语言描述
    """
    if not rows:
        return ""

    # 过滤空行
    rows = [r for r in rows if r and any(cell.strip() for cell in r)]
    if not rows:
        return ""

    # 首行作为列名（heuristic：表格首行通常是 header）
    if len(rows) == 1:
        # 只有一行：当数据处理
        header = None
        data_rows = rows
        col_names = [f"列{i+1}" for i in range(len(rows[0]))]
    else:
        header = rows[0]
        data_rows = rows[1:]
        col_names = [str(c).strip() or f"列{i+1}" for i, c in enumerate(header)]

    # 构建 NL 描述
    parts: list[str] = []

    if section_title:
        parts.append(f"{section_title}：")
    else:
        parts.append("表格数据：")

    parts.append(f"共 {len(data_rows)} 行")

    # 列名列表（去掉明显的"序号"列）
    meaningful_cols = [
        f"{name}" for name in col_names
        if name not in ("序号", "No.", "no.", "No", "#")
    ]
    if meaningful_cols:
        parts.append(f"，列：{', '.join(meaningful_cols)}。")
    else:
        parts.append("。")

    # 展开每行（用 "key=value" 形式）
    truncated = len(data_rows) > max_rows
    shown_rows = data_rows[:max_rows]

    for row in shown_rows:
        # 把行数据拼成 "列1=值1，列2=值2"
        kv_parts: list[str] = []
        for col_name, cell in zip(col_names, row):
            cell_str = str(cell).strip()
            if not cell_str:
                continue
            # 跳过"序号"列
            if col_name in ("序号", "No.", "no.", "No", "#"):
                continue
            kv_parts.append(f"{col_name}={cell_str}")
        if kv_parts:
            parts.append(f"\n- {', '.join(kv_parts)}")

    if truncated:
        parts.append(f"\n（仅显示前 {max_rows} 行，共 {len(data_rows)} 行）")

    return "".join(parts)


def table_to_csv(rows: List[List[str]]) -> str:
    """保留原 CSV 格式（用于 raw_text / 全文检索）。

    不变，保留向后兼容。
    """
    return "\n".join(", ".join(str(c) for c in r) for r in rows)


def make_table_chunk_text(
    rows: List[List[str]],
    *,
    section_title: str = "",
    max_rows: int = 50,
) -> str:
    """生成 chunk 文本：自然语言 + CSV 拼接。

    自然语言在前（让 CrossEncoder 能识别），CSV 在后（保留结构信息，方便正则/关键词检索）。
    """
    nl = table_to_natural_language(rows, section_title=section_title, max_rows=max_rows)
    csv = table_to_csv(rows)
    if nl:
        return f"{nl}\n\n【原始数据】\n{csv}"
    return csv
