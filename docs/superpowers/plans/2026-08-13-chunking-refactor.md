# 切分重构（Phase 1）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立统一 Document AST + Structure Analyzer + 双轴 Strategy Router，把切分从「策略内正则检测」升级为「解析 → 清洗 → 结构分析 → 路由 → 切分」的闭环，并落地双粒度 Parent-Child。

**Architecture:** Parser 产出 Raw AST（格式级结构），DocumentCleaner 做结构安全清洗，Structure Analyzer 归一化为 Normalized AST + 完整度，双轴 Router（文档类型 × 结构完整度）选策略，Strategy 只消费 AST 产出 leaf + parent 双粒度 chunk，ChunkFilter 质检后入向量库。

**Tech Stack:** Python 3.10、dataclasses、tiktoken（cl100k_base）、pytest、langchain_core Document、现有 `RecursiveCharacterTextSplitter`。

## Global Constraints

- 设计七原则必须满足：可理解、可测试、可观测、可维护、可扩展、可控制、可靠性。
- 禁止 Demo 跑通式开发、临时堆叠、`except Exception: pass`（异常必须记录日志或做有意义的降级）。
- Python：snake_case、类型注解、logger 替代 print、具体异常、SQL 参数化。
- 测试在 `backend/` 目录下运行：`python -m pytest tests/rag/<file> -v`（用项目 `.venv` 或 `D:/Python/python.exe`）。
- 每个 commit 只包含对应 task 的文件，先写测试再实现（TDD）。
- `Structure Analyzer` 与 `ChunkStrategy` 不得重复判断标题/章节——结构检测只在 Parser + Analyzer 做一次，Strategy 只消费 AST。

---

### Task 1: DocumentNode / DocumentAST 数据模型

**Files:**
- Create: `backend/rag/preprocessing/ast.py`
- Test: `backend/tests/rag/test_ast.py`

**Interfaces:**
- Produces: `DocumentNode`（dataclass：`type/text/level/children/rows/source_range`）、`DocumentAST`（dataclass：`root/source_file/raw_text`）、`walk(node)`、`iter_sections(ast)`。后续所有 task 消费这些类型。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/rag/test_ast.py
from backend.rag.preprocessing.ast import (
    DocumentNode, DocumentAST, walk, iter_sections,
)


def _make_tree():
    root = DocumentNode(type="section", text="", level=0)
    ch1 = DocumentNode(type="section", text="售后制度", level=1)
    ch2 = DocumentNode(type="section", text="退货流程", level=2)
    leaf = DocumentNode(type="paragraph", text="客服审核退货原因。")
    ch2.children.append(leaf)
    ch1.children.append(ch2)
    root.children.append(ch1)
    return DocumentAST(root=root, source_file="a.md", raw_text="售后制度\n退货流程\n客服审核退货原因。")


def test_walk_yields_all_nodes():
    ast = _make_tree()
    types = [n.type for n in walk(ast.root)]
    assert types == ["section", "section", "section", "paragraph"]


def test_iter_sections_returns_full_path():
    ast = _make_tree()
    paths = {n.text: path for n, path in iter_sections(ast)}
    assert paths["退货流程"] == ["售后制度", "退货流程"]


