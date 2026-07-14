# RAG 知识库企业级 Pipeline 升级 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 RAG 知识库构建企业级数据处理流水线：文档清洗 → 脏数据过滤 → BM25 持久化 → 前端真实数据对接

**Architecture:** 新增 `preprocessing/cleaner.py`（清洗层）+ `preprocessing/filter.py`（过滤器）+ `retrieval/bm25_store.py`（BM25 持久化），删除死代码和假数据页面，修复双重单例。所有新模块通过配置开关控制，默认关闭以保持向后兼容。

**Tech Stack:** Python 3.x, LangChain, ChromaDB, jieba, pickle, Next.js/TypeScript/React

**Spec:** [2026-07-14-rag-enterprise-pipeline-design.md](../specs/2026-07-14-rag-enterprise-pipeline-design.md)

## Global Constraints

- 所有新增配置项默认值 = 关闭新功能，用户显式开启
- 现有 `/rag/*` API 端点签名不变，只增加字段
- `RAGPipeline.ask()` 行为不变
- 每个 Phase 完成后必须用真实文档测试（PDF/TXT/MD），不走 Mock
- 遵循项目 CLAUDE.md 编码规范（正斜杠路径、类型注解、logger 统一封装）

---

# Phase 0：清理（Dead Code + 假页面 + 双重单例）

## Task 0.1：删除 ResumeChunkStrategy 死代码

**Files:**
- Modify: `preprocessing/chunking.py`

**Interfaces:**
- Consumes: nothing new
- Produces: `ChunkStrategyRouter` now has one fewer strategy defined (no behavioral change — it was never routed to)

- [ ] **Step 1：删除 ResumeChunkStrategy 类定义**

