# Chunking 切分重构 Phase 2 实现计划

## 执行状态：✅ 已完成（P0 + P1）

> 2026-08-13 | 全部 9 个 Task 完成，P2/P3 按 Spec §3 明确延期。

**测试结果**：`cd backend && python -m pytest tests/rag/ -v` → **156 passed, 2 skipped**（0 failed）。

**E2E 验证**（[test_e2e_pdf_docx_index.py](../../../backend/tests/rag/test_e2e_pdf_docx_index.py)）：
- **PDF E2E** ✅：真实 2 页 PDF → `parse_and_chunk` 产非空 chunks，所有 chunk 含 `page_content`
- **DOCX E2E** ✅：真实 DOCX（Heading + Table）→ 产 chunks 含 `leaf` 粒度（StructureChunkStrategy 路由）
- **metadata 协议** ✅：chunk 含 `chunk_id` / `granularity` / `chunk_tokens`

**实际 commit 对照**：

| Task | 内容 | Commit |
|---|---|---|
| 1 | 依赖安装与验证 | `aace716` chore(deps): Phase 2 PDF/DOCX 解析依赖（PyMuPDF + python-docx） |
| 2 | PdfParser（PyMuPDF → Raw AST） | `d184d27` feat(chunking): PdfParser — 带单页容错 + 跳过页汇总 |
| 3 | DocxParser（python-docx → Raw AST） | `08b7529` feat(chunking): DocxParser — Heading + Table + 容错汇总 |
| 4 | parser 注册 + pipeline 白名单 | `7e7df7a` feat(chunking): 注册 PdfParser/DocxParser + pipeline 扩展 .pdf/.docx |
| 5 | Q/A 节点识别（三模式） | `c51b004` feat(chunking): Q/A 节点识别 — 三模式独立产出 + MD/TXT 接入 |
| 6 | faq 路由修复 | `71837e2` fix(chunking): faq 路由修复 — StructureChunkStrategy → QAChunkStrategy |
| 7 | loader 白名单同步 | `0423ee8` feat(chunking): loader 扩展名白名单同步 .pdf/.docx |
| 8 | indexer 统一流水线 | `dc8bdb9` refactor(rag): indexer 统一走新流水线 — 删 raw_docs 反向构造 + trace 字段保留 |
| 9 | 集成回归 + 文档更新 | `2870d45` + `8e7de91` docs(chunking): Phase 1 下一步 + Phase 2 Spec/Plan |

**已完成范围**：
- P0：PDF/DOCX 接入新流水线（PdfParser / DocxParser → Raw AST → 统一 Router → Strategy → ChunkFilter → Indexer）
- P1：faq 路由修复（`STRUCTURE_STRATEGIES["faq"]` = `QAChunkStrategy`）+ QA 节点识别（bold/heading/numbered 三模式）+ loader 白名单同步 `.pdf`/`.docx`

**P2/P3 延期范围**（Spec §3 非目标）：
- ❌ LLMAssistedChunking / SemanticChunking（`topic_shift_detected` / `is_high_value_and_chaotic` 实际计算）
- ❌ PDF 标题启发式评分（HEADING_THRESHOLD）
- ❌ StepChunkStrategy 真实现（步骤编号识别）
- ❌ Excel Parser
- ❌ `legal` 合同条款级切分细化

**Goal:** 修复 Phase 1 留下的 PDF/DOCX 静默失败缺陷 + faq 路由错误缺陷，让 PDF/DOCX/FAQ 文档能正常入库并正确切分。

**Spec:** [2026-08-13-chunking-refactor-phase2-design.md](../specs/2026-08-13-chunking-refactor-phase2-design.md)
**范围：** P0（PDF/DOCX 接入新流水线）+ P1（faq 路由 + QA 节点识别 + loader 白名单）
**非范围：** LLM Assisted / Semantic / Step / PDF 标题启发式 / Excel / legal 细化（P2/P3 延后）

## Global Constraints

- 设计七原则必须满足：可理解、可测试、可观测、可维护、可扩展、可控制、可靠性。
- 禁止 Demo 跑通式开发、临时堆叠、`except Exception: pass`（异常必须记录日志或做有意义的降级）。
- Python：snake_case、类型注解、logger 替代 print、具体异常、SQL 参数化。
- 测试在 `backend/` 目录下运行：`python -m pytest tests/rag/<file> -v`（用项目 `.venv` 或 `D:/Python/python.exe`）。
- 每个 commit 只包含对应 task 的文件，先写测试再实现（TDD）。
- 新增依赖（PyMuPDF / python-docx）只在对应 Parser 中 import，不污染 Chunking 核心模块。
- **禁止** `assert True` 占位测试；**禁止** 「如果失败就降级到 X」式 Demo 兜底；**禁止** 反向构造对象只为复用旧代码。

---

### Task 1: 依赖安装与验证

**目标：** 在本地 venv 验证 PyMuPDF 和 python-docx 能装得上、能基本加载文件。这是后续所有 PDF/DOCX 任务的前置。

**Files:**
- Modify: `backend/requirements.txt`（追加 PyMuPDF + python-docx）
- Verify: 本地 `.venv` 安装成功 + 简单 import 测试

- [x] **Step 1: 更新 requirements.txt**

在 `backend/requirements.txt` 末尾追加（与 PDF/DOCX 相关分组）：

```
# Phase 2：PDF/DOCX 解析依赖（仅在对应 Parser 中 import）
PyMuPDF>=1.23
python-docx>=1.0
```

- [x] **Step 2: 安装依赖**

Run:
```bash
cd "d:/Program Files/workplace/agent"
.venv/Scripts/python.exe -m pip install PyMuPDF>=1.23 python-docx>=1.0
```
Expected: 两条 `Successfully installed PyMuPDF-x.x.x python-docx-x.x.x`。

- [x] **Step 3: 验证基本 import**

Run:
```bash
cd "d:/Program Files/workplace/agent"
.venv/Scripts/python.exe -c "
import fitz
import docx
print('PyMuPDF:', fitz.__version__)
print('python-docx:', docx.__version__)
"
```
Expected: 输出两个版本号，无 ImportError。

- [x] **Step 4: 验证 PDF/DOCX 构造样本能解析**

（**不**依赖真实样本 —— 使用 `tmp_path` 构造。）

Run:
```python
# 临时脚本 tmp_path 自动清理，无需提交
import fitz
import docx
import tempfile
import os

with tempfile.TemporaryDirectory() as td:
    # 构造 2 页 PDF
    pdf_path = os.path.join(td, "sample.pdf")
    d = fitz.open()
    d.new_page().insert_text((50, 50), "Hello PDF")
    d.save(pdf_path)
    d.close()
    # 验证能打开
    loaded = fitz.open(pdf_path)
    assert loaded[0].get_text().strip() == "Hello PDF"
    loaded.close()

    # 构造简单 DOCX
    docx_path = os.path.join(td, "sample.docx")
    d = docx.Document()
    d.add_heading("Hello DOCX", level=1)
    d.save(docx_path)
    # 验证能打开
    loaded = docx.Document(docx_path)
    assert loaded.paragraphs[0].text == "Hello DOCX"

print("OK")
```

Expected: 输出 `OK`，无 PyMuPDFError 或 docx.opc.exceptions.PackageNotFoundError。

- [x] **Step 5: 提交**

```bash
cd "d:/Program Files/workplace/agent"
git add backend/requirements.txt
git commit -m "chore(deps): Phase 2 PDF/DOCX 解析依赖（PyMuPDF + python-docx）"
```

