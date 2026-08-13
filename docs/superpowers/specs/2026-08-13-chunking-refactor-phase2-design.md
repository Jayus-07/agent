# Chunking 切分重构 Phase 2 设计

> 2026-08-13 | 状态：设计中
> 范围：**P0 PDF/DOCX 接入新流水线 + P1 faq 路由 + QA 节点识别**
> 关联：Phase 1 设计 [2026-08-13-chunking-refactor-design.md](2026-08-13-chunking-refactor-design.md) §11.2、Phase 1 计划完成记录（37 step 全勾选）
> 非范围：LLM Assisted / Semantic / Step / PDF 标题启发式 / Excel / legal 细化（P2/P3 延后）

---

## 1. 背景与对齐审计结果

Phase 1 已完成（17 commit / 122 测试通过 / 31 文件 / +1107/-733 行），但**实际代码 vs 设计意图**存在未修复的功能缺陷：

### 1.1 🚨 P0 缺陷：PDF/DOCX 增量索引静默失败

**链路**（[indexer.py:297-446](backend/rag/indexing/indexer.py)）：
1. 第 309-328 行：`PyPDFLoader` / `Docx2txtLoader` 加载 → `raw_docs` 有内容
2. 第 421 行：`chunks = parse_and_chunk(file_path)` 走新流水线
3. [pipeline.py:23-25](backend/rag/preprocessing/pipeline.py)：`ext not in _SUPPORTED_EXTS`（仅 `.md`/`.markdown`/`.txt`）→ `return []`
4. `chunks = []` → embed 阶段写入 0 chunk → **0 chunk 入库**

**影响**：用户上传 PDF/DOCX 后没有报错日志，但知识库里没东西。这不是「功能不支持」，是「静默失败」—— 更危险。

### 1.2 � P1 缺陷：loader 全量加载直接跳过 PDF/DOCX

[loader.py:31-32](backend/rag/preprocessing/loader.py)：
```python
if ext not in (".md", ".txt"):
    continue  # Phase 1 仅支持 md/txt；pdf/docx 延后 Phase 2
```
全量重建路径完全不支持 PDF/DOCX。

### 1.3 🚨 P1 缺陷：faq 路由错误 + QA 节点识别缺失

- [chunking.py:175](backend/rag/preprocessing/chunking.py) `"faq": StructureChunkStrategy` —— 注释「Q/A 节点识别留 Phase 2」
- `QAChunkStrategy` 类存在（[chunking.py:141-160](backend/rag/preprocessing/chunking.py)），但**没有任何 Parser 产出 `qa_question` / `qa_answer` 节点**
- `grep "qa_question\|qa_answer" backend/rag/preprocessing/parser/` → 0 命中
- **结果**：`QAChunkStrategy` 是死代码；faq 文档走 StructureChunkStrategy 当普通文档切

---

## 2. 目标

### 2.1 P0：PDF/DOCX 接入新流水线

- PDF/DOCX 文档能通过增量索引和全量加载**正常入库**，产生 chunk
- 入库的 chunk 与 MD/TXT 走**同一套** Router → Strategy → ChunkFilter
- 切分质量：能识别章节结构（PDF 标题启发式先不做，但章节边界要尽量猜）

### 2.2 P1：faq 真路由 + loader 同步 + QA 节点识别

- `markdown_parser.py` / `txt_parser.py` 识别 FAQ 模式 → 产出 `qa_question` / `qa_answer` 节点
- `chunking.py:175` `STRUCTURE_STRATEGIES["faq"]` 改为 `QAChunkStrategy`
- `loader.py:31` 扩展名白名单同步加 `.pdf`、`.docx`

## 3. 非目标（P2/P3 延后，本 Spec 不实现）

- ❌ LLMAssistedChunking / SemanticChunking 类
- ❌ `topic_shift_detected` / `is_high_value_and_chaotic` 实际计算
- ❌ PDF 标题启发式评分（字号/粗体/编号综合打分）
- ❌ StepChunkStrategy 真实现（步骤编号识别）
- ❌ Excel Parser
- ❌ `legal` 合同条款级切分细化
- ❌ `LLMAssistedChunking` / `SemanticChunking` Router 分支接入（接口已预留）

---

## 4. P0 设计：PDF/DOCX Parser

