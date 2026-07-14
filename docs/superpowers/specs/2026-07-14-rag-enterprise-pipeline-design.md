# RAG 知识库企业级 Pipeline 升级设计

> 日期：2026-07-14 | 状态：设计阶段

---

## 一、现状总览

### 现有能力（✅ 已具备）

| 模块 | 能力 |
|------|------|
| 文档加载 | .txt / .md / .pdf / .docx，递归目录遍历 |
| 结构化切片 | 4 种策略（ManualPolicy / Resume / ProjectReport / General），类型感知路由 |
| Metadata | 10 个字段（doc_id, chunk_id, source_file, doc_type, business_domain, time_refs, keywords, sections, person_names, summary） |
| 向量存储 | ChromaDB（chunk 级 + doc 级双层），KnowledgeStore 抽象 |
| 增量索引 | SHA256 diff → ADDED/MODIFIED/DELETED/UNCHANGED 分类处理 |
| 混合检索 | 向量 + BM25 + RRF 融合 |
| Rerank | CrossEncoder（BGE-reranker-base）+ 超时保护 + 分数阈值 |
| 引用校验 | 3 阶段 CrossEncoder 验证 + 句子级标注 |
| 查询分析 | QueryAnalyzer 纯规则解析（实体/意图/时间/域名） |
| API | 9 个端点（stats/documents/detail/chunks/reindex/upload/delete/search/ask） |
| 前端 | 7 个知识库页面 + UploadDialog |

### 核心缺口

```
文档清洗 ─── 零 ❌
脏数据过滤 ─ 零 ❌
OCR ─────── 零 ❌
BM25 持久化 ─ 零 ❌（每次启动重建）
前端真实数据 ─ 零 ❌（pipeline/tasks 硬编码假数据）
```

---

## 二、总体架构

### 升级后 Pipeline

```text
文档接入（批量上传 / 目录监控）
    │
    ▼
文档解析（Loader：PDF/TXT/MD/DOCX/HTML/CSV/XLSX  →  P2）
    │
    ▼
文档清洗 ← 【新增 P0】文本规范化 + PDF 页眉页脚去除
    │
    ▼
OCR ← 【新增 P1】扫描版 PDF（PaddleOCR 可插拔）
    │
    ▼
脏数据过滤 ← 【新增 P0】空白/重复/超短/纯符号/语言检测/PII 脱敏
    │
    ▼
结构化切片（增强 Overlap 统一 + 表格感知 → P1）
    │
    ▼
Metadata 提取（增加层级路径 / 时间戳 / 业务标签 → P1）
    │
    ▼
批量 Embedding（多线程 + 重试 + 进度回调 → P1）
    │
    ▼
向量存储 + 原文存储（ChromaDB / PgVector → P2）
    │
    ▼
索引建立（BM25 持久化 → P0 + Registry 增强）
    │
    ▼
混合检索（向量 + BM25 + Metadata Filter → 已有）
    │
    ▼
Rerank 重排（CrossEncoder → 已有）
    │
    ▼
LLM Answer（引用校验 + 来源标注 → 已有）
    │
    ▼
知识库运维管理（前端真实数据对接 → P1 + 统计 API）
```

### 新增模块

| 模块 | 文件 | 阶段 |
|------|------|------|
| 文档清洗器 | `preprocessing/cleaner.py` | P0 |
| 脏数据过滤器 | `preprocessing/filter.py` | P0 |
| OCR 引擎 | `preprocessing/ocr.py` | P1 |
| BM25 持久化 | `retrieval/bm25_store.py` | P0 |
| Pipeline 进度追踪 | `retrieval/progress.py` | P1 |
| 检索统计 API | `api/routes/rag_stats.py` | P1 |

---

## 三、P0 — 立即修复（3 + 3 项）

### P0-1：文档清洗层 `preprocessing/cleaner.py`

**功能：**

