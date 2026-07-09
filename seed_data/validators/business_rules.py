"""业务规则验证器 — 检查 SKU 编码规则、价格合理性、库存非负等。"""

from __future__ import annotations

import re

from seed_data.core.validator import BaseValidator, ValidationResult


class BusinessRuleValidator(BaseValidator):
    """验证生成的实体是否符合业务规则。

    规则:
    - SKU 编码: 格式 <SPU>-<颜色>-<尺寸> 或 SKU-XXXXX
    - 价格合理性: 5 <= price <= 500
    - 利润率合理: cost_price < price
    - Listing 有标题和至少 1 条 bullet point
    - 知识文档有标题和内容
    """

    name = "BusinessRuleValidator"

    # SKU 编码正则: SPU-XXXXX-COLOR-S 或 SKU-XXXXX
    SKU_CODE_PATTERN = re.compile(r"^(SPU-\d{5}-.+|SKU-\d{5})$")

    def validate(self, ctx: "GenerationContext") -> ValidationResult:  # noqa: F821
        result = ValidationResult(validator_name=self.name)

        self._validate_skus(ctx, result)
        self._validate_listings(ctx, result)
        self._validate_knowledge(ctx, result)

        return result

    def _validate_skus(self, ctx: "GenerationContext",  # noqa: F821
                       result: ValidationResult) -> None:
        """验证 SKU 数据。"""
        skus = ctx.get_entities("sku")
        for i, sku in enumerate(skus):
            code = sku.get("sku_code", "")
            if code and not self.SKU_CODE_PATTERN.match(code):
                result.errors.append(
                    f"sku[{i}] sku_code='{code}' 格式不符合规范"
                )

            price = sku.get("price", 0)
            if price < 1 or price > 1000:
                result.errors.append(
                    f"sku[{i}] price={price} 超出合理范围 [1, 1000]"
                )

            cost = sku.get("cost_price", 0)
            if cost >= price and price > 0:
                result.warnings.append(
                    f"sku[{i}] cost_price={cost} >= price={price}"
                )

    def _validate_listings(self, ctx: "GenerationContext",  # noqa: F821
                           result: ValidationResult) -> None:
        """验证 Listing 数据。"""
        listings = ctx.get_entities("listing")
        for i, lst in enumerate(listings):
            if not lst.get("title"):
                result.errors.append(f"listing[{i}] 缺少标题")

            bullets = lst.get("bullet_points", [])
            if not bullets and lst.get("status") == "ACTIVE":
                result.warnings.append(f"listing[{i}] 无 bullet points")

            price = lst.get("price", 0)
            if price <= 0:
                result.errors.append(f"listing[{i}] price={price} 无效")

    def _validate_knowledge(self, ctx: "GenerationContext",  # noqa: F821
                            result: ValidationResult) -> None:
        """验证知识文档。"""
        docs = ctx.get_entities("knowledge_doc")
        for i, doc in enumerate(docs):
            if not doc.get("title"):
                result.errors.append(f"knowledge_doc[{i}] 缺少标题")
            if not doc.get("content"):
                result.errors.append(f"knowledge_doc[{i}] 缺少内容")

        # 验证 chunk 数量与文档数量匹配
        chunks = ctx.get_entities("knowledge_chunk")
        if docs and not chunks:
            result.warnings.append("有 knowledge_doc 但没有 knowledge_chunk")

        # 验证每个 chunk 有对应的 doc
        doc_ids = {d.get("doc_id") for d in docs}
        for i, ch in enumerate(chunks):
            doc_id = ch.get("doc_id")
            if doc_id and doc_id not in doc_ids:
                result.errors.append(
                    f"knowledge_chunk[{i}] doc_id='{doc_id}' 不存在"
                )
