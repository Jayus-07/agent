"""Trace Chunk 元数据展示映射测试。"""

from backend.rag.indexing.chunk_detail import build_chunk_detail_items


def test_legacy_contract_parent_scope_uses_parent_leaf_relations():
    rows = [
        {
            "chunk_index": 0,
            "chunk_id": "doc_0",
            "granularity": "parent",
            "parent_chunk_id": "",
            "section_title": "技术服务合同",
            "chunk_metadata": {
                "chunk_type": "contract_context",
                "section_path": ["技术服务合同"],
                "section_title": "技术服务合同",
            },
        },
        {
            "chunk_index": 1,
            "chunk_id": "doc_1",
            "granularity": "leaf",
            "parent_chunk_id": "doc_0",
            "section_title": "第一条 服务内容",
            "chunk_metadata": {"section_title": "第一条 服务内容"},
        },
        {
            "chunk_index": 2,
            "chunk_id": "doc_2",
            "granularity": "leaf",
            "parent_chunk_id": "doc_0",
            "section_title": "第二条 服务费用",
            "chunk_metadata": {"section_title": "第二条 服务费用"},
        },
    ]

    items = build_chunk_detail_items(rows)

    assert items[0]["granularity"] == "parent"
    assert items[0]["parent_scope"] == "document"
    assert items[0]["document_title"] == "技术服务合同"
    assert items[0]["section_title"] == ""
    assert [item["section_title"] for item in items[1:]] == [
        "第一条 服务内容", "第二条 服务费用",
    ]


def test_parent_section_title_comes_from_structured_children():
    rows = [
        {
            "chunk_index": 0,
            "chunk_id": "doc_0",
            "granularity": "parent",
            "parent_chunk_id": "",
            "section_title": "",
            "chunk_metadata": {
                "parent_scope": "section",
                "section_titles": ["第三条 付款方式"],
            },
        },
        {
            "chunk_index": 1,
            "chunk_id": "doc_1",
            "granularity": "leaf",
            "parent_chunk_id": "doc_0",
            "section_title": "第三条 付款方式",
            "chunk_metadata": {"section_title": "第三条 付款方式"},
        },
        {
            "chunk_index": 2,
            "chunk_id": "doc_2",
            "granularity": "leaf",
            "parent_chunk_id": "another_parent",
            "section_title": "第四条 违约责任",
            "chunk_metadata": {"section_title": "第四条 违约责任"},
        },
    ]

    item = build_chunk_detail_items(rows)[0]

    assert item["parent_scope"] == "section"
    assert item["section_title"] == "第三条 付款方式"


def test_unknown_parent_scope_does_not_guess_from_section_title():
    rows = [{
        "chunk_index": 0,
        "chunk_id": "doc_0",
        "granularity": "parent",
        "parent_chunk_id": "",
        "section_title": "可能是文档标题",
        "chunk_metadata": {"chunk_type": "legacy_parent"},
    }]

    item = build_chunk_detail_items(rows)[0]

    assert item["parent_scope"] == ""
    assert item["section_title"] == ""


def test_chunk_detail_exposes_source_and_split_audit_metadata_only():
    rows = [{
        "chunk_index": 3,
        "chunk_id": "doc_3",
        "granularity": "leaf",
        "parent_chunk_id": "doc_0",
        "content": "表格第 4 行",
        "chunk_metadata": {
            "source_format": "pdf",
            "parser_version": "pdf-parser-v2",
            "chunk_policy_version": "policy-v3",
            "index_generation": "generation-7",
            "source_locators_json": [{"page": 4, "row": 4}],
            "table_id": "table-1",
            "row_index": 4,
            "chunk_tokens": 128,
            "embedding_version": "embed-v4",
            "file_path": "C:/private/source.pdf",
            "tenant_id": "internal-tenant-id",
        },
    }]

    item = build_chunk_detail_items(rows)[0]

    assert item["chunk_id"] == "doc_3"
    assert item["granularity"] == "leaf"
    assert item["parent_chunk_id"] == "doc_0"
    assert item["metadata"] == {
        "source_format": "pdf",
        "parser_version": "pdf-parser-v2",
        "chunk_policy_version": "policy-v3",
        "index_generation": "generation-7",
        "source_locators_json": [{"page": 4, "row": 4}],
        "table_id": "table-1",
        "row_index": 4,
        "chunk_tokens": 128,
        "embedding_version": "embed-v4",
    }