def test_node_defaults():
    n = DocumentNode(type="paragraph", text="x")
    assert n.level == 0 and n.children == [] and n.rows is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/rag/test_ast.py -v`
Expected: FAIL（`ModuleNotFoundError: backend.rag.preprocessing.ast`）

- [ ] **Step 3: 实现 ast.py**

```python
"""统一 Document AST — 切分流水线的中间表示。

Parser 产出 Raw AST（格式级结构），Structure Analyzer 归一化为 Normalized AST，
所有 ChunkStrategy 消费 Normalized AST。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

VALID_NODE_TYPES = {
    "heading", "section", "paragraph", "list", "table",
    "qa_question", "qa_answer",
}

LEAF_TYPES = {"paragraph", "list", "table", "qa_question", "qa_answer"}


@dataclass
class DocumentNode:
    """AST 节点。section 是容器（text=标题，children=子节点），leaf 类型是叶子。"""
    type: str
    text: str
    level: int = 0
    children: list["DocumentNode"] = field(default_factory=list)
    rows: list[list[str]] | None = None       # table 专用
    source_range: tuple[int, int] = (0, 0)    # (start, end) 在 raw_text 中的偏移


@dataclass
class DocumentAST:
    """整棵文档结构树。root 是虚拟根（type="section", level=0, text=""）。"""
    root: DocumentNode
    source_file: str = ""
    raw_text: str = ""


def walk(node: DocumentNode) -> Iterator[DocumentNode]:
    """DFS 先序遍历所有节点。"""
    yield node
    for child in node.children:
        yield from walk(child)


def iter_sections(ast: DocumentAST) -> Iterator[tuple[DocumentNode, list[str]]]:
    """为每个 section 节点产出 (node, 祖先标题链)，链不含虚拟根。"""
    def _dfs(node: DocumentNode, path: list[str]):
        for child in node.children:
            if child.type == "section":
                child_path = path + [child.text]
                yield child, child_path
                yield from _dfs(child, child_path)
            else:
                yield from _dfs(child, path)

    yield from _dfs(ast.root, [])
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/rag/test_ast.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add backend/rag/preprocessing/ast.py backend/tests/rag/test_ast.py
git commit -m "feat(chunking): DocumentNode/DocumentAST 数据模型"
```

---

### Task 2: token 计数 + 配置项

**Files:**
- Create: `backend/rag/preprocessing/token_counter.py`
- Modify: `backend/config/rag.py`（在 chunk 配置区追加新键）
- Modify: `backend/config/__init__.py`（导出新键）
- Test: `backend/tests/rag/test_token_counter.py`

**Interfaces:**
- Produces: `count_tokens(text: str) -> int`。Task 5 的 chunk_tokens 字段依赖它。
- Consumes: `backend.config` 的 `STRUCTURE_COMPLETE_THRESHOLD`（Task 4 用）。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/rag/test_token_counter.py
from backend.rag.preprocessing.token_counter import count_tokens


def test_empty_text_zero():
    assert count_tokens("") == 0


def test_cjk_counts_roughly_one_per_char():
    n = count_tokens("客服需要审核退货原因和凭证真实性")
    assert 8 <= n <= 20   # 中文每字约 1 token，13 字约 13 token


def test_english_counts_less_than_chars():
    text = "This is a sentence with several words."
    assert 0 < count_tokens(text) < len(text)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/rag/test_token_counter.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 实现 token_counter.py + 配置项**

```python
"""token 计数 — 用 tiktoken 真实 tokenizer，替代 len 字符数。"""
from __future__ import annotations

import tiktoken

from backend.shared.logger import logger

_enc = None


def _get_encoder():
    global _enc
    if _enc is None:
        _enc = tiktoken.get_encoding("cl100k_base")
    return _enc


def count_tokens(text: str) -> int:
    """返回文本 token 数（cl100k_base，DeepSeek 近似）。"""
    if not text:
        return 0
    try:
        return len(_get_encoder().encode(text))
    except Exception as e:
        # tiktoken 异常时降级字符估算，记录日志不静默吞掉
        logger.warning(f"[token_counter] tiktoken 失败({type(e).__name__})，降级字符估算")
        return _char_estimate(text)


def _char_estimate(text: str) -> int:
    """字符估算兜底：CJK 1 字 ≈ 1 token，其余 4 字符 ≈ 1 token。"""
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    other = len(text) - cjk
    return cjk + other // 4
```

在 `backend/config/rag.py` 的 chunk 配置区（`GENERAL_CHUNK_OVERLAP` 之后）追加：

```python
LEAF_CHUNK_TOKENS = int(os.getenv("LEAF_CHUNK_TOKENS", "500"))
PARENT_CHUNK_TOKENS = int(os.getenv("PARENT_CHUNK_TOKENS", "2000"))
STRUCTURE_COMPLETE_THRESHOLD = float(os.getenv("STRUCTURE_COMPLETE_THRESHOLD", "0.7"))
ENABLE_SEMANTIC_CHUNKING = os.getenv("ENABLE_SEMANTIC_CHUNKING", "false").lower() == "true"
ENABLE_LLM_CHUNKING = os.getenv("ENABLE_LLM_CHUNKING", "false").lower() == "true"
LLM_CHUNK_MIN_CHARS = int(os.getenv("LLM_CHUNK_MIN_CHARS", "2000"))
```

在 `backend/config/__init__.py` 的导出列表（`GENERAL_CHUNK_OVERLAP` 附近）追加：

```python
LEAF_CHUNK_TOKENS, PARENT_CHUNK_TOKENS, STRUCTURE_COMPLETE_THRESHOLD,
ENABLE_SEMANTIC_CHUNKING, ENABLE_LLM_CHUNKING, LLM_CHUNK_MIN_CHARS,
```

并同步加进 `__init__.py` 顶部的 import 块和文件末尾的 `__all__`（若存在）。参考现有 `GENERAL_CHUNK_SIZE` 的两处写法。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/rag/test_token_counter.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add backend/rag/preprocessing/token_counter.py backend/config/rag.py backend/config/__init__.py backend/tests/rag/test_token_counter.py
git commit -m "feat(chunking): token 计数 + 切分配置项"
```

---

### Task 3: Parser 层（Markdown / TXT → Raw AST）

**Files:**
- Create: `backend/rag/preprocessing/parser/__init__.py`
- Create: `backend/rag/preprocessing/parser/base.py`
- Create: `backend/rag/preprocessing/parser/markdown_parser.py`
- Create: `backend/rag/preprocessing/parser/txt_parser.py`
- Create: `backend/rag/preprocessing/parser/excel_parser.py`（占位）
- Test: `backend/tests/rag/test_parser.py`

**Interfaces:**
- Produces: `parse_file(file_path: str) -> DocumentAST`（dispatcher）、`MarkdownParser().parse(file_path)`、`TxtParser().parse(file_path)`。Task 7 的流水线调用 `parse_file`。

- [ ] **Step 1: 写失败测试**

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/rag/test_parser.py -v`
Expected: FAIL（`ModuleNotFoundError: backend.rag.preprocessing.parser`）

- [ ] **Step 3: 实现 parser 层**

`base.py`：

```python
"""BaseDocumentParser — 格式解析器抽象。只解析文件结构，产出 Raw AST。"""
from __future__ import annotations

from abc import ABC, abstractmethod

from backend.rag.preprocessing.ast import DocumentAST


class BaseDocumentParser(ABC):
    @abstractmethod
    def parse(self, file_path: str) -> DocumentAST:
        """读取文件并产出 Raw AST（格式级结构）。"""
        raise NotImplementedError
```

`markdown_parser.py`：

```python
"""MarkdownParser — 用 #/##/### 直接识别标题层级。"""
from __future__ import annotations

