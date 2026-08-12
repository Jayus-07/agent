# Chunking 切分重构设计

> 状态：设计已确认，待实现计划
> 日期：2026-08-13
> 关联：`backend/rag/preprocessing/chunking.py`、`backend/rag/preprocessing/loader.py`、`backend/rag/indexing/indexer.py`

## 1. 背景与问题

当前切分（`chunking.py`）已经有 `ChunkStrategyRouter` 按 `doc_type` 路由到 6 个策略，架构骨架正确。但存在以下问题：

1. **结构检测耦合在策略内部**：`_find_sections` / `_STEP_PATTERN` / `_ARTICLE_PATTERN` / `_QA_PATTERN` 等标题/步骤/条款/QA 检测逻辑，分散在各 Strategy 里各自实现，`Manual/Contract/ManualPolicy` 的「无结构→找章节→单 chunk」兜底三份重复。
2. **结构化策略零 overlap + 单层标题**：按边界硬切，overlap=0；只存当前 `section_title`，丢失「章→节→子节」完整层级路径。
3. **子分块丢标题**：超长章节用 `RecursiveCharacterTextSplitter` 子分块时，子块不带章节标题，下游 Evidence Boundary 的 `[章节]` 标签为空。
4. **没有语义切分 / Parent-Child / LLM 辅助**：长文档无结构时只能递归硬切；索引期只有扁平单粒度。
5. **字符数 ≠ token**：`length_function=len` 数字符，与「500/800 token」目标语义不符。

## 2. 目标

建立三个核心能力（本次架构升级的最终目标）：

1. **统一 Document AST** —— 所有格式先解析成统一的结构树，后续一切消费 AST。
2. **Structure Analyzer** —— 独立的规则结构检测（+ 结构完整度 score），结构混乱时 LLM 补充。
3. **双轴 Strategy Router** —— 文档类型 × 文档结构 共同决定切分策略。

## 3. 职责分离（架构红线）

**Structure Analyzer 和 ChunkStrategy 不得各自重复判断标题/章节。** 结构检测只在 Structure Analyzer 做一次，产出 AST；Strategy 只消费 AST，不再重新解析文本结构。

```
Parser              = “解析文件结构”          （格式级 → Raw AST）
DocumentCleaner     = “原始文档 → 干净且结构安全的文档”（解析后、结构分析前）
Structure Analyzer  = “判断/补全结构”        （Raw AST → Normalized AST + StructureReport）
Strategy            = “既然知道结构，应该怎么切？”（消费 Normalized AST，不重检测）
ChunkFilter         = “切完以后，这些 Chunk 是否合格？”（SimHash / PII / 长度）
Indexer             = “怎么把合格 Chunk 写入索引？”  （持久化）
```

## 4. 整体流水线

```
上传文件
  ↓
① Format Parser      PDF/DOCX/MD/TXT/Excel → Raw AST（格式级结构：标题样式/字号/列表）
  ↓
② Document Cleaning  DocumentCleaner（结构安全清洗 Raw AST 文本，保留结构节点）
  ↓
③ Structure Analyzer Raw AST → Normalized AST + StructureReport（判断/补全结构 + 完整度）
  │                    结构混乱 → LLM Structure Analyzer（只出结构，不改写）
  ↓
④ 双轴判断
     轴1 文档类型   Policy/SOP/FAQ/Product/Technical/General   (复用 classify_doc_type)
     轴2 文档结构   Heading/Paragraph/Table/List/Section + 完整度
  ↓
⑤ Chunk Strategy Router（类型 × 结构 → 策略）
  ↓
⑥ 策略  Structure / QA / Recursive / Semantic / Parent-Child / LLM Assisted
  ↓
⑦ Chunk（双粒度 leaf + parent）
  ↓
⑧ Metadata（section_path / parent_chunk_id / chunk_tokens）
  ↓
⑨ ChunkFilter（SimHash / PII / 长度 / 噪声）
  ↓
⑩ Embedding → Vector DB
```

## 5. DocumentNode AST 数据模型

统一结构树：Parser 产出 Raw AST，Structure Analyzer 归一化为 Normalized AST，所有 Strategy 消费。

