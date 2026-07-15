"""引用完整性验证器 — 检查所有 FK 引用指向存在的实体。"""

from __future__ import annotations

from backend.seed.core.validator import BaseValidator, ValidationResult


# 内置 FK 映射: entity_name → {field_name: referenced_entity}
# Generator 可覆盖此映射
BUILTIN_FK_MAP: dict[str, dict[str, str]] = {
    "product":     {"brand_id": "brand", "category_id": "category"},
    "sku":         {"product_id": "product"},
    "listing":     {"sku_id": "sku", "channel_id": "channel"},
    "order":       {"channel_id": "channel", "customer_id": "customer"},
    "order_item":  {"order_id": "order", "sku_id": "sku"},
    "shipment":    {"order_id": "order", "warehouse_id": "warehouse"},
    "shipment_item": {"shipment_id": "shipment", "order_item_id": "order_item", "sku_id": "sku"},
    "tracking_event": {"shipment_id": "shipment"},
    "inventory_level": {"warehouse_id": "warehouse", "sku_id": "sku"},
    "inventory_transaction": {"warehouse_id": "warehouse", "sku_id": "sku"},
    "review":      {"customer_id": "customer", "sku_id": "sku", "order_id": "order"},
    "campaign":    {"ad_account_id": "ad_account"},
    "ad_group":    {"campaign_id": "campaign"},
    "ad":          {"ad_group_id": "ad_group"},
    "spend_record": {"ad_id": "ad"},
    "performance_metric": {"ad_id": "ad", "sku_id": "sku"},
    "knowledge_chunk": {"doc_id": "knowledge_doc"},
    "order":          {"channel_id": "channel", "customer_id": "customer"},
    "order_item":     {"order_id": "order", "sku_id": "sku"},
    "order_event":    {"order_id": "order"},
    "customer_address": {"customer_id": "customer"},
    "review":         {"customer_id": "customer", "sku_id": "sku", "order_id": "order"},
    "inventory_level": {"warehouse_id": "warehouse", "sku_id": "sku"},
    "inventory_transaction": {"warehouse_id": "warehouse", "sku_id": "sku"},
    "inventory_health": {"sku_id": "sku", "warehouse_id": "warehouse"},
    "freight_booking": {"supplier_id": "supplier", "origin_warehouse_id": "warehouse",
                         "dest_warehouse_id": "warehouse"},
    "shipment":       {"order_id": "order", "warehouse_id": "warehouse"},
    "tracking_event": {"shipment_id": "shipment"},
    "return_authorization": {"order_id": "order", "customer_id": "customer"},
    "campaign":       {"ad_account_id": "ad_account"},
    "ad_group":       {"campaign_id": "campaign"},
    "ad":             {"ad_group_id": "ad_group"},
    "spend_record":   {"ad_id": "ad"},
    "performance_metric": {"ad_id": "ad", "sku_id": "sku"},
    "report_execution": {"report_id": "report_definition"},
}


class ReferentialValidator(BaseValidator):
    """检查所有已注册实体的外键引用完整性。

    用法:
        validator = ReferentialValidator()
        result = validator.validate(ctx)
        if not result.is_valid:
            for err in result.errors:
                print(err)
    """

    name = "ReferentialValidator"

    def __init__(self, fk_map: dict[str, dict[str, str]] | None = None):
        """初始化。

        Args:
            fk_map: 自定义 FK 映射。None 时使用内置映射。
        """
        self._fk_map = fk_map or BUILTIN_FK_MAP

    def register_fk(self, entity_name: str, field: str, ref_entity: str) -> None:
        """注册额外的 FK 关系。"""
        if entity_name not in self._fk_map:
            self._fk_map[entity_name] = {}
        self._fk_map[entity_name][field] = ref_entity

    def validate(self, ctx: "GenerationContext") -> ValidationResult:  # noqa: F821
        result = ValidationResult(validator_name=self.name)

        for entity_name, fk_fields in self._fk_map.items():
            entities = ctx.get_entities(entity_name)
            if not entities:
                continue

            for fk_field, ref_entity in fk_fields.items():
                ref_ids = self._get_ref_ids(ctx, ref_entity)
                if not ref_ids:
                    result.warnings.append(
                        f"'{entity_name}.{fk_field}' 引用 '{ref_entity}'，"
                        f"但 '{ref_entity}' 尚未注册任何实体"
                    )
                    continue

                for i, entity in enumerate(entities):
                    fk_value = entity.get(fk_field)
                    if fk_value is None:
                        result.warnings.append(
                            f"{entity_name}[{i}].{fk_field} 值为 None"
                        )
                        continue
                    if fk_value not in ref_ids:
                        result.errors.append(
                            f"{entity_name}[{i}].{fk_field} = '{fk_value}' "
                            f"→ '{ref_entity}' 中不存在"
                        )

        return result

    @staticmethod
    def _get_ref_ids(ctx: "GenerationContext", ref_entity: str) -> set:  # noqa: F821
        """获取引用实体的所有 ID 集合。"""
        entities = ctx.get_entities(ref_entity)
        ids = set()
        for e in entities:
            for key in ("id", "code", "channel_id", "warehouse_id", "supplier_id",
                        "category_id", "brand_id", "sku_id", "product_id",
                        "order_id", "customer_id", "shipment_id", "doc_id"):
                if key in e:
                    ids.add(e[key])
        return ids
