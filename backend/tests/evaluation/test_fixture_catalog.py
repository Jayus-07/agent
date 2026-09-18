"""统一 RAG 评测语料 catalog 的契约测试。"""

import pytest

from backend.evaluation.dataset.fixture_catalog import (
    RAG_EVAL_KB_ID,
    catalog_from_dict,
    load_fixture_catalog,
    resolve_eval_profile,
)


def test_catalog_has_one_kb_and_known_fixture_sets():
    """若统一 manifest 漏映射任一语料集合，评测范围会被静默缩小。"""
    catalog = load_fixture_catalog()

    catalog.validate()

    assert catalog.kb_id == RAG_EVAL_KB_ID
    assert {document.fixture_set for document in catalog.documents()} >= {
        "baseline",
        "expanded_100",
    }
    assert len(catalog.documents("baseline")) == 16
    assert len(catalog.documents("expanded_100")) == 100


def test_catalog_rejects_duplicate_fixture_doc_id():
    """若稳定 ID 重复，重入库无法可靠判断同一份测试资产。"""
    catalog = catalog_from_dict(
        {
            "kb_id": RAG_EVAL_KB_ID,
            "documents": [
                {
                    "fixture_doc_id": "same",
                    "fixture_set": "baseline",
                    "source_file": "a.md",
                    "format": "md",
                },
                {
                    "fixture_doc_id": "same",
                    "fixture_set": "expanded_100",
                    "source_file": "b.md",
                    "format": "md",
                },
            ],
        }
    )

    with pytest.raises(ValueError, match="fixture_doc_id"):
        catalog.validate()


@pytest.mark.parametrize(
    ("document", "error"),
    [
        (
            {
                "fixture_doc_id": "unknown-set",
                "fixture_set": "ad_hoc",
                "source_file": "a.md",
                "format": "md",
            },
            "fixture_set",
        ),
        (
            {
                "fixture_doc_id": "mixed-kb",
                "fixture_set": "baseline",
                "source_file": "a.md",
                "format": "md",
                "kb_id": "rag_100_docs",
            },
            "kb_id",
        ),
    ],
)
def test_catalog_rejects_invalid_document_scope(document, error):
    """若集合或 KB 边界被绕过，评测结果会混入错误语料。"""
    catalog = catalog_from_dict({"kb_id": RAG_EVAL_KB_ID, "documents": [document]})

    with pytest.raises(ValueError, match=error):
        catalog.validate()


def test_catalog_rejects_missing_source_file():
    """若源文件缺失仍允许 catalog 生效，后续入库会产生半成品。"""
    catalog = catalog_from_dict(
        {
            "kb_id": RAG_EVAL_KB_ID,
            "documents": [
                {
                    "fixture_doc_id": "missing-file",
                    "fixture_set": "baseline",
                    "source_file": "does-not-exist.md",
                    "format": "md",
                }
            ],
        }
    )

    with pytest.raises(ValueError, match="source_file"):
        catalog.validate()


def test_legacy_kb_resolves_to_its_compatibility_profile():
    """若旧 KB 失去固定映射，迁移期命令会导向错误的语料范围。"""
    profile = resolve_eval_profile("rag_100_docs")

    assert profile.target_kb_id == RAG_EVAL_KB_ID
    assert profile.fixture_set == "expanded_100"
    assert profile.deprecated is True
