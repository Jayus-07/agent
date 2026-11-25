"""structure.py — 结构化切分策略（StructureChunkStrategy）与无结构切分（_split_unstructured）。

每个 section → parent，section 内叶子 → leaf。支持嵌套章节防膨胀、超长分组、
根引导块保留及表格双层切分嵌入。
"""
from __future__ import annotations

from typing import List

from langchain_core.documents import Document

from backend.rag.preprocessing.ast import (
    DocumentAST, DocumentNode, LEAF_TYPES, walk,
)
from backend.rag.preprocessing.chunkers.base import (
    _SEPARATORS, _chunk_id, _doc_title, _doc_title_node, _enrich,
    _hard_split_by_budget, _iter_leaves_with_section, _make_doc,
    _merge_small_with_section, _split_to_budget, count_tokens,
    get_chunk_overlap, get_leaf_chunk_tokens, get_parent_chunk_tokens,
)
from backend.rag.preprocessing.chunkers.stamper import (
    _node_source_location, _source_location_metadata,
)
from backend.rag.preprocessing.chunkers.table_handler import (
    _attach_virtual_parents, _split_table_node,
)


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
                # _leaves() 会把相邻问答合并成 qa_pair；排除 QA 时必须
                # 一并排除该伪叶子，否则混合文档的 Q+A 会被 QA 策略和
                # Structure 策略各写一份，造成重复召回。
                leaves = [l for l in leaves
                          if l.type not in ("qa_question", "qa_answer", "qa_pair")]
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
        parent_budget = get_parent_chunk_tokens()
        if count_tokens(sec_text) <= parent_budget:
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
            groups = self._group_leaves_by_tokens(leaves, parent_budget)
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
                     parent_id: str, source_nodes: list | None = None) -> None:
        """append 一个 parent chunk（粒度/元数据统一）。"""
        chunks.append(_make_doc(text, {
            "granularity": "parent",
            "chunk_id": parent_id,
            "chunk_type": "section",
            "section_path": path,
            "parent_scope": "section" if sec.level > 0 else "document",
            "section_title": sec.text if sec.level > 0 else "",
            "section_level": sec.level,
            "chunk_tokens": count_tokens(text),
            **_source_location_metadata([
                _node_source_location(node) for node in (source_nodes or [])
            ]),
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
        leaf_tokens = get_leaf_chunk_tokens()
        if leaf.type == "table" and getattr(leaf, "rows", None):
            chunks.extend(_split_table_node(leaf, sec, path, file_path))
            return
        leaf_text = leaf.text
        if count_tokens(leaf_text) > leaf_tokens:
            # 超长源叶按原串连续切片，保留标点和数字边界，覆盖审计可逐字对账。
            subs = _hard_split_by_budget(leaf_text, leaf_tokens)
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
                "chunk_type": {
                    "paragraph": "paragraph",
                    "list": "list",
                    "code": "code",
                    "figure": "figure",
                    "qa_pair": "faq",
                    "qa_question": "faq",
                    "qa_answer": "faq",
                }.get(getattr(leaf, "type", ""), "paragraph"),
                "section_path": path,
                "section_title": sec.text if sec.level > 0 else "",
                "section_level": sec.level,
                "chunk_tokens": count_tokens(sub),
                **_source_location_metadata([_node_source_location(leaf)]),
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
        document_title_node = _doc_title_node(ast)

        def _dfs(node: DocumentNode, path: list):
            for child in node.children:
                if child.type == "section":
                    if child is document_title_node:
                        yield from _dfs(child, path)
                        continue
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
    leaf_tokens = get_leaf_chunk_tokens()
    if separators is None and splitter is not None:
        separators = list(getattr(splitter, "_separators", _SEPARATORS))
    if overlap is None and splitter is not None:
        overlap = int(getattr(splitter, "_chunk_overlap", get_chunk_overlap()))
    chunks: List[Document] = []
    leaf_items = list(_iter_leaves_with_section(ast))
    # 碎片化修复：小叶子先按 token 预算合并，再对超预算段切分
    # （_split_to_budget 统一严格达标收口，P0-05）
    for text, titles in _merge_small_with_section(leaf_items, leaf_tokens):
        texts = (_split_to_budget(text, leaf_tokens,
                                  separators=separators or _SEPARATORS,
                                  overlap=overlap)
                 if count_tokens(text) > leaf_tokens else [text])
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
    # 静默退化。挂文档级虚拟 parent，与 Step 等策略同构。
    return _enrich(
        _attach_virtual_parents(chunks, file_path, "plain"), file_path)
