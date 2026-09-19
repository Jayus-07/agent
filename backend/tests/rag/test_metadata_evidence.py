"""元数据证据层的确定性和冲突安全测试。"""

from backend.rag.preprocessing.metadata_evidence import extract_evidence, r0_candidate


def test_filename_and_folder_conflict_never_becomes_r0_decision():
    evidence = extract_evidence(
        "正文", "合同制度.docx", "docs/finance/合同制度.docx"
    )

    assert "legal" in evidence.candidates
    assert "policy" in evidence.candidates or "financial" in evidence.candidates
    assert evidence.conflicts
    assert evidence.r0_eligible is False
    assert r0_candidate(evidence) is None


def test_generic_word_is_weak_evidence_only():
    evidence = extract_evidence("本制度适用于订单处理", "unknown.docx", "")

    assert evidence.signals
    assert all(signal.strength != "strong" for signal in evidence.signals)
    assert evidence.r0_eligible is False


def test_every_signal_has_rule_id_and_version():
    evidence = extract_evidence("合同由甲方与乙方签订", "unknown.docx", "")

    assert evidence.signals
    assert all(signal.rule_id and signal.rules_version for signal in evidence.signals)


def test_trace_dict_is_json_friendly_and_preserves_conflicts():
    evidence = extract_evidence("合同制度", "合同制度.docx", "docs/finance/合同制度.docx")
    trace = evidence.to_trace_dict()

    assert trace["rules_version"] == evidence.rules_version
    assert trace["conflicts"] == list(evidence.conflicts)
    assert trace["signals"][0]["source"] in {"filename", "path", "body"}
