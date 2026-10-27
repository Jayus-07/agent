# -*- coding: utf-8 -*-
"""test_domain_classifier.py — 粗分类器单测（分层路由 2026-09-22）

覆盖：规则强信号 override / Confidence Gate（confidence + margin 双阈值）/
规则弱信号先验 / 可插拔后端注入 / 故障软降级 / 输出结构契约（§3）。
全部离线：embedding 用确定性假函数或桩后端，不碰网络/DB/LLM。
"""
import pytest

from backend.orchestration.router.domain_classifier import (
    CoarseIntentClassifier,
    DomainPrediction,
    EmbeddingPrototypeBackend,
    reset_coarse_classifier,
)
from backend.orchestration.router.manifest import load_manifest


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_coarse_classifier()
    yield
    reset_coarse_classifier()


class StubBackend:
    """桩后端：返回预置的全域分数（测 gate / 先验逻辑）。"""

    def __init__(self, scores: dict[str, float]):
        self.scores = scores

    def predict(self, query: str):
        return "placeholder", 0.0, dict(self.scores)


class ExplodingBackend:
    def predict(self, query: str):
        raise RuntimeError("embedding unavailable")


class TestRuleOverride:
    @pytest.mark.parametrize("query", [
        "请联网查找 Docker 官方关于容器内存限制的文档，用两句话概括并给出链接。",
        "请联网搜索公开资料，概括主要内容并给出出处。",
    ])
    def test_explicit_web_lookup_enters_knowledge(self, query):
        result = CoarseIntentClassifier(ExplodingBackend()).classify(query)
        assert result.domain == "knowledge"
        assert result.source == "rule"

    def test_negated_web_lookup_is_not_strong_rule(self):
        result = CoarseIntentClassifier(ExplodingBackend()).classify(
            "不需要联网搜索，请解释这句话。",
        )
        assert result.source != "rule"

    def test_explicit_enterprise_knowledge_request_keeps_knowledge_domain(self):
        """显式知识库检索不因含退款一词被送进客服专属域。"""
        clf = CoarseIntentClassifier(backend=ExplodingBackend())
        result = clf.classify("请在企业知识库中检索退款处理流程，依据检索结果回答并提供出处。")
        assert result.domain == "knowledge"
        assert result.source == "rule"
        action = clf.classify("帮我申请退款并查询订单")
        assert action.domain == "customer_service"

    def test_knowledge_source_constraint_beats_product_keyword(self):
        result = CoarseIntentClassifier(ExplodingBackend()).classify(
            "现货商品通常多久发货？请依据企业知识库回答并给出处。",
        )
        assert result.domain == "knowledge"
        assert result.source == "rule"

    def test_strong_rule_hits_decide_domain(self):
        """「公司的年假规定是什么」：规定+是什么 = 2 次强命中 → knowledge（rule override）。"""
        clf = CoarseIntentClassifier(backend=ExplodingBackend())  # 规则层不该碰后端
        p = clf.classify("公司的年假规定是什么")
        assert p.domain == "knowledge"
        assert p.source == "rule"
        assert p.reason_code == "RULE_OVERRIDE"
        assert p.confidence >= 0.90

    def test_rule_never_outputs_tool_name(self):
        """粗分类器严禁输出具体 tool_name（§3 职责边界）。"""
        clf = CoarseIntentClassifier(backend=ExplodingBackend())
        for q in ("公司的年假规定是什么", "查一下最近30天销售额", "分析 SKU001 销量下降原因"):
            p = clf.classify(q)
            assert "." not in p.domain  # capability 形如 <domain>.<action>


class TestConfidenceGate:
    def test_low_confidence_unknown(self):
        """top1 < 0.75 → unknown（LOW_CONFIDENCE），不强行归域。"""
        scores = {d: 0.4 for d in load_manifest().domain_names}
        scores["knowledge"] = 0.61
        clf = CoarseIntentClassifier(backend=StubBackend(scores))
        p = clf.classify("随便看看")
        assert p.domain == "unknown"
        assert p.reason_code == "LOW_CONFIDENCE"

    def test_low_margin_unknown(self):
        """confidence 达标但 top1/top2 贴近 → margin gate 拒判。"""
        scores = {d: 0.1 for d in load_manifest().domain_names}
        scores["knowledge"] = 0.85
        scores["data"] = 0.80
        clf = CoarseIntentClassifier(backend=StubBackend(scores))
        p = clf.classify("根据公司库存规定查询 SKU001 当前库存")
        assert p.domain == "unknown"
        assert p.reason_code == "LOW_MARGIN"
        assert p.second_domain == "data"

    def test_spec_conflict_example_rejected(self):
        """§10 原例：knowledge=0.61 / data=0.59 —— confidence/margin 双不达标，
        无论如何不能直接归 knowledge。"""
        scores = {d: 0.1 for d in load_manifest().domain_names}
        scores["knowledge"] = 0.61
        scores["data"] = 0.59
        clf = CoarseIntentClassifier(backend=StubBackend(scores))
        p = clf.classify("根据公司库存规定查询 SKU001 当前库存")
        assert p.domain == "unknown"
        assert p.reason_code in ("LOW_CONFIDENCE", "LOW_MARGIN")

    def test_confident_domain_passes(self):
        scores = {d: 0.2 for d in load_manifest().domain_names}
        scores["data"] = 0.88
        scores["knowledge"] = 0.51
        clf = CoarseIntentClassifier(backend=StubBackend(scores))
        p = clf.classify("查一下最近30天销售额")
        assert p.domain == "data"
        assert p.source == "classifier"
        assert p.reason_code == "DOMAIN_CONFIDENT"
        assert p.margin >= 0.12


