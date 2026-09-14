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


def _has_qa_nodes(ast: DocumentAST) -> bool:
    """AST 中是否存在 qa_question / qa_answer 节点。"""
    return any(
        n.type in ("qa_question", "qa_answer")
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
        build_table_summary, normalize_table_rows, row_to_kv,
    )

    section_anchor = ".".join(path)
    chunks: List[Document] = []
    flat_header, data_rows = normalize_table_rows(node.rows)
    if not flat_header or not data_rows:
        # 表头规范化失败 → 整表作一个 chunk 兜底
        chunks.append(_make_doc(node.text, {
            "granularity": "leaf",
            "chunk_id": _chunk_id(file_path, f"leaf:{section_anchor}", node.text),
            "parent_chunk_id": "",
            "section_path": list(path),
            "section_title": sec.text,
            "section_level": sec.level,
            "chunk_type": "table_fallback",
            "chunk_tokens": count_tokens(node.text),
        }))
        return chunks

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
        "chunk_type": "table_summary",
        "row_count": len(data_rows),
        "col_count": len(flat_header),
        "chunk_tokens": count_tokens(summary),
    }))

    # Layer 2: 行级 kv chunks(leaf) — 按行自然边界，不硬切
    for ri, row in enumerate(data_rows):
        kv_text = row_to_kv(flat_header, row, sec.text)
        if not kv_text.strip():
            continue
        leaf_id = _chunk_id(file_path, f"table_row:{section_anchor}:{ri}", kv_text)
        # 提取数值单元格到 metadata，支持按数值范围检索
        meta = {
            "granularity": "leaf",
            "chunk_id": leaf_id,
            "parent_chunk_id": parent_id,
            "table_id": parent_id,
            "section_path": list(path),
            "section_title": sec.text,
            "section_level": sec.level,
            "chunk_type": "table_row",
            "row_index": ri,
            "chunk_tokens": count_tokens(kv_text),
        }
        numeric_vals = extract_numeric_cells(flat_header, row)
        if numeric_vals:
            meta["numeric_values"] = numeric_vals
        chunks.append(_make_doc(kv_text, meta))
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

    def _parent_text(group: List[Document]) -> str:
        lines = []
        for c in group:
            t = (c.metadata.get("section_title") or "").strip()
            if not t:
                t = c.page_content.strip().split("\n")[0][:40]
            if t:
                lines.append(t)
        return "\n".join(lines) or "（无标题内容）"

    groups = ([leaf_chunks] if group_size is None else
              [leaf_chunks[i:i + group_size]
               for i in range(0, len(leaf_chunks), group_size)])

    out: List[Document] = []
    for gi, group in enumerate(groups):
        ptext = _parent_text(group)
        parent_id = _chunk_id(file_path, f"parent:{anchor}:{gi}", ptext)
        out.append(_make_doc(ptext, {
            "granularity": "parent",
            "chunk_id": parent_id,
            "parent_chunk_id": "",
            "section_path": [],
            "section_title": (group[0].metadata.get("section_title") or "").strip(),
            "section_level": 1,
            "chunk_tokens": count_tokens(ptext),
        }))
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
    """

    def split(self, ast: DocumentAST, file_path: str) -> List[Document]:
        chunks: List[Document] = []
        for sec, path in self._sections(ast):
            leaves = list(self._leaves(sec))
            section_anchor = ".".join(path)
            sec_text = self._section_text(sec)
            if count_tokens(sec_text) <= PARENT_CHUNK_TOKENS:
                # 常规：一个 section → 一个 parent（全貌）+ 直接子叶
                parent_id = _chunk_id(
                    file_path, f"parent:{section_anchor}", sec_text,
                )
                self._emit_parent(chunks, file_path, sec, path, sec_text, parent_id)
                for leaf in leaves:
                    self._emit_leaf(
                        chunks, file_path, sec, path, leaf,
                        _chunk_id(file_path, f"leaf:{section_anchor}", leaf.text),
                        parent_id,
                    )
            else:
                # 超长 section：叶子按 PARENT_CHUNK_TOKENS 预算分组
                groups = self._group_leaves_by_tokens(leaves, PARENT_CHUNK_TOKENS)
                for gi, group in enumerate(groups):
                    group_text = "\n".join([sec.text] + [l.text for l in group])
                    parent_id = _chunk_id(
                        file_path, f"parent:{section_anchor}:{gi}", group_text,
                    )
                    self._emit_parent(
                        chunks, file_path, sec, path, group_text, parent_id,
                    )
                    for leaf in group:
                        self._emit_leaf(
                            chunks, file_path, sec, path, leaf,
                            _chunk_id(
                                file_path,
                                f"leaf:{section_anchor}:{gi}", leaf.text,
                            ),
                            parent_id,
                        )
        return _enrich(chunks, file_path)

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
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=LEAF_CHUNK_TOKENS, chunk_overlap=CHUNK_OVERLAP,
                length_function=count_tokens, separators=_SEPARATORS,
            )
            subs = splitter.split_text(leaf_text)
        else:
            subs = [leaf_text]
        for si, sub in enumerate(subs):
            sub_id = _chunk_id(
                file_path, f"leaf:{'.'.join(path)}:{leaf_id}", sub,
            ) if len(subs) > 1 else leaf_id
            chunks.append(_make_doc(sub, {
                "granularity": "leaf",
                "chunk_id": sub_id,
                "parent_chunk_id": parent_id,
                "section_path": path,
                "section_title": sec.text,
                "section_level": sec.level,
                "chunk_tokens": count_tokens(sub),
            }))

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
        # 只产出直接子叶，避免嵌套层级下叶子在每个祖先 section 重复产出
        for n in section.children:
            if n.type in LEAF_TYPES:
                yield n

    @staticmethod
    def _section_text(section: DocumentNode) -> str:
        parts = [section.text] + [n.text for n in walk(section) if n is not section and n.text]
        return "\n".join(parts)


def _split_unstructured(ast: DocumentAST, file_path: str,
                        splitter) -> List[Document]:
    """Fixed/Recursive 共用的无结构切分循环（C3 上下文携带版）。

    与旧实现的差异：叶子合并时携带所属 section 标题，chunk 填
    section_title/section_path（此前恒空）；合并段跨多个 section 时
    取首个标题并打 section_mixed 标记。chunk_id 仍为内容派生（anchor="leaf"），
    不受影响。
    """
    chunks: List[Document] = []
    leaf_items = list(_iter_leaves_with_section(ast))
    # 碎片化修复：小叶子先按 token 预算合并，再对超预算段切分
    for text, titles in _merge_small_with_section(leaf_items, LEAF_CHUNK_TOKENS):
        texts = (splitter.split_text(text)
                 if count_tokens(text) > LEAF_CHUNK_TOKENS else [text])
        for sub in texts:
            meta = {
                "granularity": "leaf",
                "chunk_id": _chunk_id(file_path, "leaf", sub),
                "parent_chunk_id": "",
                "section_path": [titles[0]] if titles else [],
                "section_title": titles[0] if titles else "",
                "section_level": 1 if titles else 0,
                "chunk_tokens": count_tokens(sub),
            }
            if len(titles) > 1:
                meta["section_mixed"] = "true"
            chunks.append(_make_doc(sub, meta))
    return _enrich(chunks, file_path)


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
    """

    def split(self, ast: DocumentAST, file_path: str) -> List[Document]:
        sections = [
            n for n in walk(ast.root)
            if n.type == "section" and n.level > 0
        ]
        if sections:
            return self._split_by_sections(sections, file_path)
        return self._split_by_text(ast, file_path)

    def _split_by_sections(
        self, sections: list[DocumentNode], file_path: str,
    ) -> List[Document]:
        chunks: List[Document] = []
        for sec in sections:
            # P1-9: 章节标题继承到 chunk（section_title/section_level/section_path）
            chunks.append(_make_leaf_chunk(
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
                chunks.append(_make_leaf_chunk(
                    current, file_path,
                    section_title=current_title or fallback_title, section_level=1,
                    section_path=[current_title or fallback_title] if (current_title or fallback_title) else [],
                ))
                current = []
            if _is_section_heading(n.text):
                current_title = n.text
            current.append(n.text)
        if current:
            chunks.append(_make_leaf_chunk(
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
                # 新条款 → flush 之前的 chunk（P1-9: 条款编号作为 section_title）
                chunks.append(_make_leaf_chunk(
                    current, file_path,
                    section_title=current_clause or fallback_title, section_level=1,
                    section_path=[current_clause or fallback_title] if (current_clause or fallback_title) else [],
                ))
                current = []
            if _is_legal_clause(n.text):
                current_clause = n.text
            current.append(n.text)

        if current:
            chunks.append(_make_leaf_chunk(
                current, file_path,
                section_title=current_clause or fallback_title, section_level=1,
                section_path=[current_clause or fallback_title] if (current_clause or fallback_title) else [],
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
    """

    def split(self, ast: DocumentAST, file_path: str) -> List[Document]:
        chunks: List[Document] = []
        nodes = [
            n for n in walk(ast.root)
            if n.type in ("qa_question", "qa_answer")
        ]
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
                text = f"Q：{q_text}\nA：{a_text}" if a_text else q_text
                chunks.append(_make_doc(text, {
                    "granularity": "leaf",
                    "chunk_id": _chunk_id(file_path, "leaf", text),
                    "parent_chunk_id": "",
                    "section_path": [],
                    "section_title": q_text[:40],
                    "section_level": 0,
                    "chunk_tokens": count_tokens(text),
                }))
            else:
                # 孤立的 qa_answer（无配对问题）也保留，不丢内容
                chunks.append(_make_doc(n.text, {
                    "granularity": "leaf",
                    "chunk_id": _chunk_id(file_path, "leaf", n.text),
                    "parent_chunk_id": "",
                    "section_path": [],
                    "section_title": n.text[:40],
                    "section_level": 0,
                    "chunk_tokens": count_tokens(n.text),
                }))
                i += 1
        # C1: 文档级虚拟 parent（parent 文本=全部问题列表，便于 parent 级语义匹配）
        return _enrich(_attach_virtual_parents(chunks, file_path, "faq"), file_path)


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
                        if c.metadata.get("chunk_type") in (
                            "table_summary", "table_row", "table_fallback",
                        ):
                            c.metadata["reporting_period"] = reporting_period
                            c.metadata["fiscal_year"] = fiscal_year
                            c.metadata["is_latest"] = True
                chunks.extend(table_chunks)
            else:
                # ── 非表格 leaf：走原 StructureChunkStrategy 逻辑 ──
                leaf_text = leaf.text
                if count_tokens(leaf_text) > LEAF_CHUNK_TOKENS:
                    # 行级表格文档的非表格段落同样不设 overlap：相邻段落
                    # 语义独立，overlap 只会制造重复向量污染数值检索
                    splitter = RecursiveCharacterTextSplitter(
                        chunk_size=LEAF_CHUNK_TOKENS, chunk_overlap=0,
                        length_function=count_tokens, separators=_SEPARATORS,
                    )
                    subs = splitter.split_text(leaf_text)
                else:
                    subs = [leaf_text]
                parent_id = _chunk_id(file_path, f"parent:{section_anchor}", sec.text)
                for si, sub in enumerate(subs):
                    sub_id = (_chunk_id(file_path, f"leaf:{section_anchor}:{si}", sub)
                              if len(subs) > 1
                              else _chunk_id(file_path, f"leaf:{section_anchor}", sub))
                    chunks.append(_make_doc(sub, {
                        "granularity": "leaf",
                        "chunk_id": sub_id,
                        "parent_chunk_id": parent_id,
                        "section_path": path,
                        "section_title": sec.text,
                        "section_level": sec.level,
                        "chunk_tokens": count_tokens(sub),
                    }))
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
}


class ChunkStrategyRouter:
    """双轴路由：文档类型 × 结构完整度 → 策略。优先级 Structure > LLM > Semantic > Recursive。"""

    def route(self, doc_type: str, report: StructureReport):
        from backend.config import (
            ENABLE_SEMANTIC_CHUNKING, SEMANTIC_CHUNK_MIN_TOKENS,
        )

        if report.is_complete:
            # 资格校验：doc_type 指向的策略需要 AST 中存在对应结构特征，
            # 否则降级（文件名分类与 parser 实际结构可能不一致）。
            if doc_type in ("legal", "contract_template") and not _has_legal_clauses(report.ast):
                has_sections = any(
                    n.type == "section" and n.level > 0
                    for n in walk(report.ast.root)
                )
                fallback = StructureChunkStrategy if has_sections else RecursiveChunkStrategy
                logger.info(
                    f"[Router] {doc_type} 但 AST 无「第 N 条」条款 → "
                    + ("Structure 降级" if has_sections else "Recursive 兜底")
                )
                return fallback()

            if doc_type == "faq" and not _has_qa_nodes(report.ast):
                logger.info("[Router] faq 但 AST 无 qa 节点 → Recursive 兜底")
                return RecursiveChunkStrategy()

            cls = STRUCTURE_STRATEGIES.get(doc_type, RecursiveChunkStrategy)
            return cls()

        # Phase 3：Semantic 语义切分（无结构长文档，基于 embedding 主题边界）
        if ENABLE_SEMANTIC_CHUNKING and count_tokens(report.ast.raw_text) >= SEMANTIC_CHUNK_MIN_TOKENS:
            logger.info("[Router] 无结构长文档 → Semantic 语义切分")
            return SemanticChunkStrategy()

        # （4.5 清理：原 "Phase 2 LLM Assisted" 空壳分支已删除——
        #   ENABLE_LLM_CHUNKING 从未有实现体，分支只打日志不产出策略，
        #   属死代码。若未来要做 LLM 辅助切分，重新实现时再引入开关。）
        return RecursiveChunkStrategy()