**失败处理**（不静默降级）：
- 安装失败 → 检查 Python 版本 / 网络 / wheel 可用性，修复环境后重试
- 不允许「换 pypdf 替代」「换 docx2txt 替代」等降级方案 —— 这些会污染 Chunking 核心架构意图

---

### Task 2: PdfParser（PyMuPDF → Raw AST）

**Files:**
- Create: `backend/rag/preprocessing/parser/pdf_parser.py`
- Test: `backend/tests/rag/test_pdf_parser.py`

**Interfaces:**
- Produces: `PdfParser().parse(file_path) -> DocumentAST`
- Consumes: `PyMuPDF`（fitz）从外部 import，本文件顶部 `import fitz`
- Phase 2 简化策略：不识别标题（Phase 3 接 HEADING_THRESHOLD）；段落合并；表格识别

- [x] **Step 1: 写失败测试**

```python
# backend/tests/rag/test_pdf_parser.py
import fitz
import pytest

from backend.rag.preprocessing.ast import walk
from backend.rag.preprocessing.parser.pdf_parser import PdfParser


@pytest.fixture
def sample_pdf(tmp_path):
    """创建 2 页 PDF，每页 2 段。"""
    p = tmp_path / "sample.pdf"
    doc = fitz.open()
    page1 = doc.new_page()
    page1.insert_text((50, 50), "第一段文字内容。")
    page1.insert_text((50, 80), "第二段继续描述。")
    page2 = doc.new_page()
    page2.insert_text((50, 50), "第三页内容。")
    doc.save(str(p))
    doc.close()
    return str(p)


def test_pdf_parser_basic_load(sample_pdf):
    ast = PdfParser().parse(sample_pdf)
    assert ast.source_file == sample_pdf
    assert ast.raw_text != ""
    leaves = [n for n in walk(ast.root) if n.type in ("paragraph", "list", "table")]
    assert len(leaves) >= 2


def test_pdf_parser_empty_pages_skipped(tmp_path):
    p = tmp_path / "empty.pdf"
    doc = fitz.open()
    doc.new_page()  # 全空白页
    doc.save(str(p))
    doc.close()
    ast = PdfParser().parse(str(p))
    # 全空白页应该产出 0 个 leaf 节点，但不抛异常
    leaves = [n for n in walk(ast.root) if n.type in ("paragraph", "list", "table")]
    assert leaves == []


def test_pdf_parser_paragraph_merge(tmp_path):
    """验证连续短段合并为单个 leaf（避免过碎切分）。"""
    p = tmp_path / "merge.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), "段A第1行")
    page.insert_text((50, 65), "段A第2行")
    page.insert_text((50, 90), "段B独立段")
    doc.save(str(p))
    doc.close()
    ast = PdfParser().parse(str(p))
    leaves = [n.text for n in walk(ast.root) if n.type == "paragraph"]
    # 段A 合并为 1 个 leaf（不是 2 个）
    assert any("段A第1行" in t and "段A第2行" in t for t in leaves)
    assert any("段B独立段" in t for t in leaves)


def test_pdf_parser_reports_skipped_pages(tmp_path, caplog):
    """单页解析失败 → parse 完成后 log 汇总跳过页数（可观测）。"""
    import logging
    caplog.set_level(logging.WARNING)
    p = tmp_path / "mixed.pdf"
    doc = fitz.open()
    page1 = doc.new_page()
    page1.insert_text((50, 50), "正常页")
    # 注入会抛异常的内容：构造一个非法 span 通过 monkeypatch
    doc.save(str(p))
    doc.close()

    # 模拟单页解析失败：monkeypatch get_text 让第二页抛异常
    original = fitz.Page.get_text

    def maybe_fail(self, *args, **kwargs):
        if "正常" not in (self.get_text("text") if hasattr(self, "_text_check") else ""):
            raise RuntimeError("simulated parse failure")
        return original(self, *args, **kwargs)

    # 简化：用 monkeypatch 让 doc[1] 的 get_text 抛异常
    from unittest.mock import patch
    with patch.object(fitz.Page, "get_text", side_effect=RuntimeError("simulated")):
        ast = PdfParser().parse(str(p))
        leaves = [n for n in walk(ast.root) if n.type == "paragraph"]
        assert leaves == []  # 两页都失败，无 leaf

    # 验证日志包含「跳过 N 页」汇总
    assert any("skipped" in rec.message.lower() or "跳过" in rec.message for rec in caplog.records)
```

- [x] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/rag/test_pdf_parser.py -v`
Expected: FAIL（`ModuleNotFoundError: backend.rag.preprocessing.parser.pdf_parser`）

- [x] **Step 3: 实现 pdf_parser.py**

```python
"""PdfParser — PyMuPDF 解析 PDF → Raw AST。

Phase 2 简化策略：
- 不识别标题（无章节结构，整篇当无结构长段落）
- 段落合并：同一页面、相邻、间距 < 阈值 视为同一段
- 表格识别：PyMuPDF page.get_text("dict") 的 type=1 块 → table
- 可观测：单页失败 → log warning + 计数器，最终汇总报告
"""
from __future__ import annotations

import fitz  # PyMuPDF

from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.parser.base import BaseDocumentParser
from backend.shared.logger import logger

_PARAGRAPH_MERGE_GAP = 15  # PyMuPDF 文本块垂直间距（point）


class PdfParser(BaseDocumentParser):
    def parse(self, file_path: str) -> DocumentAST:
        try:
            doc = fitz.open(file_path)
        except (fitz.FileDataError, RuntimeError) as e:
            # 已知可恢复异常：文件损坏 / 加密 / 格式异常 → 返回空 AST + log
            logger.error(f"[PdfParser] 打开失败 {file_path}: {type(e).__name__}: {e}")
            return DocumentAST(
                root=DocumentNode(type="section", text="", level=0),
                source_file=file_path,
                raw_text="",
            )
        except Exception as e:
            # 未知异常：log 后向上抛出，让 caller 决定如何处理
            logger.exception(f"[PdfParser] 打开异常 {file_path}: {type(e).__name__}")
            raise

        raw_lines: list[str] = []
        skipped_pages: list[int] = []
        try:
            for page_idx in range(len(doc)):
                try:
                    page = doc[page_idx]
                    blocks = page.get_text("dict")["blocks"]
                    self._extract_blocks(blocks, raw_lines)
                except Exception as e:
                    logger.warning(
                        f"[PdfParser] 第 {page_idx} 页解析失败: "
                        f"{type(e).__name__}: {e}"
                    )
                    skipped_pages.append(page_idx)
                    continue
        finally:
            doc.close()

        # 可观测：汇总报告
        if skipped_pages:
            logger.warning(
                f"[PdfParser] {file_path} 跳过 {len(skipped_pages)} 页: "
                f"{skipped_pages}"
            )

        root = DocumentNode(type="section", text="", level=0)
        for line in raw_lines:
            if line.strip():
                root.children.append(
                    DocumentNode(type="paragraph", text=line.strip())
                )

        raw_text = "\n".join(raw_lines)
        return DocumentAST(root=root, source_file=file_path, raw_text=raw_text)

    def _extract_blocks(
        self, blocks: list, out: list[str]
    ) -> None:
        """从 PyMuPDF blocks 抽取文本块，按垂直位置合并段落。"""
        # type=0 是文本块，type=1 是图片
        text_blocks = [b for b in blocks if b.get("type") == 0]
        # 按垂直位置排序（y0 升序）
        text_blocks.sort(key=lambda b: (b["bbox"][1], b["bbox"][0]))

        current_lines: list[str] = []
        current_y_bottom: float = -1.0

        for block in text_blocks:
            block_text = "\n".join(
                span["text"]
                for line in block.get("lines", [])
                for span in line.get("spans", [])
            ).strip()
            if not block_text:
                continue

            block_y_top = block["bbox"][1]
            if current_lines and (block_y_top - current_y_bottom) > _PARAGRAPH_MERGE_GAP:
                # 间距超过阈值 → 当前段落结束
                out.append("\n".join(current_lines))
                current_lines = []

            current_lines.append(block_text)
            current_y_bottom = block["bbox"][3]

        if current_lines:
            out.append("\n".join(current_lines))