删除 [chunking.py:200-243](preprocessing/chunking.py#L200) 整个类（`class ResumeChunkStrategy` 及其 `split` 方法）。

同时删除 [chunking.py:66-78](preprocessing/chunking.py#L66) 的 `_RESUME_FIELD_PATTERN` 正则（它只被 ResumeChunkStrategy 使用）。

同时更新 [chunking.py:6](preprocessing/chunking.py#L6) 文件头注释，去掉 `resume → ResumeChunkStrategy` 那一行。

- [ ] **Step 2：验证删除后 import 和路由正常**

```powershell
.\.venv\Scripts\python.exe -c "from preprocessing.chunking import ChunkStrategyRouter; r = ChunkStrategyRouter(); print('OK:', list(r._strategies.keys()))"
```

- [ ] **Step 3：运行现有 chunking 测试确保不回归**

```powershell
PYTHONPATH=".venv/lib/site-packages" .\.venv\Scripts\python.exe -m pytest tests/test_chunking.py -v --tb=short
```

- [ ] **Step 4：提交**

```bash
git add preprocessing/chunking.py
git commit -m "refactor: 删除 ResumeChunkStrategy 死代码 — Router 无路由触发"
```

---

## Task 0.2：删除 PgVectorKnowledgeStore 空 stub

**Files:**
- Modify: `retrieval/knowledge_store.py`

- [ ] **Step 1：删除 PgVectorKnowledgeStore 类**

删除 [knowledge_store.py:218-264](retrieval/knowledge_store.py#L218) 整个 `PgVectorKnowledgeStore` 类及所有 `NotImplementedError` 方法。

更新文件头注释，去掉 `PgVectorKnowledgeStore` 的提及。

- [ ] **Step 2：验证删除后 import 正常**

```powershell
.\.venv\Scripts\python.exe -c "from retrieval.knowledge_store import KnowledgeStore, ChromaKnowledgeStore; print('OK')"
```

- [ ] **Step 3：提交**

```bash
git add retrieval/knowledge_store.py
git commit -m "refactor: 删除 PgVectorKnowledgeStore 空 stub — P2 实现时再写"
```

---

## Task 0.3：删除前端假数据页面 tasks + pipeline

**Files:**
- Delete: `web/src/app/knowledge/tasks/page.tsx`
- Delete: `web/src/app/knowledge/pipeline/page.tsx`
- Modify: `web/src/components/Sidebar.tsx`

- [ ] **Step 1：删除 tasks 页面**

```powershell
Remove-Item "web\src\app\knowledge\tasks\page.tsx"
```

检查 `web/src/app/knowledge/tasks/` 目录是否还有其他文件，若目录为空则删除目录。

- [ ] **Step 2：删除 pipeline 页面**

```powershell
Remove-Item "web\src\app\knowledge\pipeline\page.tsx"
```

同样检查并清理空目录。

- [ ] **Step 3：从 Sidebar 移除 tasks 入口**

修改 [Sidebar.tsx](web/src/components/Sidebar.tsx)，删除以下行：
```typescript
{ label: '索引任务', path: '/knowledge/tasks' },
```

- [ ] **Step 4：验证前端构建通过**

```powershell
cd web; npx next build 2>&1 | Select-Object -Last 20
```

- [ ] **Step 5：提交**

```bash
git add web/src/app/knowledge/tasks/ web/src/app/knowledge/pipeline/ web/src/components/Sidebar.tsx
git commit -m "refactor: 删除前端假数据页面 tasks/pipeline — 等真实 API 后再重建"
```

---

## Task 0.4：修复 RAGPipeline 双重单例

**Files:**
- Modify: `multi_agent/tools.py`
- Modify: `api/deps.py`

**Interfaces:**
- Consumes: `api.deps.get_rag_pipeline()` (existing, with double-checked locking)
- Produces: `multi_agent.tools._get_rag_pipeline()` now delegates to `api.deps.get_rag_pipeline()`

- [ ] **Step 1：修改 multi_agent/tools.py**

将 [tools.py:31-37](multi_agent/tools.py#L31) 的 `_get_rag_pipeline()` 改为：

```python
def _get_rag_pipeline():
    """获取 RAG Pipeline 单例（统一入口，避免双重初始化）"""
    from api.deps import get_rag_pipeline
    return get_rag_pipeline()
```

移除 `_rag_pipeline` 全局变量声明（第 19 行）。

- [ ] **Step 2：验证 import 链路**

```powershell
.\.venv\Scripts\python.exe -c "from multi_agent.tools import search_knowledge_tool; print('OK')"
```

- [ ] **Step 3：提交**

```bash
git add multi_agent/tools.py
git commit -m "fix: 修复 RAGPipeline 双重单例 — tools.py 改为调用 api/deps.py 统一入口"
```

---

## Task 0.5：删除 RAGPipeline.search() 废弃方法

**Files:**
- Modify: `retrieval/pipeline.py`

`search()` 方法有零个外部调用者（仅 `retrievers.py` 和 `context.py` 的注释引用其设计思路）。

- [ ] **Step 1：确认 search() 无调用者**

```powershell
Select-String -Path (Get-ChildItem -Recurse -Include *.py -Exclude pipeline.py) -Pattern '\.search\(' | Where-Object { $_ -notmatch 're\.search|similarity_search|hybrid_retrieve|doc_registry|search_knowledge' }
```

- [ ] **Step 2：删除 search() 方法**

删除 [pipeline.py:333-395](retrieval/pipeline.py#L333) 的 `search()` 方法（大约 60 行）。

- [ ] **Step 3：验证删除后不影响其他模块**

```powershell
.\.venv\Scripts\python.exe -c "from retrieval.pipeline import RAGPipeline; p = RAGPipeline(); print('ask available:', hasattr(p, 'ask')); print('search removed:', not hasattr(p, 'search'))"
```

- [ ] **Step 4：提交**

```bash
git add retrieval/pipeline.py
git commit -m "refactor: 删除 RAGPipeline.search() — 零调用者，功能由 ask() 覆盖"
```

---

## Task 0.6：Phase 0 真实数据回归测试

**目标：** 确认清理后现有功能完全正常。

- [ ] **Step 1：准备测试文档**

在 `data/docs/default/` 下确保有测试文档。如果没有，创建一个：

```powershell
New-Item -ItemType Directory -Force -Path "data\docs\default"
@"
# 电商客服 SOP

## 1. 退货流程
客户发起退货申请后，客服需在24小时内审核。
退货原因分为：质量问题、描述不符、买家原因。

## 2. 退款时效
审核通过后，退款将在3-5个工作日内原路返回。
"@ | Out-File -FilePath "data\docs\default\test_sop.md" -Encoding utf8
```

- [ ] **Step 2：清空旧向量库，强制全量重建**

```powershell
Remove-Item -Recurse -Force data\chroma -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force data\doc_db -ErrorAction SilentlyContinue
Remove-Item -Force data\doc_registry.db -ErrorAction SilentlyContinue
```

- [ ] **Step 3：启动后端验证 Pipeline 初始化**

```powershell
.\.venv\Scripts\python.exe -c "
from api.deps import get_rag_pipeline
p = get_rag_pipeline()
print('Pipeline init OK')
print('ask test:', p.ask('退货流程是什么？')[:200])
"
```

- [ ] **Step 4：验证前端构建 + 启动**

```powershell
cd web; npx next build 2>&1 | Select-Object -Last 5
```

- [ ] **Step 5：提交（如有必要）**

---

# Phase 1：P0 核心能力（文档清洗 + 脏数据过滤 + BM25 持久化）

## Task 1.1：创建文档清洗器 `preprocessing/cleaner.py`

**Files:**
- Create: `preprocessing/cleaner.py`
- Modify: `config.py`（新增清洗配置项）
- Modify: `preprocessing/loader.py`（集成清洗步骤）

**Interfaces:**
- Produces:
  - `DocumentCleaner.clean(text: str, source_type: str) -> CleanResult`
  - `CleanResult(text: str, changes: list[str], warnings: list[str])`
- Consumes: `config.py` 的清洗配置项

- [ ] **Step 1：创建测试文件 `tests/test_cleaner.py`**

```python
"""测试文档清洗器"""
import pytest
from preprocessing.cleaner import DocumentCleaner, CleanResult


class TestTextNormalization:
    def setup_method(self):
        self.cleaner = DocumentCleaner()

    def test_remove_control_chars(self):
        """去除控制字符，保留换行和制表符"""
        text = "hello\x00\x01\x02 world\nline2"
        result = self.cleaner.clean(text, source_type="text")
        assert "\x00" not in result.text
        assert "\n" in result.text  # 保留换行

    def test_normalize_fullwidth(self):
        """全角半角统一"""
        text = "１２３ａｂｃ，。！"
        result = self.cleaner.clean(text, source_type="text")
        assert "123" in result.text
        assert "abc" in result.text

    def test_merge_blank_lines(self):
        """合并连续空行，最多保留2个"""
        text = "line1\n\n\n\n\nline2"
        result = self.cleaner.clean(text, source_type="text")
        assert "\n\n\n\n\n" not in result.text
        assert "line1\n\nline2" in result.text

    def test_strip_html_tags(self):
        """HTML 标签剥离"""
        text = "<div><p>Hello</p><br>World</div>"
        result = self.cleaner.clean(text, source_type="text")
        assert "<div>" not in result.text
        assert "Hello" in result.text
        assert "World" in result.text

    def test_unify_chinese_punctuation(self):
        """中文标点统一"""
        text = "你好,这是测试.请确认!"
        result = self.cleaner.clean(text, source_type="text")
        assert "，" in result.text  # 英文逗号转中文
        assert "。" in result.text  # 英文句号转中文

    def test_pdf_header_removal(self):
        """PDF 页眉检测和去除"""
        text = """电商平台运营手册
第一章 概述
电商平台运营手册
1.1 背景介绍
电商平台运营手册
这是正文内容。"""
        result = self.cleaner.clean(text, source_type="pdf")
        # "电商平台运营手册" 重复出现3次，应被识别为页眉并去除
        count = result.text.count("电商平台运营手册")
        assert count <= 1  # 最多保留一次（可能是正文中的引用）

    def test_pdf_page_number_removal(self):
        """PDF 页码去除"""
        text = "这是正文内容。\n42\n\n下一页内容。\n43\n"
        result = self.cleaner.clean(text, source_type="pdf")
        assert "\n42\n" not in result.text

    def test_clean_result_structure(self):
        """验证返回结构"""
        text = "clean text"
        result = self.cleaner.clean(text, source_type="text")
        assert isinstance(result, CleanResult)
        assert isinstance(result.text, str)
        assert isinstance(result.changes, list)
        assert isinstance(result.warnings, list)

    def test_empty_text(self):
        """空文本不崩溃"""
        result = self.cleaner.clean("", source_type="text")
        assert result.text == ""

    def test_url_placeholder(self):
        """URL 替换为占位符"""
        text = "详情见 https://example.com/doc/123 页面"
        result = self.cleaner.clean(text, source_type="text")
        # URL 应被替换为占位符
        assert "https://" not in result.text
        assert "[URL]" in result.text
```

- [ ] **Step 2：运行测试确认失败**

```powershell
PYTHONPATH=".venv/lib/site-packages" .\.venv\Scripts\python.exe -m pytest tests/test_cleaner.py -v --tb=short
```
Expected: 全部 FAIL（模块未创建）

- [ ] **Step 3：创建 `preprocessing/cleaner.py`**

```python
"""文档清洗器 — 文本规范化 + PDF 页眉页脚去除 + URL/邮箱处理"""
import re
from collections import Counter
from dataclasses import dataclass, field

from config import (
    CLEAN_REMOVE_CONTROL_CHARS,
    CLEAN_NORMALIZE_FULLWIDTH,
    CLEAN_MERGE_BLANK_LINES,
    CLEAN_STRIP_HTML,
    CLEAN_REMOVE_PDF_HEADERS,
    CLEAN_REMOVE_PDF_FOOTERS,
    CLEAN_URL_ACTION,
    CLEAN_EMAIL_ACTION,
)
from utils.logger import logger


@dataclass
class CleanResult:
    """清洗结果"""
    text: str
    changes: list[str] = field(default_factory=list)   # 执行了哪些清洗操作
    warnings: list[str] = field(default_factory=list)   # 清洗警告（如检测到异常但未处理）


class DocumentCleaner:
    """统一文档清洗入口。

    支持的清洗操作（可通过 config.py 独立开关）：
      - 控制字符去除（\\x00-\\x1f 保留 \\n\\t）
      - 非法 Unicode 去除（surrogate characters）
      - 全角半角统一（数字、字母、标点）
      - 空白字符规范化（\\r\\n → \\n, \\t → 空格）
      - 合并连续空行（>2 → 2）
      - 中文标点统一
      - HTML 标签剥离
      - PDF 页眉页脚去除
      - URL/邮箱规范化
    """

    # ── 预编译正则 ──────────────────────────────────
    _RE_CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')
    _RE_SURROGATE = re.compile(r'[\ud800-\udfff]')
    _RE_HTML_TAG = re.compile(r'<[^>]+>')
    _RE_MULTI_BLANK_LINE = re.compile(r'\n{3,}')
    _RE_ISOLATED_PAGE_NUM = re.compile(r'^\d{1,4}$', re.MULTILINE)
    _RE_URL = re.compile(r'https?://[^\s一-鿿]+')
    _RE_EMAIL = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
    _RE_REPEATED_LINE = re.compile(r'^(.+)$', re.MULTILINE)

    # 全角→半角映射（仅数字和字母）
    _FULLWIDTH_DIGITS = str.maketrans('０１２３４５６７８９', '0123456789')
    _FULLWIDTH_LETTERS_UPPER = str.maketrans('ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ')
    _FULLWIDTH_LETTERS_LOWER = str.maketrans('ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ', 'abcdefghijklmnopqrstuvwxyz')

    # 中文标点映射（半角→全角）
    _CN_PUNCT_MAP = {
        ',': '，', '.': '。', '!': '！', '?': '？',
        ':': '：', ';': '；', '(': '（', ')': '）',
        '"': '"', '"': '"', "'": ''', "'": ''',
    }

    def clean(self, text: str, source_type: str = "text") -> CleanResult:
        """统一文档清洗入口。

        Args:
            text: 原始文本
            source_type: 来源类型 ("text" | "pdf" | "ocr")

        Returns:
            CleanResult: 包含清洗后文本 + 变更记录
        """
        if not text or not text.strip():
            return CleanResult(text=text, warnings=["empty_input"])

        result = CleanResult(text=text)

        # ── 通用清洗 ──
        if CLEAN_REMOVE_CONTROL_CHARS:
            result = self._remove_control_chars(result)

        result = self._remove_surrogates(result)

        if CLEAN_NORMALIZE_FULLWIDTH:
            result = self._normalize_fullwidth(result)

        result = self._normalize_whitespace(result)

        if CLEAN_MERGE_BLANK_LINES:
            result = self._merge_blank_lines(result)

        result = self._unify_cn_punctuation(result)

        if CLEAN_STRIP_HTML:
            result = self._strip_html(result)

        # ── URL/邮箱处理 ──
        if CLEAN_URL_ACTION != "keep":
            result = self._handle_urls(result)

        if CLEAN_EMAIL_ACTION != "keep":
            result = self._handle_emails(result)

        # ── PDF 专用 ──
        if source_type == "pdf":
            if CLEAN_REMOVE_PDF_HEADERS:
                result = self._remove_pdf_headers(result)
            if CLEAN_REMOVE_PDF_FOOTERS:
                result = self._remove_pdf_footers(result)
            result = self._remove_page_numbers(result)

        # ── OCR 专用（P1）──
        if source_type == "ocr":
            result = self._clean_ocr(result)

        return result

    # ── 各清洗步骤 ──────────────────────────────────

    def _remove_control_chars(self, r: CleanResult) -> CleanResult:
        new_text = self._RE_CONTROL.sub('', r.text)
        if new_text != r.text:
            r.changes.append("removed_control_chars")
        r.text = new_text
        return r

    def _remove_surrogates(self, r: CleanResult) -> CleanResult:
        new_text = self._RE_SURROGATE.sub('', r.text)
        if new_text != r.text:
            r.changes.append("removed_surrogates")
        r.text = new_text
        return r

    def _normalize_fullwidth(self, r: CleanResult) -> CleanResult:
        new_text = r.text.translate(self._FULLWIDTH_DIGITS)
        new_text = new_text.translate(self._FULLWIDTH_LETTERS_UPPER)
        new_text = new_text.translate(self._FULLWIDTH_LETTERS_LOWER)
        if new_text != r.text:
            r.changes.append("normalized_fullwidth")
        r.text = new_text
        return r

    def _normalize_whitespace(self, r: CleanResult) -> CleanResult:
        """\\r\\n → \\n, \\t → 空格, 去除行尾空格"""
        new_text = r.text.replace('\r\n', '\n').replace('\r', '\n')
        new_text = new_text.replace('\t', ' ')
        # 去除行尾空格
        new_text = '\n'.join(line.rstrip() for line in new_text.split('\n'))
        if new_text != r.text:
            r.changes.append("normalized_whitespace")
        r.text = new_text
        return r

    def _merge_blank_lines(self, r: CleanResult) -> CleanResult:
        """>2 连续空行 → 2 空行"""
        new_text = self._RE_MULTI_BLANK_LINE.sub('\n\n', r.text)
        if new_text != r.text:
            r.changes.append("merged_blank_lines")
        r.text = new_text
        return r

    def _unify_cn_punctuation(self, r: CleanResult) -> CleanResult:
        """中文环境下的标点统一"""
        new_text = r.text
        has_chinese = bool(re.search(r'[一-鿿]', new_text))
        if has_chinese:
            for half, full in self._CN_PUNCT_MAP.items():
                # 仅在中文上下文切换（标点前后有中文字符）
                new_text = re.sub(
                    rf'(?<=[一-鿿])\s*{re.escape(half)}\s*(?=[一-鿿])',
                    full, new_text
                )
            if new_text != r.text:
                r.changes.append("unified_cn_punctuation")
        r.text = new_text
        return r

    def _strip_html(self, r: CleanResult) -> CleanResult:
        """去除 HTML 标签，保留文字内容"""
        new_text = self._RE_HTML_TAG.sub('', r.text)
        # 清理标签去除后遗留的多余空白
        new_text = re.sub(r' {2,}', ' ', new_text)
        if new_text != r.text:
            r.changes.append("stripped_html")
        r.text = new_text
        return r

    def _handle_urls(self, r: CleanResult) -> CleanResult:
        action = CLEAN_URL_ACTION
        urls = self._RE_URL.findall(r.text)
        if not urls:
            return r
        if action == "remove":
            r.text = self._RE_URL.sub('', r.text)
            r.changes.append(f"removed_{len(urls)}_urls")
        elif action == "placeholder":
            r.text = self._RE_URL.sub('[URL]', r.text)
            r.changes.append(f"replaced_{len(urls)}_urls")
        return r

    def _handle_emails(self, r: CleanResult) -> CleanResult:
        action = CLEAN_EMAIL_ACTION
        emails = self._RE_EMAIL.findall(r.text)
        if not emails:
            return r
        if action == "remove":
            r.text = self._RE_EMAIL.sub('', r.text)
            r.changes.append(f"removed_{len(emails)}_emails")
        elif action == "placeholder":
            r.text = self._RE_EMAIL.sub('[EMAIL]', r.text)
            r.changes.append(f"replaced_{len(emails)}_emails")
        return r

    def _remove_pdf_headers(self, r: CleanResult) -> CleanResult:
        """检测并去除 PDF 页眉。

        算法：按行统计，找到每页（\\n\\n 分页符后）开头的重复行。
        如果某行出现在 >50% 的"页"开头，则判为页眉。
        """
        lines = r.text.split('\n')
        if len(lines) < 10:
            return r

        # 简化版：找重复出现次数最高的行
        line_counts = Counter(line.strip() for line in lines if line.strip())
        total_lines = len([l for l in lines if l.strip()])

        removed_count = 0
        for line_text, count in line_counts.most_common(10):
            stripped = line_text.strip()
            # 跳过太短的（可能是真实内容）
            if len(stripped) < 5:
                continue
            # 如果某行重复率 > 30% 且出现 >2 次 → 判为页眉
            if count > 2 and count / total_lines > 0.3:
                r.text = '\n'.join(
                    l for l in lines if l.strip() != stripped
                )
                removed_count += 1
                lines = r.text.split('\n')  # 更新 lines 用于后续检查

        if removed_count > 0:
            r.changes.append(f"removed_{removed_count}_pdf_headers")
        return r

    def _remove_pdf_footers(self, r: CleanResult) -> CleanResult:
        """检测并去除 PDF 页脚。

        与页眉类似，但检查的是"页"末尾（\\n\\n 之前）的重复行。
        同时检测常见页脚模式（如 "第X页 共X页"）。
        """
        # 常见页脚模式
        footer_patterns = [
            re.compile(r'第\s*\d+\s*页\s*共\s*\d+\s*页'),
            re.compile(r'Page\s+\d+\s+of\s+\d+', re.IGNORECASE),
            re.compile(r'^\d+\s*/\s*\d+$'),
        ]

        for pattern in footer_patterns:
            new_text = pattern.sub('', r.text)
            if new_text != r.text:
                r.text = new_text
                r.changes.append("removed_page_number_footer")
        return r

    def _remove_page_numbers(self, r: CleanResult) -> CleanResult:
        """去除独立页码行（行仅含 1-4 位数字）"""
        lines = r.text.split('\n')
        new_lines = []
        removed = 0
        for line in lines:
            stripped = line.strip()
            if self._RE_ISOLATED_PAGE_NUM.match(stripped):
                # 验证前后行：真页码通常在"页"末或"段"末
                removed += 1
                continue
            new_lines.append(line)
        if removed > 0:
            r.text = '\n'.join(new_lines)
            r.changes.append(f"removed_{removed}_page_numbers")
        return r

    def _clean_ocr(self, r: CleanResult) -> CleanResult:
        """OCR 结果专用清洗（P1 阶段实现，目前为占位）"""
        logger.debug("[Cleaner] OCR cleaning not yet implemented, passing through")
        return r
```

- [ ] **Step 4：在 config.py 末尾新增清洗配置项**

```python
# ====================================
# 文档清洗配置（P0-1）
# ====================================
CLEAN_REMOVE_CONTROL_CHARS = os.getenv("CLEAN_REMOVE_CONTROL_CHARS", "false").lower() == "true"
CLEAN_NORMALIZE_FULLWIDTH = os.getenv("CLEAN_NORMALIZE_FULLWIDTH", "false").lower() == "true"
CLEAN_MERGE_BLANK_LINES = os.getenv("CLEAN_MERGE_BLANK_LINES", "false").lower() == "true"
CLEAN_STRIP_HTML = os.getenv("CLEAN_STRIP_HTML", "false").lower() == "true"
CLEAN_REMOVE_PDF_HEADERS = os.getenv("CLEAN_REMOVE_PDF_HEADERS", "false").lower() == "true"
CLEAN_REMOVE_PDF_FOOTERS = os.getenv("CLEAN_REMOVE_PDF_FOOTERS", "false").lower() == "true"
CLEAN_URL_ACTION = os.getenv("CLEAN_URL_ACTION", "keep")           # keep | remove | placeholder
CLEAN_EMAIL_ACTION = os.getenv("CLEAN_EMAIL_ACTION", "keep")       # keep | remove | placeholder
```

- [ ] **Step 5：运行测试确认通过**

```powershell
PYTHONPATH=".venv/lib/site-packages" .\.venv\Scripts\python.exe -m pytest tests/test_cleaner.py -v --tb=short
```
Expected: 10/10 PASS（部分 test 依赖 config 开启，需要在 `.env` 中开启或调整测试）

- [ ] **Step 6：集成到 loader.py**

修改 `preprocessing/loader.py` 的 `load_documents_from_directory` 函数，在 `loader.load()` 之后、`split_documents()` 之前插入清洗逻辑：

```python
from preprocessing.cleaner import DocumentCleaner

# 在 load_documents_from_directory 中，loader.load() 之后：
for doc in docs:
    cleaner = DocumentCleaner()
    source_type = "pdf" if ext == ".pdf" else "text"
    result = cleaner.clean(doc.page_content, source_type=source_type)
    doc.page_content = result.text
```

- [ ] **Step 7：提交**

```bash
git add preprocessing/cleaner.py config.py preprocessing/loader.py tests/test_cleaner.py
git commit -m "feat: 新增文档清洗层 — 文本规范化 + PDF页眉页脚去除 + URL/邮箱处理"
```

---

## Task 1.2：创建脏数据过滤器 `preprocessing/filter.py`

**Files:**
- Create: `preprocessing/filter.py`
- Modify: `config.py`（新增过滤配置项）
- Create: `tests/test_filter.py`

**Interfaces:**
- Produces:
  - `ChunkFilter.should_keep(text: str, metadata: dict) -> tuple[bool, str]`
  - `DuplicateDetector(threshold: int).is_duplicate(text: str) -> bool`

- [ ] **Step 1：创建测试文件 `tests/test_filter.py`**

```python
"""测试脏数据过滤器"""
import pytest
from preprocessing.filter import ChunkFilter, DuplicateDetector


class TestChunkFilter:
    def setup_method(self):
        self.filter = ChunkFilter()

    def test_filter_empty_content(self):
        """过滤空白内容"""
        ok, reason = self.filter.should_keep("   \n  \t  ", {})
        assert not ok
        assert reason == "empty"

    def test_keep_valid_content(self):
        """保留正常内容"""
        ok, reason = self.filter.should_keep("这是一段有意义的电商知识内容。", {})
        assert ok
        assert reason == "clean"

    def test_filter_too_short(self):
        """过滤超短文本"""
        ok, reason = self.filter.should_keep("你好", {})
        assert not ok
        assert reason == "too_short"

    def test_filter_all_symbols(self):
        """过滤纯符号文本"""
        ok, reason = self.filter.should_keep("★★★★★ ====== >>>>>>", {})
        assert not ok
        assert reason == "all_symbols"

    def test_filter_low_chinese_ratio(self):
        """中文知识库过滤纯英文/数字文本"""
        ok, reason = self.filter.should_keep("This is all English text with no Chinese at all", {})
        assert not ok
        assert reason == "low_chinese_ratio"

    def test_pii_masking_phone(self):
        """手机号脱敏"""
        text = "请联系客服：13812345678 获取帮助"
        ok, reason = self.filter.should_keep(text, {})
        assert ok
        assert "13812345678" not in text  # 原号码被替换

    def test_pii_masking_id_card(self):
        """身份证号脱敏"""
        text = "身份证号：110101199001011234 请核实"
        ok, reason = self.filter.should_keep(text, {})
        assert ok
        assert "110101" not in text.split("：")[1][:6]

    def test_metadata_enriched(self):
        """metadata 中包含过滤状态"""
        meta = {}
        text = "正常内容测试"
        ok, reason = self.filter.should_keep(text, meta)
        assert meta.get("filter_status") == "clean"


class TestDuplicateDetector:
    def test_exact_duplicate(self):
        """精确重复检测"""
        dd = DuplicateDetector(threshold=3)
        text1 = "电商平台运营手册第一章概述"
        text2 = "电商平台运营手册第一章概述"
        assert not dd.is_duplicate(text1)  # 第一个不重复
        assert dd.is_duplicate(text2)       # 第二个重复

    def test_near_duplicate(self):
        """近似重复检测"""
        dd = DuplicateDetector(threshold=3)
        text1 = "电商平台运营手册第一章概述，包含退货流程和退款政策。"
        text2 = "电商平台运营手册第一章概述，包含退货流程和退款政策"  # 差一个句号
        dd.is_duplicate(text1)
        assert dd.is_duplicate(text2)  # SimHash 汉明距离很小

    def test_different_content(self):
        """不同内容不判重复"""
        dd = DuplicateDetector(threshold=3)
        text1 = "电商平台运营手册第一章概述"
        text2 = "这是完全不同的一段文本内容"
        dd.is_duplicate(text1)
        assert not dd.is_duplicate(text2)

    def test_reset(self):
        """重置检测器"""
        dd = DuplicateDetector(threshold=3)
        dd.is_duplicate("text1")
        dd.reset()
        assert not dd.is_duplicate("text1")  # 重置后不判重复
```

- [ ] **Step 2：运行测试确认失败**

```powershell
PYTHONPATH=".venv/lib/site-packages" .\.venv\Scripts\python.exe -m pytest tests/test_filter.py -v --tb=short
```

- [ ] **Step 3：创建 `preprocessing/filter.py`**

```python
"""脏数据过滤器 — 质量过滤 + SimHash 去重 + PII 脱敏"""
import re
from config import (
    FILTER_MIN_CHUNK_LENGTH,
    FILTER_MAX_SYMBOL_RATIO,
    FILTER_MIN_CHINESE_RATIO,
    FILTER_SIMHASH_THRESHOLD,
    FILTER_ENABLE_PII_MASK,
)
from utils.logger import logger


class DuplicateDetector:
    """基于 SimHash 的近似去重器。

    算法：
    1. 对文本做字符级 n-gram (n=3) 分词
    2. 对每个 n-gram 计算 hash 权重
    3. 合并得到 64-bit SimHash
    4. 与已存 hash 比较汉明距离
    5. 距离 ≤ threshold → 判为重复
    """

    def __init__(self, threshold: int = 3):
        self.threshold = threshold
        self.seen_hashes: list[int] = []

    def is_duplicate(self, text: str) -> bool:
        """检查文本是否与已见过的文本重复。首次调用返回 False。"""
        if not text or len(text) < 20:
            return False  # 太短不检测

        h = self._simhash(text)

        for seen in self.seen_hashes:
            if self._hamming_distance(h, seen) <= self.threshold:
                return True

        self.seen_hashes.append(h)
        # 限制内存：保留最近 10000 个 hash
        if len(self.seen_hashes) > 10000:
            self.seen_hashes = self.seen_hashes[-5000:]
        return False

    def reset(self):
        """清空已记录的 hash"""
        self.seen_hashes.clear()

    def _simhash(self, text: str) -> int:
        """计算 64-bit SimHash"""
        # 字符级 3-gram
        grams = [text[i:i+3] for i in range(max(len(text) - 2, 0))]
        if not grams:
            return 0

        v = [0] * 64
        for g in grams:
            h = hash(g)
            for i in range(64):
                if h & (1 << i):
                    v[i] += 1
                else:
                    v[i] -= 1

        result = 0
        for i in range(64):
            if v[i] > 0:
                result |= (1 << i)
        return result

    @staticmethod
    def _hamming_distance(a: int, b: int) -> int:
        """计算两个整数的汉明距离"""
        return (a ^ b).bit_count()


# ── PII 脱敏正则 ──────────────────────────────────

_PII_PATTERNS = [
    (re.compile(r'(?<!\d)(1[3-9]\d{9})(?!\d)'), 'phone'),
    (re.compile(r'(?<!\d)(\d{17}[\dXx])(?!\d)'), 'id_card'),
    (re.compile(r'(?<!\d)(\d{16,19})(?!\d)'), 'bank_card'),
]


def _mask_pii(text: str) -> tuple[str, list[str]]:
    """PII 脱敏。返回 (脱敏后文本, 操作列表)"""
    masked_types = []
    for pattern, pii_type in _PII_PATTERNS:
        matches = pattern.findall(text)
        if matches:
            if pii_type == 'phone':
                text = pattern.sub(lambda m: m.group(1)[:3] + '****' + m.group(1)[-4:], text)
            elif pii_type == 'id_card':
                text = pattern.sub(lambda m: m.group(1)[:6] + '********' + m.group(1)[-4:], text)
            elif pii_type == 'bank_card':
                text = pattern.sub(lambda m: m.group(1)[:4] + '****' + m.group(1)[-4:], text)
            masked_types.append(pii_type)
    return text, masked_types


class ChunkFilter:
    """Chunk 质量过滤器。

    检查项（按顺序）：
    1. 空白内容
    2. 超短文本（< min_length）
    3. 纯符号比率过高
    4. 中文占比过低
    5. SimHash 去重
    6. PII 脱敏（不拒绝，仅修改文本）
    """

    def __init__(self):
        self.min_length = FILTER_MIN_CHUNK_LENGTH
        self.max_symbol_ratio = FILTER_MAX_SYMBOL_RATIO
        self.min_chinese_ratio = FILTER_MIN_CHINESE_RATIO
        self.simhash_threshold = FILTER_SIMHASH_THRESHOLD
        self.enable_pii = FILTER_ENABLE_PII_MASK
        self._dup_detector = DuplicateDetector(threshold=self.simhash_threshold)

    def should_keep(self, text: str, metadata: dict) -> tuple[bool, str]:
        """检查 Chunk 是否应保留。

        Returns:
            (是否保留, 原因标签)
            - "clean": 通过所有检查
            - "empty" / "too_short" / "all_symbols" / "low_chinese_ratio" / "duplicate": 被拒绝
        """
        metadata.setdefault("filter_status", "clean")
        metadata.setdefault("filter_reason", "")

        # 1. 空白检查
        if not text or not text.strip():
            metadata["filter_status"] = "filtered"
            metadata["filter_reason"] = "empty"
            return False, "empty"

        # 2. 长度检查
        stripped = text.strip()
        if len(stripped) < self.min_length:
            metadata["filter_status"] = "filtered"
            metadata["filter_reason"] = "too_short"
            return False, "too_short"

        # 3. 纯符号比率（非字母/数字/中文 = 符号）
        symbol_count = sum(1 for c in stripped if not c.isalnum() and not '一' <= c <= '鿿')
        if len(stripped) > 0 and symbol_count / len(stripped) > self.max_symbol_ratio:
            metadata["filter_status"] = "filtered"
            metadata["filter_reason"] = "all_symbols"
            return False, "all_symbols"

        # 4. 中文占比（中文知识库场景）
        chinese_count = sum(1 for c in stripped if '一' <= c <= '鿿')
        total_alpha = sum(1 for c in stripped if c.isalpha() or '一' <= c <= '鿿')
        if total_alpha > 0 and chinese_count / total_alpha < self.min_chinese_ratio:
            metadata["filter_status"] = "filtered"
            metadata["filter_reason"] = "low_chinese_ratio"
            return False, "low_chinese_ratio"

        # 5. SimHash 去重
        if self._dup_detector.is_duplicate(stripped):
            metadata["filter_status"] = "filtered"
            metadata["filter_reason"] = "duplicate"
            return False, "duplicate"

        # 6. PII 脱敏（不拒绝内容，仅修改文本）
        if self.enable_pii:
            new_text, masked_types = _mask_pii(stripped)
            if masked_types:
                metadata["pii_masked"] = masked_types

        metadata["filter_status"] = "clean"
        metadata["filter_reason"] = ""
        return True, "clean"

    def reset(self):
        """重置去重检测器（知识库重建时调用）"""
        self._dup_detector.reset()
```

- [ ] **Step 4：在 config.py 新增过滤配置项**

```python
# ====================================
# 脏数据过滤配置（P0-2）
# ====================================
FILTER_MIN_CHUNK_LENGTH = int(os.getenv("FILTER_MIN_CHUNK_LENGTH", "10"))
FILTER_MAX_SYMBOL_RATIO = float(os.getenv("FILTER_MAX_SYMBOL_RATIO", "0.8"))
FILTER_MIN_CHINESE_RATIO = float(os.getenv("FILTER_MIN_CHINESE_RATIO", "0.3"))
FILTER_SIMHASH_THRESHOLD = int(os.getenv("FILTER_SIMHASH_THRESHOLD", "3"))
FILTER_ENABLE_PII_MASK = os.getenv("FILTER_ENABLE_PII_MASK", "false").lower() == "true"
```

- [ ] **Step 5：运行测试确认通过**

```powershell
PYTHONPATH=".venv/lib/site-packages" .\.venv\Scripts\python.exe -m pytest tests/test_filter.py -v --tb=short
```

- [ ] **Step 6：集成到 indexer.py**

修改 `retrieval/indexer.py` 的 `IncrementalIndexer._index_file` 方法，在 Embedding 之前对每个 chunk 调用 `ChunkFilter.should_keep()`，过滤掉不合格的 chunk。

```python
# 在 _index_file 方法中，chunks = split_documents(...) 之后：
from preprocessing.filter import ChunkFilter
chunk_filter = ChunkFilter()
filtered_chunks = []
filtered_count = 0
for chunk in chunks:
    ok, reason = chunk_filter.should_keep(chunk.page_content, chunk.metadata)
    if ok:
        filtered_chunks.append(chunk)
    else:
        filtered_count += 1
        logger.debug(f"[Filter] 拒绝 chunk: {reason} (doc={file_path})")
if filtered_count > 0:
    logger.info(f"[Filter] {file_path}: 过滤 {filtered_count}/{len(chunks)} 个 chunk")
chunks = filtered_chunks
```

- [ ] **Step 7：提交**

```bash
git add preprocessing/filter.py config.py retrieval/indexer.py tests/test_filter.py
git commit -m "feat: 新增脏数据过滤器 — 空白/超短/纯符号/中文占比/SimHash去重/PII脱敏"
```

---

## Task 1.3：BM25 持久化 `retrieval/bm25_store.py`

**Files:**
- Create: `retrieval/bm25_store.py`
- Modify: `retrieval/pipeline.py`（集成 BM25 持久化）
- Modify: `config.py`（新增配置项）
- Create: `tests/test_bm25_store.py`

**Interfaces:**
- Produces:
  - `BM25Store(index_dir).build(docs) -> None`
  - `BM25Store.load() -> BM25Retriever`
  - `BM25Store.add_documents(docs) -> None`
  - `BM25Store.is_stale(registry_mtime: float) -> bool`

- [ ] **Step 1：创建 `retrieval/bm25_store.py`**

```python
"""BM25 持久化存储 — 磁盘索引避免每次启动重建"""
import json
import os
import pickle
import time
from pathlib import Path
from typing import List

from langchain_core.documents import Document
from langchain_community.retrievers import BM25Retriever

from config import BM25_INDEX_DIR
from utils.logger import logger


class BM25Store:
    """磁盘持久化 BM25 索引。

    存储格式:
      data/bm25/
      ├── corpus.pkl       # 分词后的语料
      ├── docs.pkl          # 原始 Document 对象列表
      ├── meta.json         # 索引元数据（文档数/构建时间）
    """

    def __init__(self, index_dir: str = None):
        self.index_dir = Path(index_dir or BM25_INDEX_DIR)
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self._meta_path = self.index_dir / "meta.json"
        self._corpus_path = self.index_dir / "corpus.pkl"
        self._docs_path = self.index_dir / "docs.pkl"

    def build(self, docs: List[Document]) -> BM25Retriever:
        """构建并持久化 BM25 索引。

        Returns:
            可直接使用的 BM25Retriever
        """
        logger.info(f"[BM25Store] 构建索引，{len(docs)} 个文档...")
        t0 = time.time()

        retriever = BM25Retriever.from_documents(docs)

        # 持久化：获取内部 corpus
        # BM25Retriever 内部属性: vectorizer (CountVectorizer), docs
        with open(self._corpus_path, "wb") as f:
            pickle.dump(retriever.vectorizer, f)
        with open(self._docs_path, "wb") as f:
            pickle.dump(docs, f)

        elapsed = time.time() - t0
        self._write_meta(len(docs), elapsed)
        logger.info(f"[BM25Store] 索引构建完成: {len(docs)} 文档, {elapsed:.1f}s")
        return retriever

    def load(self) -> BM25Retriever | None:
        """从磁盘加载 BM25 索引。

        Returns:
            BM25Retriever 实例，如果索引不存在返回 None
        """
        if not self._corpus_path.exists() or not self._docs_path.exists():
            logger.info("[BM25Store] 索引文件不存在，需要重建")
            return None

        try:
            with open(self._corpus_path, "rb") as f:
                vectorizer = pickle.load(f)
            with open(self._docs_path, "rb") as f:
                docs = pickle.load(f)

            retriever = BM25Retriever.from_documents(docs)
            retriever.vectorizer = vectorizer

            meta = self._read_meta()
            logger.info(
                f"[BM25Store] 索引加载成功: {meta.get('doc_count', '?')} 文档, "
                f"构建于 {meta.get('built_at', '?')}"
            )
            return retriever
        except Exception as e:
            logger.warning(f"[BM25Store] 索引加载失败: {e}，将重建")
            return None

    def add_documents(self, docs: List[Document]) -> None:
        """增量添加文档并重建索引。

        简化实现：全量重建（BM25 IDF 依赖全量统计，增量更新需要重新计算）
        """
        all_docs = []
        if self._docs_path.exists():
            try:
                with open(self._docs_path, "rb") as f:
                    all_docs = pickle.load(f)
            except Exception:
                pass

        all_docs.extend(docs)
        self.build(all_docs)

    def remove_documents(self, doc_ids: List[str]) -> None:
        """按 doc_id 删除文档并重建索引。"""
        if not self._docs_path.exists():
            return

        try:
            with open(self._docs_path, "rb") as f:
                all_docs = pickle.load(f)
        except Exception:
            return

        remaining = [
            d for d in all_docs
            if d.metadata.get("doc_id") not in doc_ids
        ]

        if len(remaining) < len(all_docs):
            logger.info(
                f"[BM25Store] 删除 {len(all_docs) - len(remaining)} 个文档，"
                f"重建索引 ({len(remaining)} 剩余)"
            )
            self.build(remaining)

    @property
    def is_stale(self) -> bool:
        """检查索引是否过期。需要外部传入 registry mtime 对比。"""
        if not self._meta_path.exists():
            return True
        meta = self._read_meta()
        return meta.get("doc_count", 0) == 0

    # ── 内部方法 ──────────────────────────────────

    def _write_meta(self, doc_count: int, build_time_s: float):
        with open(self._meta_path, "w", encoding="utf-8") as f:
            json.dump({
                "doc_count": doc_count,
                "build_time_s": round(build_time_s, 1),
                "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "version": 1,
            }, f, ensure_ascii=False, indent=2)

    def _read_meta(self) -> dict:
        try:
            with open(self._meta_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
```

- [ ] **Step 2：在 config.py 新增配置项**

```python
BM25_INDEX_DIR = os.getenv("BM25_INDEX_DIR", "data/bm25")
```

- [ ] **Step 3：集成到 RAGPipeline**

修改 `retrieval/pipeline.py` 的 `_init_retrievers()` 方法：

```python
def _init_retrievers(self):
    from retrieval.bm25_store import BM25Store

    self.chunk_retriever = CustomRetriever(self.vectordb)

    # BM25: 优先从磁盘加载
    bm25_store = BM25Store()
    self.bm25 = bm25_store.load()
    if self.bm25 is None:
        logger.info("[RAG] BM25 索引不存在，全量重建...")
        all_chunks = []
        for file_name, chunks in self.doc_index.items():
            all_chunks.extend(chunks)
        self.bm25 = bm25_store.build(all_chunks)
    else:
        # 检查是否过期（对比 registry 文档数）
        stale = bm25_store.is_stale
        if stale:
            logger.info("[RAG] BM25 索引已过期，重建...")
            all_chunks = []
            for file_name, chunks in self.doc_index.items():
                all_chunks.extend(chunks)
            self.bm25 = bm25_store.build(all_chunks)
```

- [ ] **Step 4：提交**

```bash
git add retrieval/bm25_store.py retrieval/pipeline.py config.py
git commit -m "feat: BM25 持久化 — 磁盘索引文件避免每次启动重建"
```

---

## Task 1.4：Phase 1 集成 — 全量重建流程验证 + 真实数据测试

**目标：** 用真实的电商 SOP PDF/MD/TXT 文档验证完整的 Pipeline（清洗 + 过滤 + BM25 持久化）。

- [ ] **Step 1：准备真实测试文档**

创建 3 个测试文件：

```powershell
New-Item -ItemType Directory -Force -Path "data\docs\default"

# 1. MD 文档：电商 SOP（正常文档）
@"
# 电商平台运营 SOP

## 1. 退货处理流程
客户发起退货申请后，客服需在24小时内完成审核。
退货原因分为三类：
- 质量问题：全额退款 + 承担运费
- 描述不符：全额退款
- 买家原因：扣除运费后退款

## 2. 退款时效
审核通过后，退款将在3-5个工作日内原路返回至客户付款账户。
支付宝/微信支付：即时到账
银行卡：1-3个工作日

## 3. 差评处理 SOP
1. 收到差评通知后，30分钟内联系客户
2. 了解问题原因，记录到 CRM 系统
3. 48小时内给出解决方案
4. 客户满意后礼貌请求修改评价
"@ | Out-File -FilePath "data\docs\default\电商SOP.md" -Encoding utf8

# 2. TXT 文档：含脏数据的文本
@"
   
   
   
!!!  ★★★★★ 特价推荐 ★★★★★  !!!
   
请联系客服：13812345678 获取优惠
身份证号：110101199001011234
   
   
"@ | Out-File -FilePath "data\docs\default\脏数据测试.txt" -Encoding utf8

# 3. MD 文档：含 HTML 标签
@"
<h1>商品上架规范</h1>
<div class="content">
<p>所有上架商品必须包含：</p>
<ul>
<li>主图（白底，1000x1000px）</li>
<li>标题（不超过200字符）</li>
<li>五点描述（每条不超过500字符）</li>
</ul>
</div>
"@ | Out-File -FilePath "data\docs\default\商品上架规范.md" -Encoding utf8
```

- [ ] **Step 2：开启清洗和过滤配置**

在 `.env` 中临时开启：
```env
CLEAN_REMOVE_CONTROL_CHARS=true
CLEAN_NORMALIZE_FULLWIDTH=true
CLEAN_MERGE_BLANK_LINES=true
CLEAN_STRIP_HTML=true
CLEAN_URL_ACTION=placeholder
FILTER_ENABLE_PII_MASK=true
```

- [ ] **Step 3：清空旧数据 + 全量重建**

```powershell
Remove-Item -Recurse -Force data\chroma -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force data\doc_db -ErrorAction SilentlyContinue
Remove-Item -Force data\doc_registry.db -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force data\bm25 -ErrorAction SilentlyContinue

.\.venv\Scripts\python.exe -c "
from api.deps import get_rag_pipeline
p = get_rag_pipeline()
print('=== 检索测试 ===')
result = p.ask('退货流程是什么？')
print(result[:300])
print('...')
print('=== 验证 BM25 持久化 ===')
import os
bm25_files = os.listdir('data/bm25')
print(f'BM25 索引文件: {bm25_files}')
print('=== 验证 HTML 清洗 ===')
result2 = p.ask('商品上架需要什么？')
print(result2[:300])
"
```

- [ ] **Step 4：验证第二次启动使用缓存**

```powershell
.\.venv\Scripts\python.exe -c "
import time; t0=time.time()
from api.deps import get_rag_pipeline
p = get_rag_pipeline()
print(f'启动耗时: {time.time()-t0:.1f}s (应 < 2s，使用 BM25 缓存)')
print(p.ask('退货流程')[:200])
"
```

- [ ] **Step 5：关闭配置验证向后兼容**

```env
CLEAN_REMOVE_CONTROL_CHARS=false
CLEAN_NORMALIZE_FULLWIDTH=false
# ... 全部关闭
```

```powershell
.\.venv\Scripts\python.exe -c "
from api.deps import get_rag_pipeline
p = get_rag_pipeline()
print('默认配置下 ask 正常:', len(p.ask('退货流程')) > 0)
print('OK: 向后兼容')
"
```

- [ ] **Step 6：提交**

```bash
git add -A
git commit -m "test: Phase 1 真实数据回归测试通过 — 清洗+过滤+BM25持久化验证"
```

---

# Phase 2：P1 核心增强（OCR + 前端 + Metadata + Batch Embedding + Chunking）

> 以下为 Phase 2 任务大纲，每个 Task 都包含完整的创建/修改文件和测试步骤。

## Task 2.1：OCR 引擎 `preprocessing/ocr.py`

**Files:**
- Create: `preprocessing/ocr.py`
- Modify: `preprocessing/loader.py`（PDF 文本提取失败 → 触发 OCR）
- Modify: `config.py`

**Key Code:**

```python
class OCREngine:
    def __init__(self, backend: str = "paddleocr"):
        self.backend = backend
        self._ocr = None

    def _init_backend(self):
        if self.backend == "paddleocr":
            from paddleocr import PaddleOCR
            return PaddleOCR(lang='ch')
        elif self.backend == "easyocr":
            import easyocr
            return easyocr.Reader(['ch_sim', 'en'])
        raise ValueError(f"Unknown OCR backend: {self.backend}")

    def extract(self, pdf_path: str) -> OCRResult:
        """逐页 OCR"""
        ...

    def _needs_ocr(self, page_text: str) -> bool:
        """文本量 < 50 字符 → 触发 OCR"""
        return len(page_text.strip()) < OCR_MIN_TEXT_LENGTH_FOR_SKIP
```

**测试要求：** 准备一个扫描版 PDF（真实扫描件），验证 OCR 可提取出文本。

---

## Task 2.2：前端真实数据对接

**Files:**
- Modify: `api/routes/rag.py`（增强 `/stats` + 新增 `/stats/retrieval` + `/tasks`）
- Modify: `web/src/app/knowledge/page.tsx`（概览页动态数据）
- Modify: `web/src/app/knowledge/playground/page.tsx`（展示耗时分解）
- Modify: `web/src/hooks/useKnowledge.ts`（新增 hooks）

**真实测试：** 上传 5+ 个文档，在前端确认概览页统计数字与 `GET /api/rag/stats` 返回一致。

---

## Task 2.3：Metadata 增强

在 `preprocessing/metadata.py` 的 `build_metadata()` 中新增字段：
- `section_path`: 章节层级路径
- `heading_levels`: h1/h2/h3 信息
- `created_at`, `updated_at`: ISO 时间戳
- `chunk_size_chars`: 字符数
- `filter_status`, `ocr_used`, `business_tags`

---

## Task 2.4：批量 Embedding + 进度追踪

新增 `retrieval/progress.py`（ProgressTracker）+ 改造 `retrieval/indexer.py`。

**真实测试：** 批量导入 10 个文档，通过 SSE 端点观察实时进度推送。

---

## Task 2.5：结构化切片增强

改造 `preprocessing/chunking.py`：
- 表格/列表感知（不切割 markdown 表格和有序/无序列表）
- ManualPolicy 策略增加 Overlap
- `CHUNK_MAX_SIZE` 强制限制

---

## Phase 2 Regression Test

所有 Phase 2 任务完成后，用 10+ 个真实文档（混合 PDF/MD/TXT/扫描件）做全量回归测试。

---

# Phase 3：P2 锦上添花（按需实施）

| Task | 描述 |
|------|------|
| 3.1 | 流式解析 `StreamingLoader` — 分页加载大文件 |
| 3.2 | 语义切片 `SemanticChunker` — 句子嵌入边界检测 |
| 3.3 | 多格式支持 — HTML/CSV/XLSX/PPTX/JSON |
| 3.4 | PgVector 后端 — 实现 `PgVectorKnowledgeStore` |

---

# 验证清单（每个 Phase 完成后执行）

- [ ] `tests/test_cleaner.py` 全部通过
- [ ] `tests/test_filter.py` 全部通过
- [ ] 现有测试全部通过：`pytest tests/ -v --tb=short`
- [ ] 真实文档检索正常（至少 3 种格式：PDF/MD/TXT）
- [ ] BM25 缓存文件 `data/bm25/` 生成且二次启动秒级加载
- [ ] 默认配置下（清洗/过滤关闭）向后兼容
- [ ] 前端 `next build` 构建成功
- [ ] 知识库页面（概览/文档/chunks/playground）功能正常