```python
@dataclass
class DocumentNode:
    # type 取值：heading | section | paragraph | list | table | qa_question | qa_answer
    type: str
    text: str          # 原文（LLM 阶段也保证不改写）
    level: int = 0     # heading 层级；paragraph/table/list/qa_* = 0
    children: list["DocumentNode"] = field(default_factory=list)
    rows: list | None = None            # table 专用
    source_range: tuple[int, int] = (0, 0)  # 原文偏移，精确回切
```

节点类型语义：

- `section` 是容器节点（含 `children`），`heading` 是标题叶子节点（`section` 的 title）。
- `qa_question` / `qa_answer` 是 FAQ 叶子类型，QA Strategy 直接从 AST 取，**无需重新判断 Q/A**。

约束：

- `source_range` 保证任何节点都能**回切原文**，LLM 只标边界不改内容。
- `section_path` 不再由策略拼，直接从 AST 祖先链推导。
- AST 是**树**（非平铺列表），因为 `section_path` 需要层级。

## 6. Format Parser 层

依赖原则：**只在对应 Parser 中依赖解析库，不污染 Chunking 核心**。Parser 输出 Raw AST（格式级结构），Structure Analyzer 归一化为 Normalized AST，后续不关心原始格式。

```
PDF/DOCX/MD/TXT/Excel → Format Parser → Raw AST → DocumentCleaner → Structure Analyzer → Normalized AST
```

| 格式 | 结构来源 | 解析库 | 阶段 |
|---|---|---|---|
| Markdown | `#/##/###` | 已有（TextLoader + 正则） | Phase 1 |
| TXT | 空行 + 行长度 + 编号规则 | 已有 | Phase 1 |
| PDF | 字号/粗体/短文本/编号 | **PyMuPDF**（新增） | Phase 2 |
| DOCX | Heading 1/2/3 + Paragraph + Table | **python-docx**（新增） | Phase 2 |
| Excel | Sheet→表头→数据区 | openpyxl（暂不引入） | 占位 stub |

模块结构（新增 `parser/` 子包）：

```
preprocessing/parser/
  base.py             # BaseDocumentParser 抽象 + parse(file_path) -> DocumentAST
  markdown_parser.py  # Phase 1
  txt_parser.py       # Phase 1
  pdf_parser.py       # Phase 2（PyMuPDF）
  docx_parser.py      # Phase 2（python-docx）
  excel_parser.py     # 占位：raise NotImplementedError
```

Excel 占位：

```python
class ExcelParser(BaseDocumentParser):
    def parse(self, file_path) -> DocumentAST:
        raise NotImplementedError("Excel 解析器待实现")
```

## 7. Structure Analyzer

规则优先，产出 `StructureReport`：

```python
@dataclass
class StructureReport:
    ast: DocumentAST
    completeness: float          # 结构完整度 0~1
    deficit_signal: str          # 结构不足原因：no_heading / ocr_garbled / mixed_fragment / long_narrative / "" 
    topic_shift_detected: bool   # 主题变化是否明显（供 Semantic 降级判断）
    is_high_value_and_chaotic: bool
    is_complete: bool            # completeness >= 阈值
```

### 7.1 结构完整度（completeness）

启发式加权得分，Phase 1 校准具体权重：

- **text_coverage**：被结构节点覆盖的字符数 / 总字符数
- **node_size_fitness**：平均节点大小是否落在目标区间
- **pattern_match**：doc_type 期望的结构模式是否出现（如 Policy 期望条款、SOP 期望步骤）

### 7.2 PDF 标题启发式（Phase 2）

PDF 无真正标题概念，需综合判断：

```
heading_score = weight_font_size * font_size_score    # 字号大于正文均值
              + weight_bold * is_bold
              + weight_numbering * has_numbering      # 一、/1./1.1/（一）
              + weight_short * is_short_text
              + weight_position * position_score      # 前后留白/独立成行
is_heading = heading_score >= HEADING_THRESHOLD
```

### 7.3 LLM Structure Analyzer（默认关闭，Phase 2）

- **触发**：规则 Structure Analyzer 判定 `is_complete == False` 且命中高价值/混乱信号。
- **职责**：只识别结构边界，**不负责改写原文**。
- **输出**：`{"blocks": [{"type":"section","title":"..."}, {"type":"paragraph","text_range":[a,b]}, ...]}`。
- **Prompt 硬约束**：`你只负责划分结构边界，禁止增删改原文任何字词；每个 paragraph 的 text 必须原样取自输入`。