import re

from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.parser.base import BaseDocumentParser

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_LIST_RE = re.compile(r"^\s*[-*+]\s+(.+)$")


class MarkdownParser(BaseDocumentParser):
    def parse(self, file_path: str) -> DocumentAST:
        with open(file_path, encoding="utf-8") as f:
            raw = f.read()

        root = DocumentNode(type="section", text="", level=0)
        stack: list[DocumentNode] = [root]
        buf: list[str] = []          # 当前段落缓冲（空行或标题时 flush）

        def _flush():
            if buf:
                stack[-1].children.append(
                    DocumentNode(type="paragraph", text="\n".join(buf)))
                buf.clear()

        for line in raw.split("\n"):
            m = _HEADING_RE.match(line)
            if m:
                _flush()
                level = len(m.group(1))
                title = m.group(2).strip()
                node = DocumentNode(type="section", text=title, level=level)
                while len(stack) > 1 and stack[-1].level >= level:
                    stack.pop()
                stack[-1].children.append(node)
                stack.append(node)
                continue
            li = _LIST_RE.match(line)
            if li:
                _flush()
                stack[-1].children.append(DocumentNode(type="list", text=li.group(1).strip()))
                continue
            if not line.strip():
                _flush()
                continue
            buf.append(line.strip())

        _flush()
        return DocumentAST(root=root, source_file=file_path, raw_text=raw)
```

`txt_parser.py`：

```python
"""TxtParser — 用空行 + 编号规则识别结构。"""
from __future__ import annotations

import re

from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.parser.base import BaseDocumentParser

_NUM_HEADING_RE = re.compile(r"^(?:第[一二三四五六七八九十百千\d]+[章节条]|[一二三四五六七八九十]+、|\d+(?:\.\d+)*[、.)]?)\s*(.+)$")


class TxtParser(BaseDocumentParser):
    def parse(self, file_path: str) -> DocumentAST:
        with open(file_path, encoding="utf-8") as f:
            raw = f.read()

        root = DocumentNode(type="section", text="", level=0)
        buf: list[str] = []

        def _flush():
            if buf:
                root.children.append(DocumentNode(type="paragraph", text="\n".join(buf)))
                buf.clear()

        for line in raw.split("\n"):
            m = _NUM_HEADING_RE.match(line.strip())
            if m and len(line.strip()) <= 60:
                _flush()
                root.children.append(DocumentNode(type="section", text=m.group(1).strip(), level=1))
                continue
            if not line.strip():
                _flush()
                continue
            buf.append(line.strip())

        _flush()
        return DocumentAST(root=root, source_file=file_path, raw_text=raw)
```

`excel_parser.py`：

```python
"""ExcelParser — 占位，Phase 3 实现。"""
from __future__ import annotations

from backend.rag.preprocessing.ast import DocumentAST
from backend.rag.preprocessing.parser.base import BaseDocumentParser


class ExcelParser(BaseDocumentParser):
    def parse(self, file_path: str) -> DocumentAST:
        raise NotImplementedError("Excel 解析器待实现")
```

`__init__.py`：

```python
"""Format Parser 层 — 按扩展名分发到对应解析器。"""
from __future__ import annotations

import os

from backend.rag.preprocessing.ast import DocumentAST
from backend.rag.preprocessing.parser.markdown_parser import MarkdownParser
from backend.rag.preprocessing.parser.txt_parser import TxtParser

_PARSERS = {
    ".md": MarkdownParser,
    ".markdown": MarkdownParser,
    ".txt": TxtParser,
}


def parse_file(file_path: str) -> DocumentAST:
    """按扩展名分发解析器，未知扩展回退 TxtParser。"""
    ext = os.path.splitext(file_path)[1].lower()
    parser_cls = _PARSERS.get(ext, TxtParser)
    return parser_cls().parse(file_path)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/rag/test_parser.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add backend/rag/preprocessing/parser/ backend/tests/rag/test_parser.py
