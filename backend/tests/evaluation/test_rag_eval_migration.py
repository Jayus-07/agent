"""RAG 评测 KB 迁移与授权边界测试。"""

from backend.config.knowledge_base import authorized_kbs
from backend.evaluation.dataset.fixture_catalog import resolve_eval_profile


def test_old_kb_resolves_to_compatibility_profile():
    profile = resolve_eval_profile("rag_100_docs")

    assert profile.target_kb_id == "rag_eval_kb"
    assert profile.fixture_set == "expanded_100"
    assert profile.deprecated is True


def test_production_authorized_kbs_exclude_unified_eval_kb():
    assert "rag_eval_kb" not in authorized_kbs("customer")
    assert "rag_eval_kb" not in authorized_kbs("employee", "general")


def test_legacy_kb_is_read_only_compatibility_metadata():
    from backend.config.knowledge_base import KNOWLEDGE_BASES

    for kb_id in ("rag_test_kb", "rag_100_docs"):
        info = KNOWLEDGE_BASES[kb_id]
        assert info["read_only"] is True
        assert info["alias_for"] == "rag_eval_kb"