## 8. 双轴 Strategy Router

文档类型决定「优先找什么结构」，结构完整度决定「能不能用结构切、不能就降级到哪」。

| doc_type | 结构完整度 | 策略 | 说明 |
|---|---|---|---|
| Policy / Technical | 高（≥ 阈值） | `StructureChunking` | 章节→条款→子条款 |
| SOP | 高 | `StructureChunking`（步骤模式） | 步骤编号边界 |
| FAQ | 高 | `QAChunking` | Q/A 天然边界 |
| Product / Listing | 高 | `StructureChunking`（表格/字段） | 规格字段 |
| General 或 结构差 | 低，主题变化明显 | `SemanticChunking`（Phase 2） | embedding 相似度骤降 |
| General 或 结构差 | 低，纯无结构长段落 | `RecursiveChunking` | 段→句→空格 |
| 极复杂高价值 + 混乱 | 很低 | `LLMAssistedChunking`（Phase 2） | LLM 只划边界不改写 |

路由伪代码：

```python
def route(doc_type, structure_report) -> Strategy:
    # 优先级：Structure > LLM > Semantic > Recursive
    if structure_report.completeness >= COMPLETE_THRESHOLD:
        return STRUCTURE_STRATEGIES[doc_type]   # Policy→条款, SOP→步骤, FAQ→QA...

    if structure_report.is_high_value_and_chaotic and ENABLE_LLM_CHUNKING:
        return LLMAssistedChunking              # 高价值复杂文档 → LLM 特殊处理

    if structure_report.topic_shift_detected and ENABLE_SEMANTIC_CHUNKING:
        return SemanticChunking                 # 普通无结构文档 → 高级处理

    return RecursiveChunking                    # 兜底
```

优先级说明：LLM 是高价值复杂文档的**特殊处理**，Semantic 是普通无结构文档的**高级处理**。两者同时命中时 LLM 优先，否则 Semantic 永远触发不到。