```python
class DocumentCleaner:
    def clean(self, text: str, source_type: str = "text") -> CleanResult:
        """统一文档清洗入口"""
        ...

    def _normalize_text(self, text: str) -> str:
        """文本规范化"""
        # - 去除控制字符（\x00-\x1f 除 \n\t）
        # - 去除非法 Unicode（surrogate characters）
        # - 全角半角统一（数字、字母、标点）
        # - 空白字符规范化（\r\n → \n, \t → 空格）
        # - 合并连续空行（>2 空行 → 2 空行）
        # - 中文标点统一（，。！？""'' → 全角）
        # - HTML 标签剥离
        ...

    def _clean_pdf(self, text: str) -> str:
        """PDF 专用清洗"""
        # - 检测并去除页眉（每页顶部重复行）
        # - 检测并去除页脚（每页底部重复行）
        # - 去除独立页码行
        # - 去除重复章节标题（PDF 转换常见 artifact）
        ...

    def _clean_url_email(self, text: str) -> str:
        """URL/邮箱规范化：保留还是替换为占位符，可配置"""
        ...
```

**配置项：**

```python
# config.py 新增
CLEAN_REMOVE_CONTROL_CHARS = True      # 去除控制字符
CLEAN_NORMALIZE_FULLWIDTH = True       # 全角半角统一
CLEAN_MERGE_BLANK_LINES = True         # 合并空行
CLEAN_STRIP_HTML = True                # HTML 标签剥离
CLEAN_REMOVE_PDF_HEADERS = True        # PDF 页眉去除
CLEAN_REMOVE_PDF_FOOTERS = True        # PDF 页脚去除
CLEAN_URL_ACTION = "placeholder"       # keep | remove | placeholder
CLEAN_EMAIL_ACTION = "placeholder"     # keep | remove | placeholder
```

**集成点：** `loader.py` 的 `load_documents_from_directory()` 中，`loader.load()` 之后、`split_documents()` 之前。

---

### P0-2：脏数据过滤器 `preprocessing/filter.py`

**功能：**

```python
class ChunkFilter:
    def should_keep(self, text: str, metadata: dict) -> tuple[bool, str]:
        """返回 (是否保留, 拒绝原因)"""
        ...

    def _checks(self):
        return [
            self._check_not_empty,         # 空白内容（去除空格后为空）
            self._check_not_duplicate,     # SimHash 去重（阈值可配）
            self._check_min_length,        # 超短文本（< 10 字符）
            self._check_not_all_symbols,   # 纯符号（> 80% 非字母数字中文）
            self._check_language_ratio,    # 中文占比 > 30%（中文知识库场景）
            self._check_not_noise,         # OCR 噪声特征（大量碎片字符）
        ]

class DuplicateDetector:
    """SimHash 去重器"""
    def __init__(self, threshold: int = 3):
        self.seen_hashes: list[int] = []
        self.threshold = threshold

    def is_duplicate(self, text: str) -> bool:
        """SimHash 计算 → 汉明距离比较 → 是否重复"""
        ...
```

**敏感信息脱敏（内置到 Filter）：**

```python
# 正则匹配 → 自动替换
PII_PATTERNS = {
    "phone": r'1[3-9]\d{9}',
    "id_card": r'\d{17}[\dXx]',
    "bank_card": r'\d{16,19}',
}
# 替换策略: 1[3-9]\d{9} → 1**********（保留首字符用于上下文）
```

**配置项：**

```python
FILTER_MIN_CHUNK_LENGTH = 10
FILTER_MAX_SYMBOL_RATIO = 0.8
FILTER_MIN_CHINESE_RATIO = 0.3
FILTER_SIMHASH_THRESHOLD = 3          # 汉明距离 ≤ 3 判为重复
FILTER_ENABLE_PII_MASK = True         # 敏感信息脱敏
```

**集成点：** 切片之后、Embedding 之前。过滤统计写入 metadata（`filter_status`, `filter_reason` 用于后续审计）。

---

### P0-3：BM25 持久化 `retrieval/bm25_store.py`

**现状问题：** 每次启动遍历所有 chunk 重建 BM25 索引，文档越多越慢。

**方案：**

