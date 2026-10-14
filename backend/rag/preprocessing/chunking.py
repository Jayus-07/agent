"""chunking.py — 文档类型感知切分（消费 Normalized AST）。

Strategy 只负责「既然知道结构，怎么切」，不再重新检测标题/章节。
结构检测在 parser + structure_analyzer 完成。
"""
from __future__ import annotations

import hashlib
import math
import os
import re
from typing import TYPE_CHECKING, List

from langchain_core.documents import Document

if TYPE_CHECKING:
    from backend.rag.preprocessing.structure_analyzer import StructureReport
from langchain_text_splitters import RecursiveCharacterTextSplitter

from backend.config import LEAF_CHUNK_TOKENS, PARENT_CHUNK_TOKENS, CHUNK_OVERLAP
from backend.rag.preprocessing.ast import DocumentAST, DocumentNode, LEAF_TYPES, walk
from backend.rag.preprocessing.token_counter import count_tokens
from backend.shared.logger import logger

_SEPARATORS = ["\n\n", "\n", "。", "！", "？", ".", "!", "?", " ", ""]


def _hard_split_by_budget(text: str, budget: int) -> List[str]:
    """字符级硬切兜底：以 count_tokens(整串) 为唯一口径，保证每片 ≤ budget。

    不用 RecursiveCharacterTextSplitter：其 merge 按切分单元逐片累计，
    字符估算口径下非 CJK 单字符 = 0 token（other//4），累计永不触发
    flush，会产出「一片超限」的假收口。这里逐片搜索「不超预算的最大
    前缀」（步进粗定位 + 二分收敛），口径与超限校验完全一致。
    """
    out: List[str] = []
    start = 0
    n = len(text)
    while start < n:
        # 步进粗定位：从 start+1 起，找第一个超预算的粗上界
        step = 128
        end = start + 1
        while end < n and count_tokens(text[start:end]) <= budget:
            end = min(end + step, n)
        # [start+1, end] 内二分最大达标前缀（end 处可能已达标）
        lo, hi, ans = start + 1, end, start + 1
        while lo <= hi:
            mid = (lo + hi) // 2
            if count_tokens(text[start:mid]) <= budget:
                ans = mid
                lo = mid + 1
            else:
                hi = mid - 1
        out.append(text[start:ans])
        start = ans
    return out