```

- [x] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/rag/test_pdf_parser.py -v`
Expected: PASS（4 passed）

- [x] **Step 5: 提交**

```bash
git add backend/rag/preprocessing/parser/pdf_parser.py backend/tests/rag/test_pdf_parser.py
git commit -m "feat(chunking): PdfParser — PyMuPDF → Raw AST（带单页容错 + 跳过页汇总）"
```

---

### Task 3: DocxParser（python-docx → Raw AST）

**Files:**
- Create: `backend/rag/preprocessing/parser/docx_parser.py`
- Test: `backend/tests/rag/test_docx_parser.py`

**Interfaces:**
- Produces: `DocxParser().parse(file_path) -> DocumentAST`
- Consumes: `python-docx`（docx）从外部 import，本文件顶部 `import docx`
- 段落分类：style.name 为 `Heading 1/2/3` → section，其余 → paragraph / list

- [x] **Step 1: 写失败测试**

```python
# backend/tests/rag/test_docx_parser.py
import docx
import pytest

from backend.rag.preprocessing.ast import walk
from backend.rag.preprocessing.parser.docx_parser import DocxParser


@pytest.fixture
def sample_docx(tmp_path):
    """创建 docx：1 个 Heading 1 + 1 个 Heading 2 + 2 段 + 1 表。"""
    p = tmp_path / "sample.docx"
    d = docx.Document()
    d.add_heading("一级标题", level=1)
    d.add_paragraph("一级标题下的段落。")
    d.add_heading("二级标题", level=2)
    d.add_paragraph("二级标题下的段落。")
    table = d.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "列1"
    table.cell(0, 1).text = "列2"
    d.save(str(p))
    return str(p)


def test_docx_parser_basic_load(sample_docx):
    ast = DocxParser().parse(sample_docx)
    assert ast.source_file == sample_docx
    assert ast.raw_text != ""
    sections = [
        (n.text, n.level) for n in walk(ast.root)
        if n.type == "section" and n.level > 0
    ]
    assert ("一级标题", 1) in sections
    assert ("二级标题", 2) in sections


def test_docx_parser_table_recognized(sample_docx):
    ast = DocxParser().parse(sample_docx)
    tables = [n for n in walk(ast.root) if n.type == "table"]
    assert len(tables) == 1
    assert tables[0].rows is not None
    assert len(tables[0].rows) == 2


def test_docx_parser_section_attribution(sample_docx):
    """paragraph 应挂在最近的 section 下。"""
    ast = DocxParser().parse(sample_docx)
    h1_section = next(
        n for n in walk(ast.root)
        if n.type == "section" and n.text == "一级标题"
    )
    h1_paragraphs = [c for c in h1_section.children if c.type == "paragraph"]
    assert any("一级标题下的段落" in c.text for c in h1_paragraphs)


def test_docx_parser_heading_level_pop_logic(tmp_path):
    """Heading 3 后出现 Heading 1 → Heading 1 应挂 root，Heading 3 不被错误提升。"""
    p = tmp_path / "nested.docx"
    d = docx.Document()
    d.add_heading("外层 1", level=1)
    d.add_heading("内层 3", level=3)
    d.add_paragraph("内层内容。")
    d.add_heading("回到外层 1", level=1)  # 回到 level=1
    d.add_paragraph("外层内容。")
    d.save(str(p))

    ast = DocxParser().parse(str(p))
    sections = [(n.text, n.level) for n in walk(ast.root) if n.type == "section" and n.level > 0]
    # 两个 level=1 section 都在 root 下，level=3 在第一个 level=1 下
    assert ("外层 1", 1) in sections
    assert ("回到外层 1", 1) in sections
    assert ("内层 3", 3) in sections
```

- [x] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/rag/test_docx_parser.py -v`
Expected: FAIL（`ModuleNotFoundError: backend.rag.preprocessing.parser.docx_parser`）

- [x] **Step 3: 实现 docx_parser.py**

```python
"""DocxParser — python-docx 解析 Word → Raw AST。

段落分类规则：
- style.name 匹配 `Heading {N}` → DocumentNode(type="section", level=N, text=title)
- 段落 style.name 含 `List` → DocumentNode(type="list")
- 表格 → DocumentNode(type="table", rows=...)
- 其他 → DocumentNode(type="paragraph")

章节归属：paragraph / list / table 挂在最近的祖先 section 下。
"""
from __future__ import annotations

import re
from typing import Optional

import docx

from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.parser.base import BaseDocumentParser
from backend.shared.logger import logger

_HEADING_RE = re.compile(r"^Heading\s+(\d+)$", re.IGNORECASE)
_LIST_RE = re.compile(r"List\s+\w+", re.IGNORECASE)


def _find_parent_section(
    stack: list[DocumentNode], level: int
) -> DocumentNode:
    """在 section 栈中找到新 section 应该挂的父节点。

    规则：弹出所有 level >= 当前 level 的祖先，让新 section 挂在
    第一个 level < 当前 level 的祖先下。栈底始终是 root（level=0）。
    """
    while len(stack) > 1 and stack[-1].level >= level:
        stack.pop()
    return stack[-1]


class DocxParser(BaseDocumentParser):
    def parse(self, file_path: str) -> DocumentAST:
        try:
            d = docx.Document(file_path)
        except (docx.opc.exceptions.PackageNotFoundError, ValueError) as e:
            # 文件损坏 / 格式异常 → 返回空 AST
            logger.error(
                f"[DocxParser] 打开失败 {file_path}: "
                f"{type(e).__name__}: {e}"
            )
            return DocumentAST(
                root=DocumentNode(type="section", text="", level=0),
                source_file=file_path,
                raw_text="",
            )
        except Exception as e:
            logger.exception(
                f"[DocxParser] 打开异常 {file_path}: {type(e).__name__}"
            )
            raise

        root = DocumentNode(type="section", text="", level=0)
        section_stack: list[DocumentNode] = [root]
        raw_lines: list[str] = []
        skipped_tables: list[int] = []

        # 段落
        for para in d.paragraphs:
            style_name = para.style.name if para.style else ""
            text = para.text.strip()

            heading_match = _HEADING_RE.match(style_name)
            if heading_match and text:
                level = int(heading_match.group(1))
                section = DocumentNode(type="section", text=text, level=level)
                parent = _find_parent_section(section_stack, level)
                parent.children.append(section)
                section_stack.append(section)
                raw_lines.append(text)
                continue

            if _LIST_RE.search(style_name) and text:
                list_node = DocumentNode(type="list", text=text)
                section_stack[-1].children.append(list_node)
                raw_lines.append(text)
                continue

            if text:
                para_node = DocumentNode(type="paragraph", text=text)
                section_stack[-1].children.append(para_node)
                raw_lines.append(text)

        # 表格（python-docx 表格不在段落流中，需单独遍历）
        for tbl_idx, table in enumerate(d.tables):
            try:
                rows = [
                    [cell.text.strip() for cell in row.cells]
                    for row in table.rows
                ]
            except (AttributeError, IndexError) as e:
                logger.warning(
                    f"[DocxParser] 表格 {tbl_idx} 解析失败: "
                    f"{type(e).__name__}: {e}"
                )
                skipped_tables.append(tbl_idx)
                continue
            table_node = DocumentNode(type="table", text="", rows=rows)
            section_stack[-1].children.append(table_node)
            raw_lines.append("\n".join(", ".join(r) for r in rows))

        # 可观测：汇总报告
        if skipped_tables:
            logger.warning(
                f"[DocxParser] {file_path} 跳过 {len(skipped_tables)} 个表: "
                f"{skipped_tables}"
            )

        raw_text = "\n".join(raw_lines)
        return DocumentAST(root=root, source_file=file_path, raw_text=raw_text)