git commit -m "feat(chunking): Parser 层 Markdown/TXT → Raw AST"
```

---

### Task 4: Structure Analyzer（Raw AST → Normalized AST + StructureReport）

**Files:**
- Create: `backend/rag/preprocessing/structure_analyzer.py`
- Test: `backend/tests/rag/test_structure_analyzer.py`

**Interfaces:**
- Produces: `StructureReport`（字段：`ast/completeness/deficit_signal/topic_shift_detected/is_high_value_and_chaotic`，property `is_complete`）、`StructureAnalyzer().analyze(raw_ast) -> tuple[DocumentAST, StructureReport]`。Task 6 的 Router 消费 `StructureReport`。
- Consumes: `walk`、`count_tokens`、`STRUCTURE_COMPLETE_THRESHOLD`、`LEAF_TYPES`。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/rag/test_structure_analyzer.py
from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.structure_analyzer import StructureAnalyzer

STRUCTURED = DocumentAST(
    root=DocumentNode(type="section", text="", level=0, children=[
        DocumentNode(type="section", text="退货流程", level=1, children=[
            DocumentNode(type="paragraph", text="客服审核退货原因。"),
        ]),
        DocumentNode(type="section", text="差评处理", level=1, children=[
            DocumentNode(type="paragraph", text="48小时内给出方案。"),
        ]),
    ]),
    raw_text="退货流程\n客服审核退货原因。\n差评处理\n48小时内给出方案。",
)

UNSTRUCTURED = DocumentAST(
    root=DocumentNode(type="section", text="", level=0, children=[
        DocumentNode(type="paragraph", text="客户提交申请后，客服首先核对订单信息。对于特殊商品还需检查退货条件。确认后进入下一阶段。"),
    ]),
    raw_text="客户提交申请后，客服首先核对订单信息。对于特殊商品还需检查退货条件。确认后进入下一阶段。",
)


def test_structured_doc_high_completeness():
    _, report = StructureAnalyzer().analyze(STRUCTURED)
    assert report.is_complete is True
    assert report.deficit_signal == ""


def test_unstructured_doc_low_completeness():
    _, report = StructureAnalyzer().analyze(UNSTRUCTURED)
    assert report.is_complete is False
    assert report.deficit_signal == "no_heading"


def test_empty_doc_zero_completeness():
    empty = DocumentAST(root=DocumentNode(type="section", text="", level=0), raw_text="")
    _, report = StructureAnalyzer().analyze(empty)
    assert report.completeness == 0.0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/rag/test_structure_analyzer.py -v`
Expected: FAIL（`ModuleNotFoundError: backend.rag.preprocessing.structure_analyzer`）

- [ ] **Step 3: 实现 structure_analyzer.py**

```python
"""StructureAnalyzer — Raw AST → Normalized AST + StructureReport。

规则优先：归一化 AST + 计算结构完整度 + 判定结构不足信号。
结构混乱时的 LLM 补充是 Phase 2，本文件不实现。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from backend.config import STRUCTURE_COMPLETE_THRESHOLD, PARENT_CHUNK_TOKENS
from backend.rag.preprocessing.ast import DocumentAST, DocumentNode, LEAF_TYPES, walk
from backend.rag.preprocessing.token_counter import count_tokens


@dataclass
class StructureReport:
    ast: DocumentAST
    completeness: float
    deficit_signal: str = ""
    topic_shift_detected: bool = False        # Phase 2 接入
    is_high_value_and_chaotic: bool = False   # Phase 2 接入

    @property
    def is_complete(self) -> bool:
        return self.completeness >= STRUCTURE_COMPLETE_THRESHOLD


class StructureAnalyzer:
    def analyze(self, raw_ast: DocumentAST) -> tuple[DocumentAST, StructureReport]:
        # Phase 1 归一化为最小实现：原样透传（结构已由 Parser 建好）
        normalized = raw_ast
        completeness = self._compute_completeness(raw_ast)
        deficit = self._detect_deficit(raw_ast, completeness)
        report = StructureReport(
            ast=normalized,
            completeness=completeness,
            deficit_signal=deficit,
        )
        return normalized, report

    def _compute_completeness(self, ast: DocumentAST) -> float:
        total = len(ast.raw_text.strip())
        if total == 0:
            return 0.0
        leaves = [n for n in walk(ast.root) if n.type in LEAF_TYPES]
        if not leaves:
            return 0.0
        sections = [n for n in walk(ast.root) if n.type == "section" and n.level > 0]
        if not sections:
            return 0.1   # 无任何章节结构 → 结构性极低，交由递归兜底
        # coverage：所有结构化节点（section 标题 + leaf 正文）覆盖的字符 / 总字符。
        # 标题也计入分子，否则短文档+多标题会被低估（标题计入分母却不计入分子）。
        covered = sum(len(n.text) for n in leaves) + sum(len(n.text) for n in sections)
        coverage = covered / total
        oversized = sum(1 for n in leaves if count_tokens(n.text) > PARENT_CHUNK_TOKENS)
        size_fitness = 1.0 - oversized / len(leaves)
        has_hierarchy = 1.0 if len(sections) >= 2 else 0.0
        return round(0.5 * min(coverage, 1.0) + 0.3 * size_fitness + 0.2 * has_hierarchy, 4)

    def _detect_deficit(self, ast: DocumentAST, completeness: float) -> str:
        if completeness >= STRUCTURE_COMPLETE_THRESHOLD:
            return ""
        sections = [n for n in walk(ast.root) if n.type == "section" and n.level > 0]
        if not sections:
            return "no_heading"
        return "long_narrative"
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/rag/test_structure_analyzer.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add backend/rag/preprocessing/structure_analyzer.py backend/tests/rag/test_structure_analyzer.py
git commit -m "feat(chunking): StructureAnalyzer 结构完整度评分"
```

---