class TestRuleHintPrior:
    def test_weak_hit_bonus_helps_cross_margin(self):
        """1 次弱命中加成：data 0.85 / business 0.74（margin 0.11 差一点），
        「统计」弱命中 → data 0.90 / margin 0.16，跨过 gate。"""
        base = {d: 0.1 for d in load_manifest().domain_names}
        base["data"] = 0.85
        base["business"] = 0.74
        clf = CoarseIntentClassifier(backend=StubBackend(base))
        p = clf.classify("统计一下")
        assert p.domain == "data"
        assert p.confidence == pytest.approx(0.9)

    def test_weak_hit_bonus_cannot_save_genuine_tie(self):
        """双方各有一次弱命中（data「库存」+ knowledge「规定」）→ 加成互相抵消，
        margin 依旧不达门槛 → 拒判（§10 冲突问题不强行归域）。"""
        base = {d: 0.1 for d in load_manifest().domain_names}
        base["data"] = 0.85
        base["knowledge"] = 0.85
        clf = CoarseIntentClassifier(backend=StubBackend(base))
        p = clf.classify("根据公司库存规定查询当前库存")
        assert p.domain == "unknown"
        assert p.reason_code == "LOW_MARGIN"


class TestDegraded:
    def test_backend_failure_degrades_to_unknown(self):
        clf = CoarseIntentClassifier(backend=ExplodingBackend())
        p = clf.classify("查一下最近30天销售额")
        assert p.domain == "unknown"
        assert p.source == "degraded"
        assert p.reason_code == "EMBEDDING_UNAVAILABLE"

    def test_empty_query(self):
        clf = CoarseIntentClassifier(backend=ExplodingBackend())
        p = clf.classify("   ")
        assert p.domain == "unknown"
        assert p.reason_code == "EMPTY_QUERY"


class TestPredictionShape:
    def test_prediction_contract_fields(self):
        """§3 统一输出结构：domain/confidence/second_domain/margin/source/reason_code。"""
        scores = {d: 0.2 for d in load_manifest().domain_names}
        scores["report"] = 0.9
        scores["business"] = 0.6
        clf = CoarseIntentClassifier(backend=StubBackend(scores))
        # 用弱命中 query（「总结」1 次）：避免规则强命中走 override 分支
        # （override 不带 second_domain，契约字段由 embedding 分支体现）
        p = clf.classify("帮我出一份库存总结")
        assert isinstance(p, DomainPrediction)
        dumped = p.model_dump()
        assert set(dumped) >= {
            "domain", "confidence", "second_domain", "second_confidence",
            "margin", "source", "reason_code",
        }
        assert dumped["second_domain"] == "business"
        assert dumped["domain"] == "report"


class TestEmbeddingPrototypeBackend:
    def test_fake_embedding_end_to_end(self):
        """注入离线假 embedding：词面重叠高的 query 归对应域（prototype 语义）。"""
        import hashlib

        def fake_embed(text: str) -> list[float]:
            dim = 512
            vec = [0.0] * dim
            # 字符 bigram 哈希多热向量：同域 example 与 query 共享词面 → 高余弦
            for i in range(len(text) - 1):
                gram = text[i:i + 2]
                idx = int(hashlib.md5(gram.encode()).hexdigest(), 16) % dim
                vec[idx] += 1.0
            return vec

        class FakeEmbedding:
            def embed_documents(self, texts):
                return [fake_embed(t) for t in texts]

            def embed_query(self, text):
                return fake_embed(text)

        backend = EmbeddingPrototypeBackend(embedding_fn=FakeEmbedding())
        clf = CoarseIntentClassifier(backend=backend)
        # 与 general examples（你好/谢谢…）词面完全重叠 → general 胜出
        p = clf.classify("你好")
        assert p.domain == "general"
        # reset 后可重建域心（域声明变更场景）
        backend.reset()
        assert backend._centroids is None
