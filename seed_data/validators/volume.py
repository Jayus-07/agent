"""数量级验证器 — 检查生成数量与 Profile 配置的一致性。"""

from __future__ import annotations

from seed_data.core.validator import BaseValidator, ValidationResult


class VolumeValidator(BaseValidator):
    """检查已生成的实体数量是否符合 Profile 配置。

    用法:
        validator = VolumeValidator(tolerance=0.05)  # 5% 容差
        result = validator.validate(ctx)
    """

    name = "VolumeValidator"

    def __init__(self, tolerance: float = 0.0):
        """初始化。

        Args:
            tolerance: 数量偏差容忍度（0.0 = 严格相等，0.05 = 允许 5% 偏差）
        """
        self.tolerance = tolerance

    def validate(self, ctx: "GenerationContext") -> ValidationResult:  # noqa: F821
        result = ValidationResult(validator_name=self.name)

        for entity_name in ctx.entity_names:
            expected = None
            try:
                expected = ctx.profile.entity_count(entity_name)
            except KeyError:
                # Profile 中没有配置的实体，跳过
                continue

            actual = ctx.count(entity_name)

            if self.tolerance == 0:
                if actual != expected:
                    result.errors.append(
                        f"实体 '{entity_name}': 期望 {expected}, 实际 {actual}"
                    )
            else:
                deviation = abs(actual - expected) / expected
                if deviation > self.tolerance:
                    result.errors.append(
                        f"实体 '{entity_name}': 期望 {expected}, 实际 {actual} "
                        f"(偏差 {deviation:.1%} > {self.tolerance:.0%})"
                    )

        return result
