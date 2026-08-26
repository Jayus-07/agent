"""表格 → 自然语言转换工具。

背景：原表格 chunk 的 text 是 CSV 格式（如 "异常类型, 处理时效, 责任人\n丢件, 48 小时内核查, 物流客服"），
CrossEncoder rerank 不擅长"列名+数据"格式，给表格 chunk 打分偏低，导致表格内容被叙述段 chunk 顶替。

修复：把 CSV 风格转换成自然语言描述（含行数、列名、每行数据），让 CrossEncoder 能识别语义。

保留原始 rows 用于表格展示（前端可渲染为 HTML <table>）。
"""
from __future__ import annotations

from typing import List, Tuple


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


# ── 多级表头检测与拍平 ──────────────────────────────────


def _is_numeric_cell(val: str) -> bool:
    """判断单元格值是否为数值型（财务表头行中数值占比极低）。"""
    from backend.rag.preprocessing.financial_normalizer import is_numeric_cell
    return is_numeric_cell(val)


def detect_multi_level_header(
    rows: List[List[str]],
    max_header_rows: int = 3,
) -> Tuple[int, List[List[str]], List[List[str]]]:
    """启发式检测多级表头：前 N 行中数值型单元格占比 < 20% 视为表头行。

    财务报表常有多级表头：
        第一行："资产" | "负债" (一级)
        第二行："流动资产" | "非流动资产" (二级)
        第三行："货币资金" | "应收账款" (三级)
        第四行：数值数据行

    Args:
        rows: 表格二维数组
        max_header_rows: 最多检测几行表头

    Returns:
        (header_row_count, header_rows, data_rows)
    """
    if not rows or len(rows) < 2:
        return (1, rows[:1], rows[1:]) if rows else (0, [], [])

    header_rows: List[List[str]] = []
    for i, row in enumerate(rows[:max_header_rows + 1]):
        # 空行跳过
        meaningful_cells = [str(c).strip() for c in row if str(c).strip()]
        if not meaningful_cells:
            continue
        numeric_count = sum(1 for c in meaningful_cells if _is_numeric_cell(c))
        numeric_ratio = numeric_count / max(len(meaningful_cells), 1)
        if numeric_ratio < 0.2:
            header_rows.append(row)
        else:
            break

    if not header_rows:
        header_rows = [rows[0]]

    header_count = len(header_rows)
    data_rows = rows[header_count:]
    return (header_count, header_rows, data_rows)


def flatten_multi_level_header(
    header_rows: List[List[str]],
) -> List[str]:
    """将多级表头拍平为 "parent_child" 形式。

    输入:
        [["资产", "资产", "负债"],
         ["流动资产", "非流动资产", "应付账款"]]
    输出:
        ["资产_流动资产", "资产_非流动资产", "负债_应付账款"]

    去重连续相同前缀（避免 "资产_资产"），空值向前继承上级表头。
    """
    if not header_rows:
        return []

    ncols = max(len(r) for r in header_rows)
    # 补齐每行的列数，空值用 "" 填充
    padded = [list(r) + [""] * (ncols - len(r)) for r in header_rows]

    result: List[str] = []
    for col_idx in range(ncols):
        parts: List[str] = []
        for row_idx in range(len(padded)):
            val = str(padded[row_idx][col_idx]).strip()
            if not val:
                continue
            if parts and parts[-1] == val:
                continue  # 去重连续相同
            parts.append(val)
        result.append("_".join(parts) if parts else f"列{col_idx + 1}")
    return result


def normalize_table_rows(rows: List[List[str]]) -> Tuple[List[str], List[List[str]]]:
    """表格行规范化：检测多级表头并拍平，返回 (扁平表头, 数据行)。

    兼容单级表头：只有一行表头时直接返回 (header, data_rows)。
    """
    if not rows:
        return [], []
    header_count, header_rows, data_rows = detect_multi_level_header(rows)
    if header_count <= 1:
        header = [str(c).strip() or f"列{i + 1}" for i, c in enumerate(rows[0])]
        return header, rows[1:]
    flat_header = flatten_multi_level_header(header_rows)
    return flat_header, data_rows


# ── 行级 kv 转换（用于行级 chunk） ──────────────────────────────────


def row_to_kv(
    header: List[str],
    row: List[str],
    section_title: str = "",
) -> str:
    """单行 → kv 文本，指标名与数值用空格分隔，优化 BM25 分词。

    格式：
        [section_title]
        科目 货币资金
        Q3金额 1234567.00
        环比 +5.2%

    与 table_to_natural_language 的区别：
      - 粒度单行（非全表），用于行级 chunk
      - 用空格分隔而非逗号，避免 BM25 分词器把指标名和数值粘在一起
      - 跳过序号列
    """
    parts: list[str] = []
    if section_title:
        parts.append(f"[{section_title}]")
    for col_name, cell in zip(header, row):
        col = str(col_name).strip()
        cell_str = str(cell).strip() if cell is not None else ""
        if not col or not cell_str:
            continue
        if col in ("序号", "No.", "no.", "No", "#"):
            continue
        parts.append(f"{col} {cell_str}")
    return "\n".join(parts)


def build_table_summary(
    rows: List[List[str]],
    section_title: str = "",
) -> str:
    """构建表级摘要文本（parent chunk），包含表名、行列数、列名概述。

    用于表级 parent chunk 的 page_content，供 CrossEncoder 语义匹配。
    """
    if not rows:
        return ""
    # 规范化多级表头
    header, data_rows = normalize_table_rows(rows)
    if not header:
        return ""
    # 去序号列
    meaningful_cols = [
        h for h in header if h not in ("序号", "No.", "no.", "No", "#")
    ]
    parts: list[str] = []
    if section_title:
        parts.append(f"{section_title}：")
    else:
        parts.append("财务表格：")
    parts.append(f"共 {len(data_rows)} 行数据")
    if meaningful_cols:
        parts.append(f"，列：{', '.join(meaningful_cols)}。")
    else:
        parts.append("。")
    return "".join(parts)
