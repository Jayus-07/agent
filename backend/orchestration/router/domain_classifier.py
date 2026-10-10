"""domain_classifier.py — 粗分类器 CoarseIntentClassifier（分层路由第一级）

职责边界（分层路由改造 2026-09-22）：
  本模块**只回答「这个问题属于哪个业务域」**，严禁输出具体 capability /
  tool_name —— 那是 Domain Tool Registry + 细路由（hierarchical.py）的职责。

组合方式（用户规格 §5：规则不做独立路由系统，统一收进粗分类器）：
  Rule Hint（manifest domains[].keywords 正则，~µs）
    → Domain Semantic Classifier（embedding prototype / 域心余弦，~30ms）
    → Confidence Gate（top1 置信度 + top1/top2 margin 双阈值 → unknown）

可插拔：`predict` 后端实现 `DomainBackend` 协议即可替换（未来接真正的
supervised classifier 时零改动上游）；embedding 函数支持注入（测试用
离线假 embedding，不碰网络/DB）。

硬约束（与 query_understanding.py 同一纪律）：
  - 模块级**零重依赖 import**（langchain/torch 只能在方法内延迟引入）；
  - 分类结果不含自然语言长解释，只落结构化字段（reason_code）。
"""
from __future__ import annotations

import math
import re
import time
from typing import Any, Protocol

from pydantic import BaseModel, Field

from backend.config import (
    COARSE_DOMAIN_CONFIDENCE,
    COARSE_DOMAIN_MIN_MARGIN,
    COARSE_RULE_HINT_BONUS,
    COARSE_RULE_STRONG_HITS,
)

__all__ = [
    "DomainPrediction",
    "DomainBackend",
    "CoarseIntentClassifier",
    "get_coarse_classifier",
]

# softmax 数值下溢防护：exp 参数超过该值时按最大值平移（softmax 平移不变性）
_EXP_OVERFLOW = 700.0

# softmax 仅用于把 prototype 相似度转换成排序分数；未做校准评测，不能
# 解读为真实概率。Confidence Gate 阈值针对该排序分数，不代表统计置信度。


class DomainPrediction(BaseModel):
    """粗分类统一输出（结构化，无自然语言长解释）。"""
    domain: str = Field(..., description="粗域：knowledge/data/business/... 或 unknown")
    confidence: float = Field(
        0.0, ge=0.0, le=1.0,
        description="兼容字段；表示 top1 路由分数，语义由 score_type 指明，不是校准概率",
    )
    second_domain: str = Field("", description="次优域（空 = 无有效次优）")
    second_confidence: float = Field(0.0, ge=0.0, le=1.0)
    margin: float = Field(0.0, description="top1 - top2 置信度差")
    source: str = Field("classifier", description="rule | classifier | gate | degraded")
    score_type: str = Field("unknown", description="rule_strength_heuristic | softmax_rank_score | none")
    reason_code: str = Field(
        "DOMAIN_CONFIDENT",
        description="DOMAIN_CONFIDENT | RULE_OVERRIDE | LOW_CONFIDENCE | LOW_MARGIN | EMBEDDING_UNAVAILABLE",
    )


class DomainBackend(Protocol):
    """可插拔分类后端协议：query → (domain, top1 路由分数, 全域分数)。

    分数 dict 必须覆盖全部声明域（margin 计算需要 top2）。
    """

    def predict(self, query: str) -> tuple[str, float, dict[str, float]]:
        ...


def _rule_hint_scores(query: str) -> tuple[dict[str, int], float]:
    """规则 hint：按 manifest domains[].keywords 统计各域命中数。

    Returns:
        (各域命中数, 归一化耗时秒)
    """
    from backend.orchestration.router.manifest import load_manifest

    groups = load_manifest().domain_keyword_groups
    query_lower = query.lower()
    hits = {
        domain: sum(1 for kw in kws if re.search(kw, query_lower))
        for domain, kws in groups.items()
    }
    return hits, 0.0