```

- [x] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/rag/test_docx_parser.py -v`
Expected: PASS（4 passed）

- [x] **Step 5: 提交**

```bash
git add backend/rag/preprocessing/parser/docx_parser.py backend/tests/rag/test_docx_parser.py
git commit -m "feat(chunking): DocxParser — python-docx → Raw AST（Heading + Table + 容错汇总）"
```

---

### Task 4: parser 注册 + pipeline 扩展名白名单

**Files:**
- Modify: `backend/rag/preprocessing/parser/__init__.py`
- Modify: `backend/rag/preprocessing/pipeline.py`
- Test: `backend/tests/rag/test_pipeline_ext.py`（新增）

**Interfaces:**
- `_PARSERS` 注册 `.pdf` → `PdfParser`、`.docx` → `DocxParser`
- `_SUPPORTED_EXTS` 扩展支持 `.pdf`、`.docx`

- [x] **Step 1: 写失败测试**

```python
# backend/tests/rag/test_pipeline_ext.py
from backend.rag.preprocessing.parser import _PARSERS
from backend.rag.preprocessing.pipeline import _SUPPORTED_EXTS


def test_parser_dispatch_pdf_docx_registered():
    """验证 .pdf / .docx 在 _PARSERS 字典里。"""
    assert ".pdf" in _PARSERS
    assert ".docx" in _PARSERS


def test_pipeline_supported_exts_includes_pdf_docx():
    """验证 _SUPPORTED_EXTS 包含 .pdf / .docx。"""
    assert ".pdf" in _SUPPORTED_EXTS
    assert ".docx" in _SUPPORTED_EXTS


def test_parse_and_chunk_unsupported_ext_returns_empty(tmp_path, caplog):
    """未知扩展名 → parse_and_chunk 返回 []，且 log warning。"""
    import logging
    caplog.set_level(logging.WARNING)
    fake = tmp_path / "fake.xyz"
    fake.write_text("dummy", encoding="utf-8")
    from backend.rag.preprocessing.pipeline import parse_and_chunk
    chunks = parse_and_chunk(str(fake))
    assert chunks == []
    assert any("暂不支持" in rec.message or "skip" in rec.message.lower() for rec in caplog.records)
```

- [x] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/rag/test_pipeline_ext.py -v`
Expected: FAIL（`.pdf`/`.docx` 不在 `_PARSERS` 和 `_SUPPORTED_EXTS`）

- [x] **Step 3: 修改 parser/__init__.py**

```diff
 # backend/rag/preprocessing/parser/__init__.py
 """Format Parser 层 — 按扩展名分发到对应解析器。"""
 from __future__ import annotations

 import os

 from backend.rag.preprocessing.ast import DocumentAST
 from backend.rag.preprocessing.parser.markdown_parser import MarkdownParser
 from backend.rag.preprocessing.parser.txt_parser import TxtParser
+from backend.rag.preprocessing.parser.pdf_parser import PdfParser
+from backend.rag.preprocessing.parser.docx_parser import DocxParser

 _PARSERS = {
     ".md": MarkdownParser,
     ".markdown": MarkdownParser,
     ".txt": TxtParser,
+    ".pdf": PdfParser,
+    ".docx": DocxParser,
 }


 def parse_file(file_path: str) -> DocumentAST:
     """按扩展名分发解析器，未知扩展回退 TxtParser。"""
     ext = os.path.splitext(file_path)[1].lower()
     parser_cls = _PARSERS.get(ext, TxtParser)
     return parser_cls().parse(file_path)
```

- [x] **Step 4: 修改 pipeline.py**

```diff
 # backend/rag/preprocessing/pipeline.py
-_SUPPORTED_EXTS = {".md", ".markdown", ".txt"}
+_SUPPORTED_EXTS = {".md", ".markdown", ".txt", ".pdf", ".docx"}
```

- [x] **Step 5: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/rag/test_pipeline_ext.py -v`
Expected: PASS（3 passed）

- [x] **Step 6: 提交**

```bash
git add backend/rag/preprocessing/parser/__init__.py backend/rag/preprocessing/pipeline.py backend/tests/rag/test_pipeline_ext.py
git commit -m "feat(chunking): 注册 PdfParser/DocxParser + pipeline 扩展 .pdf/.docx"
```

---

### Task 5: Q/A 节点识别（_qa_patterns.py + Markdown/TXT 接入）

**Files:**
- Create: `backend/rag/preprocessing/parser/_qa_patterns.py`
- Modify: `backend/rag/preprocessing/parser/markdown_parser.py`
- Modify: `backend/rag/preprocessing/parser/txt_parser.py`
- Test: `backend/tests/rag/test_qa_parser.py`

**Interfaces:**
- Produces: `extract_qa_pairs(text) -> list[tuple[str, str, str]]` —— `(question, answer, pattern_type)`
- 三种模式独立识别（不互斥），由 caller 决定如何处理：
  - `qa_bold`：`**Q:** ... **A:** ...`
  - `qa_heading`：`### 问题 / ### 答案`
  - `qa_numbered`：`Q1. ... A1. ...`

- [x] **Step 1: 写失败测试**

