"""Evaluator 基类 — 所有指标 Provider 的抽象接口。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from backend.evaluation.models import TestCase


class Evaluator(ABC):
    """指标 Provider 抽象基类。

    子类实现 `evaluate()`，接收用例和管线上下文，返回指标字典。
    `name` 用于日志和报告中的 Provider 标识。
    """

    name: str = ""

    @abstractmethod
    def evaluate(self, case: TestCase, ctx: dict[str, Any]) -> dict[str, float]:
        """对单条用例计算指标。

        Args:
            case: 测试用例（含 expected / metadata）
            ctx: 管线上下文，包含检索结果、中间数据等。
                 具体字段由各 Evaluator 文档化。

        Returns:
            dict[str, float] — 指标名到分数的映射。
            空 dict 表示该 Provider 对此用例不适用。
        """
        ...

    def should_run(self, case: TestCase, ctx: dict[str, Any]) -> bool:
        """判断此 Provider 是否应对当前用例运行。默认 True。"""
        return True
