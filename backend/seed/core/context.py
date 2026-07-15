"""GenerationContext — 全局生成上下文。

跟踪所有已生成的实体，提供 RNG 实例、FK 引用解析、实体采样服务。
整个生成流程共享同一个 Context 实例。
"""

from __future__ import annotations

import random
from typing import Any

from faker import Faker


class GenerationContext:
    """全局生成上下文。

    职责:
    1. 持有 random.Random 和 Faker 实例（保证 seed 可复现）
    2. 存储所有已生成实体（entity_name → list[dict]）
    3. 建立索引（entity_name → {key_field → entity}）用于 FK 解析
    4. 提供实体随机采样（用于建立关系）

    用法:
        ctx = GenerationContext(profile, seed=42)
        ctx.register("brand", {"id": "B001", "name": "MeridiHome"})
        brand = ctx.sample("brand")  # {"id": "B001", "name": "MeridiHome"}
    """

    # FK 解析时搜索的 ID 字段列表（按优先级）
    _ID_KEYS = (
        "id", "code",
        "customer_id", "order_id", "order_item_id", "event_id",
        "product_id", "sku_id", "listing_id",
        "supplier_id", "warehouse_id", "channel_id",
        "category_id", "brand_id",
        "doc_id", "chunk_id",
        "shipment_id", "address_id", "review_id",
        "txn_id", "health_id",
        "report_id", "campaign_id", "ad_id",
    )

    def __init__(self, profile: "SeedProfile", seed: int = 42):  # noqa: F821
        from backend.seed.core.profile import SeedProfile
        self.profile: SeedProfile = profile
        self.seed = seed
        self.rng = random.Random(seed)
        self.faker = Faker(["en_US", "zh_CN"])
        self.faker.seed_instance(seed)

        # _store: entity_name → list[dict]
        self._store: dict[str, list[dict]] = {}
        # _index: entity_name → {key_value → entity}
        self._index: dict[str, dict[str, dict]] = {}

        # 全局递增 ID 计数器
        self._counters: dict[str, int] = {}

    # ---- 注册 ----

    def register(self, entity_name: str, entity: dict, key_field: str = "id") -> None:
        """注册单条实体。"""
        if entity_name not in self._store:
            self._store[entity_name] = []
            self._index[entity_name] = {}

        self._store[entity_name].append(entity)

        key_value = entity.get(key_field)
        if key_value is not None:
            self._index[entity_name][str(key_value)] = entity

    def register_batch(self, entity_name: str, entities: list[dict],
                       key_field: str = "id") -> None:
        """批量注册实体。"""
        if entity_name not in self._store:
            self._store[entity_name] = []
            self._index[entity_name] = {}

        self._store[entity_name].extend(entities)

        for entity in entities:
            key_value = entity.get(key_field)
            if key_value is not None:
                self._index[entity_name][str(key_value)] = entity

    # ---- 查询 ----

    def get_entities(self, entity_name: str) -> list[dict]:
        """获取某类实体的完整列表。"""
        return self._store.get(entity_name, [])

    def get_entity(self, entity_name: str, key_value: str) -> dict | None:
        """按 key 精确查询某条实体。

        先查索引，失败则回退扫描 store（处理 key_field 不是 "id" 的情况）。
        """
        # 索引查询
        idx = self._index.get(entity_name, {})
        result = idx.get(str(key_value))
        if result is not None:
            return result

        # 回退: 扫描 store，尝试所有可能的 ID 字段
        for entity in self._store.get(entity_name, []):
            for key in ("id", "code", "sku_id", "product_id", "order_id",
                        "customer_id", "shipment_id", "doc_id", "chunk_id",
                        "listing_id", "supplier_id", "warehouse_id",
                        "channel_id", "category_id", "brand_id",
                        "report_id", "campaign_id", "ad_id"):
                if entity.get(key) == key_value:
                    return entity
        return None

    def sample(self, entity_name: str, count: int = 1) -> list[dict]:
        """从已注册实体中随机采样（可重复）。"""
        entities = self._store.get(entity_name, [])
        if not entities:
            raise ValueError(f"实体 '{entity_name}' 尚未注册，无法采样")
        return [self.rng.choice(entities) for _ in range(count)]

    def sample_one(self, entity_name: str) -> dict:
        """随机采样一条实体。"""
        return self.sample(entity_name, 1)[0]

    def count(self, entity_name: str) -> int:
        """已注册的某类实体数量。"""
        return len(self._store.get(entity_name, []))

    # ---- FK 引用解析 ----

    def resolve_ref(self, ref: str) -> Any:
        """解析 $ref:entity:index 引用格式。

        Args:
            ref: "$ref:brand:3" 或 "$ref:brand:B001"

        Returns:
            对应实体的 key_field 值
        """
        if not ref.startswith("$ref:"):
            return ref  # 非引用，原样返回

        parts = ref[5:].split(":", 1)
        if len(parts) != 2:
            raise ValueError(f"无效的引用格式: {ref}")

        entity_name, ref_key = parts

        # ref_key 可能是数字索引或字符串 key
        entities = self._store.get(entity_name, [])
        if not entities:
            raise ValueError(f"引用实体 '{entity_name}' 尚未注册（ref={ref}）")

        if ref_key.isdigit():
            idx = int(ref_key)
            if idx >= len(entities):
                raise IndexError(f"引用索引 {idx} 越界，{entity_name} 共 {len(entities)} 条")
            # 返回实体的 key_field（默认 "id"）
            entity = entities[idx]
            # 尝试 id 字段
            for key in self._ID_KEYS:
                if key in entity:
                    return entity[key]
            return entity.get("id", str(idx))
        else:
            # 字符串 key → 直接查询 index
            entity = self.get_entity(entity_name, ref_key)
            if entity is None:
                raise KeyError(f"引用 entity '{entity_name}' key '{ref_key}' 不存在")
            for key in self._ID_KEYS:
                if key in entity:
                    return entity[key]
            return entity.get("id", ref_key)

    # ---- 全量 FK 解析 ----

    def resolve_all_refs(self) -> int:
        """解析所有已注册实体中的 $ref 占位符（原地修改）。

        遍历所有实体的所有字段，将 "$ref:entity:index" 替换为实际 FK 值。
        静默跳过无法解析的引用（保留原值）。

        Returns:
            成功解析的引用数量
        """
        resolved_count = 0
        for entity_name in self._store:
            for entity in self._store[entity_name]:
                for key, value in entity.items():
                    if isinstance(value, str) and value.startswith("$ref:"):
                        try:
                            entity[key] = self.resolve_ref(value)
                            resolved_count += 1
                        except (ValueError, KeyError, IndexError):
                            pass  # 保留原值
        return resolved_count

    # ---- ID 生成 ----

    def next_id(self, entity_name: str, prefix: str = "") -> str:
        """生成递增 ID（全局唯一序号）。

        Args:
            entity_name: 实体类型名（用于计数器隔离）
            prefix: ID 前缀（如 "B"、"CAT"、"SKU"）

        Returns:
            格式: "{prefix}{序号:04d}" 如 "B0001"
        """
        if entity_name not in self._counters:
            self._counters[entity_name] = 0
        self._counters[entity_name] += 1
        return f"{prefix}{self._counters[entity_name]:04d}"

    def reset_counter(self, entity_name: str) -> None:
        """重置某实体的 ID 计数器。"""
        self._counters[entity_name] = 0

    # ---- 工具 ----

    @property
    def entity_names(self) -> list[str]:
        """所有已注册的实体类型名称。"""
        return list(self._store.keys())

    def summary(self) -> dict[str, int]:
        """生成摘要: {entity_name: count}。"""
        return {name: len(entities) for name, entities in self._store.items()}
