"""导入模板单一事实源的契约测试。"""

import csv
import io
import json

import pytest

from backend.evaluation_v2.import_templates import build_template


def test_rag_jsonl_template_matches_import_case_schema():
    filename, content, media_type = build_template("rag", fmt="jsonl")

    assert filename == "rag-dataset-template.jsonl"
    assert media_type == "application/x-ndjson"
    cases = [json.loads(line) for line in content.decode("utf-8").splitlines() if line.strip()]
    assert len(cases) == 2
    for case in cases:
        assert case["question"].strip()
        expected = case["expected"]
        assert expected["behavior"] == "answer"
        assert expected["reference_answer"].strip()
        assert expected["required_facts"]
        for anchor in expected["evidence"]:
            assert anchor["doc_id"].strip()
            assert anchor["quote"].strip()
        assert case["metadata"]["tier"]
        assert case["metadata"]["query_type"]


def test_csv_template_carries_bom_and_standard_columns():
    filename, content, media_type = build_template("cs", fmt="csv")

    assert filename == "cs-dataset-template.csv"
    assert media_type.startswith("text/csv")
    raw = content.decode("utf-8-sig")
    assert content.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM，Excel 打开不乱码
    reader = csv.DictReader(io.StringIO(raw))
    assert reader.fieldnames is not None
    assert {"id", "module", "question", "expected", "metadata"}.issubset(set(reader.fieldnames))
    rows = list(reader)
    assert len(rows) == 2
    for row in rows:
        expected = json.loads(row["expected"])
        assert expected["behavior"] == "answer"
        assert expected["required_facts"]
        # 非 RAG 用例不允许绑定知识库证据（与转正校验同口径）
        assert "evidence" not in expected
        assert row["module"] == "cs"


def test_fields_doc_covers_both_formats_key_fields():
    _, content, _ = build_template("rag", fmt="fields")

    text = content.decode("utf-8")
    for keyword in ("required_facts", "evidence", "doc_id", "quote", "behavior", "csv_mapping", "CSV UTF-8"):
        assert keyword in text, f"字段说明缺少 {keyword}"


def test_invalid_module_format_combinations_rejected():
    with pytest.raises(ValueError, match="JSONL"):
        build_template("cs", fmt="jsonl")
    with pytest.raises(ValueError, match="JSONL"):
        build_template("rag", fmt="csv")
    with pytest.raises(ValueError, match="模板格式"):
        build_template("rag", fmt="xlsx")