### 4.1 模块结构

新增两个 Parser：

```
backend/rag/preprocessing/parser/
  pdf_parser.py       # PyMuPDF → Raw AST（结构感知：标题字号/粗体）
  docx_parser.py      # python-docx → Raw AST（结构感知：Heading 1/2/3 + Table）
```

依赖（仅在对应 Parser 中 import，不污染 Chunking 核心）：

```
PyMuPDF>=1.23     # pdf_parser.py 内部
python-docx>=1.0  # docx_parser.py 内部
```

### 4.2 pdf_parser.py 接口

```python
class PdfParser(BaseDocumentParser):
    def parse(self, file_path: str) -> DocumentAST:
        """PyMuPDF 解析 → Raw AST。

        段落识别规则（Phase 2 简化版，未做字号启发式）：
        - 每页 Document.page → DocumentNode(type="paragraph")
        - 段落合并：连续非空 paragraph 合并为单个 leaf
        - 不识别 Heading（Phase 3 接 HEADING_THRESHOLD 时再做）
        - 表格识别：PyMuPDF page.get_text("dict") 的 type=1 块 → table
        """
```

**Phase 2 简化策略**：PDF 不做标题启发式，整体当成「无章节长段落」处理 → Router 走 `RecursiveChunkStrategy` 兜底切分。这比当前「静默 0 chunk」已经是巨大改进。

### 4.3 docx_parser.py 接口

```python
class DocxParser(BaseDocumentParser):
    def parse(self, file_path: str) -> DocumentAST:
        """python-docx 解析 → Raw AST。

        段落识别规则：
        - doc.paragraphs 按 style.name 分类：
          - Heading 1/2/3 → DocumentNode(type="section", level=N, text=title)
          - Normal/List Bullet/Number → DocumentNode(type="paragraph"|"list")
        - doc.tables → DocumentNode(type="table", rows=...)
        - 章节归属：标题下的 paragraph/list 挂在该 section.children
        """
```

### 4.4 pipeline.py 扩展名白名单

[pipeline.py:17](backend/rag/preprocessing/pipeline.py)：

```python
_SUPPORTED_EXTS = {".md", ".markdown", ".txt", ".pdf", ".docx"}
```

### 4.5 indexer.py PDF/DOCX 处理路径统一

**当前**：[indexer.py:309-328](backend/rag/indexing/indexer.py) 用 `PyPDFLoader` / `Docx2txtLoader` 加载到 `raw_docs`，但 chunk 段用 `parse_and_chunk` 又走 Parser → 双链路并行。

**Phase 2 方案**：
- **方案 A**（推荐）：删掉 [indexer.py:309-328](backend/rag/indexing/indexer.py) 的 `PyPDFLoader`/`Docx2txtLoader` 加载分支，`raw_docs` 直接由 `parse_and_chunk` 产出
- **方案 B**：保留双链路但用 `try/except` 隔离，PDF/DOCX 走新流水线、其他走旧 langchain loader

**选 A**：统一走新流水线，符合 Phase 1 架构意图（Parser 唯一入口）。

---

## 5. P1 设计：faq 路由 + loader 白名单 + QA 节点识别

### 5.1 markdown_parser.py 增加 Q/A 节点识别

**识别模式**（优先级从高到低）：

```
1. `**Q:** ... **A:** ...`   → qa_question + qa_answer
2. `### 问题 / ### 答案`     → qa_question + qa_answer
3. `Q1. ... A1. ... Q2. ...` → qa_question + qa_answer（按编号配对）
```

实现细节（放在 MarkdownParser._parse_qa()）：

```python
_QA_PATTERNS = [
    (re.compile(r"\*\*Q[:：]?\s*(.+?)\*\*\s*\n?\s*\*\*A[:：]?\s*(.+?)\*\*", re.S), "bold"),
    (re.compile(r"^#{2,4}\s*(?:问题|Question|问)[：:]?\s*(.+?)\n+(.+?)(?=\n#{2,4}|\Z)", re.M | re.S), "heading_qa"),
    (re.compile(r"^Q\d*[.、]?\s*(.+?)\n+A\d*[.、]?\s*(.+?)(?=\nQ\d*|\Z)", re.M | re.S), "numbered"),
]