### Task 5: ChunkStrategy 重构（消费 AST + 双粒度 Parent-Child）

**Files:**
- Modify: `backend/rag/preprocessing/chunking.py`
- Test: `backend/tests/rag/test_chunking_strategies.py`

**Interfaces:**
- Produces: `StructureChunkStrategy`、`StepChunkStrategy`、`QAChunkStrategy`、`RecursiveChunkStrategy`，每个实现 `split(ast: DocumentAST, file_path: str) -> list[Document]`；返回的 Document 携带统一 metadata（`granularity`/`parent_chunk_id`/`section_path`/`section_title`/`section_level`/`chunk_tokens`）。
- Consumes: `DocumentAST`、`walk`、`iter_sections`、`count_tokens`、`LEAF_CHUNK_TOKENS`、`PARENT_CHUNK_TOKENS`。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/rag/test_chunking_strategies.py
from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.chunking import StructureChunkStrategy, RecursiveChunkStrategy

AST = DocumentAST(
    root=DocumentNode(type="section", text="", level=0, children=[
        DocumentNode(type="section", text="售后制度", level=1, children=[
            DocumentNode(type="section", text="退货流程", level=2, children=[
                DocumentNode(type="paragraph", text="客服审核退货原因。"),
            ]),
            DocumentNode(type="section", text="差评处理", level=2, children=[
                DocumentNode(type="paragraph", text="48小时内给出方案。"),
            ]),
        ]),
    ]),
    source_file="a.md",
    raw_text="",
)


def test_structure_strategy_produces_parent_and_leaf():
    chunks = StructureChunkStrategy().split(AST, "a.md")
    granules = {c.metadata["granularity"] for c in chunks}
    assert granules == {"leaf", "parent"}
    # 每个 leaf 有 parent_chunk_id + section_path
    leaf = next(c for c in chunks if c.metadata["granularity"] == "leaf")
    assert leaf.metadata["parent_chunk_id"]
    assert leaf.metadata["section_path"]


def test_leaf_links_to_its_section_parent():
    chunks = StructureChunkStrategy().split(AST, "a.md")
    parents = {c.metadata["chunk_id"]: c for c in chunks if c.metadata["granularity"] == "parent"}
    for c in chunks:
        if c.metadata["granularity"] == "leaf":
            pid = c.metadata["parent_chunk_id"]
            assert pid in parents
            assert c.metadata["section_path"] == parents[pid].metadata["section_path"]


def test_structure_leaves_not_duplicated_across_sections():
    chunks = StructureChunkStrategy().split(AST, "a.md")
    leaf_texts = [c.page_content for c in chunks if c.metadata["granularity"] == "leaf"]
    # 每个段落叶子只出现一次（不因嵌套层级在每个祖先 section 重复产出）
    assert leaf_texts.count("客服审核退货原因。") == 1
    assert leaf_texts.count("48小时内给出方案。") == 1


QA_AST = DocumentAST(
    root=DocumentNode(type="section", text="", level=0, children=[
        DocumentNode(type="qa_question", text="怎么退货？"),
        DocumentNode(type="qa_answer", text="提交申请后客服审核。"),
    ]),
    raw_text="",
)


def test_all_strategies_emit_metadata_protocol():
    cases = [
        (StructureChunkStrategy(), AST),
        (RecursiveChunkStrategy(), AST),
        (QAChunkStrategy(), QA_AST),
    ]
    for strategy, ast in cases:
        chunks = strategy.split(ast, "a.md")
        for c in chunks:
            for key in ("chunk_id", "chunk_tokens"):
                assert key in c.metadata, f"{strategy.__class__.__name__} 缺 {key}"
            if c.metadata["granularity"] == "leaf":
                for key in ("parent_chunk_id", "section_path",
                            "section_title", "section_level"):
                    assert key in c.metadata, f"{strategy.__class__.__name__} leaf 缺 {key}"


def test_recursive_strategy_splits_long_leaf():
    long_text = "这是一个没有结构的长段落。" * 200
    ast = DocumentAST(
        root=DocumentNode(type="section", text="", level=0, children=[
            DocumentNode(type="paragraph", text=long_text),
        ]),
        raw_text=long_text,
    )
    chunks = RecursiveChunkStrategy().split(ast, "b.txt")
    assert len(chunks) > 1
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/rag/test_chunking_strategies.py -v`
Expected: FAIL（`ImportError: cannot import name 'StructureChunkStrategy'`）

- [ ] **Step 3: 实现策略层（重写 chunking.py 的 Strategy 部分）**

删除原有 `_find_sections` / `_STEP_PATTERN` / `_ARTICLE_PATTERN` / `_QA_PATTERN` / `GeneralChunkStrategy` / `ManualPolicyChunkStrategy` / `ProjectReportChunkStrategy` / `ManualChunkStrategy` / `ContractChunkStrategy` / `QAChunkStrategy`（旧版），替换为下面四个策略。**保留文件顶部的 import 与日志风格**，其余旧类在 Task 6 完成后一并清理。

```python
"""chunking.py — 文档类型感知切分（消费 Normalized AST）。

Strategy 只负责「既然知道结构，怎么切」，不再重新检测标题/章节。
结构检测在 parser + structure_analyzer 完成。
"""
from __future__ import annotations

