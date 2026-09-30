"""seed 引用完整性 FK 映射 —— 表结构守护（结构病审查 P2-7）。

病史：`BUILTIN_FK_MAP` 是一个 dict 字面量，但历史上被两份清单**首尾相接**拼在
同一个字面量里——35 个键、其中 12 个重复（order / order_item / shipment /
review / campaign / ad / ad_group / spend_record / performance_metric /
tracking_event / inventory_level / inventory_transaction）。

Python 对字面量里的重复键取**后者**，前者被静默丢弃：
  - 改第一份不生效，也不报错；
  - 两份如果哪天不一致，行为会随「谁写在后面」而变，看代码看不出来。

已合并去重为 23 个实体（重复项当时取值逐字相同，行为零变化）。
本文件用 AST 直接解析源码，锁死「不得再出现重复键」——运行期 dict 里已经看不到
重复了，只能从源码层面守。
"""
from __future__ import annotations

import ast
from pathlib import Path

from backend.seed.validators.referential import BUILTIN_FK_MAP, ReferentialValidator

_REFERENTIAL_PY = (
    Path(__file__).resolve().parents[1] / "seed" / "validators" / "referential.py"
)


def _fk_map_source_keys() -> list[str]:
    """从源码 AST 取出字面量的键（保留重复，运行期 dict 取不到）。"""
    tree = ast.parse(_REFERENTIAL_PY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "BUILTIN_FK_MAP":
            return [k.value for k in node.value.keys]
    raise AssertionError("没能在 referential.py 里定位 BUILTIN_FK_MAP 字面量")


def test_no_duplicate_entity_keys_in_source():
    """源码字面量里不得出现重复实体名（修复前实测 12 个重复）。"""
    keys = _fk_map_source_keys()
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    assert not dupes, (
        f"BUILTIN_FK_MAP 出现重复实体（后者会静默覆盖前者）：{dupes}"
    )


def test_source_key_count_matches_runtime():
    """字面量键数必须等于运行期条目数——不等就说明存在被覆盖的重复键。"""
    keys = _fk_map_source_keys()
    assert len(keys) == len(BUILTIN_FK_MAP), (
        f"字面量 {len(keys)} 个键、运行期 {len(BUILTIN_FK_MAP)} 个条目，存在重复覆盖"
    )


def test_entity_set_locked():
    """实体清单锁死（新增/删除实体必须显式改这里，防再次悄悄拼接）。"""
    assert set(BUILTIN_FK_MAP) == {
        "product", "sku", "listing", "knowledge_chunk",
        "order", "order_item", "order_event", "customer_address",
        "shipment", "shipment_item", "tracking_event", "return_authorization",
        "inventory_level", "inventory_transaction", "inventory_health",
        "review",
        "campaign", "ad_group", "ad", "spend_record", "performance_metric",
        "freight_booking",
        "report_execution",
    }


def test_representative_relations_intact():
    """抽查关键外键关系（去重过程不得改变取值）。"""
    assert BUILTIN_FK_MAP["shipment"] == {"order_id": "order", "warehouse_id": "warehouse"}
    assert BUILTIN_FK_MAP["review"] == {
        "customer_id": "customer", "sku_id": "sku", "order_id": "order"
    }
    assert BUILTIN_FK_MAP["performance_metric"] == {"ad_id": "ad", "sku_id": "sku"}
    assert BUILTIN_FK_MAP["report_execution"] == {"report_id": "report_definition"}


def test_validator_defaults_to_builtin_map():
    """不传自定义映射时，校验器用的就是这张表。"""
    assert ReferentialValidator()._fk_map is BUILTIN_FK_MAP
    custom = {"foo": {"bar_id": "bar"}}
    assert ReferentialValidator(custom)._fk_map is custom
