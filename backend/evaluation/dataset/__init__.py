"""兼容垫片 — 实际实现已拆分到 loader / validator 子模块。"""

from backend.evaluation.dataset.loader import (
    DATASET_DIR,
    load_dataset,
    load_dataset_directory,
    load_dataset_file,
    select_cases,
)
from backend.evaluation.dataset.validator import (
    validate_dataset,
)
from backend.evaluation.models import TestCase

__all__ = [
    "DATASET_DIR",
    "load_dataset",
    "load_dataset_directory",
    "load_dataset_file",
    "select_cases",
    "validate_dataset",
    "TestCase",
]
