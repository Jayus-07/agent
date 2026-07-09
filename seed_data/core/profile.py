"""Profile 配置模型 — YAML 驱动的实体规模与分布参数。

Profile 文件在 seed_data/profiles/*.yaml，由本模块加载。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


class EntitySpec:
    """单个实体的生成规格。"""

    def __init__(self, name: str, config: dict):
        self.name = name
        self.count: int = config.get("count", 0)
        self.fixed: bool = config.get("fixed", False)
        self.max_depth: int | None = config.get("max_depth", None)
        self.extra: dict = {k: v for k, v in config.items()
                            if k not in ("count", "fixed", "max_depth")}

    def __repr__(self):
        return f"EntitySpec({self.name}, count={self.count}, fixed={self.fixed})"


class SeedProfile:
    """YAML 驱动的种子数据规模配置。

    用法:
        profile = SeedProfile.from_yaml("seed_data/profiles/tiny.yaml")
        count = profile.entity_count("sku")  # → 30
    """

    def __init__(self, name: str, description: str, seed: int,
                 entities: dict[str, EntitySpec],
                 distributions: dict | None = None):
        self.name = name
        self.description = description
        self.seed = seed
        self.entities = entities
        self.distributions = distributions or {}

    # ---- 工厂方法 ----

    @classmethod
    def from_yaml(cls, path: str | Path) -> "SeedProfile":
        """从 YAML 文件加载 Profile。"""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Profile 文件不存在: {path}")

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        entities = {}
        for name, config in data.get("entities", {}).items():
            entities[name] = EntitySpec(name, config)

        return cls(
            name=data.get("name", path.stem),
            description=data.get("description", ""),
            seed=data.get("seed", 42),
            entities=entities,
            distributions=data.get("distributions", {}),
        )

    @classmethod
    def from_name(cls, profile_name: str) -> "SeedProfile":
        """按名称加载 Profile（自动定位 profiles/ 目录）。

        Args:
            profile_name: "tiny" / "mvp" / "medium" / "full"
        """
        # 先在项目根目录下找
        profile_dir = Path(__file__).parent.parent / "profiles"
        path = profile_dir / f"{profile_name}.yaml"
        if path.exists():
            return cls.from_yaml(path)

        # 再在当前工作目录下找
        cwd_path = Path(os.getcwd()) / f"{profile_name}.yaml"
        if cwd_path.exists():
            return cls.from_yaml(cwd_path)

        raise FileNotFoundError(
            f"找不到 Profile '{profile_name}'，搜索路径: {path}, {cwd_path}"
        )

    # ---- 查询方法 ----

    def entity_count(self, entity_name: str) -> int:
        """获取某实体的生成数量。"""
        spec = self.entities.get(entity_name)
        if spec is None:
            raise KeyError(f"Profile '{self.name}' 中未定义实体 '{entity_name}'")
        return spec.count

    def entity_spec(self, entity_name: str) -> EntitySpec:
        """获取某实体的完整规格。"""
        spec = self.entities.get(entity_name)
        if spec is None:
            raise KeyError(f"Profile '{self.name}' 中未定义实体 '{entity_name}'")
        return spec

    def has_entity(self, entity_name: str) -> bool:
        """检查是否配置了某实体。"""
        return entity_name in self.entities

    def get_distribution(self, domain: str, key: str, default: Any = None) -> Any:
        """获取分布参数。

        例如: profile.get_distribution("order", "status")
              → {"DELIVERED": 0.70, "SHIPPED": 0.12, ...}
        """
        domain_cfg = self.distributions.get(domain, {})
        return domain_cfg.get(key, default)

    # ---- 元信息 ----

    @property
    def entity_names(self) -> list[str]:
        """所有已配置的实体名称列表。"""
        return list(self.entities.keys())

    @property
    def total_entities(self) -> int:
        """所有实体数量之和（粗略规模估算）。"""
        return sum(spec.count for spec in self.entities.values())

    def __repr__(self):
        return f"SeedProfile({self.name}, entities={len(self.entities)}, total={self.total_entities})"
