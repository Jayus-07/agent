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

# 公共终止符：answer 部分在遇到下一个 Q、markdown 标题、中文编号章节时终止，
# 避免吞掉后续章节标题（P1-6：01_FAQ/02_售后FAQ 的 answer 曾吞掉"## 二、物流配送"等）。
_ANSWER_TERMINATOR = r"(?=\n+\s*\*\*Q[:：]|\n+#{1,6}\s|\n+[一二三四五六七八九十]+、|\Z)"

# 模式 1: **Q:** 问题 \n **A:** 答案
# 兼容两种加粗写法：
#   - 标签加粗、正文不加粗（真实文档常见）：`**Q:** 问题` / `**A:** 答案`
#   - 整行加粗（旧测试格式）：`**Q: 问题**` / `**A: 答案**`
_QA_BOLD_RE = re.compile(
    rf"\*\*Q[:：]\*{{0,2}}\s*(.+?)(?:\*\*)?\n+\s*\*\*A[:：]\*{{0,2}}\s*(.+?)(?:\*\*)?{_ANSWER_TERMINATOR}",
    re.DOTALL,
)

# 模式 2: ## 问题 ... ## 答案 ...（heading 配对）
# 配对模式：问题 heading + 内容 + 答案 heading + 内容
_QA_HEADING_RE = re.compile(
    r"(?m)^#{2,4}\s+(?:问题|Question|问|疑问)[:：]?\s*\n+(.+?)\n+"
    r"#{2,4}\s+(?:答案|Answer|答|解答)[:：]?\s*\n+(.+?)(?=\n#{2,4}|\Z)",
    re.DOTALL,
)

# 模式 3: Q1. ... A1. ...（编号配对）
_QA_NUMBERED_RE = re.compile(
    rf"^Q(\d+)[.、]?\s*(.+?)\n+A\1[.、]?\s*(.+?)(?=\nQ\d+|\n+#{{1,6}}\s|\n+[一二三四五六七八九十]+、|\Z)",
    re.MULTILINE | re.DOTALL,
)

# 模式 4: Q：问题 \n A：答案（全角/半角冒号，无加粗无编号，DOCX FAQ 常见）
_QA_COLON_RE = re.compile(
    rf"(?m)^Q[:：]\s*(.+?)\n+A[:：]\s*(.+?)(?=\n+Q[:：]|\n+#{{1,6}}\s|\n+[一二三四五六七八九十]+、|\Z)",
    re.DOTALL,
)

_PATTERNS: list[tuple[str, re.Pattern, int, int]] = [
    # (pattern_type, regex, question_group_idx, answer_group_idx)
    ("qa_bold", _QA_BOLD_RE, 1, 2),
    ("qa_heading", _QA_HEADING_RE, 1, 2),
    ("qa_numbered", _QA_NUMBERED_RE, 2, 3),  # g1=Q 数字（跳过）, g2=问题, g3=答案
    ("qa_colon", _QA_COLON_RE, 1, 2),
]


def extract_qa_pairs(
    text: str,
) -> list[tuple[str, str, str]]:
    """提取所有 Q/A 对，每个元素 = (question, answer, pattern_type)。

    三种模式独立识别并产出，互不冲突。返回顺序按 pattern 在 _PATTERNS
    中的顺序；同 pattern 内按出现顺序。
    """
    results: list[tuple[str, str, str]] = []
    for ptype, pattern, q_idx, a_idx in _PATTERNS:
        for m in pattern.finditer(text):
            q = m.group(q_idx).strip()
            a = m.group(a_idx).strip()
            if q and a:  # 过滤空匹配
                results.append((q, a, ptype))
    return results


def looks_like_qa_doc(text: str, min_pairs: int = 1) -> bool:
    """判断文档是否像 FAQ（任意 pattern 累计 ≥ min_pairs 个 Q/A 对）。

    默认 min_pairs=1：单条 Q/A 也算 FAQ（用户上传的单问题 FAQ 常见）。
    """
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