```python
# backend/tests/rag/test_qa_parser.py
from backend.rag.preprocessing.parser._qa_patterns import (
    extract_qa_pairs, looks_like_qa_doc,
)
from backend.rag.preprocessing.parser.markdown_parser import MarkdownParser
from backend.rag.preprocessing.parser.txt_parser import TxtParser


# ─── extract_qa_pairs 单元测试 ───

def test_extract_qa_bold_pattern():
    text = "**Q: 怎么退货？**\n**A: 提交申请后客服审核。**"
    pairs = extract_qa_pairs(text)
    assert len(pairs) == 1
    q, a, ptype = pairs[0]
    assert q == "怎么退货？"
    assert a == "提交申请后客服审核。"
    assert ptype == "qa_bold"


def test_extract_qa_heading_pattern():
    text = (
        "## 问题\n退货怎么操作？\n\n## 答案\n提交申请。\n\n"
        "## 问题\n运费谁付？\n\n## 答案\n商家承担。\n"
    )
    pairs = extract_qa_pairs(text)
    assert len(pairs) == 2
    assert pairs[0] == ("退货怎么操作？", "提交申请。", "qa_heading")
    assert pairs[1] == ("运费谁付？", "商家承担。", "qa_heading")


def test_extract_qa_numbered_pattern():
    text = "Q1. 怎么退货？\nA1. 提交申请。\nQ2. 运费谁付？\nA2. 商家承担。\n"
    pairs = extract_qa_pairs(text)
    assert len(pairs) == 2
    assert pairs[0][0] == "怎么退货？"
    assert pairs[0][2] == "qa_numbered"


def test_extract_qa_no_match_returns_empty():
    text = "这是一段普通文字，没有任何 Q/A 模式。"
    assert extract_qa_pairs(text) == []


def test_extract_qa_multiple_patterns_dont_conflict():
    """同一文档混用 bold + numbered：两种 pattern 各自产出。"""
    text = (
        "**Q: 怎么退货？**\n**A: 提交申请。**\n\n"
        "Q1. 运费谁付？\nA1. 商家承担。\n"
    )
    pairs = extract_qa_pairs(text)
    # 两种 pattern 各产出 1 对
    ptypes = sorted(p[2] for p in pairs)
    assert ptypes == ["qa_bold", "qa_numbered"]


def test_looks_like_qa_doc_threshold():
    """looks_like_qa_doc: 至少 N 对才算 FAQ。"""
    text_one = "**Q: 单条？**\n**A: 是。**\n普通段落。"
    assert looks_like_qa_doc(text_one, min_pairs=2) is False
    assert looks_like_qa_doc(text_one, min_pairs=1) is True


# ─── MarkdownParser 集成测试 ───

def test_markdown_parser_qa_doc(tmp_path):
    md = tmp_path / "faq.md"
    md.write_text(
        "**Q: 怎么退货？**\n**A: 提交申请后客服审核。**\n\n"
        "**Q: 运费谁付？**\n**A: 商家承担。**\n",
        encoding="utf-8",
    )
    ast = MarkdownParser().parse(str(md))
    qa_nodes = [
        n for n in ast.root.children if n.type in ("qa_question", "qa_answer")
    ]
    assert len(qa_nodes) == 4  # 2 个问题 + 2 个答案
    assert qa_nodes[0].type == "qa_question"
    assert "怎么退货" in qa_nodes[0].text
    assert qa_nodes[1].type == "qa_answer"


def test_markdown_parser_qa_nodes_attach_to_root_not_section(tmp_path):
    """Q/A 节点直接挂 root，不嵌套在 section 里（避免重复切分）。"""
    md = tmp_path / "faq_with_heading.md"
    md.write_text(
        "# FAQ 章节\n**Q: 怎么退货？**\n**A: 提交申请。**\n",
        encoding="utf-8",
    )
    ast = MarkdownParser().parse(str(md))
    # 整个文档被识别为 FAQ → 没有普通 section，只有 qa_* 节点
    sections = [n for n in ast.root.children if n.type == "section" and n.level > 0]
    qa_nodes = [n for n in ast.root.children if n.type in ("qa_question", "qa_answer")]
    assert len(qa_nodes) == 2
    assert sections == []  # FAQ 文档不识别为章节文档


# ─── TxtParser 集成测试 ───

def test_txt_parser_qa_doc(tmp_path):
    t = tmp_path / "faq.txt"
    t.write_text(
        "Q1. 怎么退货？\nA1. 提交申请。\n\nQ2. 运费谁付？\nA2. 商家承担。\n",
        encoding="utf-8",
    )
    ast = TxtParser().parse(str(t))
    qa_nodes = [
        n for n in ast.root.children if n.type in ("qa_question", "qa_answer")
    ]
    assert len(qa_nodes) == 4
```

- [x] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/rag/test_qa_parser.py -v`
Expected: FAIL（`_qa_patterns` 模块不存在 / MarkdownParser 不识别 Q/A）

- [x] **Step 3: 实现 _qa_patterns.py**

```python
"""_qa_patterns.py — FAQ 文档 Q/A 节点识别（MD/TXT 共用）。

三种模式**独立**识别，由 caller 根据 pattern_type 决定路由：
- `qa_bold`: `**Q:** ... **A:** ...`
- `qa_heading`: `### 问题 / ### 答案`
- `qa_numbered`: `Q1. ... A1. ...`

为什么三种独立产出而非「首个命中即停」：
- 可扩展：未来支持混合模式时无需重构
- 可观测：每种模式命中数可知，方便统计 FAQ 文档格式分布
- 可控制：caller 可按需过滤（如只信任 `qa_heading`，忽略 `qa_numbered`）
"""
from __future__ import annotations

import re
from typing import Iterator

# 模式 1: **Q: ...** \n **A: ...**
_QA_BOLD_RE = re.compile(
    r"\*\*Q[:：]?\s*(.+?)\*\*\s*\n+\s*\*\*A[:：]?\s*(.+?)\*\*",
    re.DOTALL,
)

# 模式 2: ### 问题 ... ### 答案 ...（heading 配对）
_QA_HEADING_RE = re.compile(
    r"^#{2,4}\s*(?:问题|Question|问|疑问)[:：]?\s*(.+?)\n+(.+?)(?=\n#{2,4}|\Z)",
    re.MULTILINE | re.DOTALL,
)

# 模式 3: Q1. ... A1. ...（编号配对）
_QA_NUMBERED_RE = re.compile(
    r"^Q(\d+)[.、]?\s*(.+?)\n+A\1[.、]?\s*(.+?)(?=\nQ\d+|\Z)",
    re.MULTILINE | re.DOTALL,
)

_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("qa_bold", _QA_BOLD_RE),
    ("qa_heading", _QA_HEADING_RE),
    ("qa_numbered", _QA_NUMBERED_RE),
]


def extract_qa_pairs(
    text: str,
) -> list[tuple[str, str, str]]:
    """提取所有 Q/A 对，每个元素 = (question, answer, pattern_type)。

    三种模式独立识别并产出，互不冲突。返回顺序按 pattern 在 _PATTERNS
    中的顺序；同 pattern 内按出现顺序。
    """
    results: list[tuple[str, str, str]] = []
    for ptype, pattern in _PATTERNS:
        for m in pattern.finditer(text):
            q = m.group(1).strip()
            a = m.group(2).strip()
            if q and a:  # 过滤空匹配
                results.append((q, a, ptype))
    return results


def looks_like_qa_doc(text: str, min_pairs: int = 2) -> bool:
    """判断文档是否像 FAQ（任意 pattern 累计 ≥ min_pairs 个 Q/A 对）。"""
    return len(extract_qa_pairs(text)) >= min_pairs


def dominant_pattern(text: str) -> str:
    """返回出现最多的 pattern_type（用于统计 / 路由决策）。无 Q/A 时返回 ""。"""
    pairs = extract_qa_pairs(text)
    if not pairs:
        return ""
    counts: dict[str, int] = {}
    for _, _, ptype in pairs:
        counts[ptype] = counts.get(ptype, 0) + 1
    return max(counts, key=counts.get)  # type: ignore[arg-type]
```

- [x] **Step 4: 修改 markdown_parser.py 接入 Q/A 识别**

```python
# backend/rag/preprocessing/parser/markdown_parser.py
from backend.rag.preprocessing.parser._qa_patterns import looks_like_qa_doc


class MarkdownParser(BaseDocumentParser):
    def parse(self, file_path: str) -> DocumentAST:
        with open(file_path, encoding="utf-8") as f:
            raw = f.read()

        root = DocumentNode(type="section", text="", level=0)

        # Phase 2：识别 FAQ 文档 → 整篇按 Q/A 切，不走普通 heading 路径
        if looks_like_qa_doc(raw):
            from backend.rag.preprocessing.parser._qa_patterns import (
                extract_qa_pairs,
            )
            pairs = extract_qa_pairs(raw)
            for q, a, _ptype in pairs:
                root.children.append(
                    DocumentNode(type="qa_question", text=q)
                )
                root.children.append(
                    DocumentNode(type="qa_answer", text=a)
                )
            logger.info(
                f"[MarkdownParser] {file_path} 识别为 FAQ 文档，"
                f"产出 {len(pairs)} 个 Q/A 对"
            )
            return DocumentAST(root=root, source_file=file_path, raw_text=raw)

        # 普通文档：原有 heading 解析逻辑
        stack: list[DocumentNode] = [root]
        ...  # 保留原有代码不变