import hashlib
import os
from typing import List

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from backend.config import LEAF_CHUNK_TOKENS, PARENT_CHUNK_TOKENS
from backend.rag.preprocessing.ast import DocumentAST, DocumentNode, LEAF_TYPES, walk
from backend.rag.preprocessing.token_counter import count_tokens
from backend.shared.logger import logger

_SEPARATORS = ["\n\n", "\n", "。", "！", "？", ".", "!", "?", " ", ""]


def _chunk_id(doc_id: str, index: int) -> str:
    return hashlib.md5(f"{doc_id}:{index}".encode()).hexdigest()[:12]


def _enrich(chunks: List[Document], file_path: str) -> List[Document]:
    """统一 metadata：parent_doc_id / chunk_index / source_file / file_path。"""
    parent_doc_id = hashlib.md5(file_path.encode()).hexdigest()[:10]
    source_file = os.path.basename(file_path)
    for i, c in enumerate(chunks):
        c.metadata.update({
            "parent_doc_id": parent_doc_id,
            "chunk_index": i,
            "total_chunks": len(chunks),
            "source_file": source_file,
            "file_path": file_path,
        })
    return chunks


def _make_doc(text: str, meta: dict) -> Document:
    return Document(page_content=text, metadata=meta)


class StructureChunkStrategy:
    """结构化切分：每个 section → parent，section 内叶子 → leaf。"""

    def split(self, ast: DocumentAST, file_path: str) -> List[Document]:
        chunks: List[Document] = []
        for sec, path in self._sections(ast):
            sec_text = self._section_text(sec)
            parent_meta = {
                "granularity": "parent",
                "chunk_id": _chunk_id(":".join(path), "parent"),
                "section_path": path,
                "section_title": sec.text,
                "section_level": sec.level,
                "chunk_tokens": count_tokens(sec_text),
            }
            parent_id = parent_meta["chunk_id"]
            chunks.append(_make_doc(sec_text, dict(parent_meta)))
            for i, leaf in enumerate(self._leaves(sec)):
                chunks.append(_make_doc(leaf.text, {
                    "granularity": "leaf",
                    "chunk_id": _chunk_id(":".join(path), f"leaf:{i}"),
                    "parent_chunk_id": parent_id,
                    "section_path": path,
                    "section_title": sec.text,
                    "section_level": sec.level,
                    "chunk_tokens": count_tokens(leaf.text),
                }))
        return _enrich(chunks, file_path)

    @staticmethod
    def _sections(ast: DocumentAST):
        def _dfs(node: DocumentNode, path: list):
            for child in node.children:
                if child.type == "section":
                    yield child, path + [child.text]
                    yield from _dfs(child, path + [child.text])
                else:
                    yield from _dfs(child, path)
        yield from _dfs(ast.root, [])

    @staticmethod
    def _leaves(section: DocumentNode):
        # 只产出直接子叶，避免嵌套层级下叶子在每个祖先 section 重复产出
        for n in section.children:
            if n.type in LEAF_TYPES:
                yield n

    @staticmethod
    def _section_text(section: DocumentNode) -> str:
        parts = [n.text for n in walk(section) if n is not section and n.text]
        return "\n".join(parts)


class RecursiveChunkStrategy:
    """递归切分兜底：把叶子文本按 token 上限递归切分（不做结构检测）。"""

    def split(self, ast: DocumentAST, file_path: str) -> List[Document]:
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=LEAF_CHUNK_TOKENS, chunk_overlap=50,
            length_function=count_tokens, separators=_SEPARATORS,
        )
        chunks: List[Document] = []
        for n in walk(ast.root):
            if n.type not in LEAF_TYPES:
                continue
            texts = (splitter.split_text(n.text)
                     if count_tokens(n.text) > LEAF_CHUNK_TOKENS else [n.text])
            for i, sub in enumerate(texts):
                chunks.append(_make_doc(sub, {
                    "granularity": "leaf",
                    "chunk_id": _chunk_id(n.text[:50], str(i)),
                    "parent_chunk_id": "",
                    "section_path": [],
                    "section_title": "",
                    "section_level": 0,
                    "chunk_tokens": count_tokens(sub),
                }))
        return _enrich(chunks, file_path)


class StepChunkStrategy:
    """步骤切分（sop/training）：Phase 1 先复用结构切分，步骤级优化 Phase 2。"""

    def split(self, ast: DocumentAST, file_path: str) -> List[Document]:
        return StructureChunkStrategy().split(ast, file_path)


class QAChunkStrategy:
    """FAQ 切分：每个 qa_question/qa_answer 对 → 一个 chunk。"""

    def split(self, ast: DocumentAST, file_path: str) -> List[Document]:
        chunks: List[Document] = []
        for i, n in enumerate([n for n in walk(ast.root)
                               if n.type in ("qa_question", "qa_answer")]):
            chunks.append(_make_doc(n.text, {
                "granularity": "leaf",
                "chunk_id": _chunk_id(n.text[:50], str(i)),
                "parent_chunk_id": "",
                "section_path": [],
                "section_title": n.text[:40],
                "section_level": 0,
                "chunk_tokens": count_tokens(n.text),
            }))
        return _enrich(chunks, file_path)
