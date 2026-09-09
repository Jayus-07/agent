"""兼容垫片 — 实际实现已移至 runners/registry.py。"""
from backend.evaluation.runners.registry import (
    register_runner, get_runner, list_registered,
)

__all__ = ["register_runner", "get_runner", "list_registered"]
