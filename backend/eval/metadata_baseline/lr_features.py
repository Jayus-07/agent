"""lr_features.py — L1 路由分类器的特征管道（规划阶段 3.2 预建）。

黄金集就绪后 train_lr.py 当天可训练；本模块只做特征提取，零训练依赖
（sklearn 仅 train_lr.py 用）。

特征组（固定顺序，FEATURE_NAMES 记录于模型卡）：
  1. 词表信号（14 维）：每类 DOC_TYPE_RULES 加权命中得分（log1p 压缩）——
     与规则链同源但**只读冻结词表**，不引入新正则（规划 §0.2）；
  2. 文件名/路径提示（15 维）：每类命中 one-hot + 总命中数；
  3. 结构特征（6 维）：标题数、表格行数、法律条款数、风险词命中、
     正文长度（log1p）、数字/字母占比；
  4. 嵌入特征（14 维，可选）：taxonomy 描述 cosine 相似度——需传 embedding，
     离线训练与在线推理必须同开同关（模型卡记录 embedding_on 标志）。

注意：特征里**没有** LLM 输出——L1 的定位是"零 LLM 成本层"。
"""
from __future__ import annotations

import math
import os
import re

from backend.rag.preprocessing.domain_data import (
    DOC_TYPE_RULES, FILENAME_TYPE_HINTS, FOLDER_TYPE_HINTS,
)
from backend.rag.preprocessing.metadata_schema import DOC_TYPES

_RISK_RE = re.compile(r"合同|GDPR|隐私|审计|监管|处罚|罚款|合规|诉讼|知识产权|保密")
_CLAUSE_RE = re.compile(r"第[一二三四五六七八九十百\d]+条")
_HEADING_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_TABLE_RE = re.compile(r"^\|.+\|", re.MULTILINE)


def feature_names(embedding_on: bool = True) -> list[str]:
    names = [f"lex_{t}" for t in DOC_TYPES]
    names += [f"fname_{t}" for t in DOC_TYPES] + ["fname_hits"]
    names += ["struct_headings", "struct_tables", "struct_clauses",
              "struct_risk", "struct_len_log", "struct_alnum_ratio"]
    if embedding_on:
        names += [f"emb_{t}" for t in DOC_TYPES]
    return names


def _lexicon_scores(text_lower: str) -> dict[str, float]:
    scores: dict[str, float] = {t: 0.0 for t in DOC_TYPES}
    for dt, rules in DOC_TYPE_RULES.items():
        if dt not in scores:
            continue
        s = 0.0
        for pattern, weight in rules:
            try:
                found = re.findall(pattern, text_lower)
            except re.error:
                continue
            if found:
                s += weight * len(found)
        scores[dt] = s
    return scores


def _filename_hits(filename: str, file_path: str) -> dict[str, float]:
    hits = {t: 0.0 for t in DOC_TYPES}
    total = 0.0
    if filename:
        fname = os.path.splitext(filename)[0].lower()
        for hint, t in FILENAME_TYPE_HINTS.items():
            if t in hits and hint.lower() in fname:
                hits[t] = 1.0
                total += 1.0
    if file_path:
        parts = os.path.dirname(file_path).lower().replace("\\", "/").split("/")
        for hint, t in FOLDER_TYPE_HINTS.items():
            if t in hits and hint.lower() in parts:
                hits[t] = 1.0
                total += 1.0
    hits["__total__"] = total
    return hits


def extract_features(text: str, filename: str = "", file_path: str = "",
                     embedding_sims: dict[str, float] | None = None) -> list[float]:
    """提取特征向量（顺序与 feature_names(embedding_sims is not None) 一致）。

    embedding_sims：taxonomy 嵌入相似度 {doc_type: cos}——由调用方
    （训练管道 / metadata_router L1）传入；None 则向量不含嵌入组。
    """
    sample = text[:6000]
    text_lower = sample.lower()
    feats: list[float] = []

    lex = _lexicon_scores(text_lower)
    feats += [math.log1p(lex[t]) for t in DOC_TYPES]

    fname = _filename_hits(filename, file_path)
    feats += [fname[t] for t in DOC_TYPES] + [fname["__total__"]]

    chars = max(len(sample), 1)
    alnum = sum(1 for c in sample if c.isalnum())
    feats += [
        len(_HEADING_RE.findall(sample)),
        len(_TABLE_RE.findall(sample)),
        len(_CLAUSE_RE.findall(sample)),
        len(_RISK_RE.findall(sample)),
        math.log1p(len(text)),
        alnum / chars,
    ]

    if embedding_sims is not None:
        feats += [float(embedding_sims.get(t, 0.0)) for t in DOC_TYPES]
    return feats


def embedding_sims_via_router(text: str, embedding, timeout: float = 10.0) -> dict[str, float] | None:
    """复用级联路由的 taxonomy 索引取嵌入相似度（训练/推理同源）。"""
    import asyncio

    from backend.rag.preprocessing.metadata_router import _taxonomy_index

    async def _go():
        return await _taxonomy_index.classify(text, embedding, timeout)

    try:
        ranked = asyncio.run(_go())
    except RuntimeError:
        # 已在事件循环中：由调用方改用 await 版本
        raise
    if ranked is None:
        return None
    return {t: s for t, s in ranked}