```

> 说明：本 Step 会删除旧策略类，`backend/rag/indexing/indexer.py` 若引用旧类名（`GeneralChunkStrategy` 等）会暂时报错——这是预期，Task 7 会改成新流水线。Task 5 只要求 `test_chunking_strategies.py` 通过。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/rag/test_chunking_strategies.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add backend/rag/preprocessing/chunking.py backend/tests/rag/test_chunking_strategies.py
git commit -m "feat(chunking): Strategy 消费 AST + 双粒度 Parent-Child"
```

---

### Task 6: ChunkStrategyRouter 双轴路由

**Files:**
- Modify: `backend/rag/preprocessing/chunking.py`
- Test: `backend/tests/rag/test_chunking_router.py`

**Interfaces:**
- Produces: `ChunkStrategyRouter().route(doc_type: str, report: StructureReport) -> object`（返回 Strategy 实例）。
- Consumes: `StructureReport`、`STRUCTURE_STRATEGIES` 映射、`ENABLE_LLM_CHUNKING`、`ENABLE_SEMANTIC_CHUNKING`。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/rag/test_chunking_router.py
from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.chunking import (
    ChunkStrategyRouter, StructureChunkStrategy, QAChunkStrategy, RecursiveChunkStrategy,
)
from backend.rag.preprocessing.structure_analyzer import StructureReport


def _report(completeness: float):
    return StructureReport(
        ast=DocumentAST(root=DocumentNode(type="section", text="", level=0)),
        completeness=completeness,
    )


def test_structure_complete_routes_by_doc_type():
    r = ChunkStrategyRouter()
    assert isinstance(r.route("policy", _report(0.9)), StructureChunkStrategy)
    assert isinstance(r.route("faq", _report(0.9)), QAChunkStrategy)


def test_low_completeness_falls_back_to_recursive():
    r = ChunkStrategyRouter()
    assert isinstance(r.route("policy", _report(0.3)), RecursiveChunkStrategy)
    assert isinstance(r.route("general", _report(0.1)), RecursiveChunkStrategy)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/rag/test_chunking_router.py -v`
Expected: FAIL（`ChunkStrategyRouter` 旧接口无 `route(doc_type, report)` 签名，报 TypeError/AttributeError）

- [ ] **Step 3: 实现 Router（替换旧 `ChunkStrategyRouter`）**

在 `chunking.py` 末尾追加（删除旧 `ChunkStrategyRouter` 类）：

```python
STRUCTURE_STRATEGIES = {
    "policy": StructureChunkStrategy,
    "compliance": StructureChunkStrategy,
    "security": StructureChunkStrategy,
    "financial": StructureChunkStrategy,
    "customer_data": StructureChunkStrategy,
    "product_spec": StructureChunkStrategy,
    "listing": StructureChunkStrategy,
    "sop": StepChunkStrategy,
    "training": StepChunkStrategy,
    "legal": StructureChunkStrategy,      # 合同条款级结构 Phase 2 细化
    "contract_template": StructureChunkStrategy,
    "faq": QAChunkStrategy,
    "ad_policy": RecursiveChunkStrategy,
}


class ChunkStrategyRouter:
    """双轴路由：文档类型 × 结构完整度 → 策略。优先级 Structure > LLM > Semantic > Recursive。"""

    def route(self, doc_type: str, report: StructureReport):
        from backend.config import ENABLE_LLM_CHUNKING, ENABLE_SEMANTIC_CHUNKING

        if report.is_complete:
            cls = STRUCTURE_STRATEGIES.get(doc_type, RecursiveChunkStrategy)
            return cls()

        # Phase 2：LLM 高价值特殊处理、Semantic 高级处理（默认关闭，暂不触发）
        if report.is_high_value_and_chaotic and ENABLE_LLM_CHUNKING:
            logger.info("[Router] 高价值混乱文档 → LLM Assisted（Phase 2）")
        if report.topic_shift_detected and ENABLE_SEMANTIC_CHUNKING:
            logger.info("[Router] 主题变化 → Semantic（Phase 2）")

        return RecursiveChunkStrategy()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/rag/test_chunking_router.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add backend/rag/preprocessing/chunking.py backend/tests/rag/test_chunking_router.py