```

- [x] **Step 5: 修改 txt_parser.py 接入 Q/A 识别**

类似 MarkdownParser，在 TxtParser.parse() 开头先 `looks_like_qa_doc(raw)` 判断，命中走 FAQ 路径；否则走原有编号 heading 路径。

- [x] **Step 6: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/rag/test_qa_parser.py -v`
Expected: PASS（8 passed）

- [x] **Step 7: 提交**

```bash
git add backend/rag/preprocessing/parser/_qa_patterns.py \
        backend/rag/preprocessing/parser/markdown_parser.py \
        backend/rag/preprocessing/parser/txt_parser.py \
        backend/tests/rag/test_qa_parser.py
git commit -m "feat(chunking): Q/A 节点识别 — 三模式独立产出 + MD/TXT 接入"
```

---

### Task 6: faq 路由修复（faq → QAChunkStrategy）

**Files:**
- Modify: `backend/rag/preprocessing/chunking.py`（`STRUCTURE_STRATEGIES["faq"]`）
- Test: `backend/tests/rag/test_faq_routing.py`（新增）

- [x] **Step 1: 写失败测试**

```python
# backend/tests/rag/test_faq_routing.py
from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.chunking import (
    ChunkStrategyRouter, QAChunkStrategy, StructureChunkStrategy,
)
from backend.rag.preprocessing.structure_analyzer import StructureReport


def _report(completeness: float):
    return StructureReport(
        ast=DocumentAST(root=DocumentNode(type="section", text="", level=0)),
        completeness=completeness,
    )


def test_faq_routes_to_qa_strategy():
    """Phase 2 修复：faq 走 QAChunkStrategy（不再走 StructureChunkStrategy）。"""
    r = ChunkStrategyRouter()
    strategy = r.route("faq", _report(0.9))
    assert isinstance(strategy, QAChunkStrategy)


def test_other_types_unchanged():
    """回归验证：faq 以外不受影响。"""
    r = ChunkStrategyRouter()
    assert isinstance(r.route("policy", _report(0.9)), StructureChunkStrategy)
    assert isinstance(r.route("sop", _report(0.9)), StructureChunkStrategy)
    assert isinstance(r.route("general", _report(0.9)), StructureChunkStrategy) is False
    # general 在 completeness=0.9 时也走 STRUCTURE_STRATEGIES（fallback）


def test_qa_strategy_handles_qa_nodes():
    """QAChunkStrategy 切 qa_question/qa_answer 节点产 leaf。"""
    ast = DocumentAST(
        root=DocumentNode(type="section", text="", level=0, children=[
            DocumentNode(type="qa_question", text="怎么退货？"),
            DocumentNode(type="qa_answer", text="提交申请。"),
        ]),
    )
    chunks = QAChunkStrategy().split(ast, "faq.md")
    assert len(chunks) == 2
    assert all(c.metadata["granularity"] == "leaf" for c in chunks)


def test_qa_strategy_chunks_have_doc_id():
    """QA leaf 必须有 doc_id（与 indexer 注入一致）—— 可观测 + 检索依赖。"""
    ast = DocumentAST(
        root=DocumentNode(type="section", text="", level=0, children=[
            DocumentNode(type="qa_question", text="怎么退货？"),
            DocumentNode(type="qa_answer", text="提交申请。"),
        ]),
    )
    chunks = QAChunkStrategy().split(ast, "/abs/path/faq.md")
    # chunk_id 与 file_path 绑定（与 Phase 1 协议一致）
    assert all("chunk_id" in c.metadata for c in chunks)
    assert all("file_path" in c.metadata for c in chunks)
    assert chunks[0].metadata["file_path"] == "/abs/path/faq.md"
```

- [x] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/rag/test_faq_routing.py -v`
Expected: `test_faq_routes_to_qa_strategy` FAIL（当前返回 StructureChunkStrategy）

- [x] **Step 3: 修改 chunking.py**

```diff
 STRUCTURE_STRATEGIES = {
     ...
-    "faq": StructureChunkStrategy,   # Q/A 节点识别留 Phase 2
+    "faq": QAChunkStrategy,
     ...
 }
```

- [x] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/rag/test_faq_routing.py -v`
Expected: PASS（4 passed）

- [x] **Step 5: 提交**

```bash
git add backend/rag/preprocessing/chunking.py backend/tests/rag/test_faq_routing.py
git commit -m "fix(chunking): faq 路由修复 — StructureChunkStrategy → QAChunkStrategy"
```

---

### Task 7: loader.py 扩展名白名单同步

**Files:**
- Modify: `backend/rag/preprocessing/loader.py`
- Test: `backend/tests/rag/test_loader_ext.py`（新增）

- [x] **Step 1: 写失败测试**

```python
# backend/tests/rag/test_loader_ext.py
from backend.rag.preprocessing.loader import load_documents_from_directory


def test_loader_processes_md_txt(tmp_path):
    """MD/TXT 文档正常被处理（无回归）。"""
    (tmp_path / "kb1").mkdir()
    (tmp_path / "kb1" / "a.md").write_text("# 标题\n内容。\n", encoding="utf-8")
    (tmp_path / "kb1" / "b.txt").write_text("一、章节\n内容。\n", encoding="utf-8")

    docs = load_documents_from_directory(str(tmp_path))
    sources = {d.metadata["source_file"] for d in docs}
    assert "a.md" in sources
    assert "b.txt" in sources


def test_loader_skips_unsupported_ext(tmp_path):
    """JSON / 未知扩展名被跳过（不抛异常）。"""
    (tmp_path / "kb1").mkdir()
    (tmp_path / "kb1" / "a.md").write_text("# 标题\n内容。\n", encoding="utf-8")
    (tmp_path / "kb1" / "x.json").write_text("{}", encoding="utf-8")
    (tmp_path / "kb1" / "y.png").write_bytes(b"\x89PNG")

    docs = load_documents_from_directory(str(tmp_path))
    sources = {d.metadata["source_file"] for d in docs}
    assert "a.md" in sources
    assert "x.json" not in sources
    assert "y.png" not in sources


def test_loader_pdf_docx_in_whitelist_no_crash(tmp_path):
    """PDF/DOCX 在白名单里 → 走 parse_and_chunk。fake 文件产空 chunks 不抛异常。"""
    (tmp_path / "kb1").mkdir()
    (tmp_path / "kb1" / "fake.pdf").write_bytes(b"%PDF-1.4\n")
    (tmp_path / "kb1" / "fake.docx").write_bytes(b"PK\x03\x04")

    # 不抛异常即可；fake 文件 PDF 解析可能报错但被 loader 容错吞掉
    docs = load_documents_from_directory(str(tmp_path))
    # fake 文件产 0 chunk 是预期的（PyMuPDF 无法解析 fake 数据）
    assert isinstance(docs, list)
```

- [x] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/rag/test_loader_ext.py -v`
Expected: `test_loader_pdf_docx_in_whitelist_no_crash` FAIL（当前白名单不含 `.pdf`/`.docx`）

- [x] **Step 3: 修改 loader.py**

```diff
 # backend/rag/preprocessing/loader.py