class EmbeddingPrototypeBackend:
    """Embedding prototype（域心）分类器 —— 第一版轻量实现。

    启动后首次调用时：把每个域的 description + examples 各 embed 一次，
    逐条归一化后取均值再归一化 = 域心；查询时 query embed 一次，与全部
    域心算余弦，再经 softmax 温度化为排序分数。该分数未经统计校准，
    不作为概率解释；top1 分数与分差仅用于内部路由门槛。
    域数量 ~9、维度 1024 → 内存运算为微秒级，无额外基础设施。

    未来替换为 supervised classifier 时，实现同一 DomainBackend 协议即可。
    """

    def __init__(self, embedding_fn=None):
        # embedding_fn 注入点：None = 运行时用全局单例（复用现有
        # embedding 角色/供应商绑定）；测试传离线假函数。
        self._embedding_fn = embedding_fn
        self._centroids: dict[str, list[float]] | None = None

    def _get_embedding(self):
        if self._embedding_fn is not None:
            return self._embedding_fn
        # 延迟 import：本模块处在 router 热路径，模块级引入 langchain
        # 会拖垮全仓导入链（torch 6.3s 教训，见 config/model_roles.py）
        from backend.rag.embedding_singleton import get_embedding
        return get_embedding()

    @staticmethod
    def _l2_normalize(vec: list[float]) -> list[float]:
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def _build_centroids(self) -> dict[str, list[float]]:
        from backend.orchestration.router.manifest import load_manifest

        embedding = self._get_embedding()
        manifest = load_manifest()
        texts: list[str] = []
        owners: list[str] = []
        for d in manifest.domains:
            for sample in [d.description, *d.examples]:
                texts.append(sample)
                owners.append(d.name)

        vectors = embedding.embed_documents(texts)
        acc: dict[str, list[float]] = {}
        counts: dict[str, int] = {}
        for owner, vec in zip(owners, vectors):
            normalized = self._l2_normalize(list(vec))
            bucket = acc.setdefault(owner, [0.0] * len(normalized))
            for i, v in enumerate(normalized):
                bucket[i] += v
            counts[owner] = counts.get(owner, 0) + 1

        centroids: dict[str, list[float]] = {}
        for domain, bucket in acc.items():
            # 域心 = 归一化均值（prototype），量纲与单条向量一致
            centroids[domain] = self._l2_normalize(
                [v / counts[domain] for v in bucket])
        return centroids

    def _ensure_centroids(self) -> dict[str, list[float]]:
        if self._centroids is None:
            self._centroids = self._build_centroids()
        return self._centroids

    def reset(self) -> None:
        """域声明变更后重建域心（测试 / 动态配置用）。"""
        self._centroids = None

    def predict(self, query: str) -> tuple[str, float, dict[str, float]]:
        from backend.config import COARSE_SOFTMAX_TEMP

        centroids = self._ensure_centroids()
        query_vec = self._l2_normalize(self._get_embedding().embed_query(query))
        cosines = {
            domain: sum(a * b for a, b in zip(centroid, query_vec))
            for domain, centroid in centroids.items()
        }
        # softmax 温度化：余弦相似度 → 内部排序分数（不是校准概率）。
        # 平移不变性：减去最大余弦防 exp 溢出。
        max_cos = max(cosines.values())
        exp_scores = {
            domain: math.exp(min(0.0, (cos - max_cos)) / COARSE_SOFTMAX_TEMP)
            for domain, cos in cosines.items()
        }
        total = sum(exp_scores.values()) or 1.0
        probs = {domain: e / total for domain, e in exp_scores.items()}
        ranked = sorted(probs.items(), key=lambda kv: (-kv[1], kv[0]))
        top_domain, top_prob = ranked[0]
        return top_domain, top_prob, probs


