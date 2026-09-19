"""TaxonomySpec 与统一元数据决策契约测试。"""

from pathlib import Path

import pytest

from backend.rag.preprocessing.metadata_schema import DOMAINS, DOC_TYPES
from backend.rag.preprocessing.taxonomy_spec import (
    fingerprint_for_path,
    get_taxonomy,
    normalize_domain,
    taxonomy_fingerprint,
)


def test_taxonomy_has_existing_doc_types_and_domains():
    taxonomy = get_taxonomy()

    assert len(taxonomy.doc_types) == 14
    assert set(taxonomy.doc_types) == set(DOC_TYPES)
    assert set(taxonomy.domains) == set(DOMAINS)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("finance", "financial"),
        ("marketing", "advertising"),
        ("after_sale", "customer"),
        ("unknown-domain", "general"),
    ],
)
def test_domain_legacy_aliases_normalize_to_canonical_values(value, expected):
    assert normalize_domain(value) == expected


def test_taxonomy_fingerprint_changes_when_catalog_changes(tmp_path: Path):
    original = taxonomy_fingerprint()
    altered = tmp_path / "metadata_taxonomy.yaml"
    altered.write_text(get_taxonomy().raw_text + "\n# changed\n", encoding="utf-8")

    assert fingerprint_for_path(altered) != original