def _split_to_budget(text: str, budget: int,
                     separators: list[str] | None = None,
                     overlap: int | None = None) -> List[str]:
    """按 token 预算切分，严格保证每片 ≤ budget（P0-05 验收口径）。

    语义切分（RecursiveCharacterTextSplitter）在 keep_separator/overlap
    合并、以及估算口径 per-piece 低估时可能产生 > budget 的片——
    P0-05 要求「每个 Chunk 满足对应实际 Token 上限」，超限片用
    _hard_split_by_budget（整串口径硬切）二次收口。
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=budget,
        chunk_overlap=CHUNK_OVERLAP if overlap is None else overlap,
        length_function=count_tokens,
        separators=separators if separators is not None else _SEPARATORS,
    )
    subs = splitter.split_text(text)
    if all(count_tokens(s) <= budget for s in subs):
        return subs
    out: List[str] = []
    for s in subs:
        if count_tokens(s) <= budget:
            out.append(s)
        else:
            out.extend(_hard_split_by_budget(s, budget))
    return out


def _chunk_id(doc_id: str, anchor: str, text: str) -> str:
    """内容派生 chunk_id（生产加固）。

    原实现 md5(doc_id:遍历index) 依赖遍历序——文档局部微变会让后续
    chunk 的 index 整体偏移、chunk_id 全部漂移，增量重索引后无法追踪
    单个 chunk 的生命周期。现改为 doc_id + anchor（section 路径/粒度）
    + 内容哈希派生：
      - 同一文档相同内容重复切分 → 相同 chunk_id（幂等重索引）
      - 文档局部微变 → 未受影响 chunk 的 id 保持稳定
    anchor 提供上下文区分（parent/leaf + section 路径），
    内容哈希保证内容绑定。格式保持 md5 前 12 位 hex 不变。
    """
    text_hash = hashlib.md5(text.encode("utf-8")).hexdigest()[:8]
    return hashlib.md5(
        f"{doc_id}:{anchor}:{text_hash}".encode("utf-8")
    ).hexdigest()[:12]


def _enrich(chunks: List[Document], file_path: str,
            max_chunks: int | None = None) -> List[Document]:
    """统一 metadata：parent_doc_id / chunk_index / source_file / file_path。

    生产防护：chunk 数量超过上限时截断 + 告警，防止解析失控的异常文档
    无界产出撑爆 embedding/向量库。截断不是静默的：所有保留 chunk 打上
    chunks_truncated 标记，indexer 会写入 registry quality_issues（截断
    文档的检索覆盖天然不完整，必须可追溯）。财务文档行级切分产出多，
    由调用方传 FINANCIAL_MAX_CHUNKS_PER_DOC 上限。
    """
    from backend.config import MAX_CHUNKS_PER_DOC
    limit = max_chunks or MAX_CHUNKS_PER_DOC
    truncated = len(chunks) > limit
    if truncated:
        logger.warning(
            f"[Chunking] {file_path} 产出 {len(chunks)} chunks 超过上限 "
            f"{limit}，截断保留前 {limit} 个"
        )
        chunks = chunks[:limit]
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
        if truncated:
            # Chroma metadata 只收标量，用字符串标记
            c.metadata["chunks_truncated"] = "true"
    return chunks


def _make_doc(text: str, meta: dict) -> Document:
    return Document(page_content=text, metadata=meta)


# ── Semantic 语义切分辅助函数 ──────────────────────────

_SENTENCE_SPLIT_RE = re.compile(r"[。！？；]+")


def _split_sentences(text: str) -> list[str]:
    """按中文句子边界切分，过滤空句。

    PDF 提取的文本常含硬换行（非语义边界），先统一为空格，
    避免误切「接口」「流程」这类被换行拆开的词。
    """
    normalized = text.replace("\r", " ").replace("\n", " ")
    return [p.strip() for p in _SENTENCE_SPLIT_RE.split(normalized) if p.strip()]


_SENT_SPLIT_KEEP_RE = re.compile(r"([。！？；]+)")


def _split_sentences_keep_punct(text: str) -> list[str]:
    """C4：切句保留句尾标点（重组时原样拼回，！？；不再被 "。" 覆盖）。"""
    normalized = text.replace("\r", " ").replace("\n", " ")
    parts = _SENT_SPLIT_KEEP_RE.split(normalized)
    out: list[str] = []
    for i in range(0, len(parts) - 1, 2):
        s = (parts[i] + parts[i + 1]).strip()
        if s:
            out.append(s)
    if len(parts) % 2 == 1 and parts[-1].strip():
        out.append(parts[-1].strip())
    return out


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """余弦相似度，零向量返回 0.0。"""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _detect_boundaries(
    sentences: list[str], vecs: list[list[float]], threshold: float,
) -> list[bool]:
    """检测语义边界：相邻句子相似度骤降处标记为新 chunk 起点。

    Returns:
        starts: starts[i]=True 表示 sentences[i] 是新 chunk 的起点（starts[0] 恒 True）。
    """
    starts = [True] + [False] * (len(sentences) - 1)
    for i in range(1, len(sentences)):
        if _cosine_similarity(vecs[i - 1], vecs[i]) < threshold:
            starts[i] = True
    return starts


# 中文编号章节标题（一、二、三、），用于 SOP 文档的步骤切分
_SECTION_HEADING_RE = re.compile(r"^[一二三四五六七八九十]+、")


def _doc_title(ast: DocumentAST) -> str:
    """取文档首个一级标题（H1）作为兜底 section_title（P1-9：文档头部无章节号时用）。"""
    for n in walk(ast.root):
        if n.type == "section" and n.level == 1 and n.text.strip():
            return n.text.strip()
    return ""


def _is_section_heading(text: str) -> bool:
    """判断文本是否中文编号章节标题（一、二、三、）。"""
    return bool(_SECTION_HEADING_RE.match(text.strip()))


# 法律条款编号（第 N 条），用于 legal 文档的条款级切分
_LEGAL_CLAUSE_RE = re.compile(r"^第[一二三四五六七八九十百\d]+条")


def _is_legal_clause(text: str) -> bool:
    """判断文本是否法律条款编号（第一条 / 第2条 / 第十二条）。"""
    return bool(_LEGAL_CLAUSE_RE.match(text.strip()))


def _has_legal_clauses(ast: DocumentAST) -> bool:
    """AST 中是否存在「第 N 条」法律条款编号。"""
    return any(
        n.type in LEAF_TYPES and _is_legal_clause(n.text)
        for n in walk(ast.root)
    )


def _has_table_nodes(ast: DocumentAST) -> bool:
    """AST 中是否存在有内容的表格节点。"""
    return any(
        node.type == "table" and bool(node.rows)
        for node in walk(ast.root)
    )


def _has_qa_nodes(ast: DocumentAST) -> bool:
    """AST 中是否存在 qa_question / qa_answer 节点。"""
    return any(
        n.type in ("qa_question", "qa_answer")
        for n in walk(ast.root)
    )


def _ast_has_sections(ast: DocumentAST) -> bool:
    """AST 中是否存在正层级 section（资格校验的实时判断）。"""
    return any(
        n.type == "section" and n.level > 0
        for n in walk(ast.root)
    )


def _make_leaf_chunk(texts: list[str], file_path: str,
                     section_title: str = "", section_level: int = 0,
                     section_path: list | None = None) -> Document:
    """合并文本列表成一个 leaf chunk（Step/Legal 等按编号切分的策略共用）。

    chunk_id 由合并内容派生（幂等）：同内容重复切分产出相同 id。
    P1-9: 支持注入 section_title/section_level/section_path（Step/Legal 策略
    此前产出空标题，检索上下文弱）。
    """
    text = "\n".join(texts)
    return _make_doc(text, {
        "granularity": "leaf",
        "chunk_id": _chunk_id(file_path, "leaf", text),
        "parent_chunk_id": "",
        "section_path": list(section_path or []),
        "section_title": section_title,
        "section_level": section_level,
        "chunk_tokens": count_tokens(text),
    })


def _make_leaf_chunks_bounded(texts: list[str], file_path: str,
                              section_title: str = "", section_level: int = 0,
                              section_path: list | None = None,
                              budget: int | None = None,
                              preserve_source: bool = False) -> List[Document]:
    """合并文本为 leaf，超 token 预算时继续切分，每个子块保留节点身份（P0-05）。

    背景：Step/Legal/QA 策略此前把「一个章节/条款/问答」直接合并成单一
    leaf 且无上限检查——超长 SOP 单步骤或合同条款会产出几千甚至几万
    token 的巨型 chunk，撑爆 embedding 输入并稀释检索精度。
    切分不改变身份：section_title/section_path 原样带到每个子块，
    chunk_id 仍按子块内容派生（幂等）。
    """
    if budget is None:
        budget = LEAF_CHUNK_TOKENS
    text = "\n".join(texts)
    if count_tokens(text) <= budget:
        return [_make_leaf_chunk(texts, file_path, section_title,
                                 section_level, section_path)]
    # Legal 长条款的 Coverage 需要能从实际 Leaf 无损重建源文；递归切分
    # 会产生重叠片段，直接重组会重复边界。复用结构策略已有硬切，保持连续。
    subs = (
        _hard_split_by_budget(text, budget)
        if preserve_source else _split_to_budget(text, budget)
    )
    path = list(section_path or [])
    split_group_id = _chunk_id(
        file_path, f"split:{section_title}", text,
    )
    chunks = []
    for index, sub in enumerate(subs):
        metadata = {
            "granularity": "leaf",
            "chunk_id": _chunk_id(file_path, "leaf", sub),
            "parent_chunk_id": "",
            "section_path": path,
            "section_title": section_title,
            "section_level": section_level,
            "chunk_tokens": count_tokens(sub),
            "split_group_id": split_group_id,
            "split_part": index,
            "split_total": len(subs),
        }
        chunks.append(_make_doc(sub, metadata))
    return chunks


def _merge_small_texts(texts: list[str], budget: int) -> list[str]:
    """相邻小文本按 token 预算贪心合并（碎片化修复）。

    背景：无结构文档的 AST 小叶子若逐一成 chunk，会产出大量
    平均 20〜90 字符的碎片，稀释 embedding 区分度。合并规则：
      - 累计不超 budget 就继续并入下一段；超了先 flush 再重新累计
      - 自身已超 budget 的单段不参与合并（由调用方二次切分）
    """
    merged: list[str] = []
    buf: list[str] = []
    buf_tokens = 0
    for t in texts:
        if count_tokens(t) > budget:
            if buf:
                merged.append("\n".join(buf))
                buf, buf_tokens = [], 0
            merged.append(t)
            continue
        if buf and buf_tokens + count_tokens(t) > budget:
            merged.append("\n".join(buf))
            buf, buf_tokens = [], 0
        buf.append(t)
        buf_tokens += count_tokens(t)
    if buf:
        merged.append("\n".join(buf))
    return merged


def stamp_chunk_pages(chunks: list, ast: DocumentAST) -> None:
    """把叶子节点的页码（PDF 解析 §5.2 可追溯字段 page_number）映射进 chunk metadata。

    原文定位（P0，2026-10-03）：chunk 是叶子文本的合并/二次切分产物，切分
    策略只保留文本不保留节点引用，这里用双向文本包含做确定性回映射：
      - 叶子前缀 ⊆ chunk 文本 → 叶子被并入此 chunk（合并型策略）
      - chunk 核心 ⊆ 叶子全文 → chunk 是该叶子的切片（Fixed/Recursive 对
        超长叶子二次切分，切片可能不含叶子开头）
    两个方向都不含（策略改写了文本）→ 不标页码，宁缺勿错——错误的页码比
    没有页码更伤引用可信度。存标量逗号串（向量库 metadata 标量化惯例，
    同 ocr_triggered）；非 PDF 文档叶子无 page_number → 整体无页码可标。
    单点收口在 parse_and_chunk_full，8 个切分策略零改动（G2）。
    """
    leaves: list[tuple[str, str, int]] = []
    for n in walk(ast.root):
        if n.type not in LEAF_TYPES:
            continue
        try:
            page = int(getattr(n, "page_number", 0) or 0)
        except (TypeError, ValueError):
            continue
        if page <= 0:
            continue
        text = (n.text or "").strip()
        if text:
            leaves.append((text[:60], text, page))
    if not leaves:
        return
    for c in chunks:
        meta = getattr(c, "metadata", None)
        body = (getattr(c, "page_content", "") or "").strip()
        if not isinstance(meta, dict) or not body:
            continue
        core = body[:200]
        pages: set = set()
        for prefix, full, page in leaves:
            if prefix in body or (len(core) >= 40 and core in full):
                pages.add(page)
        if pages:
            meta["pages"] = ",".join(str(p) for p in sorted(pages))


def _iter_leaves_with_section(ast: DocumentAST):
    """遍历叶子并携带其所属 section 标题（C3 上下文携带）。

    leaf 的 section_title = 最近的祖先 section（level>0）标题；
    section 容器外的叶子标题为空串。
    """
    def _dfs(node: DocumentNode, title: str):
        for child in node.children:
            if child.type in LEAF_TYPES:
                yield child.text, title
            elif child.type == "section" and child.level > 0:
                yield from _dfs(child, child.text)
            else:
                yield from _dfs(child, title)
    yield from _dfs(ast.root, "")


def _merge_small_with_section(leaf_items: list, budget: int) -> list:
    """C3：_merge_small_texts 的 section 感知版。

    Args:
        leaf_items: [(text, section_title), ...]（_iter_leaves_with_section 产出）。

    Returns:
        [(merged_text, [来源标题去重列表]), ...]——合并段跨多个 section 时
        标题列表长度 > 1，调用方打 section_mixed 标记。
    """
    merged: list = []
    buf: list[str] = []
    buf_titles: list[str] = []
    buf_tokens = 0

    def _flush():
        nonlocal buf, buf_titles, buf_tokens
        if buf:
            merged.append(("\n".join(buf), list(dict.fromkeys(buf_titles))))
            buf, buf_titles, buf_tokens = [], [], 0

    for text, title in leaf_items:
        if count_tokens(text) > budget:
            _flush()
            merged.append((text, [title] if title else []))
            continue
        if buf and buf_tokens + count_tokens(text) > budget:
            _flush()
        buf.append(text)
        if title:
            buf_titles.append(title)
        buf_tokens += count_tokens(text)
    _flush()
    return merged


# ── 表格类 chunk 的类型标识（唯一一份）────────────────────────────────
# 结构病审查 P3-1：判定「这条 chunk 是不是表格」此前有三处口径 —— 生产点写字面量、
# 下方 reporting_period 回填用元组白名单、`filter.py` 用 ``startswith("table_")``。
# 任一处改名，其余两处静默失配；filter 失配的后果是**财务表格的 PII 不再强制脱敏**
# （隐私方向必须 fail-closed）。现在值只定义在这里，判定一律走 is_table_chunk()。
CHUNK_TYPE_TABLE_ROW = "table_row"
CHUNK_TYPE_TABLE_SUMMARY = "table_summary"
CHUNK_TYPE_TABLE_FALLBACK = "table_fallback"
CHUNK_TYPE_TABLE_HEADER = "table_header"
TABLE_CHUNK_TYPES: tuple[str, ...] = (
    CHUNK_TYPE_TABLE_ROW, CHUNK_TYPE_TABLE_SUMMARY, CHUNK_TYPE_TABLE_FALLBACK,
    CHUNK_TYPE_TABLE_HEADER,
)
TABLE_CHUNK_PREFIX = "table_"


def is_table_chunk(chunk_type: str | None) -> bool:
    """是否表格类 chunk。

    判定 = 「已知三种（白名单）」或「table_ 前缀」，两者取并：

    - 前缀保证 fail-closed：将来新增表格类型（如 table_header）自动被认作表格。
      漏判会让财务表格的 PII 漏脱敏，多判只是多脱敏一次 —— 隐私方向宁可多判。
    - 白名单兜住命名约定被改的情形（table_ → tbl_），且
      tests/rag/test_chunk_type_contract.py 会在改名时变红，而不是静默放行。
    """
    if not chunk_type:
        return False
    return chunk_type in TABLE_CHUNK_TYPES or chunk_type.startswith(TABLE_CHUNK_PREFIX)


def _split_table_node(node, sec, path: list, file_path: str) -> List[Document]:
    """表格节点双层切分（C2 通用化）——表级摘要(parent) + 行级 kv(leaf)。

    原本只有 FinancialTableChunkStrategy 有此能力，policy/product_spec 等
    走 StructureChunkStrategy 的类型遇到超长表格时被 RecursiveCharacterText
    Splitter 按字符硬切、行从中间切断。本 helper 抽出该逻辑供两类策略共用：
      - Layer 1: 表级摘要(parent) — 表名/行列数/列名概述，供语义检索
      - Layer 2: 行级 kv(leaf) — 每行一个 chunk，kv 格式供精确检索，
        metadata 含 numeric_values（规范化数值）支持按数值范围过滤
    表头规范化失败 → 整表作单个 chunk 兜底（不丢内容）。

    financial 特有的 reporting_period/fiscal_year/is_latest 不在此层——
    由 FinancialTableChunkStrategy 在调用后对返回 chunks 补元数据。
    """
    from backend.rag.preprocessing.financial_normalizer import extract_numeric_cells
    from backend.rag.preprocessing.parser._table_nl import (
        build_table_summary, detect_multi_level_header, normalize_table_rows,
        row_to_kv, table_to_csv,
    )

    section_anchor = ".".join(path)
    chunks: List[Document] = []
    _, source_header_rows, _ = detect_multi_level_header(node.rows)
    flat_header, data_rows = normalize_table_rows(node.rows)
    if not flat_header or not data_rows:
        # 表头规范化失败 → 整表作 chunk 兜底（不丢内容）。
        # P0-05：超大表兜底不能是单一巨型 chunk——超 LEAF 预算按行分片，
        # 每片保留表格身份（chunk_type/section），行列内容不丢。
        # P0 指标（2026-10-13）：兜底分片同样挂虚拟 parent（此前 parent 空，
        # 「命中 leaf → 扩 parent」对兜底表静默退化）。
        fallback_meta = {
            "section_path": list(path),
            "section_title": sec.text,
            "section_level": sec.level,
            "chunk_type": CHUNK_TYPE_TABLE_FALLBACK,
        }
        if count_tokens(node.text) <= LEAF_CHUNK_TOKENS:
            chunks.append(_make_doc(node.text, {
                "granularity": "leaf",
                "chunk_id": _chunk_id(file_path, f"leaf:{section_anchor}", node.text),
                "parent_chunk_id": "",
                "chunk_tokens": count_tokens(node.text),
                **fallback_meta,
            }))
            return _attach_virtual_parents(
                chunks, file_path, f"tblfb:{section_anchor}",
            )
        for si, sub in enumerate(_split_to_budget(
                node.text, LEAF_CHUNK_TOKENS, separators=["\n", " ", ""],
                overlap=0)):
            chunks.append(_make_doc(sub, {
                "granularity": "leaf",
                "chunk_id": _chunk_id(
                    file_path, f"leaf:{section_anchor}:fb:{si}", sub),
                "parent_chunk_id": "",
                "chunk_tokens": count_tokens(sub),
                **fallback_meta,
            }))
        # 兜底分片挂文档内虚拟 parent（表级摘要不可用时的一致退路）
        return _attach_virtual_parents(
            chunks, file_path, f"tblfb:{section_anchor}")

    # Layer 1: 表级摘要(parent)
    summary = build_table_summary(node.rows, section_title=sec.text)
    parent_id = _chunk_id(file_path, f"table_parent:{section_anchor}", summary)
    chunks.append(_make_doc(summary, {
        "granularity": "parent",
        "chunk_id": parent_id,
        "table_id": parent_id,
        "section_path": list(path),
        "section_title": sec.text,
        "section_level": sec.level,
        "chunk_type": CHUNK_TYPE_TABLE_SUMMARY,
        "row_count": len(data_rows),
        "col_count": len(flat_header),
        "chunk_tokens": count_tokens(summary),
    }))

    # Layer 2: 原始表头 Leaf 保留多级表头的每个来源行。
    for hi, header_row in enumerate(source_header_rows):
        header_text = table_to_csv([header_row])
        for si, header_part in enumerate(_hard_split_by_budget(
                header_text, LEAF_CHUNK_TOKENS)):
            if not header_part.strip():
                continue
            header_id = _chunk_id(
                file_path, f"table_header:{section_anchor}:{hi}:{si}",
                header_part,
            )
            chunks.append(_make_doc(header_part, {
                "granularity": "leaf",
                "chunk_id": header_id,
                "parent_chunk_id": parent_id,
                "table_id": parent_id,
                "section_path": list(path),
                "section_title": sec.text,
                "section_level": sec.level,
                "chunk_type": CHUNK_TYPE_TABLE_HEADER,
                "row_index": hi,
                "chunk_tokens": count_tokens(header_part),
            }))

    # Layer 3: 行级 kv + 原始行，保留自然语言召回及所有源单元格。
    for ri, row in enumerate(data_rows):
        kv_text = row_to_kv(flat_header, row, sec.text)
        raw_row_text = table_to_csv([row])
        row_text = "\n【原始行】\n".join(
            part for part in (kv_text, raw_row_text) if part
        )
        if not row_text.strip():
            continue
        row_parts = (
            _hard_split_by_budget(row_text, LEAF_CHUNK_TOKENS)
            if count_tokens(row_text) > LEAF_CHUNK_TOKENS
            else [row_text]
        )
        numeric_vals = extract_numeric_cells(flat_header, row)
        for part_index, row_part in enumerate(row_parts):
            leaf_id = _chunk_id(
                file_path,
                f"table_row:{section_anchor}:{ri}:{part_index}",
                row_part,
            )
            meta = {
                "granularity": "leaf",
                "chunk_id": leaf_id,
                "parent_chunk_id": parent_id,
                "table_id": parent_id,
                "section_path": list(path),
                "section_title": sec.text,
                "section_level": sec.level,
                "chunk_type": CHUNK_TYPE_TABLE_ROW,
                "row_index": ri,
                "row_part_index": part_index,
                "chunk_tokens": count_tokens(row_part),
            }
            if numeric_vals:
                meta["numeric_values"] = numeric_vals
            chunks.append(_make_doc(row_part, meta))
    return chunks


def _attach_virtual_parents(leaf_chunks: List[Document], file_path: str,
                            anchor: str, group_size: int | None = None) -> List[Document]:
    """为无结构产出的 leaf 挂虚拟 parent（C1 修复：Step/Legal/QA 的
    parent_chunk_id 此前恒空，「命中 leaf → 取 parent 扩上下文」对这三类
    文档静默退化）。

    Args:
        leaf_chunks: 策略产出的 leaf 列表（保持顺序）。
        anchor: chunk_id anchor 前缀（区分策略，避免与既有 id 撞车）。
        group_size: None = 全部 leaf 挂一个文档级 parent（Step/QA）；
            N = 每 N 个 leaf 一组各挂一个 parent（Legal 条款区间）。

    Returns:
        parent chunks（在前）+ 原 leaf 列表（parent_chunk_id 已回填）。
    """
    if not leaf_chunks:
        return leaf_chunks

    def _parent_text(group: List[Document]) -> tuple[str, bool]:
        lines = []
        for c in group:
            t = (c.metadata.get("section_title") or "").strip()
            if not t:
                t = c.page_content.strip().split("\n")[0][:40]
            if t:
                lines.append(t)
        text = "\n".join(lines) or "（无标题内容）"
        truncated = False
        # P0-05：QA 全部问题列表 / Legal 大区间标题在极端文档下会超 parent
        # 预算，构造超长 embedding 输入。parent 是辅助上下文，截断 + 打标
        # （parent_truncated）可观测，不静默。
        if count_tokens(text) > PARENT_CHUNK_TOKENS:
            truncated = True
            kept: list[str] = []
            used = 0
            for line in lines:
                t = count_tokens(line) + 1
                if kept and used + t > PARENT_CHUNK_TOKENS:
                    break
                kept.append(line)
                used += t
            text = "\n".join(kept) + "\n…（超出 parent 预算截断）"
        return text, truncated

    groups = ([leaf_chunks] if group_size is None else
              [leaf_chunks[i:i + group_size]
               for i in range(0, len(leaf_chunks), group_size)])

    out: List[Document] = []
    for gi, group in enumerate(groups):
        ptext, p_truncated = _parent_text(group)
        parent_id = _chunk_id(file_path, f"parent:{anchor}:{gi}", ptext)
        pmeta = {
            "granularity": "parent",
            "chunk_id": parent_id,
            "parent_chunk_id": "",
            "section_path": [],
            "section_title": (group[0].metadata.get("section_title") or "").strip(),
            "section_level": 1,
            "chunk_tokens": count_tokens(ptext),
        }
        if p_truncated:
            pmeta["parent_truncated"] = "true"
        out.append(_make_doc(ptext, pmeta))
        for c in group:
            c.metadata["parent_chunk_id"] = parent_id
        out.extend(group)
    return out


class StructureChunkStrategy:
    """结构化切分：每个 section → parent，section 内叶子 → leaf。

    生产加固（超长保护）：嵌套 section 的顶层 parent 用 _section_text
    全量 walk 拼接，可远超 PARENT_CHUNK_TOKENS，单个超大 parent 会构造
    超长 embedding 输入。section 文本超限时，叶子按 token 预算分组，
    每组产一个 parent（标题 + 组内叶子），leaf 关联其所属组。

    P0-02（2026-10-13）：根节点直接子叶（首个标题之前的正文）此前被
    _sections() 静默丢弃——本策略按「根引导块」产出：文档级 parent
    （标题 = 首个 H1 或空）+ 全部直接子叶。嵌套 section 的父 parent
    不再 walk 全量拼接（与子 section parent 重复入索引），只含直接子叶。
    """

    def split(self, ast: DocumentAST, file_path: str,
              exclude_qa: bool = False) -> List[Document]:
        chunks: List[Document] = []
        # 根引导块：首个标题之前的正文不再丢失
        root_leaves = [
            n for n in ast.root.children
            if n.type in LEAF_TYPES
            and not (exclude_qa and n.type in ("qa_question", "qa_answer"))
        ]
        if root_leaves:
            root_pseudo = DocumentNode(
                type="section", text=_doc_title(ast), level=0)
            self._emit_section_chunks(
                chunks, file_path, root_pseudo, [], root_leaves, anchor="root",
            )
        for sec, path in self._sections(ast):
            leaves = list(self._leaves(sec))
            if exclude_qa:
                leaves = [l for l in leaves
                          if l.type not in ("qa_question", "qa_answer")]
            if not leaves:
                # 纯容器 section（只有子章节无直接叶子）：子章节各自有
                # parent，此处不再产空 parent
                continue
            self._emit_section_chunks(
                chunks, file_path, sec, path, leaves,
                anchor=".".join(path),
            )
        return _enrich(chunks, file_path)

    def _emit_section_chunks(self, chunks: list, file_path: str, sec,
                             path: list, leaves: list, anchor: str) -> None:
        """一个 section 的叶子 → parent（标题+叶子组）+ leaf chunks。

        文本 ≤ PARENT 预算 → 单 parent；超限 → 叶子按预算分组，每组
        一个 parent（保持与旧实现的 chunk_id anchor 兼容：单组无 :gi 后缀）。
        parent 文本 = 标题 + 直接子叶：不再 walk 拼接嵌套 section 全文
        （旧实现与子 section parent 重复入索引，嵌套层级越深重复越多）。
        """
        table_leaves = [
            leaf for leaf in leaves
            if leaf.type == "table" and getattr(leaf, "rows", None)
        ]
        if table_leaves:
            plain_leaves = [leaf for leaf in leaves if leaf not in table_leaves]
            if plain_leaves:
                self._emit_section_chunks(
                    chunks, file_path, sec, path, plain_leaves, anchor,
                )
            for leaf in table_leaves:
                self._emit_leaf(
                    chunks, file_path, sec, path, leaf,
                    _chunk_id(
                        file_path, f"leaf:{anchor}", leaf.text,
                    ),
                    "",
                )
            return

        parts = ([sec.text] if sec.text else []) + [l.text for l in leaves]
        sec_text = "\n".join(parts)
        if count_tokens(sec_text) <= PARENT_CHUNK_TOKENS:
            parent_id = _chunk_id(file_path, f"parent:{anchor}", sec_text)
            self._emit_parent(chunks, file_path, sec, path, sec_text, parent_id)
            for leaf in leaves:
                self._emit_leaf(
                    chunks, file_path, sec, path, leaf,
                    _chunk_id(file_path, f"leaf:{anchor}", leaf.text),
                    parent_id,
                )
        else:
            # 超长 section：叶子按 PARENT_CHUNK_TOKENS 预算分组
            groups = self._group_leaves_by_tokens(leaves, PARENT_CHUNK_TOKENS)
            for gi, group in enumerate(groups):
                group_text = "\n".join([sec.text] + [l.text for l in group])
                parent_id = _chunk_id(
                    file_path, f"parent:{anchor}:{gi}", group_text,
                )
                self._emit_parent(
                    chunks, file_path, sec, path, group_text, parent_id,
                )
                for leaf in group:
                    self._emit_leaf(
                        chunks, file_path, sec, path, leaf,
                        _chunk_id(
                            file_path,
                            f"leaf:{anchor}:{gi}", leaf.text,
                        ),
                        parent_id,
                    )

    @staticmethod
    def _emit_parent(chunks: list, file_path: str, sec, path, text: str,
                     parent_id: str) -> None:
        """append 一个 parent chunk（粒度/元数据统一）。"""
        chunks.append(_make_doc(text, {
            "granularity": "parent",
            "chunk_id": parent_id,
            "section_path": path,
            "section_title": sec.text,
            "section_level": sec.level,
            "chunk_tokens": count_tokens(text),
        }))

    @staticmethod
    def _emit_leaf(chunks: list, file_path: str, sec, path, leaf,
                   leaf_id: str, parent_id: str) -> None:
        """append 一个 leaf chunk，parent_chunk_id 关联所属 parent。

        P1-7: 超长 leaf（> LEAF_CHUNK_TOKENS）用 RecursiveCharacterTextSplitter
        二次切分，保证单个 leaf 不超 token 预算（否则超大段落 leaf 会撑爆
        embedding 输入并稀释检索精度）。

        C2 通用化：表格 leaf（有 rows）走 _split_table_node 双层切分，
        不再被字符级硬切切断行——原先只有 financial 策略有此能力。
        """
        from langchain_text_splitters import RecursiveCharacterTextSplitter
        from backend.config import LEAF_CHUNK_TOKENS
        if leaf.type == "table" and getattr(leaf, "rows", None):
            chunks.extend(_split_table_node(leaf, sec, path, file_path))
            return
        leaf_text = leaf.text
        if count_tokens(leaf_text) > LEAF_CHUNK_TOKENS:
            # 超长源叶按原串连续切片，保留标点和数字边界，覆盖审计可逐字对账。
            subs = _hard_split_by_budget(leaf_text, LEAF_CHUNK_TOKENS)
        else:
            subs = [leaf_text]
        for si, sub in enumerate(subs):
            sub_id = _chunk_id(
                file_path, f"leaf:{'.'.join(path)}:{leaf_id}", sub,
            ) if len(subs) > 1 else leaf_id
            metadata = {
                "granularity": "leaf",
                "chunk_id": sub_id,
                "parent_chunk_id": parent_id,
                "section_path": path,
                "section_title": sec.text,
                "section_level": sec.level,
                "chunk_tokens": count_tokens(sub),
            }
            if len(subs) > 1:
                metadata["split_group_id"] = leaf_id
                metadata["split_part"] = si
                metadata["split_total"] = len(subs)
            chunks.append(_make_doc(sub, metadata))

    @staticmethod
    def _group_leaves_by_tokens(leaves, budget: int) -> list[list]:
        """叶子按累计 token 预算分组（保持顺序），每组 ≤ budget。

        单个叶子本身超过 budget（罕见，正常 leaf ≤ LEAF_CHUNK_TOKENS）
        会强制单独成组，保证不丢内容。
        """
        groups: list[list] = []
        cur: list = []
        cur_tokens = 0
        for leaf in leaves:
            t = count_tokens(leaf.text)
            if cur and cur_tokens + t > budget:
                groups.append(cur)
                cur = []
                cur_tokens = 0
            cur.append(leaf)
            cur_tokens += t
        if cur:
            groups.append(cur)
        return groups

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
        # 只产出直接子叶，避免嵌套层级下叶子在每个祖先 section 重复产出。
        # 相邻 qa_question+qa_answer 合并为一个伪叶子（"Q：...\nA：..."），
        # 防止问答在 Structure 路径下被拆成两个独立 leaf（问答分离回归）。
        children = section.children
        i = 0
        while i < len(children):
            n = children[i]
            if n.type in LEAF_TYPES:
                if (n.type == "qa_question"
                        and i + 1 < len(children)
                        and children[i + 1].type == "qa_answer"):
                    a = children[i + 1]
                    merged = DocumentNode(
                        type="qa_pair",
                        text=f"Q：{n.text}\nA：{a.text}",
                    )
                    yield merged
                    i += 2
                    continue
                yield n
            i += 1

    @staticmethod
    def _section_text(section: DocumentNode) -> str:
        parts = [section.text] + [n.text for n in walk(section) if n is not section and n.text]
        return "\n".join(parts)


def _split_unstructured(ast: DocumentAST, file_path: str,
                        splitter=None, separators: list[str] | None = None,
                        overlap: int | None = None) -> List[Document]:
    """Fixed/Recursive 共用的无结构切分循环（C3 上下文携带版）。

    与旧实现的差异：叶子合并时携带所属 section 标题，chunk 填
    section_title/section_path（此前恒空）；合并段跨多个 section 时
    取首个标题并打 section_mixed 标记。chunk_id 仍为内容派生（anchor="leaf"），
    不受影响。P0-05：超预算段统一走 _split_to_budget 严格达标；
    separators/overlap 缺省从 splitter 实例读取（兼容旧签名）。
    """
    if separators is None and splitter is not None:
        separators = list(getattr(splitter, "_separators", _SEPARATORS))
    if overlap is None and splitter is not None:
        overlap = int(getattr(splitter, "_chunk_overlap", CHUNK_OVERLAP))
    chunks: List[Document] = []
    leaf_items = list(_iter_leaves_with_section(ast))
    # 碎片化修复：小叶子先按 token 预算合并，再对超预算段切分
    # （_split_to_budget 统一严格达标收口，P0-05）
    for text, titles in _merge_small_with_section(leaf_items, LEAF_CHUNK_TOKENS):
        texts = (_split_to_budget(text, LEAF_CHUNK_TOKENS,
                                  separators=separators or _SEPARATORS,
                                  overlap=overlap)
                 if count_tokens(text) > LEAF_CHUNK_TOKENS else [text])
        split_group_id = (
            _chunk_id(file_path, "split:unstructured", text)
            if len(texts) > 1 else ""
        )
        for part_index, sub in enumerate(texts):
            meta = {
                "granularity": "leaf",
                "chunk_id": _chunk_id(file_path, "leaf", sub),
                "parent_chunk_id": "",
                "section_path": [titles[0]] if titles else [],
                "section_title": titles[0] if titles else "",
                "section_level": 1 if titles else 0,
                "chunk_tokens": count_tokens(sub),
            }
            if split_group_id:
                meta["split_group_id"] = split_group_id
                meta["split_part"] = part_index
                meta["split_total"] = len(texts)
            if len(titles) > 1:
                meta["section_mixed"] = "true"
            chunks.append(_make_doc(sub, meta))
    # C1 同构修复（2026-10-13）：无结构文档的 leaf 此前 parent_chunk_id
    # 恒空——「命中 leaf → 取 parent 扩上下文」对 Fixed/Recursive 路径
    # 静默退化。挂文档级虚拟 parent，与 Step/Legal/QA 策略同构。
    return _enrich(
        _attach_virtual_parents(chunks, file_path, "plain"), file_path)


class FixedSizeChunkStrategy:
    """固定长度切分：直接按 token 上限硬切 + overlap，不查分隔符。

    适用「普通文本、兜底」——无结构文本，无需递归查找分隔符。
    与 RecursiveChunkStrategy 的区别：递归优先在分隔符处切（保持句子完整），
    固定长度直接按 token 切（可能切在句子中间），更快更简单。
    """

    def split(self, ast: DocumentAST, file_path: str) -> List[Document]:
        # separators=[""] → 不查分隔符，直接按 chunk_size 字符级硬切
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=LEAF_CHUNK_TOKENS, chunk_overlap=CHUNK_OVERLAP,
            length_function=count_tokens, separators=[""],
        )
        return _split_unstructured(ast, file_path, splitter)


class RecursiveChunkStrategy:
    """递归切分兜底：把叶子文本按 token 上限递归切分（不做结构检测）。"""

    def split(self, ast: DocumentAST, file_path: str) -> List[Document]:
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=LEAF_CHUNK_TOKENS, chunk_overlap=CHUNK_OVERLAP,
            length_function=count_tokens, separators=_SEPARATORS,
        )
        return _split_unstructured(ast, file_path, splitter)


class StepChunkStrategy:
    """步骤切分（SOP/培训）：按章节切分，每个章节一个语义单元。

    章节标题（一、二、三、）由 DocxParser 识别为 section 节点；
    本策略按 section 切分，章节标题 + 内容合并成一个 chunk。
    无 section 结构（flat 旧数据）时，回退按「一、二、三」文本切分。

    P0-02：section 之外的根直接叶子（章节之间的正文）不再丢失。
    P0-05：超长章节按 token 预算继续切分（子块保留章节身份），
    不再产出单一巨型 leaf。
    """

    def split(self, ast: DocumentAST, file_path: str) -> List[Document]:
        sections = [
            n for n in walk(ast.root)
            if n.type == "section" and n.level > 0
        ]
        if sections:
            return self._split_by_sections(sections, file_path, ast)
        return self._split_by_text(ast, file_path)

    def _split_by_sections(
        self, sections: list[DocumentNode], file_path: str,
        ast: DocumentAST | None = None,
    ) -> List[Document]:
        chunks: List[Document] = []
        # P0-02：章节之间的根直接叶子（引导段/插注）照常保留
        if ast is not None:
            root_leaves = [
                n for n in ast.root.children if n.type in LEAF_TYPES
            ]
            if root_leaves:
                fallback_title = _doc_title(ast)
                chunks.extend(_make_leaf_chunks_bounded(
                    [n.text for n in root_leaves], file_path,
                    section_title=fallback_title, section_level=1,
                    section_path=[fallback_title] if fallback_title else [],
                ))
        for sec in sections:
            # P1-9: 章节标题继承到 chunk（section_title/section_level/section_path）
            # P0-05: 超长章节继续切分，子块保留章节身份
            chunks.extend(_make_leaf_chunks_bounded(
                [self._section_text(sec)], file_path,
                section_title=sec.text, section_level=sec.level,
                section_path=[sec.text],
            ))
        # C1: 文档级虚拟 parent，修复 SOP/培训 leaf 无 parent_chunk_id
        return _enrich(_attach_virtual_parents(chunks, file_path, "step"), file_path)

    def _split_by_text(self, ast: DocumentAST, file_path: str) -> List[Document]:
        """无 section 结构（flat）→ 按「一、二、三」文本切分。"""
        chunks: List[Document] = []
        current: list[str] = []
        current_title = ""
        fallback_title = _doc_title(ast)
        for n in walk(ast.root):
            if n.type not in LEAF_TYPES:
                continue
            if _is_section_heading(n.text) and current:
                chunks.extend(_make_leaf_chunks_bounded(
                    current, file_path,
                    section_title=current_title or fallback_title, section_level=1,
                    section_path=[current_title or fallback_title] if (current_title or fallback_title) else [],
                ))
                current = []
            if _is_section_heading(n.text):
                current_title = n.text
            current.append(n.text)
        if current:
            chunks.extend(_make_leaf_chunks_bounded(
                current, file_path,
                section_title=current_title or fallback_title, section_level=1,
                section_path=[current_title or fallback_title] if (current_title or fallback_title) else [],
            ))
        # C1: 文档级虚拟 parent（flat 旧数据分支）
        return _enrich(_attach_virtual_parents(chunks, file_path, "step_flat"), file_path)

    @staticmethod
    def _section_text(section: DocumentNode) -> str:
        parts = [section.text] + [
            n.text for n in walk(section) if n is not section and n.text
        ]
        return "\n".join(parts)


class LegalChunkStrategy:
    """合同条款切分（legal/contract_template）：按「第 N 条」条款编号切分。

    条款编号作为 chunk 边界，条款内容合并成一个 chunk，
    保证「一个条款一个语义单元」。
    """

    def split(self, ast: DocumentAST, file_path: str) -> List[Document]:
        # 无条款降级：内容找不到「第 N 条」边界时，旧实现会把全文合成
        # 单一巨型 chunk（误分类为 legal 的流程/制度文档尤其受害）。
        # 有章节结构 → Structure 切分；否则 Recursive 兜底，不静默堆大 chunk。
        has_clause = any(
            n.type in LEAF_TYPES and _is_legal_clause(n.text)
            for n in walk(ast.root)
        )
        if not has_clause:
            has_sections = any(
                n.type == "section" and n.level > 0
                for n in walk(ast.root)
            )
            logger.info(
                "[LegalChunk] 无「第 N 条」条款编号 → "
                + ("Structure 结构切分降级" if has_sections else "Recursive 兜底")
            )
            if has_sections:
                return StructureChunkStrategy().split(ast, file_path)
            return RecursiveChunkStrategy().split(ast, file_path)

        chunks: List[Document] = []
        current: list[str] = []
        current_clause = ""
        fallback_title = _doc_title(ast)

        for n in walk(ast.root):
            if n.type not in LEAF_TYPES:
                continue
            if _is_legal_clause(n.text) and current:
                # 新条款 → flush 之前的 chunk（P1-9: 条款编号作为 section_title；
                # P0-05: 超长条款继续切分，子块保留条款身份）
                chunks.extend(_make_leaf_chunks_bounded(
                    current, file_path,
                    section_title=current_clause or fallback_title, section_level=1,
                    section_path=[current_clause or fallback_title] if (current_clause or fallback_title) else [],
                    preserve_source=True,
                ))
                current = []
            if _is_legal_clause(n.text):
                current_clause = n.text
            current.append(n.text)

        if current:
            chunks.extend(_make_leaf_chunks_bounded(
                current, file_path,
                section_title=current_clause or fallback_title, section_level=1,
                section_path=[current_clause or fallback_title] if (current_clause or fallback_title) else [],
                preserve_source=True,
            ))

        # C1: 每连续 N 条款挂一个虚拟 parent（默认 5，LEGAL_CLAUSES_PER_PARENT），
        # 修复 legal/contract_template leaf 无 parent_chunk_id 的检索退化
        from backend.config import LEGAL_CLAUSES_PER_PARENT
        return _enrich(
            _attach_virtual_parents(chunks, file_path, "legal",
                                    group_size=max(LEGAL_CLAUSES_PER_PARENT, 1)),
            file_path,
        )


class QAChunkStrategy:
    """FAQ 切分：每个 qa_question/qa_answer 对 → 一个 chunk。

    P1-6: 原实现将 question 与 answer 各自独立成 chunk，导致问答分离
    （检索命中问题 chunk 时答案缺失）。现合并为"Q：...\nA：..."单一 chunk，
    保证一个 FAQ 条目完整表达语义。

    P0-02（2026-10-13）：parser 改局部 QA 标注后，AST 中会同时存在
    qa 节点与普通章节/段落——本策略不再丢弃非 QA 内容：普通叶子按
    Structure 语义切分（section → parent + leaf），QA 对照常成 chunk。
    P0-05：长答案不再产单一巨型 chunk——A 部分超预算时继续切分，
    每个子块保留 Q 前缀（FAQ 条目语义完整）。
    """

    def split(self, ast: DocumentAST, file_path: str) -> List[Document]:
        chunks: List[Document] = self._qa_chunks(ast, file_path)
        # 混合文档：非 QA 的 section/叶子照常产出（P0-02 不丢内容）。
        # 纯 FAQ 文档（全树只有 qa 节点）无普通叶子，跳过。
        if any(n.type not in ("qa_question", "qa_answer") for n in walk(ast.root)):
            chunks.extend(self._non_qa_chunks(ast, file_path))
        # C1: 文档级虚拟 parent（parent 文本=全部问题列表，便于 parent 级语义匹配）
        return _enrich(_attach_virtual_parents(chunks, file_path, "faq"), file_path)

    def _qa_chunks(self, ast: DocumentAST, file_path: str) -> List[Document]:
        nodes = [
            n for n in walk(ast.root)
            if n.type in ("qa_question", "qa_answer")
        ]
        chunks: List[Document] = []
        i = 0
        while i < len(nodes):
            n = nodes[i]
            if n.type == "qa_question":
                q_text = n.text
                a_text = ""
                # 合并紧跟的 qa_answer（parser 按 Q、A 顺序产出节点）
                if i + 1 < len(nodes) and nodes[i + 1].type == "qa_answer":
                    a_text = nodes[i + 1].text
                    i += 2
                else:
                    i += 1
                full = f"Q：{q_text}\nA：{a_text}" if a_text else q_text
                # P0-05：超预算 → A 部分继续切，每个子块带 Q 前缀
                if count_tokens(full) > LEAF_CHUNK_TOKENS and a_text:
                    chunks.extend(self._split_long_qa(
                        file_path, q_text, a_text))
                else:
                    chunks.append(_make_doc(full, {
                        "granularity": "leaf",
                        "chunk_id": _chunk_id(file_path, "leaf", full),
                        "parent_chunk_id": "",
                        "section_path": [],
                        "section_title": q_text[:40],
                        "section_level": 0,
                        "chunk_tokens": count_tokens(full),
                    }))
            else:
                # 孤立的 qa_answer（无配对问题）也保留，不丢内容
                chunks.extend(_make_leaf_chunks_bounded(
                    [n.text], file_path,
                    section_title=n.text[:40], section_level=0,
                ))
                i += 1
        return chunks

    @staticmethod
    def _split_long_qa(file_path: str, q_text: str, a_text: str) -> List[Document]:
        prefix = f"Q：{q_text}\nA："
        budget = LEAF_CHUNK_TOKENS - count_tokens(prefix)
        if budget < 50:
            # 问题本身超长：整条按预算切（退化为普通文本切分）
            return _make_leaf_chunks_bounded(
                [f"Q：{q_text}\nA：{a_text}"], file_path,
                section_title=q_text[:40], section_level=0,
            )
        subs = _split_to_budget(a_text, budget)
        chunks: List[Document] = []
        split_group_id = _chunk_id(file_path, f"split:qa:{q_text}", a_text)
        for si, sub in enumerate(subs):
            text = prefix + sub
            chunks.append(_make_doc(text, {
                "granularity": "leaf",
                "chunk_id": _chunk_id(file_path, f"leaf:qa:{si}", text),
                "parent_chunk_id": "",
                "section_path": [],
                "section_title": q_text[:40],
                "section_level": 0,
                "chunk_tokens": count_tokens(text),
                "split_group_id": split_group_id,
                "split_part": si,
            }))
        return chunks

    def _non_qa_chunks(self, ast: DocumentAST, file_path: str) -> List[Document]:
        """混合文档的普通部分：有 section 结构走 Structure，否则按叶子合并。

        qa 节点已由 _qa_chunks 产出：有 section 时 exclude_qa=True 复用
        Structure；无结构时按文本排除 qa 叶子后再合并，避免重复入索引。
        """
        has_sections = any(
            n.type == "section" and n.level > 0 for n in walk(ast.root)
        )
        if has_sections:
            return StructureChunkStrategy().split(ast, file_path, exclude_qa=True)
        qa_texts = {
            n.text for n in walk(ast.root)
            if n.type in ("qa_question", "qa_answer")
        }
        leaf_items = [
            (text, title)
            for text, title in _iter_leaves_with_section(ast)
            if text not in qa_texts
        ]
        chunks: List[Document] = []
        for text, titles in _merge_small_with_section(leaf_items, LEAF_CHUNK_TOKENS):
            chunks.extend(_make_leaf_chunks_bounded(
                [text], file_path,
                section_title=titles[0] if titles else "",
                section_level=1 if titles else 0,
                section_path=[titles[0]] if titles else [],
            ))
        return chunks


class SemanticChunkStrategy:
    """语义切分：基于 embedding 相似度检测主题边界。

    适用于无结构长文档——RecursiveChunkStrategy 按固定 token 硬切 + overlap
    会把完整信息单元切碎、稀释。本策略在相邻句子的语义相似度骤降处切分，
    保持 chunk 语义连贯。

    可靠性：embedding 调用失败时降级 RecursiveChunkStrategy，不静默吞异常。
    """

    def split(self, ast: DocumentAST, file_path: str) -> List[Document]:
        from backend.rag.embedding_singleton import get_embedding
        return self._split_with_embedding(ast, file_path, get_embedding())

    @staticmethod
    def _embed_sentences_batched(
        sentences: list[str], embedding,
    ) -> list[list[float]] | None:
        """分批 embedding + 每批重试；全部批次失败返回 None（调用方降级）。

        生产加固：整篇句子一次性 embed_documents 会构造超大输入，
        且任一失败即整体降级丢失已算批次。分批后单批失败可重试
        （对齐 indexer 主路径 _embed_with_retry 的 EMBED_RETRY_MAX 模式），
        批次之间相互独立，避免可用句子的向量被一次失败拖垮。
        """
        from backend.config import SEMANTIC_EMBED_BATCH_SIZE, SEMANTIC_EMBED_RETRY
        # 批大小与 indexer 主路径同口径：云端 embedding 声明的
        # embed_batch_size（DashScope 单请求上限）取 min，
        # 避免外层大批在 OpenAIEmbeddings 内部再拆、白多 RTT
        _declared = getattr(embedding, "embed_batch_size", None)
        batch_size = (
            min(SEMANTIC_EMBED_BATCH_SIZE, _declared)
            if isinstance(_declared, int) and _declared > 0
            else SEMANTIC_EMBED_BATCH_SIZE
        )
        vecs: list[list[float]] = []
        for i in range(0, len(sentences), batch_size):
            batch = sentences[i:i + batch_size]
            batch_no = i // batch_size
            for attempt in range(SEMANTIC_EMBED_RETRY):
                try:
                    vecs.extend(embedding.embed_documents(batch))
                    break
                except Exception as e:
                    if attempt == SEMANTIC_EMBED_RETRY - 1:
                        logger.warning(
                            f"[SemanticChunk] 句子批次 {batch_no} 嵌入失败 "
                            f"{SEMANTIC_EMBED_RETRY} 次({type(e).__name__})，"
                            f"整体降级递归切分: {e}"
                        )
                        return None
                    logger.debug(
                        f"[SemanticChunk] 句子批次 {batch_no} 重试 "
                        f"{attempt + 1}/{SEMANTIC_EMBED_RETRY}: {e}"
                    )
        return vecs

    def _split_with_embedding(
        self, ast: DocumentAST, file_path: str, embedding,
    ) -> List[Document]:
        from backend.config import SEMANTIC_SIMILARITY_THRESHOLD

        chunks: List[Document] = []
        # C4 三项修补：
        #   1. 碎片合并——小叶子先按 token 预算合并（复用 C3 的
        #      _merge_small_with_section），不再逐一成 chunk；
        #   2. 句子重组保留原标点（_split_sentences_keep_punct，此前 ！？；
        #      全部被 "。" 覆盖）；
        #   3. 边界查找 O(n) 预计算（原 next() 最坏 O(n²)）。
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=LEAF_CHUNK_TOKENS, chunk_overlap=CHUNK_OVERLAP,
            length_function=count_tokens, separators=_SEPARATORS,
        )
        leaf_items = list(_iter_leaves_with_section(ast))
        for text, titles in _merge_small_with_section(leaf_items, LEAF_CHUNK_TOKENS):
            sentences = _split_sentences_keep_punct(text)
            if len(sentences) <= 1:
                chunks.append(_make_doc(
                    text, self._leaf_meta(file_path, text, titles),
                ))
                continue
            try:
                vecs = self._embed_sentences_batched(sentences, embedding)
            except Exception as e:
                # 外层防御：批处理逻辑自身异常也降级，不静默吞掉
                logger.warning(
                    f"[SemanticChunk] embedding 调用异常({type(e).__name__})，"
                    f"降级递归切分: {e}"
                )
                return RecursiveChunkStrategy().split(ast, file_path)
            if vecs is None:
                # 全部分批失败 → 降级递归切分（可观测：上面已 warning）
                return RecursiveChunkStrategy().split(ast, file_path)
            starts = _detect_boundaries(sentences, vecs, SEMANTIC_SIMILARITY_THRESHOLD)

            # O(n) 预计算每个 start 的区间终点：next_start[i] = min{j>i: starts[j]}，无则 len
            n = len(sentences)
            next_start = [n] * n
            last = n
            for i in range(n - 1, -1, -1):
                next_start[i] = last
                if starts[i]:
                    last = i

            for i, is_start in enumerate(starts):
                if not is_start:
                    continue
                end = next_start[i]
                seg_text = "".join(sentences[i:end])
                # 超长 chunk（无边界可切时）兜底递归切分，避免超大 chunk
                if count_tokens(seg_text) > LEAF_CHUNK_TOKENS:
                    for sub in splitter.split_text(seg_text):
                        chunks.append(_make_doc(sub, self._leaf_meta(file_path, sub, titles)))
                else:
                    chunks.append(_make_doc(seg_text, self._leaf_meta(file_path, seg_text, titles)))

        logger.info(
            f"[SemanticChunk] {file_path} 语义切分产出 {len(chunks)} 个 chunk"
        )
        return _enrich(chunks, file_path)

    @staticmethod
    def _leaf_meta(file_path: str, text: str, titles: list[str] | None = None) -> dict:
        title = (titles[0] if titles else "") or ""
        meta = {
            "granularity": "leaf",
            "chunk_id": _chunk_id(file_path, "leaf", text),
            "parent_chunk_id": "",
            "section_path": [title] if title else [],
            "section_title": title,
            "section_level": 1 if title else 0,
            "chunk_tokens": count_tokens(text),
        }
        if titles and len(titles) > 1:
            meta["section_mixed"] = "true"
        return meta


class FinancialTableChunkStrategy:
    """财务表格专用切分：表级摘要(parent) + 行级 kv(leaf) 双层索引。

    替代 StructureChunkStrategy 对 financial 文档的处理。原策略把整张表
    当作一个 leaf chunk，表格超过 LEAF_CHUNK_TOKENS 时被
    RecursiveCharacterTextSplitter 按字符硬切，会把一行财务数据从中间切断。

    本策略按行自然边界拆分：
      - Layer 1: 表级摘要(parent) — NL 描述表名/行列数/列名，供语义检索
      - Layer 2: 行级 kv(leaf) — 每行一个 chunk，kv 格式供精确数值检索
    行级 chunk 通过 parent_chunk_id 关联表级摘要，metadata 含
    numeric_values（规范化数值）支持按数值范围过滤。

    非表格 leaf（段落等）走 StructureChunkStrategy 逻辑。
    """

    def split(self, ast: DocumentAST, file_path: str) -> List[Document]:
        from backend.config import FINANCIAL_TABLE_ROWS_PER_CHUNK

        chunks: List[Document] = []
        # 根引导块（P0-02 同构，2026-10-13）：首个标题前的直接子叶
        # （含 PDF 表格-标题混排时挂 root 的表格）不再静默丢失
        root_leaves = [n for n in ast.root.children if n.type in LEAF_TYPES]
        if root_leaves:
            root_pseudo = DocumentNode(
                type="section", text=_doc_title(ast), level=0,
                children=list(root_leaves))
            has_table = any(
                n.type == "table" and n.rows for n in root_leaves)
            if has_table:
                chunks.extend(self._split_with_tables(
                    root_pseudo, [], file_path, FINANCIAL_TABLE_ROWS_PER_CHUNK,
                ))
                non_table = [
                    n for n in root_leaves
                    if not (n.type == "table" and n.rows)
                ]
            else:
                non_table = root_leaves
            if non_table:
                self._emit_plain_group(
                    chunks, file_path, root_pseudo, [], non_table,
                )
        for sec, path in self._sections(ast):
            leaves = list(self._leaves(sec))
            section_anchor = ".".join(path)
            has_table = any(n.type == "table" and n.rows for n in leaves)

            if not has_table:
                # 无表格 → 走原 StructureChunkStrategy 逻辑
                sec_text = StructureChunkStrategy._section_text(sec)
                if count_tokens(sec_text) <= PARENT_CHUNK_TOKENS:
                    parent_id = _chunk_id(file_path, f"parent:{section_anchor}", sec_text)
                    StructureChunkStrategy._emit_parent(chunks, file_path, sec, path, sec_text, parent_id)
                    for leaf in leaves:
                        StructureChunkStrategy._emit_leaf(chunks, file_path, sec, path, leaf,
                            _chunk_id(file_path, f"leaf:{section_anchor}", leaf.text), parent_id)
                else:
                    groups = StructureChunkStrategy._group_leaves_by_tokens(leaves, PARENT_CHUNK_TOKENS)
                    for gi, group in enumerate(groups):
                        group_text = "\n".join([sec.text] + [leaf.text for leaf in group])
                        parent_id = _chunk_id(file_path, f"parent:{section_anchor}:{gi}", group_text)
                        StructureChunkStrategy._emit_parent(chunks, file_path, sec, path, group_text, parent_id)
                        for leaf in group:
                            StructureChunkStrategy._emit_leaf(chunks, file_path, sec, path, leaf,
                                _chunk_id(file_path, f"leaf:{section_anchor}:{gi}", leaf.text), parent_id)
                continue

            # 有表格 → 财务表格专用切分
            chunks.extend(self._split_with_tables(
                sec, path, file_path, FINANCIAL_TABLE_ROWS_PER_CHUNK,
            ))
        # 财务行级切分产出多，走独立上限（此前 FINANCIAL_MAX_CHUNKS_PER_DOC
        # 定义了但从未被使用，财务文档被通用 5000 上限截断，与配置意图相悖）
        from backend.config import FINANCIAL_MAX_CHUNKS_PER_DOC
        return _enrich(chunks, file_path, max_chunks=FINANCIAL_MAX_CHUNKS_PER_DOC)

    @staticmethod
    def _emit_plain_group(chunks: list, file_path: str, sec, path: list,
                          leaves: list) -> None:
        """无表格叶子组 → Structure 语义的 parent + leaf（根引导块用）。"""
        sec_text = "\n".join(
            ([sec.text] if sec.text else []) + [l.text for l in leaves])
        anchor = ".".join(path)
        if count_tokens(sec_text) <= PARENT_CHUNK_TOKENS:
            parent_id = _chunk_id(file_path, f"parent:{anchor}", sec_text)
            StructureChunkStrategy._emit_parent(
                chunks, file_path, sec, path, sec_text, parent_id)
            for leaf in leaves:
                StructureChunkStrategy._emit_leaf(
                    chunks, file_path, sec, path, leaf,
                    _chunk_id(file_path, f"leaf:{anchor}", leaf.text), parent_id)
        else:
            split_group_id = _chunk_id(
                file_path, f"split:financial:{anchor}", sec_text,
            )
            parent_groups = []
            for gi, sub in enumerate(_hard_split_by_budget(
                    sec_text, PARENT_CHUNK_TOKENS)):
                parent_id = _chunk_id(
                    file_path, f"parent:{anchor}:{gi}", sub)
                leaf_texts = _hard_split_by_budget(sub, LEAF_CHUNK_TOKENS)
                parent_groups.append((gi, sub, parent_id, leaf_texts))

            split_total = sum(len(group[3]) for group in parent_groups)
            split_part = 0
            for gi, sub, parent_id, leaf_texts in parent_groups:
                StructureChunkStrategy._emit_parent(
                    chunks, file_path, sec, path, sub, parent_id)
                # 根正文超 Parent 预算时也必须保留可检索 Leaf；每个 Leaf
                # 归属当前 Parent，并再次按 Leaf 预算切分，避免 Parent 被
                # 写入全文但实际索引侧无内容。
                for li, leaf_text in enumerate(leaf_texts):
                    chunks.append(_make_doc(leaf_text, {
                        "granularity": "leaf",
                        "chunk_id": _chunk_id(
                            file_path, f"leaf:{anchor}:{gi}:{li}", leaf_text,
                        ),
                        "parent_chunk_id": parent_id,
                        "section_path": path,
                        "section_title": sec.text,
                        "section_level": sec.level,
                        "chunk_tokens": count_tokens(leaf_text),
                        "split_group_id": split_group_id,
                        "split_part": split_part,
                        "split_total": split_total,
                    }))
                    split_part += 1

    def _split_with_tables(
        self, sec, path: list, file_path: str, rows_per_chunk: int,
    ) -> List[Document]:
        """处理含表格的 section：表格走双层切分（共用 helper），非表格走原逻辑。

        C2 重构：表格双层切分逻辑抽至模块级 _split_table_node，与
        StructureChunkStrategy 共用（消除重复，行为不变）。本方法仅保留
        financial 特有的 reporting_period/fiscal_year/is_latest 元数据补丁。
        """
        from backend.rag.preprocessing.financial_normalizer import (
            extract_reporting_period,
        )

        chunks: List[Document] = []
        section_anchor = ".".join(path)

        # 提取报告期用于版本快照（financial 文档保留历史版本向量）
        reporting_period, fiscal_year = extract_reporting_period(
            file_path, sec.text,
        )

        for leaf in self._leaves(sec):
            if leaf.type == "table" and leaf.rows:
                # ── 表格双层切分（共享 helper）──
                table_chunks = _split_table_node(leaf, sec, path, file_path)
                if reporting_period:
                    for c in table_chunks:
                        if is_table_chunk(c.metadata.get("chunk_type")):
                            c.metadata["reporting_period"] = reporting_period
                            c.metadata["fiscal_year"] = fiscal_year
                            c.metadata["is_latest"] = True
                chunks.extend(table_chunks)
            else:
                # ── 混合 section 中的正文继续使用结构策略的有界 Parent/Leaf。 ──
                self._emit_plain_group(
                    chunks, file_path, sec, path, [leaf],
                )
        return chunks

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
        for n in section.children:
            if n.type in LEAF_TYPES:
                yield n


STRUCTURE_STRATEGIES = {
    "policy": StructureChunkStrategy,
    "compliance": StructureChunkStrategy,
    "security": StructureChunkStrategy,
    "financial": FinancialTableChunkStrategy,
    "customer_data": StructureChunkStrategy,
    "product_spec": StructureChunkStrategy,
    "listing": StructureChunkStrategy,
    "sop": StepChunkStrategy,
    "training": StepChunkStrategy,
    "legal": LegalChunkStrategy,
    "contract_template": LegalChunkStrategy,
    "faq": QAChunkStrategy,
    "ad_policy": FixedSizeChunkStrategy,
    # M3：旅游攻略——POI 小节「一店一 chunk」，标题感知切分（文档规范
    # docs/travel-rag-doc-spec.md 的 ### 每 POI 一节 ≤500 token 与此配合）
    "travel_guide": StructureChunkStrategy,
}


class ChunkRouteDecision:
    """Router 可审计决策记录（P1，2026-10-13）。

    回答「这份文档为什么用这个策略」：输入结构信号、业务类型、最终策略、
    策略版本、降级原因与处理范围。随 qc 进质量记录与 Trace，供管理端
    与评测归因。字段演进保持向后兼容（新增带默认值）。
    """

    def __init__(self, *, doc_type: str, completeness: float,
                 strategy: str, strategy_version: str,
                 signals: dict, degrade_reason: str = "",
                 threshold: float = 0.0, scope: str = ""):
        self.doc_type = doc_type
        self.completeness = completeness
        self.strategy = strategy
        self.strategy_version = strategy_version
        self.signals = signals
        self.degrade_reason = degrade_reason
        self.threshold = threshold
        self.scope = scope

    def as_dict(self) -> dict:
        return {
            "doc_type": self.doc_type,
            "completeness": self.completeness,
            "strategy": self.strategy,
            "strategy_version": self.strategy_version,
            "signals": self.signals,
            "degrade_reason": self.degrade_reason,
            "threshold": self.threshold,
            "scope": self.scope,
        }


# 策略版本表（决策记录元数据，集中维护）：切分行为变更的类递增——
# v2 = 2026-10-13 批次1（根引导块/覆盖补全/严格 Token 上限/局部 QA 配对）。
_STRATEGY_VERSIONS = {
    "StructureChunkStrategy": "v2",
    "FixedSizeChunkStrategy": "v2",
    "RecursiveChunkStrategy": "v2",
    "StepChunkStrategy": "v2",
    "LegalChunkStrategy": "v2",
    "QAChunkStrategy": "v2",
    "FinancialTableChunkStrategy": "v2",
    "SemanticChunkStrategy": "v1",
}

# 强信号豁免阈值（P1 Structure Profile）：强结构信号不因整篇完整度
# 低于 STRUCTURE_COMPLETE_THRESHOLD 失效。信号计数的下限防误判——
# 普通文本中偶现的「第N条」/单条 Q/A 不足以支撑整篇按该结构切。
_SIGNAL_MIN_BY_FIELD = {
    "legal_clause_count": 3,   # ≥3 条「第N条」→ Legal 策略豁免完整度阈值
    "qa_pair_count": 1,        # qa 节点即强信号（parser 局部配对已保证配对可信）
    "step_heading_count": 3,   # ≥3 个「一、二、三、」标题 → Step 策略豁免
}

# doc_type → 豁免信号名（profile 字段）
_STRONG_SIGNAL_FIELD = {
    "legal": "legal_clause_count",
    "contract_template": "legal_clause_count",
    "faq": "qa_pair_count",
    "sop": "step_heading_count",
    "training": "step_heading_count",
}


class ChunkStrategyRouter:
    """双轴路由：文档类型 × 结构完整度 → 策略。优先级 Structure > Semantic > Recursive。

    P1（2026-10-13）：
    - 强信号豁免：doc_type 与 AST 强结构信号同时成立时，不因整篇完整度
      低于阈值降级 Recursive（此前合同/FAQ/SOP 强结构文档偶因完整度
      0.6x 被整篇递归硬切）。
    - route_with_decision 输出可审计决策记录（signals/策略版本/降级原因/
      处理范围），route() 保持旧签名兼容。
    """

    ROUTER_VERSION = "v2"

    def route(self, doc_type: str, report: StructureReport):
        return self.route_with_decision(doc_type, report)[0]

    def route_with_decision(self, doc_type: str, report: StructureReport):
        from backend.config import (
            ENABLE_SEMANTIC_CHUNKING, SEMANTIC_CHUNK_MIN_TOKENS,
            STRUCTURE_COMPLETE_THRESHOLD,
        )
        profile = getattr(report, "profile", None)
        signals = profile.as_dict() if profile is not None else {}

        def _decision(cls, degrade_reason: str = "", scope: str = ""):
            return cls(), ChunkRouteDecision(
                doc_type=doc_type,
                completeness=report.completeness,
                strategy=cls.__name__,
                strategy_version=_STRATEGY_VERSIONS.get(cls.__name__, "v1"),
                signals=signals,
                degrade_reason=degrade_reason,
                threshold=STRUCTURE_COMPLETE_THRESHOLD,
                scope=scope,
            )

        # ── 强信号豁免：完整度不足但 doc_type 与 AST 强信号同时成立 ──
        if not report.is_complete:
            field_name = _STRONG_SIGNAL_FIELD.get(doc_type)
            min_required = (
                _SIGNAL_MIN_BY_FIELD.get(field_name, 1) if field_name else 0
            )
            if field_name and signals.get(field_name, 0) >= min_required:
                cls = STRUCTURE_STRATEGIES.get(doc_type, RecursiveChunkStrategy)
                # 豁免前仍做资格兜底：legal 声明但条款信号不足时降级
                if doc_type in ("legal", "contract_template") and not _has_legal_clauses(report.ast):
                    has_sections = _ast_has_sections(report.ast)
                    fallback = StructureChunkStrategy if has_sections else RecursiveChunkStrategy
                    logger.info(
                        f"[Router] {doc_type} 完整度不足且无「第 N 条」条款 → "
                        + ("Structure 降级" if has_sections else "Recursive 兜底"))
                    return _decision(
                        fallback,
                        degrade_reason="legal_without_clauses",
                        scope="全篇" if not has_sections else "按章节",
                    )
                logger.info(
                    f"[Router] {doc_type} 完整度 {report.completeness} 低于阈值，"
                    f"但强结构信号成立（{field_name}={signals.get(field_name)}）"
                    f"→ {cls.__name__} 豁免"
                )
                return _decision(
                    cls,
                    degrade_reason="strong_signal_override",
                    scope=f"强信号豁免（{field_name}={signals.get(field_name)}）",
                )

            # 无专用类型策略的表格文档仍走既有 Structure 策略，
            # 以表格行而非整段 CSV 文本作为可索引 Leaf。
            if _has_table_nodes(report.ast):
                return _decision(
                    StructureChunkStrategy,
                    degrade_reason="table_structure_fallback",
                    scope="表格行级切分",
                )

            # Phase 3：Semantic 语义切分（无结构长文档，基于 embedding 主题边界）
            if ENABLE_SEMANTIC_CHUNKING and count_tokens(report.ast.raw_text) >= SEMANTIC_CHUNK_MIN_TOKENS:
                logger.info("[Router] 无结构长文档 → Semantic 语义切分")
                return _decision(
                    SemanticChunkStrategy,
                    degrade_reason="incomplete_structure_semantic",
                    scope="全篇按语义边界",
                )

            # （4.5 清理：原 "Phase 2 LLM Assisted" 空壳分支已删除——
            #   ENABLE_LLM_CHUNKING 从未有实现体，分支只打日志不产出策略，
            #   属死代码。若未来要做 LLM 辅助切分，重新实现时再引入开关。）
            logger.info(
                f"[Router] 完整度 {report.completeness} 低于阈值且无强信号"
                f"→ Recursive 兜底")
            return _decision(
                RecursiveChunkStrategy,
                degrade_reason=report.deficit_signal or "incomplete_structure",
                scope="全篇递归切分",
            )

        # ── 完整结构：doc_type 指向策略的资格校验 ──
        # has_sections 从 AST 实时判断（report 可能由调用方手工构造、
        # profile 缺省；profile 只服务豁免信号，不作为资格判定事实源）
        if doc_type in ("legal", "contract_template") and not _has_legal_clauses(report.ast):
            has_sections = _ast_has_sections(report.ast)
            fallback = StructureChunkStrategy if has_sections else RecursiveChunkStrategy
            logger.info(
                f"[Router] {doc_type} 但 AST 无「第 N 条」条款 → "
                + ("Structure 降级" if has_sections else "Recursive 兜底")
            )
            return _decision(
                fallback,
                degrade_reason="legal_without_clauses",
                scope="按章节" if has_sections else "全篇",
            )

        if doc_type == "faq" and not _has_qa_nodes(report.ast):
            logger.info("[Router] faq 但 AST 无 qa 节点 → Recursive 兜底")
            return _decision(
                RecursiveChunkStrategy,
                degrade_reason="faq_without_qa_nodes",
                scope="全篇递归切分",
            )

        cls = STRUCTURE_STRATEGIES.get(doc_type, RecursiveChunkStrategy)
        if cls is RecursiveChunkStrategy and _has_table_nodes(report.ast):
            return _decision(
                StructureChunkStrategy,
                degrade_reason="table_structure_fallback",
                scope="表格行级切分",
            )
        if cls is RecursiveChunkStrategy:
            return _decision(
                cls,
                degrade_reason="doc_type_unmapped",
                scope="全篇递归切分",
            )
        return _decision(cls)


def coverage_audit(ast: DocumentAST, chunks: List[Document],
                   max_audit_nodes: int = 2000,
                   normalize_pii: bool = False) -> dict:
    """源节点 → 输出 Leaf Chunk 覆盖对账（P0-02，2026-10-13）。

    策略产出的 chunk 应覆盖 AST 全部可索引叶子；本审计用双向文本包含
    匹配（与 stamp_chunk_pages 同口径）找出未覆盖叶子：
      - 叶子前缀 ⊆ 某 chunk 文本 → 被合并覆盖
      - 某 chunk 核心 ⊆ 叶子全文 → 被切片覆盖
      - 两者皆否 → uncovered（策略静默丢内容的信号，进入 quality_issues）

    超大文档（叶子或 chunk 超 max_audit_nodes）抽样审计并标 sampled=true，
    防止 O(n²) 子串匹配拖垮索引主流程。审计失败不抛异常——返回
    {"error": ...}，安全网自身不能成为新的失败点。
    """
    def _normalize(text: str, mask_pii: bool = False) -> str:
        value = str(text or "")
        if mask_pii:
            # 只为实际标记脱敏的 Leaf 生成对照文本；未脱敏 Leaf 必须与原文
            # 对账，不能因其他 Leaf 脱敏而把它缺失的数字误当成已覆盖。
            from backend.rag.preprocessing.filter import ChunkFilter

            value = ChunkFilter.apply_pii_mask(value)
        return " ".join(value.split()).casefold()

    def _windows(text: str) -> list[str]:
        if len(text) <= 120:
            return [text] if text else []
        stride = 80
        windows = [text[i:i + 120] for i in range(0, len(text), stride)]
        if windows and len(windows[-1]) < 40:
            windows[-1] = text[-120:]
        return windows

    try:
        source_leaves = [
            n for n in walk(ast.root)
            if n.type in LEAF_TYPES and (
                (n.text or "").strip()
                or (n.type == "table" and any(
                    str(cell).strip()
                    for row in (n.rows or []) for cell in row
                    if cell is not None
                ))
            )
        ]
        # Parent 只供上下文扩展，绝不能计入可索引内容覆盖。
        bodies = [
            (
                _normalize(
                    c.page_content,
                    mask_pii=bool(normalize_pii and (
                        getattr(c, "metadata", {}) or {}
                    ).get("pii_masked")),
                ),
                bool(normalize_pii and (
                    getattr(c, "metadata", {}) or {}
                ).get("pii_masked")),
            )
            for c in chunks
            if (getattr(c, "page_content", None)
                and getattr(c, "metadata", {}).get("granularity") == "leaf")
        ]
        split_entries: dict[
            tuple[bool, str], list[tuple[int, int, str]]
        ] = {}
        for chunk in chunks:
            metadata = getattr(chunk, "metadata", {}) or {}
            group_id = metadata.get("split_group_id")
            part_index = metadata.get("split_part")
            part_total = metadata.get("split_total")
            if (
                metadata.get("granularity") != "leaf"
                or not group_id
                or not isinstance(part_index, int)
                or not isinstance(part_total, int)
                or part_total <= 1
                or not getattr(chunk, "page_content", None)
            ):
                continue
            is_masked = bool(normalize_pii and metadata.get("pii_masked"))
            split_entries.setdefault((is_masked, str(group_id)), []).append(
                (part_index, part_total, chunk.page_content)
            )
        split_bodies: dict[bool, list[str]] = {False: [], True: []}
        for (is_masked, _), entries in split_entries.items():
            totals = {part_total for _, part_total, _ in entries}
            if len(totals) != 1:
                continue
            part_total = next(iter(totals))
            if (
                len(entries) != part_total
                or sorted(index for index, _, _ in entries)
                != list(range(part_total))
            ):
                continue
            combined = "".join(
                content for _, _, content in sorted(entries)
            )
            split_bodies[is_masked].append(
                _normalize(combined, mask_pii=is_masked)
            )
        row_leaf_bodies: dict[tuple[str, int, str, bool], list[str]] = {}
        for chunk in chunks:
            metadata = getattr(chunk, "metadata", {}) or {}
            if (
                metadata.get("granularity") != "leaf"
                or metadata.get("chunk_type") not in (
                    CHUNK_TYPE_TABLE_ROW, CHUNK_TYPE_TABLE_HEADER,
                )
                or metadata.get("table_id") is None
                or metadata.get("row_index") is None
                or not getattr(chunk, "page_content", None)
            ):
                continue
            row_key = (
                str(metadata["table_id"]), int(metadata["row_index"]),
                str(metadata.get("chunk_type")),
                bool(normalize_pii and metadata.get("pii_masked")),
            )
            row_leaf_bodies.setdefault(row_key, []).append(
                _normalize(
                    chunk.page_content,
                    mask_pii=row_key[3],
                )
            )
        row_bodies = [
            ("".join(parts), key[3])
            for key, parts in row_leaf_bodies.items()
        ]

        max_audit_nodes = max(1, int(max_audit_nodes))
        sampled = len(source_leaves) > max_audit_nodes or len(bodies) > max_audit_nodes
        if len(source_leaves) > max_audit_nodes:
            stride = max(1, math.ceil(len(source_leaves) / max_audit_nodes))
            leaves = source_leaves[::stride][:max_audit_nodes]
        else:
            leaves = source_leaves

        def _suffix_prefix_overlap(left: str, right: str) -> int:
            """返回左串后缀与右串前缀的最长重合长度。"""
            limit = min(len(left), len(right))
            if not limit:
                return 0
            pattern = right[:limit]
            sequence = pattern + "\0" + left[-limit:]
            prefix = [0] * len(sequence)
            for index in range(1, len(sequence)):
                matched = prefix[index - 1]
                while matched and sequence[index] != sequence[matched]:
                    matched = prefix[matched - 1]
                if sequence[index] == sequence[matched]:
                    matched += 1
                prefix[index] = matched
            return min(prefix[-1], limit)

        boundary_bodies: dict[bool, list[str]] = {False: [], True: []}
        max_body_length = max((len(body) for body, _ in bodies), default=120)
        previous_masked: bool | None = None
        stitched_tail = ""
        for body, is_masked in bodies:
            if is_masked != previous_masked:
                stitched_tail = body
                previous_masked = is_masked
                continue
            overlap = _suffix_prefix_overlap(stitched_tail, body)
            boundary_bodies[is_masked].append(
                stitched_tail[-120:] + body[overlap:overlap + 120]
            )
            stitched_tail = (
                stitched_tail + body[overlap:]
            )[-max_body_length:]

        def _covered_text(text: str) -> bool:
            raw_probes = _windows(_normalize(text))
            masked_probes = (
                _windows(_normalize(text, mask_pii=True))
                if normalize_pii else []
            )
            if not raw_probes:
                return False

            def _all_probes_covered(probes: list[str], masked: bool) -> bool:
                candidates = [
                    body for body, is_masked in bodies if is_masked == masked
                ]
                candidates.extend(boundary_bodies[masked])
                candidates.extend(split_bodies[masked])
                return all(
                    any(probe in body for body in candidates)
                    for probe in probes
                )

            raw_covered = _all_probes_covered(raw_probes, masked=False)
            masked_covered = bool(masked_probes) and _all_probes_covered(
                masked_probes, masked=True,
            )
            return raw_covered or masked_covered

        covered = 0
        uncovered: list[dict] = []
        table_rows_total = 0
        table_rows_uncovered = 0
        table_cells_total = 0
        table_cells_uncovered = 0

        for node in leaves:
            text = (node.text or "").strip()
            rows = getattr(node, "rows", None) if node.type == "table" else None
            if not rows:
                hit = _covered_text(text)
                if hit:
                    covered += 1
                else:
                    uncovered.append({"type": node.type, "preview": text[:80]})
                continue

            # 行级和单元格级核对；双层财务切分会改写表格文本为 kv，
            # 但保留每个非空单元格，故 Parent/表级摘要不能代替行数据。
            source_row_counts: dict[tuple[str, ...], int] = {}
            row_cells_by_signature: dict[tuple[str, ...], list[str]] = {}
            for row in rows:
                cells = [
                    str(cell) for cell in row
                    if cell is not None and str(cell).strip()
                ]
                if not cells:
                    continue
                signature = tuple(_normalize(cell) for cell in cells)
                source_row_counts[signature] = source_row_counts.get(signature, 0) + 1
                row_cells_by_signature[signature] = cells
                table_rows_total += 1
                table_cells_total += len(cells)

            missing_rows = 0
            missing_cells = 0
            for signature, required_count in source_row_counts.items():
                raw_cells = row_cells_by_signature[signature]
                # 每个带源行身份的 Leaf 只计一次；不能把同一个 Leaf 同时
                # 从原始 bodies 与拼接行 bodies 加入两次，掩盖重复源行缺失。
                matching_rows = [
                    (body, body_is_masked)
                    for body, body_is_masked in row_bodies
                    if all(
                        _normalize(cell, mask_pii=body_is_masked) in body
                        for cell in raw_cells
                    )
                ]
                if matching_rows:
                    matching_bodies = len(matching_rows)
                else:
                    # 无源行身份的 fallback 表格（例如仅有一条数据的 Sheet）
                    # 仍按实际输出 Leaf 检查，不能被其他表格的行块遮蔽。
                    table_bodies = [
                        body for body, body_is_masked in bodies
                        if not body_is_masked
                    ]
                    matching_bodies = sum(
                        1 for body in table_bodies
                        if all(
                            _normalize(cell) in body for cell in raw_cells
                        )
                    )
                if matching_bodies < required_count:
                    missing_rows += required_count - matching_bodies
                for cell in raw_cells:
                    if not any(
                        _normalize(cell, mask_pii=body_is_masked) in body
                        for body, body_is_masked in bodies
                    ) and not any(
                        _normalize(cell, mask_pii=body_is_masked) in body
                        for body, body_is_masked in row_bodies
                    ):
                        missing_cells += 1

            table_rows_uncovered += missing_rows
            table_cells_uncovered += missing_cells
            if not missing_rows and not missing_cells and source_row_counts:
                covered += 1
            else:
                uncovered.append({
                    "type": node.type,
                    "preview": text[:80],
                    "missing_rows": missing_rows,
                    "missing_cells": missing_cells,
                })

        audited_count = len(leaves)
        coverage_rate = (
            None if sampled or not source_leaves
            else covered / len(source_leaves)
        )
        return {
            "leaf_total": len(source_leaves),
            "audited_leaf_count": audited_count,
            "covered": covered,
            "uncovered_count": len(uncovered),
            "uncovered_samples": uncovered[:10],
            "table_rows_total": table_rows_total,
            "table_rows_uncovered": table_rows_uncovered,
            "table_cells_total": table_cells_total,
            "table_cells_uncovered": table_cells_uncovered,
            "coverage_rate": coverage_rate,
            "sampled": sampled,
            "pii_mask_normalized": normalize_pii,
        }
    except Exception as e:  # noqa: BLE001 — QualityGate 将错误按硬异常处理
        logger.warning(f"[CoverageAudit] 失败: {type(e).__name__}: {e}")
        return {
            "error": f"{type(e).__name__}: {e}",
            "leaf_total": 0,
            "audited_leaf_count": 0,
            "covered": 0,
            "uncovered_count": 0,
            "uncovered_samples": [],
            "coverage_rate": None,
            "sampled": False,
            "pii_mask_normalized": normalize_pii,
        }