```python
class BM25Store:
    """磁盘持久化 BM25 索引"""

    def __init__(self, index_dir: str = "data/bm25"):
        self.index_dir = Path(index_dir)

    def build(self, docs: list[Document]) -> None:
        """构建索引 → 写入磁盘（tokenized corpus + IDF 表 + doc store）"""
        ...

    def load(self) -> BM25Retriever:
        """从磁盘加载索引 → 返回可用 Retriever"""
        ...

    def add_documents(self, docs: list[Document]) -> None:
        """增量添加文档"""
        ...

    def remove_documents(self, doc_ids: list[str]) -> None:
        """增量删除"""
        ...

    @property
    def is_stale(self) -> bool:
        """检查索引是否过期（对比 registry 更新时间）"""
        ...
```

**存储格式（pickle + json）：**
```
data/bm25/
├── corpus.pkl       # 分词后的语料
├── idf.json         # IDF 值表
├── doc_store.json   # doc_id → index 映射
└── meta.json        # 索引元数据（文档数/构建时间/版本）
```

**集成点：** `RAGPipeline._init_retrievers()` 优先加载磁盘索引，仅在 `is_stale` 或不存在时重建。`IncrementalIndexer` 新增/删除后增量更新 BM25。

---

### P0-4：删除死代码

| 删除项 | 文件 | 原因 |
|--------|------|------|
| `ResumeChunkStrategy` | `preprocessing/chunking.py` | Router 无路由触发，死代码 |
| `PgVectorKnowledgeStore` | `retrieval/knowledge_store.py` | 空 stub，待 P2 实现时再写 |

### P0-5：删除前端假数据页面

| 页面 | 处理 |
|------|------|
| `tasks/page.tsx` | 删除。等 P1 有了真实 API 再重建 |
| `pipeline/page.tsx` | 删除。等 P1 SSE 进度实现后再重建 |
| sidebar 中对应入口 | 移除（暂不展示不可用功能） |

### P0-6：修复双重单例

**问题：** `api/deps.py` 和 `multi_agent/tools.py` 各自懒加载 `RAGPipeline`，可能加载两份 Embedding 模型。

**方案：** 统一为 `api/deps.py` 的 `get_rag_pipeline()`，`multi_agent/tools.py` 改为调用同一个惰性单例。

---

## 四、P1 — 核心增强（5 项）

### P1-1：OCR 引擎 `preprocessing/ocr.py`

```python
class OCREngine:
    """可插拔 OCR 引擎"""

    def __init__(self, backend: str = "paddleocr"):
        self.backend = self._init_backend(backend)

    def extract(self, pdf_path: str) -> OCRResult:
        """对 PDF 逐页 OCR → 结构化结果"""
        ...

    def _needs_ocr(self, page_text: str) -> bool:
        """判断页面是否需要 OCR（文本量 < 阈值 或 全是乱码）"""
        ...

class OCRResult:
    text: str
    confidence: float
    pages_processed: int
    pages_skipped: int       # 有文本的页跳过 OCR
    needs_post_clean: bool   # 是否需要后续 OCR 清洗
```

**OCR 后处理（内置于 cleaner.py）：**

```python
def _clean_ocr(self, text: str) -> str:
    """OCR 结果专用清洗"""
    # - 去除识别碎片（单字行）
    # - 合并断行（OCR 常见行内换行）
    # - 修正常见 OCR 错误（数字/字母混淆表）
    # - 过滤水印文字（重复出现的低置信度文字块）
```

**配置：**

```python
OCR_BACKEND = "paddleocr"            # paddleocr | easyocr | tesseract
OCR_CONFIDENCE_THRESHOLD = 0.6       # 低于此置信度的文字丢弃
OCR_MIN_TEXT_LENGTH_FOR_SKIP = 50    # 页文本 > 50 字符则跳过 OCR
OCR_MAX_PAGES_PER_DOC = 500          # 单文档最大 OCR 页数
```

---

### P1-2：前端真实数据对接

**新增/改造的 API：**