# ── 局部 QA 标注（P0-01 修复，2026-10-13）─────────────────────────────
#
# 旧实现（markdown/txt/docx parser）：looks_like_qa_doc 命中（min_pairs=1，
# 单组 Q/A 即命中）→ 整篇只产 qa_question/qa_answer，普通章节、段落、表格
# 全部丢弃。混合文档（FAQ + 正文）内容静默丢失。
#
# 新实现：parser 一律走正常结构解析（结构零丢失），再由 annotate_qa_nodes
# 在 AST 上做局部配对——命中的段落/章节转成 qa 节点，其余节点原样保留。
# 识别正则与 extract_qa_pairs 同族，保证「哪些段落算 Q/A」口径一致。

# Q 标签正则 MULTILINE：解析器常把 Q 行与后续行合并成同一段落，
# `(.*)$` 需在首行尾锚定（组=第一行问题），剩余行由 caller 并入答案。
_Q_LABEL_RE = re.compile(r"^\s*(?:\*\*)?Q[:：]\s*(.*)$", re.MULTILINE)
_A_LABEL_RE = re.compile(r"^\s*(?:\*\*)?A[:：]\s*(.*)$", re.DOTALL)
_Q_NUM_LABEL_RE = re.compile(r"^\s*Q(\d+)[.、]\s*(.*)$", re.MULTILINE)
_A_NUM_LABEL_RE = re.compile(r"^\s*A(\d+)[.、]\s*(.*)$", re.DOTALL)
# section 标题形态的问题/答案（qa_heading 模式的 AST 形态）
_Q_SECTION_RE = re.compile(r"^\s*(?:问题|Question|问|疑问)[:：]?\s*(.*)$")
_A_SECTION_RE = re.compile(r"^\s*(?:答案|Answer|答|解答)[:：]?\s*(.*)$")

_QA_NODE_TYPES = ("qa_question", "qa_answer")


def _leaf_texts_of(section) -> list[str]:
    """收集 section 子树中全部叶子文本（qa_heading 配对时答案取内容用）。"""
    from backend.rag.preprocessing.ast import LEAF_TYPES, walk

    return [
        n.text for n in walk(section)
        if n is not section and n.type in LEAF_TYPES and n.text.strip()
    ]


def _starts_q(text: str) -> bool:
    """段落是否以 Q 标签/编号开头（答案吸收的终止条件之一）。"""
    t = text.strip()
    return bool(
        _Q_LABEL_RE.match(t) or _Q_NUM_LABEL_RE.match(t)
    )


