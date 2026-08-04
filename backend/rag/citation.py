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

from backend.shared.logger import logger

# Citation 校验阈值（与 RAGChain._verify_support 一致）
CITATION_SUPPORT_THRESHOLD = 0.0  # 默认不做事后过滤，依靠 Rerank 分数已足够

# 文档类型中文标签
_TYPE_LABEL_MAP: dict[str, str] = {
    "listing": "Listing",
    "sop": "SOP",
    "ad_policy": "广告政策",
    "faq": "FAQ",
    "product_spec": "产品规格",
    "training": "培训",
    "policy": "制度规范",
    "report": "报告",
    "manual": "操作手册",
}


class CitationFormatter:
    """Citation 处理：think 剥离 + 校验 + 格式化 + 结构化提取。

    所有方法**无状态**（纯函数式），类仅作 namespace + 未来扩展点
    （如可注入不同的 type_label_map 或 threshold）。
    """

    def strip_think(self, text: str) -> str:
        """剥离 <think>...</think> 推理块。未闭合标签保留后续内容，避免误删。"""
        cleaned = re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL)
        if "<think>" in cleaned and "</think>" not in cleaned:
            cleaned = re.sub(r"<think>.*", "", cleaned, flags=re.DOTALL)
        return cleaned.strip()

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
            type_label = _TYPE_LABEL_MAP.get(doc_type, doc_type)
            fname = meta.get("source_file", meta.get("source", ""))
            parts = [f"{idx}. **{fname}**"]
            if type_label:
                parts.append(f" ({type_label})")
            if score is not None:
                parts.append(f" — 相关度: {score:.2f}")
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
                seen[fname] = {
                    "index": idx,
                    "filename": fname,
                    "doc_type": doc_type,
                    "type_label": _TYPE_LABEL_MAP.get(doc_type, doc_type),
                    "score": round(float(score), 2) if score is not None else None,
                }

        return sorted(seen.values(), key=lambda s: s.get("index", 0))

    def _extract_cited_indexes(self, answer: str) -> set[int]:
        """从回答中提取所有 [1] [2] 引用编号集合。"""
        cited: set[int] = set()
        for m in re.finditer(r"\[(\d+)\]", answer):
            cited.add(int(m.group(1)))
        return cited


__all__ = ["CitationFormatter", "CITATION_SUPPORT_THRESHOLD"]
