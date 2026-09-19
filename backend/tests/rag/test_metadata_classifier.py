"""R1 校准分类器的加载、失配和 abstain 语义测试。"""

from pathlib import Path

import joblib
import numpy as np
import pytest

from backend.rag.preprocessing.metadata_classifier import MetadataClassifier
from backend.rag.preprocessing.taxonomy_spec import metadata_rule_version, taxonomy_fingerprint


class _FakeClassifier:
    classes_ = np.array(["legal", "policy"])

    def __init__(self, probabilities=(0.97, 0.03)):
        self.probabilities = probabilities

    def predict_proba(self, features):
        return np.asarray([self.probabilities for _ in features])


def _card(*, thresholds=None, min_margin=0.05, embedding_on=False):
    from backend.eval.metadata_baseline.lr_features import feature_names

    return {
        "model_version": "model-test-v1",
        "taxonomy_fingerprint": taxonomy_fingerprint(),
        "rules_version": metadata_rule_version(),
        "feature_version": "metadata-features-v2",
        "feature_names": feature_names(embedding_on=embedding_on),
        "embedding_features_on": embedding_on,
        "accept_thresholds": thresholds or {"legal": 0.98, "policy": 0.98},
        "min_margin": min_margin,
    }


@pytest.fixture
def fake_loaded_classifier():
    return MetadataClassifier(_FakeClassifier(), _card())


def test_model_fingerprint_mismatch_returns_none(tmp_path: Path, monkeypatch):
    artifact = tmp_path / "lr_model.joblib"
    joblib.dump(
        {"clf": _FakeClassifier(), "card": _card() | {"taxonomy_fingerprint": "old"}},
        artifact,
    )
    monkeypatch.setattr(
        "backend.rag.preprocessing.metadata_classifier.taxonomy_fingerprint",
        lambda: "new",
    )

    assert MetadataClassifier.load(artifact) is None


def test_probability_below_per_class_threshold_abstains(fake_loaded_classifier):
    prediction = fake_loaded_classifier.predict("正文", "unknown.docx", "", None)

    assert prediction.accepted is False
    assert prediction.abstain_reason == "below_class_threshold"


def test_top1_margin_is_required_for_acceptance():
    classifier = MetadataClassifier(
        _FakeClassifier((0.60, 0.40)),
        _card(thresholds={"legal": 0.50, "policy": 0.50}, min_margin=0.25),
    )

    prediction = classifier.predict("冲突正文", "unknown.docx", "", None)

    assert prediction.accepted is False
    assert prediction.abstain_reason == "insufficient_margin"


def test_accepted_prediction_contains_ranked_candidates():
    classifier = MetadataClassifier(
        _FakeClassifier((0.995, 0.005)),
        _card(thresholds={"legal": 0.98, "policy": 0.98}),
    )

    prediction = classifier.predict("合同条款", "unknown.docx", "", None)

    assert prediction.accepted is True
    assert prediction.label == "legal"
    assert prediction.candidates[0][0] == "legal"
    assert prediction.model_version == "model-test-v1"