def _extract_qa(text: str) -> list[tuple[str, str]]:
    """提取 (question, answer) 对；不命中返回空。"""
    for pat, _ in _QA_PATTERNS:
        for m in pat.finditer(text):
            yield (m.group(1).strip(), m.group(2).strip())
```

在 MarkdownParser.parse() 中：先扫一遍 `text`，如果匹配到 Q/A 对 → 产 `qa_question` + `qa_answer` 节点挂在 root.children，**跳过普通 section 解析**（避免和 heading 重复切分）。

### 5.2 txt_parser.py 增加 Q/A 节点识别

复用同一套 `_QA_PATTERNS`（抽到 `parser/_qa_patterns.py` 共用）。

### 5.3 chunking.py 修复 faq 路由

```diff
 STRUCTURE_STRATEGIES = {
     ...
-    "faq": StructureChunkStrategy,   # Q/A 节点识别留 Phase 2
+    "faq": QAChunkStrategy,
     ...
 }
```

### 5.4 loader.py 扩展名白名单

[loader.py:31](backend/rag/preprocessing/loader.py)：

```diff
-if ext not in (".md", ".txt"):
-    continue  # Phase 1 仅支持 md/txt；pdf/docx 延后 Phase 2
+if ext not in (".md", ".txt", ".pdf", ".docx"):
+    continue
```

### 5.5 classify_doc_type 识别 FAQ 文档

确认 [preprocessing/metadata.py:classify_doc_type](backend/rag/preprocessing/metadata.py) 已有 faq 识别逻辑。如果没有，在文件名/内容含「FAQ」「问答」「常见问题」时返回 `"faq"`。

---

## 6. 接口总览

### 6.1 新增

| 接口 | 文件 | 说明 |
|---|---|---|
| `PdfParser.parse(file_path)` | `parser/pdf_parser.py` | PyMuPDF → Raw AST |
| `DocxParser.parse(file_path)` | `parser/docx_parser.py` | python-docx → Raw AST |
| `_extract_qa(text)` | `parser/_qa_patterns.py` | Q/A 对抽取（MD/TXT 共用） |

### 6.2 修改

| 文件 | 改动 |
|---|---|
| `parser/__init__.py` | `_PARSERS` 字典加 `.pdf` → `PdfParser`、`.docx` → `DocxParser` |
| `parser/markdown_parser.py` | 增加 `_extract_qa` 调用 |
| `parser/txt_parser.py` | 增加 `_extract_qa` 调用 |
| `pipeline.py` | `_SUPPORTED_EXTS` 加 `.pdf`、`.docx` |
| `chunking.py` | `STRUCTURE_STRATEGIES["faq"]` 改 `QAChunkStrategy` |
| `loader.py` | 扩展名白名单加 `.pdf`、`.docx` |
| `indexer.py` | 删除 PDF/DOCX 老 langchain loader 加载分支（统一走 `parse_and_chunk`） |

### 6.3 测试新增

| 测试 | 覆盖 |
|---|---|
| `tests/rag/test_pdf_parser.py` | PyMuPDF 加载、paragraph 合并、表格识别 |
| `tests/rag/test_docx_parser.py` | Heading 识别、Table 识别、章节归属 |
| `tests/rag/test_qa_parser.py` | 3 种 Q/A 模式（**Q:**/heading/编号）、负向用例 |
| `tests/rag/test_faq_routing.py` | doc_type="faq" 走 QAChunkStrategy |
| `tests/rag/test_loader_ext.py` | loader 扩展名白名单同步 |

---

## 7. 验收标准

### 7.1 P0 验收

**功能验证**（手工或 e2e）：
1. 上传 1 个 PDF 文件 → SSE 进度流 `index_vector_db` 阶段的 metrics.written_count > 0
2. 上传 1 个 DOCX 文件 → 同上
3. `/rag/search` 能召回这两个文档的 chunk

**单元测试**：
- `test_pdf_parser.py`：≥ 3 用例（基本加载 / paragraph 合并 / 表格识别）
- `test_docx_parser.py`：≥ 3 用例（Heading 解析 / Table 解析 / 章节归属）
- 全量 `pytest tests/rag/` 122 + 新增用例全绿，无回归

**回归验证**：
- MD/TXT 文档的索引流程不变
- 已有 122 测试用例继续通过

### 7.2 P1 验收

**Q/A 节点识别**：
- `test_qa_parser.py` 覆盖 3 种 Q/A 模式 + 负向用例
- 解析后 AST 包含 `qa_question` / `qa_answer` 节点（用 `walk(ast.root)` 验证）

**faq 路由**：
- `test_faq_routing.py`：doc_type="faq" + `StructureReport(completeness=0.9)` → `QAChunkStrategy` 实例
- `doc_type="policy"` + `completeness=0.9` → `StructureChunkStrategy`（无回归）

**loader 白名单**：
- `test_loader_ext.py`：遍历包含 `.md`/`.txt`/`.pdf`/`.docx`/`.json`（应跳过）的目录，只处理前 4 种

### 7.3 整体

- 全量测试：`pytest tests/rag/` 全绿（122 + 新增）
- TypeScript 类型检查（如有 frontend 改动）：`npx tsc --noEmit` 通过（本次无 frontend 改动，可跳过）
- 性能：单文件 PDF（< 50 页）解析 + 切分 < 10s（无强约束，记录 baseline）

---

## 8. 风险与依赖

### 8.1 依赖变更

| 依赖 | 用途 | 是否需要 requirements 更新 |
|---|---|---|
| PyMuPDF (fitz) | PDF 解析 | 是 |
| python-docx | DOCX 解析 | 是 |

需要在 `backend/requirements.txt` 加：

```
PyMuPDF>=1.23
python-docx>=1.0
```

### 8.2 风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| PyMuPDF 在 Windows + Python 3.10 安装失败 | PDF 解析跑不起来 | 先在本地 venv 验证 `pip install PyMuPDF`；失败时降级 `pypdf` 或 `pdfplumber` |
| PDF 无章节结构 → Router 走 Recursive | 切分质量低 | Phase 2 接受；Phase 3 再加 HEADING_THRESHOLD |
| Q/A 模式漏识别（FAQ 文档多种格式） | 切分粒度粗 | Phase 2 接受常见 3 种模式；异常 case 进 Issue 池 |
| indexer 删除老 PDF/DOCX 加载分支可能影响 PDF 表格 OCR | 暂不影响（PyMuPDF 也不做 OCR） | 记录在 P3 待办 |
| 既有 indexer trace span 结构变化（删除 PDF 加载分支） | trace metrics 字段变化 | 在 `index_parse` span 内记录 `loader="pipeline"` 标记 |

### 8.3 与 P2/P3 接口预留的兼容性

- Router 的 `ENABLE_LLM_CHUNKING` / `ENABLE_SEMANTIC_CHUNKING` 分支保留（仍只 log，不 return）
- `StructureReport.topic_shift_detected` / `is_high_value_and_chaotic` 字段保留（仍 False 占位）
- 不破坏 Phase 3 接 HEADING_THRESHOLD 时的扩展点

---

## 9. 不在本 Spec 的后续工作

| 序号 | 内容 | 优先级 |
|---|---|---|
| 1 | PDF 标题启发式评分（§7.2） | P3 |
| 2 | `LLMAssistedChunking` + Router 接入 | P3 |
| 3 | `SemanticChunking` + `topic_shift_detected` 实际计算 | P3 |
| 4 | `StepChunkStrategy` 真实现 | P3 |
| 5 | Excel Parser | P3 |
| 6 | `legal` 合同条款级切分细化 | P3 |

---

## 10. 与 Phase 1 的边界

**Phase 1 已完成的，Phase 2 不重做**：
- DocumentNode / DocumentAST / DocumentCleaner / Structure Analyzer / 双轴 Router 基础 / 4 个 Strategy / 双粒度 / 统一 metadata / 流水线编排 / 重索引

**Phase 2 边界**：
- 只解决 P0 缺陷（PDF/DOCX 静默失败）+ P1 缺陷（faq 路由 + QA 节点 + loader 白名单）
- 不引入新架构概念、不改 Router 路由逻辑（仅改 `STRUCTURE_STRATEGIES` 一行）

**Phase 1 设计文档 §11.2 列的范围**：
- 本 Spec 实现 PyMuPDF + python-docx 两条 ✅
- Semantic / LLM Assisted / PDF 标题启发式 / Step / Excel / legal → **不实现**（P3）
