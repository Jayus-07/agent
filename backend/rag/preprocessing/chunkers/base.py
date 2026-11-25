"""base.py — 通用切分算法、AST 遍历与公共元数据注入工具。

提供字符级/Token级硬切兜底、分句边界检测、文档结构节点嗅探及 Leaf 构造合并工具。
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import sys
from typing import TYPE_CHECKING, List

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

import backend.config as _cfg
from backend.rag.preprocessing.ast import DocumentAST, DocumentNode, LEAF_TYPES, walk
from backend.rag.preprocessing.redaction import mask_sensitive_text
from backend.rag.preprocessing.token_counter import count_tokens
from backend.shared.logger import logger

# Token 预算常量（优先读取测试 monkeypatch 或运行时动态配置）
LEAF_CHUNK_TOKENS = _cfg.LEAF_CHUNK_TOKENS
PARENT_CHUNK_TOKENS = _cfg.PARENT_CHUNK_TOKENS
CHUNK_OVERLAP = _cfg.CHUNK_OVERLAP


def get_leaf_chunk_tokens() -> int:
    """动态获取叶子 Token 预算（支持测试猴子补丁或外部动态注入）。"""
    mod = sys.modules.get("backend.rag.preprocessing.chunking")
    if mod is not None:
        val = getattr(mod, "LEAF_CHUNK_TOKENS", None)
        if val is not None and val != _cfg.LEAF_CHUNK_TOKENS:
            return val
    if LEAF_CHUNK_TOKENS != _cfg.LEAF_CHUNK_TOKENS:
        return LEAF_CHUNK_TOKENS
    if mod is not None and hasattr(mod, "LEAF_CHUNK_TOKENS"):
        return getattr(mod, "LEAF_CHUNK_TOKENS")
    return _cfg.LEAF_CHUNK_TOKENS


def get_parent_chunk_tokens() -> int:
    """动态获取父级 Token 预算。"""
    mod = sys.modules.get("backend.rag.preprocessing.chunking")
    if mod is not None:
        val = getattr(mod, "PARENT_CHUNK_TOKENS", None)
        if val is not None and val != _cfg.PARENT_CHUNK_TOKENS:
            return val
    if PARENT_CHUNK_TOKENS != _cfg.PARENT_CHUNK_TOKENS:
        return PARENT_CHUNK_TOKENS
    if mod is not None and hasattr(mod, "PARENT_CHUNK_TOKENS"):
        return getattr(mod, "PARENT_CHUNK_TOKENS")
    return _cfg.PARENT_CHUNK_TOKENS


def get_chunk_overlap() -> int:
    """动态获取切分重叠预算。"""
    mod = sys.modules.get("backend.rag.preprocessing.chunking")
    if mod is not None:
        val = getattr(mod, "CHUNK_OVERLAP", None)
        if val is not None and val != _cfg.CHUNK_OVERLAP:
            return val
    if CHUNK_OVERLAP != _cfg.CHUNK_OVERLAP:
        return CHUNK_OVERLAP
    if mod is not None and hasattr(mod, "CHUNK_OVERLAP"):
        return getattr(mod, "CHUNK_OVERLAP")
    return _cfg.CHUNK_OVERLAP


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
    effective_overlap = get_chunk_overlap() if overlap is None else overlap
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=budget,
        chunk_overlap=effective_overlap,
        length_function=count_tokens,
        separators=separators if separators is not None else _SEPARATORS,
        # 分块边界空格/换行属于源文本；移除它会让 Coverage Audit 将有效文档
        # 判成缺口，也会改变英文词间隔。保留分隔符，由后续索引过滤处理空白。
        strip_whitespace=False,
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


def _doc_title_node(ast: DocumentAST) -> DocumentNode | None:
    return ast.document_title_node


def _doc_title(ast: DocumentAST) -> str:
    """读取解析器明确识别的文档标题，不从章节标题推断。"""
    return ast.document_title.strip()


def _section_ancestor_titles(ast: DocumentAST, target: DocumentNode) -> list[str]:
    """返回条款 section 的章节祖先标题，保持 AST 中的层级顺序。"""
    document_title_node = _doc_title_node(ast)

    def _find(node: DocumentNode, path: list[str]) -> list[str] | None:
        for child in node.children:
            if child.type != "section":
                continue
            if child is target:
                return path
            next_path = path
            if child.level > 0 and child is not document_title_node and child.text.strip():
                next_path = path + [child.text.strip()]
            found = _find(child, next_path)
            if found is not None:
                return found
        return None

    return _find(ast.root, []) or []


def _iter_leaf_nodes_with_section_path(ast: DocumentAST):
    """按文档顺序遍历叶子，并保留其章节祖先路径。"""
    document_title_node = _doc_title_node(ast)

    def _visit(node: DocumentNode, path: list[str]):
        for child in node.children:
            if child.type == "section":
                next_path = path
                if (
                    child.level > 0
                    and child is not document_title_node
                    and child.text.strip()
                ):
                    next_path = path + [child.text.strip()]
                yield from _visit(child, next_path)
            elif child.type in LEAF_TYPES:
                yield child, path
            elif child.children:
                yield from _visit(child, path)

    yield from _visit(ast.root, [])


def _is_section_heading(text: str) -> bool:
    """判断文本是否中文编号章节标题（一、二、三、）。"""
    return bool(_SECTION_HEADING_RE.match(text.strip()))


# 法律条款编号（第 N 条），用于 legal 文档的条款级切分
_LEGAL_CLAUSE_RE = re.compile(r"^第[零〇两一二三四五六七八九十百千万\d]+条")


def _is_legal_clause(text: str) -> bool:
    """判断文本是否法律条款编号（第一条 / 第2条 / 第十二条）。"""
    return bool(_LEGAL_CLAUSE_RE.match(text.strip()))


def _has_legal_clauses(ast: DocumentAST) -> bool:
    """AST 中是否存在「第 N 条」法律条款编号。"""
    return any(
        (n.type in LEAF_TYPES or n.type == "section")
        and _is_legal_clause(n.text)
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
                     section_path: list | None = None,
                     chunk_type: str = "") -> Document:
    """合并文本列表成一个 leaf chunk（Step/Legal 等按编号切分的策略共用）。

    chunk_id 由合并内容派生（幂等）：同内容重复切分产出相同 id。
    P1-9: 支持注入 section_title/section_level/section_path（Step/Legal 策略
    此前产出空标题，检索上下文弱）。
    """
    text = "\n".join(texts)
    metadata = {
        "granularity": "leaf",
        "chunk_id": _chunk_id(file_path, "leaf", text),
        "parent_chunk_id": "",
        "section_path": list(section_path or []),
        "section_title": section_title,
        "section_level": section_level,
        "chunk_tokens": count_tokens(text),
    }
    if chunk_type:
        metadata["chunk_type"] = chunk_type
    return _make_doc(text, metadata)


def _make_leaf_chunks_bounded(texts: list[str], file_path: str,
                              section_title: str = "", section_level: int = 0,
                              section_path: list | None = None,
                              budget: int | None = None,
                              preserve_source: bool = False,
                              chunk_type: str = "") -> List[Document]:
    """合并文本为 leaf，超 token 预算时继续切分，每个子块保留节点身份（P0-05）。

    背景：Step/Legal/QA 策略此前把「一个章节/条款/问答」直接合并成单一
    leaf 且无上限检查——超长 SOP 单步骤或合同条款会产出几千甚至几万
    token 的巨型 chunk，撑爆 embedding 输入并稀释检索精度。
    切分不改变身份：section_title/section_path 原样带到每个子块，
    chunk_id 仍按子块内容派生（幂等）。
    """
    if budget is None:
        budget = get_leaf_chunk_tokens()
    text = "\n".join(texts)
    if count_tokens(text) <= budget:
        return [_make_leaf_chunk(
            texts, file_path, section_title, section_level, section_path,
            chunk_type=chunk_type,
        )]
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
        if chunk_type:
            metadata["chunk_type"] = chunk_type
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


def _iter_leaves_with_section(ast: DocumentAST):
    """遍历叶子并携带其所属 section 标题（C3 上下文携带）。

    leaf 的 section_title = 最近的祖先 section（level>0）标题；
    section 容器外的叶子标题为空串。
    """
    document_title_node = _doc_title_node(ast)

    def _dfs(node: DocumentNode, title: str):
        for child in node.children:
            if child.type in LEAF_TYPES:
                yield child.text, title
            elif child.type == "section" and child.level > 0:
                next_title = (
                    title if child is document_title_node else child.text
                )
                yield from _dfs(child, next_title)
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
