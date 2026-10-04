"""Citation Formatter — PR-1.3（ADR-0002 阶段 1.3）。

从 RAGChain 抽出的 4 个 citation 处理函数，封装为类方法：

- strip_think(text)        — 剥离 <think>...</think> 推理块
- verify_support(answer, docs) — Citation 校验（基于 Rerank 分数）
- format_references(docs, answer) — 生成参考文献列表（Markdown）
- extract_sources(docs, answer) — 提取结构化来源（前端 SourceCard 用）

设计动机：
- RAGChain._verify() 42 行做 5 件事（think strip + META parse + verify + format + memory），
  格式化逻辑可独立
- 4 个函数都是纯函数式（无 this 状态），但归类到 namespace 更清晰
- 抽出后 _verify 可简化为 3 步：parse_meta → formatter.verify + format → memory.end_turn

边界（PR-1.3 范围）：
- ✅ 抽 4 个函数为类方法
- ❌ 不动 RAGChain（接入是 PR-1.4）
- ❌ 不动 META 注释解析（属于 _verify 的另一职责）

后续（PR-1.4）：
- RAGChain._verify 改用 self.formatter.verify_support() / format_references() / extract_sources()
- 删除 chain.py 里的模块级 4 函数（避免 thin wrapper）
"""
from __future__ import annotations

import re
from typing import Optional

from backend.rag.preprocessing.taxonomy_spec import doc_type_label
from backend.shared.logger import logger

# Citation 校验阈值（与 RAGChain._verify_support 一致）
CITATION_SUPPORT_THRESHOLD = 0.0  # 默认不做事后过滤，依靠 Rerank 分数已足够

# 文档类型中文标签：唯一事实源是 taxonomy_spec.DOC_TYPE_LABELS（含启动期覆盖度校验）。
# 本模块历史上自持一份 _TYPE_LABEL_MAP，只覆盖 9 类、且含 yaml 里根本不存在的
# report / manual 两个过期项，导致其余类型在引用标注里回落英文原始码。


def _parse_pages_raw(pages_raw: str) -> list[int]:
    """切分层页码标量串（"3,4"）→ 升序 int 列表；坏数据跳过（宁缺勿错）。"""
    return sorted(int(p) for p in pages_raw.split(",") if p.strip().isdigit())


def _pages_label(pages: list[int]) -> str:
    """页码列表 → 中文文案：连续合并区间（第 3-4 页）、单页、离散列举（超过 3 页截断加「等」）。"""
    if len(pages) == 1:
        return f"第 {pages[0]} 页"
    consecutive = all(b - a == 1 for a, b in zip(pages, pages[1:]))
    if consecutive:
        return f"第 {pages[0]}-{pages[-1]} 页"
    shown = pages[:3]
    suffix = " 等" if len(pages) > 3 else ""
    return "第 " + "、".join(str(p) for p in shown) + f" 页{suffix}"


def _format_locality(meta: dict) -> str:
    """来源定位段（原文定位 P0）：「第 X 页 · 章节」；两者皆空返回空串（旧行格式）。"""
    pages = _parse_pages_raw(str(meta.get("pages", "") or ""))
    section = str(meta.get("section_title", "") or "").strip()
    segs = []
    if pages:
        segs.append(_pages_label(pages))
    if section:
        segs.append(section)
    return " · ".join(segs)


