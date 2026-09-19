"""R1 元数据校准分类器的严格加载与保守预测。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from backend.eval.metadata_baseline.lr_features import extract_features, feature_names
from backend.observability.metrics import metadata_classifier_mismatch_total
from backend.rag.preprocessing.metadata_schema import DOC_TYPES
from backend.rag.preprocessing.taxonomy_spec import (
    metadata_rule_version,
    taxonomy_fingerprint,
)
from backend.shared.logger import logger


@dataclass(frozen=True)
class ClassifierPrediction:
    label: str
    confidence: float
    candidates: list[tuple[str, float]]
    accepted: bool
    abstain_reason: str
    model_version: str
    feature_version: str


def _prediction(
    *,
    label: str = "general",
    confidence: float = 0.0,
    candidates: list[tuple[str, float]] | None = None,
    accepted: bool = False,
    abstain_reason: str = "",
    model_version: str = "",
    feature_version: str = "",
) -> ClassifierPrediction:
    return ClassifierPrediction(
        label=label,
        confidence=round(float(confidence), 6),
        candidates=candidates or [],
        accepted=accepted,
        abstain_reason=abstain_reason,
        model_version=model_version,
        feature_version=feature_version,
    )


class MetadataClassifier:
    """带模型卡契约校验的分类器包装器。"""

    def __init__(self, clf: Any, card: Mapping[str, Any]):
        self.clf = clf
        self.card = dict(card)

    @classmethod
    def load(cls, path: Path) -> "MetadataClassifier | None":
        """加载模型；任何版本/结构失配都按 skip 处理，不抛入索引主链路。"""
        artifact = Path(path)
        if not artifact.exists() or not artifact.is_file():
            _record_mismatch("artifact_missing")
            return None
        try:
            import joblib

            payload = joblib.load(artifact)
            if not isinstance(payload, Mapping):
                return _invalid("artifact_not_mapping")
            clf = payload.get("clf")
            card = payload.get("card")
            if clf is None or not isinstance(card, Mapping):
                return _invalid("artifact_shape")
            if not callable(getattr(clf, "predict_proba", None)):
                return _invalid("predict_proba_missing")
            embedding_on = bool(card.get("embedding_features_on", False))
            expected_names = feature_names(embedding_on=embedding_on)
            if list(card.get("feature_names", [])) != expected_names:
                return _invalid("feature_names_mismatch")
            if not _same_fingerprint(
                card.get("taxonomy_fingerprint"), taxonomy_fingerprint()
            ):
                return _invalid("taxonomy_fingerprint_mismatch")
            if str(card.get("rules_version", "")) != metadata_rule_version():
                return _invalid("rules_version_mismatch")
            if not str(card.get("model_version", "")).strip():
                return _invalid("model_version_missing")
            classes = set(str(value) for value in getattr(clf, "classes_", []))
            if not classes or not classes.issubset(set(DOC_TYPES)):
                return _invalid("class_labels_mismatch")
            return cls(clf, card)
        except Exception as exc:
            logger.warning(f"[MetaClassifier] 模型加载失败，按失配跳过: {exc}")
            _record_mismatch("load_error")
            return None

    def predict(
        self,
        text: str,
        filename: str,
        file_path: str,
        embedding_sims: Mapping[str, float] | None,
    ) -> ClassifierPrediction:
        """返回保守预测；未达阈值或 margin 时必须 abstain。"""
        model_version = str(self.card.get("model_version", ""))
        feature_version = str(self.card.get("feature_version", ""))
        embedding_on = bool(self.card.get("embedding_features_on", False))
        if embedding_on and embedding_sims is None:
            return _prediction(
                abstain_reason="embedding_unavailable",
                model_version=model_version,
                feature_version=feature_version,
            )
        try:
            features = extract_features(
                text,
                filename,
                file_path,
                embedding_sims=dict(embedding_sims) if embedding_on else None,
            )
            probabilities = self.clf.predict_proba([features])[0]
            classes = [str(value) for value in self.clf.classes_]
            ranked = sorted(
                zip(classes, probabilities), key=lambda item: float(item[1]), reverse=True
            )
        except Exception as exc:
            logger.warning(f"[MetaClassifier] 预测失败，按 abstain 处理: {exc}")
            return _prediction(
                abstain_reason="prediction_error",
                model_version=model_version,
                feature_version=feature_version,
            )

        candidates = [(label, round(float(probability), 6)) for label, probability in ranked]
        if not ranked:
            return _prediction(
                abstain_reason="empty_probability",
                model_version=model_version,
                feature_version=feature_version,
            )
        top_label, top_probability = ranked[0]
        second_probability = float(ranked[1][1]) if len(ranked) > 1 else 0.0
        margin = float(top_probability) - second_probability
        thresholds = self.card.get("accept_thresholds", {})
        threshold = float(thresholds.get(top_label, 0.98))
        if float(top_probability) < threshold:
            reason = "below_class_threshold"
            accepted = False
        elif margin < float(self.card.get("min_margin", 0.05)):
            reason = "insufficient_margin"
            accepted = False
        else:
            reason = ""
            accepted = True
        return _prediction(
            label=top_label,
            confidence=float(top_probability),
            candidates=candidates,
            accepted=accepted,
            abstain_reason=reason,
            model_version=model_version,
            feature_version=feature_version,
        )


def _same_fingerprint(value: Any, expected: str) -> bool:
    normalized = str(value or "")
    return normalized in {expected, f"sha256:{expected}"}


def _record_mismatch(reason: str) -> None:
    metadata_classifier_mismatch_total.labels(reason=reason).inc()


def _invalid(reason: str) -> None:
    _record_mismatch(reason)
    return None