| 端点 | 说明 |
|------|------|
| `GET /rag/stats` | 增强：增加 avg_chunk_length, duplicate_rate, ocr_doc_count, vector_count |
| `GET /rag/stats/retrieval` | 新增：暴露 MetricsCollector 数据（检索次数/命中率/耗时/Rerank 耗时） |
| `GET /rag/index/progress` | 新增：当前索引任务进度（如"正在处理 3/12，当前文件 xxx.pdf"） |
| `GET /rag/tasks` | 新增：近期索引任务历史（成功/失败/耗时） |

**前端改造：**

| 页面 | 改造内容 |
|------|----------|
| 概览页 | 去掉硬编码文案，全部读 API；增加重复率/OCR 数量/平均 Chunk 长度卡片 |
| tasks 页 | 删除假数据页面；重建为真实任务历史页面（从新 API 拉取） |
| pipeline 页 | 删除假数据页面；等 P1-4 SSE 进度实现后展示实时 Pipeline 状态 |
| 知识库 playground | 展示检索耗时分解（retrieval/rerank/total） |
| 概览页 Chunk 策略 | 从 API 动态获取，不再硬编码 |

---

### P1-3：Metadata 增强

**新增字段：**

```python
# 每个 Chunk 的 metadata 新增
"section_path": str,          # "第一章 > 1.1 概述 > 1.1.2 详细说明"
"heading_levels": dict,       # {"h1": "第一章", "h2": "1.1 概述", "h3": "1.1.2 详细说明"}
"created_at": str,            # ISO 时间戳
"updated_at": str,
"chunk_size_chars": int,      # 实际字符数
"filter_status": str,         # "clean" | "filtered_empty" | "filtered_duplicate" | ...
"ocr_used": bool,             # 是否使用了 OCR
"business_tags": list[str],   # 用户自定义标签（从上传接口传入或 LLM 自动生成）
```

**配置：**

```python
METADATA_EXTRACT_BUSINESS_TAGS = True   # 是否用 LLM 自动提取业务标签
METADATA_MAX_TAGS = 5                   # 每文档最多标签数
```

---

### P1-4：批量 Embedding 增强

```python
class BatchEmbedder:
    """批量向量化，支持多线程 + 重试 + 进度回调"""

    def __init__(self, embedding_model, max_workers: int = 4):
        self.model = embedding_model
        self.max_workers = max_workers
        self.progress_callback: Callable | None = None

    def embed_batch(
        self,
        texts: list[str],
        metadatas: list[dict] = None,
        batch_size: int = 32,
        max_retries: int = 3,
    ) -> BatchResult:
        """多线程批量 embedding"""
        ...

class BatchResult:
    total: int
    succeeded: int
    failed: int
    retried: int
    errors: list[tuple[int, str]]     # (index, error_message)
    duration_ms: int
```

**进度回调接口：**

```python
# retrieval/progress.py
@dataclass
class IndexProgress:
    stage: str              # "parsing" | "cleaning" | "chunking" | "embedding" | "storing"
    current: int
    total: int
    current_file: str
    elapsed_ms: int

class ProgressTracker:
    """全局进度追踪器（线程安全）"""
    def update(self, progress: IndexProgress): ...
    def get(self) -> IndexProgress: ...
    def subscribe(self) -> Generator: ...    # SSE 流式推送
```

**SSE 端点：** `GET /rag/index/progress/stream` → text/event-stream

---

### P1-5：结构化切片增强

| 增强点 | 说明 |
|--------|------|
| **表格感知** | 检测 Markdown/HTML 表格，保持表格完整性，不从中切割 |
| **列表感知** | 检测有序/无序列表块，尽量保持列表在同一 Chunk |
| **Overlap 统一** | ManualPolicy 策略也增加 overlap（用滑动窗口在章节边界内重叠） |
| **Chunk 边界优化** | 优先在句子边界（。！？\n\n）切分，而不是硬截断 |
| **最大 Chunk 限制** | 增加 `CHUNK_MAX_SIZE` 配置，超大章节强制子切分（带 overlap） |

---

## 五、P2 — 锦上添花（4 项）

### P2-1：流式解析

