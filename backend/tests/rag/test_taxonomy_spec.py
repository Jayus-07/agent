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

    # 2026-10-03 枚举扩展：+travel_guide（旅游攻略，旅游域语料分类）
    assert len(taxonomy.doc_types) == 15
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


def test_travel_filename_v9_set_classifies_thirty_real_documents_as_travel_guide():
    """旅游语料的文件名先验必须覆盖现场 30 条样本。"""
    from backend.rag.preprocessing.metadata import classify_doc_type

    filenames = [
        "福州-景点-九头马民居.md",
        "福州-景点-船政学堂.md",
        "厦门-美食-土笋冻.md",
        "福州-景点-圣寿宝塔.md",
        "厦门-景点-胡里山炮台.md",
        "福州-美食-鱼露.md",
        "厦门-景点-南普陀寺.md",
        "厦门-美食-姜母鸭.md",
        "福州-美食-线面.md",
        "福州-景点-三坊七巷和朱紫坊建筑群.md",
        "厦门-景点-鼓浪屿近代建筑群.md",
        "福州-景点-名山室.md",
        "福州-景点-昭忠祠-(福州).md",
        "福州-景点-昙石山遗址.md",
        "厦门-景点-中山路-(厦门).md",
        "厦门-景点-厦门中山公园.md",
        "福州-景点-华林寺-(福州).md",
        "福州-美食-闽菜.md",
        "福州-美食-鱼丸.md",
        "福州-美食-鼎边糊.md",
        "福州-景点-烟台山-(福州).md",
        "福州-城市-福州.md",
        "福州-美食-扁肉燕.md",
        "厦门-景点-青礁慈济宫.md",
        "厦门-美食-蚵仔煎.md",
        "福州-美食-光饼.md",
        "厦门-景点-厦门破狱斗争旧址.md",
        "福州-景点-福州文庙.md",
        "福州-景点-陈太尉宫.md",
        "福州-景点-泛船浦圣多明我主教座堂.md",
    ]

    assert len(filenames) == 30
    actual = [
        classify_doc_type(
            "本条为旅游语料分类验收样本。",
            filename=name,
            file_path=f"/app/data/docs/travel/general/{name}",
        )
        for name in filenames
    ]
    assert actual == ["travel_guide"] * len(filenames)