git commit -m "feat(chunking): ChunkStrategyRouter 双轴路由"
```

---

### Task 7: 流水线编排 + indexer 集成 + 重索引

**Files:**
- Create: `backend/rag/preprocessing/pipeline.py`
- Modify: `backend/rag/indexing/indexer.py`（`_index_file_inner` 的 chunk 段改用新流水线）
- Test: `backend/tests/rag/test_chunking_pipeline.py`

**Interfaces:**
- Produces: `parse_and_chunk(file_path: str, doc_type_hint: str = "") -> list[Document]`（Parser → Cleaner → Analyzer → Router → Strategy）。
- Consumes: `parse_file`、`StructureAnalyzer`、`ChunkStrategyRouter`、`classify_doc_type`、`DocumentCleaner`。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/rag/test_chunking_pipeline.py
from backend.rag.preprocessing.pipeline import parse_and_chunk

MD = """# 售后制度
## 退货流程
### 审核
客服审核退货原因。
## 差评处理
48小时内给出方案。
"""


def test_pipeline_end_to_end(tmp_path):
    p = tmp_path / "a.md"
    p.write_text(MD, encoding="utf-8")
    chunks = parse_and_chunk(str(p))
    assert chunks
    assert {"leaf", "parent"} <= {c.metadata["granularity"] for c in chunks}
    leaf = next(c for c in chunks if c.metadata["granularity"] == "leaf")
    assert leaf.metadata["parent_chunk_id"]
    assert leaf.metadata["section_path"]


def test_pipeline_unstructured_falls_back(tmp_path):
    t = tmp_path / "b.txt"
    t.write_text("客户提交申请后，客服核对订单信息。" * 100, encoding="utf-8")
    chunks = parse_and_chunk(str(t))
    assert chunks
    assert all(c.metadata.get("chunk_tokens", 0) > 0 for c in chunks)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/rag/test_chunking_pipeline.py -v`
Expected: FAIL（`ModuleNotFoundError: backend.rag.preprocessing.pipeline`）

- [ ] **Step 3: 实现 pipeline.py**

```python
"""切分流水线编排 — Parser → Cleaner → Analyzer → Router → Strategy。"""
from __future__ import annotations

from typing import List

from langchain_core.documents import Document

from backend.rag.preprocessing.cleaner import DocumentCleaner
from backend.rag.preprocessing.metadata import classify_doc_type
from backend.rag.preprocessing.parser import parse_file
from backend.rag.preprocessing.structure_analyzer import StructureAnalyzer
from backend.rag.preprocessing.chunking import ChunkStrategyRouter
from backend.rag.preprocessing.ast import walk
from backend.shared.logger import logger


def parse_and_chunk(file_path: str, doc_type_hint: str = "") -> List[Document]:
    """单文件完整切分流水线。返回 leaf + parent 双粒度 chunk。"""
    raw_ast = parse_file(file_path)

    # 结构安全清洗：清洗每个节点文本，保留结构
    cleaner = DocumentCleaner()
    source_type = "pdf" if file_path.lower().endswith(".pdf") else "text"
    for node in walk(raw_ast.root):
        if node.type not in ("table",):  # table 的 rows 不在 text 清洗范围
            node.text = cleaner.clean(node.text, source_type=source_type).text

    normalized_ast, report = StructureAnalyzer().analyze(raw_ast)

    doc_type = doc_type_hint or classify_doc_type(raw_ast.raw_text, filename=file_path, file_path=file_path)
    strategy = ChunkStrategyRouter().route(doc_type, report)
    logger.info(
        f"[ChunkPipeline] {file_path} doc_type={doc_type} "
        f"completeness={report.completeness} → {strategy.__class__.__name__}"
    )
    return strategy.split(normalized_ast, file_path)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/rag/test_chunking_pipeline.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 修改 indexer.py 接入新流水线**

在 `backend/rag/indexing/indexer.py` 的 `_index_file_inner` chunk 段（原 `split_documents` 调用处）替换为：

```python
            from backend.rag.preprocessing.pipeline import parse_and_chunk
            chunks = parse_and_chunk(file_path)
            strategy_name = "pipeline"   # 具体策略名由 pipeline 日志输出
            chunk_size = LEAF_CHUNK_TOKENS
            chunk_overlap = 50
```

并确认：`doc_id` / `chunk_index` / `source_file` / `file_path` 的注入与 ChunkFilter 段（原 431-452 行）保持不变，仍在新 chunk 上执行。

- [ ] **Step 6: 触发重索引**

bump 版本触发全量重建：删除 `CHROMA_PATH` 和 `DOC_DB_PATH` 下的 `.version` 文件（或直接删库目录），重启后端让 `_need_rebuild` 判定重建。同时验证 `pytest tests/rag/` 全量不回归。

- [ ] **Step 7: 回归 + 提交**

Run: `cd backend && python -m pytest tests/rag/ -v`
Expected: 全部 PASS，且 `test_indexer_trace.py` / `test_keyword_dedup.py` 等既有测试不回归。

```bash
git add backend/rag/preprocessing/pipeline.py backend/rag/indexing/indexer.py backend/tests/rag/test_chunking_pipeline.py
git commit -m "feat(chunking): 流水线编排 + indexer 接入新切分"
```

---

## 自审记录

- **Spec 覆盖**：Phase 1 全部交付物（Parser / Raw AST / DocumentCleaner / Structure Analyzer / 双轴 Router / Structure+QA+Recursive / Parent+Leaf / section_path / token counting / ChunkFilter 复用 / 重索引）均有对应 Task。Semantic / LLM Assisted / PyMuPDF / python-docx / Excel 属 Phase 2/3，本计划不实现（Router 留了开关分支，Excel 留了占位类）。
- **类型一致性**：`DocumentNode`/`DocumentAST`/`StructureReport`/`count_tokens`/`parse_file`/`split(ast, file_path)`/`route(doc_type, report)` 在各 Task 定义与引用一致。
- **占位检查**：无 TBD/TODO；异常处理均记录日志或有意义降级，无 `except Exception: pass`。