`STRUCTURE_STRATEGIES[doc_type]` 是现有 [ChunkStrategyRouter._strategies](backend/rag/preprocessing/chunking.py#L552-L572) 映射的升级版——但结构检测已抽到 Structure Analyzer，策略只负责「AST → chunk」。

## 9. Chunk Strategy 层

**只消费 AST，不重新检测结构。** 现有策略升级而非推翻重写：

- 删除策略内的 `_find_sections` / `_STEP_PATTERN` / `_ARTICLE_PATTERN` / `_QA_PATTERN` 检测逻辑（移入 Structure Analyzer）。
- 策略改为「遍历 AST 节点 → 按类型切 leaf + parent」。
- 合并三份「无结构→找章节→单 chunk」兜底为统一降级链。

### 9.1 双粒度 Parent-Child

section 节点 → parent chunk；paragraph/list/table 节点 → leaf 原料。

```
Parent 切分：doc_type 结构化策略切出 parent（章节/条款/步骤/QA），≈800–2000 token，带 section_path
Leaf 切分：parent 内按语义/递归细切 leaf（≈300–500 token，overlap 50–100），继承 section_path + parent_chunk_id
```

查询侧：召回 leaf → 取 top-K leaf 的 parent → parent 原文注入 context（替代现有查询时 AdaptiveRetriever 拉全文的事后补丁）。

### 9.2 统一 Metadata 协议

所有策略输出一致字段：

```python
{
  "chunk_id": "md5(doc_id+index)",
  "granularity": "leaf" | "parent",
  "parent_chunk_id": "...",
  "section_path": ["第一章", "1.2 退款"],
  "section_title": "1.2 退款",
  "section_level": 2,
  "chunk_tokens": 380,   # 真实 token 数（替换 len 字符数）
}
```

## 10. 两层清洗：DocumentCleaner 与 ChunkFilter

数据清洗需要做，但拆成两层，且**不要暴力清洗破坏结构信息**（标题/编号/表格/段落边界）。

### 10.1 第一层 DocumentCleaner（结构安全清洗，解析后、结构分析前）

复用现有 [DocumentCleaner](backend/rag/preprocessing/cleaner.py)，职责：原始文档 → 干净且结构安全的文档。

处理：控制字符、非法 Unicode、全角半角、空白规范化、连续空行、HTML 标签、PDF 页眉页脚、页码、URL/邮箱。

**必须保留**：标题、编号、列表、表格、段落边界、页码（heading_path 所需信息）。

结构安全风险点（实现时确认不误伤）：

- `_normalize_fullwidth` 只转数字/字母，不影响编号语义（① 等不在映射内）。
- `_remove_page_numbers` 只对 PDF 生效，但 `^\d{1,4}$` 可能误删「独立成行的步骤号」，Phase 1 需验证。
- 表格分隔符 `\t` 在 `_normalize_whitespace` 中被转成空格，需确认不影响表格结构识别。

### 10.2 第二层 ChunkFilter（质量检查，切分后）

复用现有 [ChunkFilter](backend/rag/preprocessing/filter.py)：SimHash 去重 + PII 脱敏 + 长度/噪声过滤。

### 10.3 PII 不前置

PII 脱敏**不放 DocumentCleaner**，保留在 ChunkFilter（切分后）。原因：前置脱敏会破坏实体识别、文档分类、章节判断、Citation 原文对应。若企业有强制隐私合规要求，再在上传入口级单独加 PII 策略。

## 11. 分阶段实施

架构先完整，代码分阶段落地。**不要为「一次性企业级」同时实现所有能力。**

### Phase 1：核心主干（Markdown/TXT + 双粒度 Parent-Child）

```
Markdown/TXT → Parser → Raw AST → DocumentCleaner → Structure Analyzer → 结构完整度
→ 双轴路由 → Structure/QA/Recursive → Parent + Leaf → section_path → token counting
→ ChunkFilter → 重索引
```

注意：Phase 1 中 Semantic / LLM Assisted 均默认关闭，`topic_shift_detected` 依赖 embedding（Phase 2 才有），因此 Phase 1 的 Router 实际是**二分支**：结构完整 → `STRUCTURE_STRATEGIES[doc_type]`；结构不完整 → `RecursiveChunking` 兜底。Semantic / LLM 分支在 Phase 2 才接入。

验证点：

- AST 是否合理
- heading_path 是否正确
- structure completeness 是否稳定
- Strategy Router 是否正确降级
- parent/leaf 关系是否正确
- 现有 RAG 检索是否不回归

### Phase 2：复杂格式 + 高级策略（按需逐步）

PyMuPDF（PDF 结构）、python-docx（DOCX 结构）、Semantic Chunking、LLM Assisted Chunking。

### Phase 3（可选）

Excel Parser。

### 默认开关

| 能力 | 默认 |
|---|---|
| Semantic Chunking | 关闭 |
| LLM Assisted Chunking | 关闭 |
| Excel Parser | 不实现（占位） |
| PDF/DOCX | 不阻塞 Phase 1 |

## 12. 配置项清单（新增）

```
ENABLE_SEMANTIC_CHUNKING      = false
ENABLE_LLM_CHUNKING           = false
LLM_CHUNK_MIN_CHARS           = 2000     # 规模达标才值得烧 token
STRUCTURE_COMPLETE_THRESHOLD  = 0.7      # Phase 1 校准
LEAF_CHUNK_TOKENS             = 500
PARENT_CHUNK_TOKENS           = 2000
HEADING_THRESHOLD             = 0.6      # PDF 标题打分阈值（Phase 2）
```

## 13. 重索引

元数据协议变了（section_path / granularity / parent_chunk_id），bump `.version` 触发全量重建（复用 [pipeline.py:311](backend/rag/pipeline.py#L311) 现有 version 机制）。

## 14. 测试与验证

- **单元测试**：各 Parser → AST 正确性；Structure Analyzer 完整度稳定性；Router 降级矩阵。
- **回归**：现有 `pytest tests/sql/` 不回归；RAG 检索（`POST /rag/search`）召回不退化。
- **契约测试**：Strategy 不重检测结构（断言 Strategy 不 import/调用 `_find_sections` 等检测函数）。

## 15. 依赖变更

| 依赖 | 用途 | 阶段 |
|---|---|---|
| PyMuPDF | PDF 字号/字体/粗体结构信息 | Phase 2 |
| python-docx | DOCX Heading/Table 结构信息 | Phase 2 |

仅在对应 Parser 中 import，不污染 Chunking 核心模块。