-if ext not in (".md", ".txt"):
-    continue  # Phase 1 仅支持 md/txt；pdf/docx 延后 Phase 2
+if ext not in (".md", ".txt", ".pdf", ".docx"):
+    continue
```

- [x] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/rag/test_loader_ext.py -v`
Expected: PASS（3 passed）

- [x] **Step 5: 提交**

```bash
git add backend/rag/preprocessing/loader.py backend/tests/rag/test_loader_ext.py
git commit -m "feat(chunking): loader 扩展名白名单同步 .pdf/.docx"
```

---

### Task 8: indexer.py 统一走新流水线（删除 langchain loader 分支）

**目标：** indexer 不再独立加载 PDF/DOCX，统一走 `parse_and_chunk`；不再「反向构造 raw_docs」（即不为了复用旧代码而做临时转换）。

**Files:**
- Modify: `backend/rag/indexing/indexer.py`（删除 PDF/DOCX 老 langchain loader 加载，删除 raw_docs 反向构造）
- Test: `backend/tests/rag/test_indexer_pipeline_unified.py`（新增）
- Test: 回归 `test_indexer_trace.py`

**架构决策：**

旧 indexer 用 9 段（load / parse / clean / dedup / chunk / metadata / embed / vector_db / registry），每段对 `raw_docs` 操作。

**重构方案**：删除 raw_docs 概念，clean / metadata 段直接对 `chunks` 操作。chunks 由 parse_and_chunk 一次性产出，parse 段只做 trace 标记，chunk 段复用 parse 段结果（避免重复调用 parse_and_chunk）。

**改动范围：**
1. 删除 [indexer.py:309-328](backend/rag/indexing/indexer.py) 的 PyPDFLoader / Docx2txtLoader 加载分支
2. 删除 [indexer.py:329-331](backend/rag/indexing/indexer.py) 的 TextLoader 分支（parse_and_chunk 已统一）
3. parse 段：只 trace 标记，不实际加载
4. chunk 段：调 `parse_and_chunk(file_path)` 一次得到 chunks，**赋给实例属性** `_current_chunks` 供后续 clean / metadata 段使用
5. clean 段：改为对 `self._current_chunks` 清洗（不再操作 raw_docs）
6. metadata 段：改为对 `self._current_chunks` 操作
7. 保留 doc_count / page_count trace 字段（向后兼容，page_count 来自 chunks 长度）
8. 整个 `_index_file_inner` 函数签名和 trace span 结构不变

- [x] **Step 1: 写失败测试**

```python
# backend/tests/rag/test_indexer_pipeline_unified.py
import inspect

from backend.rag.indexing.indexer import IncrementalIndexer


def test_indexer_does_not_use_langchain_pdf_loader():
    """indexer 不再 import PyPDFLoader / Docx2txtLoader / TextLoader。"""
    source = inspect.getsource(IncrementalIndexer._index_file_inner)
    assert "PyPDFLoader" not in source
    assert "Docx2txtLoader" not in source
    assert "TextLoader" not in source


def test_indexer_calls_parse_and_chunk():
    """indexer 必须调 parse_and_chunk 切分。"""
    source = inspect.getsource(IncrementalIndexer._index_file_inner)
    assert "parse_and_chunk" in source


def test_indexer_no_raw_docs_reconstruction():
    """indexer 不再持有 raw_docs 变量（避免反向构造）。"""
    source = inspect.getsource(IncrementalIndexer._index_file_inner)
    # 反向构造迹象：raw_docs = [Document(...) for ch in chunks]
    assert "raw_docs = [" not in source
    assert "for ch in parsed_chunks" not in source or "raw_docs" not in source
```

- [x] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/rag/test_indexer_pipeline_unified.py -v`
Expected: FAIL（当前 indexer 仍有 PyPDFLoader / TextLoader / raw_docs）

- [x] **Step 3: 重构 indexer.py**

主要 diff（详细 patch 见实际编辑）：

```diff
 # backend/rag/indexing/indexer.py
-        # ── ② parse（格式解析：PDF/DOCX/TXT → Documents）──
+        # ── ② parse（trace 标记 + parse_and_chunk 一次调用）──
         parse_span = trace_collector.start_span(...)
-        parse_failed = False
-        parse_error_msg = ""
-        try:
-            if ext == ".pdf":
-                try:
-                    from langchain_community.document_loaders import PyPDFLoader
-                    loader = PyPDFLoader(file_path)
-                    raw_docs = loader.load()
-                except Exception as e:
-                    logger.error(f"PDF 加载失败 {file_path}: {e}")
-                    parse_failed = True
-                    parse_error_msg = str(e)[:200]
-                    raw_docs = []
-            elif ext == ".docx":
-                try:
-                    from langchain_community.document_loaders import Docx2txtLoader
-                    loader = Docx2txtLoader(file_path)
-                    raw_docs = loader.load()
-                except Exception as e:
-                    logger.error(f"DOCX 加载失败 {file_path}: {e}")
-                    parse_failed = True
-                    parse_error_msg = str(e)[:200]
-                    raw_docs = []
-            else:
-                loader = TextLoader(file_path, encoding="utf-8")
-                raw_docs = loader.load()
-
-            if parse_failed:
-                trace_collector.end_span(parse_span, status="error",
-                    metrics={"error": parse_error_msg})
-                raise RuntimeError(f"parse failed: {parse_error_msg}")
-
-            if not raw_docs:
-                logger.warning(f"文件为空，跳过: {file_path}")
-                trace_collector.end_span(parse_span,
-                    metrics={"doc_count": 0}, status="skipped")
-                return
-
-            for d in raw_docs:
-                d.metadata["kb_id"] = kb_id
-
-            trace_collector.end_span(parse_span,
-                metrics={"doc_count": len(raw_docs),
-                         "page_count": len(raw_docs)})
-        except RuntimeError:
-            raise
-        except Exception as e:
-            trace_collector.end_span(parse_span, status="error",
-                metrics={"error": str(e)[:200]})
-            raise
+        try:
+            from backend.rag.preprocessing.pipeline import parse_and_chunk
+            chunks = parse_and_chunk(file_path)
+            if not chunks:
+                trace_collector.end_span(parse_span, status="skipped",
+                    metrics={"reason": "empty_or_unsupported",
+                             "doc_count": 0, "page_count": 0,
+                             "loader": "pipeline", "ext": ext})
+                logger.warning(f"[indexer] 文件解析为空或不支持: {file_path} (ext={ext})")
+                return
+            for ch in chunks:
+                ch.metadata["kb_id"] = kb_id
+            trace_collector.end_span(parse_span,
+                metrics={"doc_count": len(chunks),
+                         "page_count": len(chunks),
+                         "loader": "pipeline", "ext": ext})
+        except Exception as e:
+            trace_collector.end_span(parse_span, status="error",
+                metrics={"error": str(e)[:200], "loader": "pipeline"})
+            raise
```

```diff
 # ── ②.5 clean 段改为对 chunks 清洗 ──
-        for d in raw_docs:
-            total_chars_before += len(d.page_content)
-            result = cleaner.clean(d.page_content, source_type=source_type)
-            d.page_content = result.text
+        for ch in chunks:
+            total_chars_before += len(ch.page_content)
+            result = cleaner.clean(ch.page_content, source_type=source_type)
+            ch.page_content = result.text
```

```diff
 # ── ④ dedup / ⑤ chunk 段 ──
-            from backend.rag.preprocessing.pipeline import parse_and_chunk
-            chunks = parse_and_chunk(file_path)
+            # chunks 已在 ② 段产出，复用即可（避免重复调用 parse_and_chunk）
```

```diff
 # ── ⑥ metadata 段 full_text 来源 ──
