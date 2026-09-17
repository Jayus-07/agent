"""抽查：新增形态能否被现有解析管线读通（GBK CSV / 多 Sheet XLSX / 复杂版面 PDF / 扫描件 PDF / DOCX）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FIX = ROOT / "backend" / "evaluation" / "fixtures" / "rag_100_docs" / "files"

from backend.rag.preprocessing.parser.csv_parser import CsvParser          # noqa: E402
from backend.rag.preprocessing.parser.excel_parser import ExcelParser      # noqa: E402
from backend.rag.preprocessing.parser.pdf_parser import PdfParser          # noqa: E402
from backend.rag.preprocessing.parser.docx_parser import DocxParser        # noqa: E402


def walk(node, tables, pages, depth=0):
    if getattr(node, "type", "") == "table":
        tables.append(node)
    pn = getattr(node, "page_number", None)
    if pn is not None:
        pages.add(pn)
    for ch in getattr(node, "children", []) or []:
        walk(ch, tables, pages, depth + 1)


def show(label, ast):
    tables, pages = [], set()
    walk(ast.root, tables, pages)
    raw = (ast.raw_text or "")
    print(f"  {label}")
    print(f"    raw_text {len(raw)} 字符 | table 节点 {len(tables)} 个 | "
          f"page_number 取值 {sorted(pages) if pages else '无'}")
    if tables:
        rows = getattr(tables[0], "rows", None)
        print(f"    首个表格首行: {rows[0] if rows else None}")
    preview = raw[:60].replace("\n", " / ")
    print(f"    预览: {preview}")


CASES = [
    ("GBK CSV", CsvParser(), "csv/table_供应商联系人名录_GBK.csv"),
    ("GB18030 CSV", CsvParser(), "csv/table_渠道销售_2025_GB18030.csv"),
    ("UTF-8 无 BOM CSV", CsvParser(), "csv/table_在售产品目录_UTF8无BOM.csv"),
    ("UTF-8-sig CSV", CsvParser(), "csv/table_发票台账_2026.csv"),
    ("多 Sheet XLSX(4)", ExcelParser(), "xlsx/table_2026年度预算表.xlsx"),
    ("多 Sheet XLSX(3)", ExcelParser(), "xlsx/table_2026人力编制台账.xlsx"),
    ("复杂版面 PDF", PdfParser(), "pdf/report_2025年度财务报告.pdf"),
    ("复杂版面 PDF", PdfParser(), "pdf/sop_线上故障应急响应_v3.pdf"),
    ("文本 PDF", PdfParser(), "pdf/policy_费用报销管理办法_v3.pdf"),
    ("扫描件 PDF(应无文字层)", PdfParser(), "pdf/scan_费用报销单.pdf"),
    ("DOCX", DocxParser(), "docx/legal_劳动合同模板_2026版.docx"),
]

fail = 0
for label, parser, rel in CASES:
    p = FIX / rel
    if not p.exists():
        print(f"  !! 文件不存在: {rel}")
        fail += 1
        continue
    try:
        show(f"[{label}] {rel}", parser.parse(str(p)))
    except Exception as e:
        print(f"  !! 解析异常 {rel}: {type(e).__name__}: {e}")
        fail += 1

print("\nPARSE FAILURES:", fail)