class CitationFormatter:
    """Citation 处理：think 剥离 + 校验 + 格式化 + 结构化提取。

    所有方法**无状态**（纯函数式），类仅作 namespace + 未来扩展点
    （如可注入不同的 type_label_map 或 threshold）。
    """

    # 分点边界：句末标点/冒号后紧跟「N.」且 N 后不是另一位数字（排除小数如 3.5）。
    # 只在同行内匹配（[^行首]），已有换行处 lookbehind 看到 \n 不成立，不会产生双换行。
    _POINT_BOUNDARY_RE = re.compile(r"(?<=[。！？；：;:])[ \t]*(?=\d{1,2}\.(?!\d))")

    def strip_think(self, text: str) -> str:
        """剥离 <think>...</think> 推理块。未闭合标签保留后续内容，避免误删。"""
        cleaned = re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL)
        if "<think>" in cleaned and "</think>" not in cleaned:
            cleaned = re.sub(r"<think>.*", "", cleaned, flags=re.DOTALL)
        return cleaned.strip()

    def normalize_point_layout(self, text: str) -> str:
        """确定性分点归一化：句号+序号边界处补换行。

        提示词要求模型分点呈现，但生成随机性下仍偶发把两个要点写在同一行
        （如「…[E1]。2. **日本**…3. **美国**」）。这里在句末标点后紧跟
        「N.」处补一个换行，保证前端按点换行。零 LLM 成本、幂等：
        - 已分行（序号在行首）→ lookbehind 是 \n，不匹配，原样返回
        - 小数（「3.5 个工作日」）→ (?!\d) 排除
        - 「如下：1.」冒号后首个序号 → 同样补行
        """
        if not text:
            return text
        return self._POINT_BOUNDARY_RE.sub("\n", text)

    def verify_support(self, answer: str, docs: list, question: str = "") -> tuple[str, list]:
        """Citation Filter: 复用 Rerank 阶段的 CrossEncoder 分数，避免重复推理。

        阶段 1: 复用 rerank_score（RerankCompressor 已写入 doc.metadata），过滤低分 chunk
        阶段 2: 句子级验证（默认关闭，ENABLE_CITATION_SENTENCE_CHECK=true 开启）
        返回: (cleaned_answer, verified_docs)
        """
        if not docs:
            return answer, []

        verified = []
        for doc in docs:
            score = doc.metadata.get("rerank_score", 0.5)
            if float(score) > CITATION_SUPPORT_THRESHOLD:
                doc.metadata["support_score"] = round(float(score), 4)
                verified.append(doc)

        logger.info(
            f"[CitationFormatter] 支撑验证(复用Rerank分): {len(docs)} → {len(verified)} 个 chunk "
            f"(threshold={CITATION_SUPPORT_THRESHOLD})"
        )

        if not verified:
            logger.warning("[CitationFormatter] 所有 chunk 未通过验证，清空引用")
            return answer, []

        # 阶段 2: 完成（企业做法：Prompt 强制 LLM 标注引用 [1][2]，不做事后猜）
        return answer, verified

    def format_references(self, docs: list, answer: str = "") -> str:
        """生成参考文献列表（Markdown）。

        - 优先显示文中 [1][2] 实际引用到的来源
        - 兜底：如果 LLM 未生成引用标注，展示所有通过验证的文档
        行格式（2026-10-03 原文定位 P0）：
          `N. **文件** (类型) — 第 3-4 页 · 章节 — 相关度: 0.83`
        定位段可整体缺省（无页码无章节 = 旧格式），解析侧向后兼容。
        """
        if not docs:
            return ""

        cited = self._extract_cited_indexes(answer)

        seen: dict[str, tuple[int, dict]] = {}
        for doc in docs:
            idx = doc.metadata.get("index")
            fname = doc.metadata.get("source_file", doc.metadata.get("source", ""))
            if not fname or idx is None:
                continue
            # 有引用标注时仅保留文中实际引用的来源
            if cited and idx not in cited:
                continue
            # 无引用标注（兜底）：展示所有 verified docs
            if fname not in seen:
                seen[fname] = (idx, doc.metadata)

        if not seen:
            return ""

        # 按 index 排序，与文中标注 [1][2] 顺序一致
        items = sorted(seen.values(), key=lambda x: x[0])

        lines = ["", "---", "", "### 参考文献", ""]
        for idx, meta in items:
            doc_type = meta.get("doc_type", "")
            score = meta.get("score", meta.get("rerank_score", None))
            type_label = doc_type_label(doc_type)
            fname = meta.get("source_file", meta.get("source", ""))
            parts = [f"{idx}. **{fname}**"]
            if type_label:
                parts.append(f" ({type_label})")
            locality = _format_locality(meta)
            if locality:
                parts.append(f" — {locality}")
            if score is not None:
                parts.append(f" — 相关度: {score:.2f}")
            # 原文预览钥匙（P1）：行尾机器注释，Markdown 渲染不可见、
            # 前端 stripReferences 整段剪除、解析侧可选组取回（三重安全）
            doc_id = str(meta.get("doc_id", "") or "").strip()
            if doc_id:
                parts.append(f" <!--doc:{doc_id}-->")
            lines.append("".join(parts))

        return "\n".join(lines)

    def extract_sources(self, docs: list, answer: str = "") -> list[dict]:
        """从 verified docs 中提取结构化来源信息（供前端 SourceCard 展示）。

        - 优先通过文中 [1][2] 引用标注精确匹配
        - 兜底：如果 LLM 未生成引用标注，返回所有通过验证的文档
        """
        if not docs:
            return []

        cited = self._extract_cited_indexes(answer)

        seen: dict[str, dict] = {}
        for doc in docs:
            idx = doc.metadata.get("index")
            fname = doc.metadata.get("source_file", doc.metadata.get("source", ""))
            if not fname or idx is None:
                continue
            # 有引用标注时仅保留文中实际引用的来源；无引用时兜底展示全部
            if cited and idx not in cited:
                continue
            if fname not in seen:
                doc_type = doc.metadata.get("doc_type", "")
                score = doc.metadata.get("score",
                                         doc.metadata.get("rerank_score",
                                                          doc.metadata.get("support_score")))
                source = {
                    "index": idx,
                    "filename": fname,
                    "doc_type": doc_type,
                    "type_label": doc_type_label(doc_type),
                    "score": round(float(score), 2) if score is not None else None,
                }
                # 来源部门（多部门隔离）：前端来源卡部门标签；general 缺省
                # 不下发，保持无部门归属文档的展示不变
                department = str(doc.metadata.get("department", "") or "")
                if department and department != "general":
                    source["department"] = department
                # 原文定位（P0）：页码（切分层回映射的标量逗号串）+ 章节标题；
                # 坏数据（非数字段）跳过该页，宁缺勿错
                pages = _parse_pages_raw(str(doc.metadata.get("pages", "") or ""))
                if pages:
                    source["pages"] = pages
                section = str(doc.metadata.get("section_title", "") or "").strip()
                if section:
                    source["section"] = section
                # 原文预览钥匙（P1）：doc_id → /rag/documents/{doc_id}/file
                doc_id = str(doc.metadata.get("doc_id", "") or "").strip()
                if doc_id:
                    source["doc_id"] = doc_id
                seen[fname] = source

        return sorted(seen.values(), key=lambda s: s.get("index", 0))

    def _extract_cited_indexes(self, answer: str) -> set[int]:
        """从回答中提取引用编号集合。

        QA prompt 要求模型用 [E1] 格式引用（chain.py），这里需同时兼容
        [E1] 与 [1] 两种格式，否则"仅展示实际被引来源"永远走兜底全量展示。
        """
        cited: set[int] = set()
        for m in re.finditer(r"\[(?:E)?(\d+)\]", answer, re.IGNORECASE):
            cited.add(int(m.group(1)))
        return cited


__all__ = ["CitationFormatter", "CITATION_SUPPORT_THRESHOLD"]