class CoarseIntentClassifier:
    """粗分类器统一入口：Rule Hint → Semantic Backend → Confidence Gate。"""

    def __init__(self, backend: DomainBackend | None = None):
        self._backend = backend  # None = 首次调用时建 EmbeddingPrototypeBackend

    def _ensure_backend(self) -> DomainBackend:
        if self._backend is None:
            self._backend = EmbeddingPrototypeBackend()
        return self._backend

    def classify(self, query: str, context: dict | None = None) -> DomainPrediction:
        """query → DomainPrediction。任何后端故障软降级为 unknown+degraded，
        由调用方（RoutingEngine）执行受控降级，绝不阻塞请求。"""
        t0 = time.perf_counter()
        query = (query or "").strip()
        if not query:
            return DomainPrediction(
                domain="unknown", confidence=0.0, margin=0.0,
                source="gate", reason_code="EMPTY_QUERY",
            )

        try:
            rule_hits, _ = _rule_hint_scores(query)
            strong = [d for d, n in rule_hits.items() if n >= COARSE_RULE_STRONG_HITS]
            if len(strong) == 1:
                # 规则强信号：确定性 domain_override（§5 domain_override + Trace 记录）
                domain = strong[0]
                # 置信度 = 满分基础 + 命中数加成（封顶 0.99，规则不是证据链顶点）
                hits = rule_hits[domain]
                conf = min(0.99, 0.90 + 0.01 * (hits - COARSE_RULE_STRONG_HITS))
                return DomainPrediction(
                    domain=domain, confidence=round(conf, 2),
                    second_domain="", second_confidence=0.0, margin=conf,
                    source="rule", reason_code="RULE_OVERRIDE",
                score_type="rule_strength_heuristic",
                )

            domain, confidence, all_scores = self._ensure_backend().predict(query)
        except Exception:
            # embedding 不可用（未绑定供应商/网络故障）→ 交回统一引擎
            return DomainPrediction(
                domain="unknown", confidence=0.0, margin=0.0,
                source="degraded", reason_code="EMBEDDING_UNAVAILABLE",
            )

        # 规则弱信号先验：每命中一次给该域加 COARSE_RULE_HINT_BONUS（封顶 0.1）
        if rule_hits:
            bonus = {d: min(0.1, n * COARSE_RULE_HINT_BONUS) for d, n in rule_hits.items() if n}
            all_scores = {d: min(1.0, s + bonus.get(d, 0.0)) for d, s in all_scores.items()}
            ranked = sorted(all_scores.items(), key=lambda kv: (-kv[1], kv[0]))
            domain, confidence = ranked[0]

        ranked = sorted(all_scores.items(), key=lambda kv: (-kv[1], kv[0]))
        top1_domain, top1 = ranked[0]
        top2_domain, top2 = ranked[1] if len(ranked) > 1 else ("", 0.0)
        margin = top1 - top2

        # ── Confidence Gate（§10）：双阈值任一不满足 → unknown，不强行归域 ──
        if top1 < COARSE_DOMAIN_CONFIDENCE:
            return DomainPrediction(
                domain="unknown", confidence=round(top1, 3),
                second_domain=top2_domain, second_confidence=round(top2, 3),
                margin=round(margin, 3),
                source="gate", reason_code="LOW_CONFIDENCE",
                score_type="softmax_rank_score",
            )
        if margin < COARSE_DOMAIN_MIN_MARGIN:
            return DomainPrediction(
                domain="unknown", confidence=round(top1, 3),
                second_domain=top2_domain, second_confidence=round(top2, 3),
                margin=round(margin, 3),
                source="gate", reason_code="LOW_MARGIN",
                score_type="softmax_rank_score",
            )
        return DomainPrediction(
            domain=top1_domain, confidence=round(top1, 3),
            second_domain=top2_domain, second_confidence=round(top2, 3),
            margin=round(margin, 3),
            source="classifier", reason_code="DOMAIN_CONFIDENT",
            score_type="softmax_rank_score",
        )


# ── 模块级单例（域心构建一次，进程内复用）─────────────────────
_coarse_classifier: CoarseIntentClassifier | None = None


def get_coarse_classifier() -> CoarseIntentClassifier:
    global _coarse_classifier
    if _coarse_classifier is None:
        _coarse_classifier = CoarseIntentClassifier()
    return _coarse_classifier


def reset_coarse_classifier() -> None:
    """重置单例（测试用）。"""
    global _coarse_classifier
    _coarse_classifier = None
