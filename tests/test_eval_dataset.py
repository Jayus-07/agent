"""测试 evaluation/dataset.py 的加载和校验逻辑。"""
import pytest
from pathlib import Path
from evaluation.dataset import load_dataset, validate_dataset, DATASET_DIR
from evaluation.models import TestCase


class TestLoadDataset:
    def test_load_planner(self):
        cases = load_dataset("planner")
        assert len(cases) >= 15
        assert all(isinstance(c, TestCase) for c in cases)
        assert all(c.module == "planner" for c in cases)

    def test_load_rag(self):
        cases = load_dataset("rag")
        assert len(cases) >= 25
        assert all(c.module == "rag" for c in cases)

    def test_load_sql(self):
        cases = load_dataset("sql")
        assert len(cases) >= 15
        assert all(c.module == "sql" for c in cases)

    def test_load_e2e(self):
        cases = load_dataset("e2e")
        assert len(cases) >= 15
        assert all(c.module == "e2e" for c in cases)

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            load_dataset("nonexistent")  # type: ignore


class TestValidateDataset:
    def test_valid_dataset_passes(self):
        cases = load_dataset("planner")
        errors = validate_dataset(cases)
        assert errors == []

    def test_duplicate_ids_detected(self):
        cases = [
            TestCase(id="P001", question="q1", module="planner"),
            TestCase(id="P001", question="q2", module="planner"),
        ]
        errors = validate_dataset(cases)
        assert any("duplicate" in e.lower() for e in errors)

    def test_missing_question_detected(self):
        cases = [
            TestCase(id="X001", question="", module="planner"),
        ]
        errors = validate_dataset(cases)
        assert any("question" in e.lower() for e in errors)

    def test_all_rag_has_expected(self):
        cases = load_dataset("rag")
        errors = validate_dataset(cases)
        assert errors == []
        for c in cases:
            assert "relevant_docs" in c.expected or "min_relevant_chunks" in c.expected