def _annotate_container(children: list) -> list:
    """对一个容器的 children 序列做局部 QA 配对，返回新序列。

    三种形态（与 extract_qa_pairs 的模式族对齐）：
    1. 段内自含（qa_bold）：单段落含 **Q:** ... **A:** ... → 拆成一对；
    2. 相邻段落（qa_colon / qa_numbered）：Q 段开始，紧邻 A 段吸收，
       再吞并后续普通段落直至下一个 Q 段 / section 边界（对齐旧提取的
       answer 语义；内容只挪归属，不丢弃）；
    3. section 标题配对（qa_heading）：问题 section + 答案 section → 一对，
       答案文本 = 答案 section 子树全部叶子。
    编号配对要求 Q/A 编号一致，不一致不当 FAQ（保守不误配）。
    """
    out: list = []
    i = 0
    n = len(children)
    while i < n:
        node = children[i]
        ntype = getattr(node, "type", "")
        text = (getattr(node, "text", "") or "").strip()

        if ntype in ("paragraph", "list") and text:
            # 形态 1：段内自含 bold Q/A
            m_bold = _QA_BOLD_RE.search(text)
            if m_bold and m_bold.group(1).strip() and m_bold.group(2).strip():
                out.append(_make_qa_node("qa_question", m_bold.group(1).strip()))
                out.append(_make_qa_node("qa_answer", m_bold.group(2).strip()))
                i += 1
                continue
            # 形态 1b：段内自含 colon/numbered（"Q：x\nA：y"/"Q1. x\nA1. y"
            # 常被解析器合并进同一段落——与 extract_qa_pairs 同正则拆分）
            m_inline = None
            if _starts_q(text):
                for rx, qi, ai in (
                    (_QA_COLON_RE, 1, 2), (_QA_NUMBERED_RE, 2, 3),
                ):
                    m = rx.search(text)
                    if m and m.start() == 0 and m.group(qi).strip():
                        m_inline = (m, qi, ai)
                        break
            if m_inline:
                m, qi, ai = m_inline
                out.append(_make_qa_node("qa_question", m.group(qi).strip()))
                out.append(_make_qa_node("qa_answer", m.group(ai).strip()))
                i += 1
                continue
            # 形态 2a：Q 标签段（Q: / Q1.）
            m_ql = _Q_LABEL_RE.match(text)
            m_qn = _Q_NUM_LABEL_RE.match(text)
            if m_ql or m_qn:
                m_q = m_ql or m_qn
                q_text = m_q.group(2 if m_qn else 1).strip()
                q_no = m_qn.group(1) if m_qn else None
                # Q 行之后的段落剩余行并入答案（不丢内容）
                extra = text[m_q.end():].strip()
                j = i + 1
                a_parts: list[str] = []
                if extra:
                    a_parts.append(extra)
                # 紧邻 A 段（要求编号一致）
                if j < n:
                    nxt = children[j]
                    ntext = (getattr(nxt, "text", "") or "").strip()
                    if getattr(nxt, "type", "") in ("paragraph", "list"):
                        m_al = _A_LABEL_RE.match(ntext)
                        m_an = _A_NUM_LABEL_RE.match(ntext)
                        if m_al and q_no is None:
                            a_parts.append(m_al.group(1).strip())
                            j += 1
                        elif m_an and q_no is not None and m_an.group(1) == q_no:
                            a_parts.append(m_an.group(2).strip())
                            j += 1
                # 吸收后续普通段落直至下一个 Q 段 / section（内容不丢，归属答案）
                while j < n:
                    nxt = children[j]
                    if getattr(nxt, "type", "") != "paragraph":
                        break
                    ntext = (getattr(nxt, "text", "") or "").strip()
                    if not ntext or _starts_q(ntext):
                        break
                    a_parts.append(ntext)
                    j += 1
                out.append(_make_qa_node("qa_question", q_text))
                out.append(_make_qa_node(
                    "qa_answer", "\n".join(a_parts) or "（空答案）"))
                i = j
                continue

        # 形态 3：问题/答案 section 标题配对
        if ntype == "section" and text and _Q_SECTION_RE.match(text):
            m_qs = _Q_SECTION_RE.match(text)
            if i + 1 < n:
                nxt = children[i + 1]
                if getattr(nxt, "type", "") == "section":
                    m_as = _A_SECTION_RE.match(
                        (getattr(nxt, "text", "") or "").strip())
                    if m_as:
                        # 标题含内联问题（"问题：xxx"）→ 取 xxx；
                        # 标题只是标签（"## 问题"）→ 问题文本 = 本节内容
                        q_inline = (m_qs.group(1) or "").strip()
                        q_leaves = _leaf_texts_of(node)
                        if q_inline:
                            q_text = q_inline
                        elif q_leaves:
                            q_text = "\n".join(q_leaves)
                        else:
                            q_text = text
                        a_leaves = _leaf_texts_of(nxt)
                        out.append(_make_qa_node("qa_question", q_text))
                        out.append(_make_qa_node(
                            "qa_answer", "\n".join(a_leaves) or "（空答案）"))
                        i += 2
                        continue

        # 嵌套 section 递归标注
        if ntype == "section" and getattr(node, "children", None):
            node.children[:] = _annotate_container(node.children)
        out.append(node)
        i += 1
    return out


def _make_qa_node(ntype: str, text: str):
    from backend.rag.preprocessing.ast import DocumentNode

    return DocumentNode(type=ntype, text=text)


def annotate_qa_nodes(root) -> int:
    """对正常解析后的 AST 做局部 QA 标注，返回转换出的 Q/A 对数。

    只转换命中 Q/A 形态的节点，其余结构原样保留——混合文档（FAQ+正文）
    不再因 FAQ 识别而丢内容（P0-01）。识别口径与 extract_qa_pairs 一致，
    保证 doc_type=faq 的路由判定与 AST 实际 qa 节点对齐。
    """
    before = _count_qa(root)
    if root.children:
        root.children[:] = _annotate_container(list(root.children))
    after = _count_qa(root)
    return (after - before) // 2


def _count_qa(root) -> int:
    from backend.rag.preprocessing.ast import walk

    return sum(1 for n in walk(root) if n.type in _QA_NODE_TYPES)
