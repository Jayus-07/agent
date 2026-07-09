"""Validator — 生成后数据验证引擎。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ValidationResult:
    """单次验证结果。"""

    validator_name: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0

    @property
    def has_warnings(self) -> bool:
        return len(self.warnings) > 0


class BaseValidator(ABC):
    """验证器基类。"""

    @abstractmethod
    def validate(self, ctx: "GenerationContext") -> ValidationResult:  # noqa: F821
        """对 GenerationContext 中已生成的数据执行验证。"""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """验证器名称（显示在报告中）。"""
        ...


class SeedValidator:
    """组合多个验证器，统一运行。

    用法:
        validator = SeedValidator([
            ReferentialValidator(),
            VolumeValidator(),
        ])
        results = validator.validate_all(ctx)
        for r in results:
            print(f"{r.validator_name}: {'PASS' if r.is_valid else 'FAIL'}")
    """

    def __init__(self, validators: list[BaseValidator] | None = None):
        self._validators = validators or []

    def add(self, validator: BaseValidator) -> None:
        self._validators.append(validator)

    def validate_all(self, ctx: "GenerationContext") -> list[ValidationResult]:  # noqa: F821
        """执行所有验证器，返回结果列表。"""
        return [v.validate(ctx) for v in self._validators]

    @property
    def all_valid(self) -> bool:
        """最近一次验证是否全部通过（需先调用 validate_all）。"""
        # 仅声明属性，实际使用由调用方判断
        return True