```python
class StreamingLoader:
    """分页加载大文件，避免 OOM"""

    def load_pdf_stream(self, path: str, page_batch: int = 10):
        """每次加载 N 页 → yield → 继续"""
        ...

    def load_text_stream(self, path: str, chunk_bytes: int = 1024 * 1024):
        """按字节块流式读取"""
        ...
```

### P2-2：语义切片

```python
class SemanticChunker:
    """基于句子嵌入相似度的语义边界检测"""

    def split(self, text: str, similarity_threshold: float = 0.5) -> list[str]:
        """
        1. 按句子分割
        2. 计算相邻句子的嵌入余弦相似度
        3. 在相似度骤降处切分（话题切换点）
        """
        ...
```

### P2-3：多格式支持

| 格式 | Loader | 优先级 |
|------|--------|--------|
| `.html` | `BSHTMLLoader` | P2 |
| `.csv` | `CSVLoader` | P2 |
| `.xlsx` | `UnstructuredExcelLoader` | P2 |
| `.pptx` | `UnstructuredPowerPointLoader` | P2 |
| `.json` | `JSONLoader` | P2 |

### P2-4：PgVector 后端

实现 `PgVectorKnowledgeStore`，利用已有的 PostgreSQL + pgvector。

---

## 六、建议删除/精简清单（非 Pipeline 功能，但影响质量）

### 删除

| # | 模块 | 原因 |
|---|------|------|
| D1 | `ResumeChunkStrategy` in `chunking.py` | 死代码，Router 无路由触发 |
| D2 | `PgVectorKnowledgeStore` stub | 空壳，P2 实现时再写 |
| D3 | `web/src/app/knowledge/tasks/page.tsx` | 100% 硬编码假数据 |
| D4 | `web/src/app/knowledge/pipeline/page.tsx` | 100% 硬编码假数据 |
| D5 | sidebar 中 tasks/pipeline 入口 | 页面删了入口也应移除 |

### 精简

| # | 模块 | 改造 |
|---|------|------|
| S1 | RAGPipeline 双重单例 | 统一到 `api/deps.py`，`tools.py` 改为调用同一实例 |
| S2 | `RAGPipeline.search()` | 移除（无调用者），功能合并到 `ask()` |
| S3 | 来源提取重复 | `chain.py::_extract_sources` + `reporter.py::_extract_rag_references` → 统一 |
| S4 | Doc 级 ChromaDB | 评估后可砍：如果 ChunkLevelRetriever 效果足够，去掉 doc 级向量库省一半存储 |

---

## 七、实施计划

### Phase 0：清理（1 天）

- D1-D5：删除死代码 + 假数据页面
- S1-S2：修复双重单例 + 移除 search()

### Phase 1：P0 核心能力（3-4 天）

- P0-1：文档清洗层（cleaner.py）
- P0-2：脏数据过滤器（filter.py）
- P0-3：BM25 持久化（bm25_store.py）
- 集成到现有 Pipeline + 冒烟测试

### Phase 2：P1 增强（5-7 天）

- P1-1：OCR 引擎
- P1-2：前端真实数据对接
- P1-3：Metadata 增强
- P1-4：批量 Embedding + 进度追踪
- P1-5：结构化切片增强

### Phase 3：P2 锦上添花（按需）

- P2-1 ~ P2-4：按实际需求排期

### Phase 4：精简（混合在各 Phase 中）

- S3：统一来源提取
- S4：评估 doc 级向量库去留

---

## 八、兼容性保证

所有新增模块遵循以下原则：

1. **Loader → Cleaner → Filter → Chunker 链路可配置**：每阶段可通过配置跳过
2. **现有 API 接口不变**：`/rag/*` 端点签名保持兼容，只增加字段
3. **现有 RAGPipeline.ask() 行为不变**：清洗/过滤仅在入库时生效，不影响已有检索链路
4. **配置文件默认值保持现有行为**：新增配置项的默认值 = 关闭新功能，用户显式开启
5. **Metadata 字段向后兼容**：新增字段不影响已有 Chunk 的 metadata 结构