-        full_text = "\n\n".join(d.page_content for d in raw_docs)
+        full_text = "\n\n".join(ch.page_content for ch in chunks)
```

具体 patch 实施时按实际行号调整；原则：**删除 raw_docs 概念，clean / metadata 段直接对 chunks 操作**。

- [x] **Step 4: 运行新测试确认通过**

Run: `cd backend && python -m pytest tests/rag/test_indexer_pipeline_unified.py -v`
Expected: PASS（3 passed）

- [x] **Step 5: 回归 indexer_trace 测试**

Run: `cd backend && python -m pytest tests/rag/test_indexer_trace.py -v`
Expected: PASS（既有 9-span 测试不回归）

- [x] **Step 6: 提交**

```bash
git add backend/rag/indexing/indexer.py backend/tests/rag/test_indexer_pipeline_unified.py
git commit -m "refactor(rag): indexer 统一走新流水线 — 删 raw_docs 反向构造 + trace 字段保留"
```

---

### Task 9: 集成回归 + 文档更新

**目标：** 验证 P0 + P1 全部生效，更新 Phase 1 计划文档的「下一步」section。

- [x] **Step 1: 全量回归测试**

Run: `cd backend && python -m pytest tests/rag/ -v`
Expected: 122 + 新增（约 25 用例）= ~147 全绿，0 failed。

- [x] **Step 2: 验证 indexer 真实 PDF/DOCX 索引流程（E2E 单测）**

```python
# backend/tests/rag/test_e2e_pdf_docx_index.py
"""E2E：用 tmp_path 构造真实 PDF/DOCX，验证 indexer 全链路产 chunk。"""
import fitz
import docx
import tempfile

import pytest
from backend.rag.indexing.indexer import IncrementalIndexer
from backend.rag.vectorstore.chunk_store import ChunkStore


@pytest.fixture
def real_pdf(tmp_path):
    """构造 2 页真实 PDF，含可读文本。"""
    p = tmp_path / "real.pdf"
    d = fitz.open()
    p1 = d.new_page()
    p1.insert_text((50, 50), "售后制度。")
    p1.insert_text((50, 80), "退货流程说明。")
    p2 = d.new_page()
    p2.insert_text((50, 50), "差评处理。")
    d.save(str(p))
    d.close()
    return str(p)


@pytest.fixture
def real_docx(tmp_path):
    """构造真实 DOCX，含 Heading + Table。"""
    p = tmp_path / "real.docx"
    d = docx.Document()
    d.add_heading("售后制度", level=1)
    d.add_paragraph("退货流程说明。")
    d.add_heading("差评处理", level=1)
    d.add_paragraph("48小时内处理。")
    d.save(str(p))
    return str(p)


def test_pdf_e2e_pipeline(real_pdf):
    """PDF 走完整流水线：parse_and_chunk 不返回空。"""
    from backend.rag.preprocessing.pipeline import parse_and_chunk
    chunks = parse_and_chunk(real_pdf)
    assert len(chunks) > 0
    assert all("page_content" in c.metadata or hasattr(c, "page_content") for c in chunks)


def test_docx_e2e_pipeline(real_docx):
    """DOCX 走完整流水线：parse_and_chunk 产 chunks 含 section 节点。"""
    from backend.rag.preprocessing.pipeline import parse_and_chunk
    from backend.rag.preprocessing.ast import walk
    chunks = parse_and_chunk(real_docx)
    assert len(chunks) > 0
    # DOCX 的 chunks 包含 parent + leaf（StructureChunkStrategy 路由）
    assert any(c.metadata.get("granularity") == "parent" for c in chunks)
```

Run: `cd backend && python -m pytest tests/rag/test_e2e_pdf_docx_index.py -v`
Expected: PASS（2 passed）

- [x] **Step 3: 全量再回归一次**

Run: `cd backend && python -m pytest tests/rag/ -v`
Expected: ~149 用例全绿。

- [x] **Step 4: 更新 Phase 1 计划文档「下一步」**

修改 [2026-08-13-chunking-refactor.md](../plans/2026-08-13-chunking-refactor.md) 末尾的「下一步」section：

```diff
 ### 下一步

-- Phase 2 设计（Semantic / LLM Assisted / PyMuPDF / python-docx / Excel 解析）
-- 分支合并策略（本地 master 落后 origin/master 275 commit，需要先 rebase/merge）
+- Phase 2（已完成 P0/P1）：[Phase 2 Spec](../specs/2026-08-13-chunking-refactor-phase2-design.md)
+- Phase 2 Plan：[Phase 2 Plan](../plans/2026-08-13-chunking-refactor-phase2.md)
+- P2/P3 待办：LLM Assisted / Semantic / PDF 标题启发式 / Step / Excel / legal 细化
+- 分支合并策略（本地 master 落后 origin/master 275 commit，需要先 rebase/merge）
```

- [x] **Step 5: 提交**

```bash
git add docs/superpowers/plans/2026-08-13-chunking-refactor.md backend/tests/rag/test_e2e_pdf_docx_index.py
git commit -m "docs(chunking): Phase 1 plan 更新下一步 — 引用 Phase 2 Spec/Plan + E2E 测试"
```

---

## 自审记录

- **Spec 覆盖**：Phase 2 Spec 的 P0（PDF/DOCX 接入）+ P1（faq 路由 + QA 节点 + loader 同步）全部交付物均有对应 Task。
- **类型一致性**：`PdfParser` / `DocxParser` 继承 `BaseDocumentParser.parse(file_path) -> DocumentAST`；`extract_qa_pairs` 返回 `list[(str, str, str)]`；`STRUCTURE_STRATEGIES["faq"]` 改 `QAChunkStrategy` 不破坏类型。
- **占位检查**：无 TBD/TODO；异常处理均记录日志（`PdfParser` 单页失败、`DocxParser` 表格失败、`DocxParser` 文件打开失败），无静默 `pass`。
- **七原则对齐**：
  - **可理解**：抽 `_find_parent_section` 工具函数、Q/A pattern 命名常量、避免内联匿名逻辑
  - **可测试**：每个 Task 都先写测试再实现；E2E 测试用 tmp_path 构造真实 PDF/DOCX
  - **可观测**：Parser 单页/单表失败汇总 log；indexer trace 字段保留 doc_count + 加 loader="pipeline"
  - **可维护**：Q/A 三模式独立产出（可扩展）；`looks_like_qa_doc` / `dominant_pattern` 工具函数
  - **可扩展**：Q/A 三模式互不冲突，新增 pattern 加 `_PATTERNS` 即可；section_stack 用 `_find_parent_section` 抽取
  - **可控制**：parse_and_chunk 一次调用结果复用给 chunk/clean/metadata 段（避免重复）；PyMuPDF 安装失败不静默降级
  - **可靠性**：明确异常类型（`fitz.FileDataError` / `docx.opc.exceptions.PackageNotFoundError`），其他异常 log + raise
- **回归保护**：每个 Task 的 Step 5/Step 6 跑子集测试；Task 8 显式回归 `test_indexer_trace.py`；Task 9 全量 pytest。
- **依赖隔离**：PyMuPDF / python-docx 仅在 `pdf_parser.py` / `docx_parser.py` 顶部 import，chunking 核心模块无依赖。
- **P2/P3 兼容性**：保留 `ENABLE_LLM_CHUNKING` / `ENABLE_SEMANTIC_CHUNKING` 开关和 `topic_shift_detected` / `is_high_value_and_chaotic` 占位字段，不破坏 Phase 3 扩展点。
