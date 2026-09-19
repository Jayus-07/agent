"""元数据规则证据层。

这里只产生可审计信号，不直接做最终分类，也不调用 LLM、Embedding 或数据库。
"""
from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from typing import Literal, Sequence

from backend.rag.preprocessing.taxonomy_spec import (
    get_taxonomy,
    metadata_rule_version,
)


EvidenceSource = Literal["filename", "path", "body", "structure", "dynamic"]
EvidenceStrength = Literal["strong", "weak"]


@dataclass(frozen=True)
class EvidenceSignal:
    """单条规则命中，所有字段都可写入 trace。"""

    rule_id: str
    value: str
    source: EvidenceSource
    strength: EvidenceStrength
    target: str | None
    score: float
    rules_version: str


@dataclass(frozen=True)
class EvidenceBundle:
    """一个文档的完整规则证据快照。"""

    signals: Sequence[EvidenceSignal]
    candidates: Sequence[str]
    conflicts: Sequence[str]
    features: dict[str, float]
    r0_eligible: bool
    rules_version: str

    def to_trace_dict(self) -> dict:
        return {
            "signals": [asdict(signal) for signal in self.signals],
            "candidates": list(self.candidates),
            "conflicts": list(self.conflicts),
            "features": dict(self.features),
            "r0_eligible": self.r0_eligible,
            "rules_version": self.rules_version,
        }


def _safe_match_value(match: re.Match[str]) -> str:
    return match.group(0)[:120]


def _hint_rule_id(source: Literal["filename", "path"], target: str, hint: str) -> str:
    """将兼容 hint 映射到目标类型的首条 catalog 规则。

    只有 catalog 的 r0_allowlist 能把该兼容 hint 提升为强证据；普通
    文件名/目录词默认仍然只是弱证据。
    """
    del source, hint
    high_precision_rule = {
        "ad_policy": "003",
        "compliance": "004",
        "contract_template": "013",
        "customer_data": "016",
        "financial": "034",
        "legal": "029",
        "product_spec": "017",
        "security": "020",
        "training": "017",
    }.get(target, "001")
    return f"doc_type.{target}.{high_precision_rule}"


def _append_hint_signals(
    signals: list[EvidenceSignal],
    *,
    source: Literal["filename", "path"],
    value: str,
    hints: dict[str, str],
    rules_version: str,
) -> None:
    for hint, target in hints.items():
        if source == "filename":
            matched = hint.lower() in os.path.splitext(value)[0].lower()
        else:
            parts = os.path.dirname(value).lower().replace("\\", "/").split("/")
            matched = hint.lower() in parts
        if not matched:
            continue
        rule_id = _hint_rule_id(source, target, hint)
        signals.append(
            EvidenceSignal(
                rule_id=rule_id,
                value=hint,
                source=source,
                strength=(
                    "strong" if rule_id in get_taxonomy().r0_allowlist else "weak"
                ),
                target=target,
                score=1.0,
                rules_version=rules_version,
            )
        )


def _append_body_signals(
    signals: list[EvidenceSignal],
    text: str,
    rules_version: str,
) -> None:
    taxonomy = get_taxonomy()
    sample = text[:6000].lower()
    for doc_type, rules in taxonomy.doc_type_rules.items():
        if doc_type == "general":
            continue
        for rule in rules:
            match = re.search(rule.pattern, sample)
            if match is None:
                continue
            strong = rule.rule_id in taxonomy.r0_allowlist
            signals.append(
                EvidenceSignal(
                    rule_id=rule.rule_id,
                    value=_safe_match_value(match),
                    source="body",
                    strength="strong" if strong else "weak",
                    target=doc_type,
                    score=float(rule.weight),
                    rules_version=rules_version,
                )
            )


def _conflicts(signals: Sequence[EvidenceSignal]) -> list[str]:
    strong_targets = {
        signal.target for signal in signals
        if signal.strength == "strong" and signal.target
    }
    hint_targets = {
        signal.target for signal in signals
        if signal.source in {"filename", "path"} and signal.target
    }
    conflicts: list[str] = []
    if len(strong_targets) > 1:
        conflicts.append("strong_evidence_targets_conflict")
    if len(hint_targets) > 1:
        conflicts.append("filename_or_path_targets_conflict")
    if strong_targets and hint_targets and not strong_targets.issubset(hint_targets):
        conflicts.append("strong_evidence_disagrees_with_filename_or_path")
    return conflicts


def extract_evidence(
    full_text: str,
    filename: str = "",
    file_path: str = "",
) -> EvidenceBundle:
    """抽取只读证据，不返回最终分类。"""
    taxonomy = get_taxonomy()
    rules_version = metadata_rule_version()
    signals: list[EvidenceSignal] = []
    _append_hint_signals(
        signals,
        source="filename",
        value=filename,
        hints=dict(taxonomy.filename_hints),
        rules_version=rules_version,
    )
    _append_hint_signals(
        signals,
        source="path",
        value=file_path,
        hints=dict(taxonomy.folder_hints),
        rules_version=rules_version,
    )
    _append_body_signals(signals, full_text or "", rules_version)

    scores: dict[str, float] = {}
    for signal in signals:
        if signal.target:
            scores[signal.target] = scores.get(signal.target, 0.0) + signal.score
    candidates = tuple(
        target for target, _ in sorted(
            scores.items(), key=lambda item: (-item[1], item[0])
        )
    )
    conflicts = tuple(_conflicts(signals))
    strong_targets = {
        signal.target for signal in signals
        if signal.strength == "strong" and signal.target
    }
    r0_eligible = len(strong_targets) == 1 and not conflicts
    features = {
        "signal_count": float(len(signals)),
        "strong_signal_count": float(
            sum(signal.strength == "strong" for signal in signals)
        ),
        "candidate_count": float(len(candidates)),
        "conflict_count": float(len(conflicts)),
    }
    return EvidenceBundle(
        signals=tuple(signals),
        candidates=candidates,
        conflicts=conflicts,
        features=features,
        r0_eligible=r0_eligible,
        rules_version=rules_version,
    )


def r0_candidate(evidence: EvidenceBundle) -> str | None:
    """无冲突且由 catalog allowlist 支持的唯一强证据类型。"""
    if not evidence.r0_eligible:
        return None
    targets = {
        signal.target for signal in evidence.signals
        if signal.strength == "strong" and signal.target
    }
    return next(iter(targets)) if len(targets) == 1 else None
