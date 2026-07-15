"""
parsers/json_parser.py — JSON 解析器

解析 JSON 原始文本为结构化记录列表。
支持嵌套字段展平: {"parent": {"child": "val"}} → {"parent_child": "val"}
"""

import json
from typing import Any

from backend.data_collection.parsers.base import AbstractParser, ParsedData
from backend.data_collection.fetchers.base import RawData
from backend.shared.logger import logger


class JsonParser(AbstractParser):
    """JSON 数据解析器

    用法:
        parser = JsonParser()
        parsed = parser.parse(raw, schema={"价格": "float", "数量": "int"})
    """

    def __init__(self, flatten_nested: bool = True, separator: str = "_"):
        """
        Args:
            flatten_nested: 是否展平嵌套 dict 字段
            separator: 展平时的连接符
        """
        self._flatten = flatten_nested
        self._sep = separator

    def supports(self, fmt: str) -> bool:
        return fmt.lower() == "json"

    def parse(
        self, raw: RawData, schema: dict[str, Any] | None = None
    ) -> ParsedData:
        """将 JSON 文本解析为 list[dict]

        schema 支持两种用法:
          1. 类型映射: {"售价": float, "数量": int} → 解析时强制转换类型
          2. 字段映射: {"old_name": "new_name"} → 重命名字段
        """
        errors: list[str] = []
        records: list[dict[str, Any]] = []
        schema = schema or {}

        # 拆分为类型映射和字段映射
        type_map: dict[str, type] = {}
        field_map: dict[str, str] = {}
        for k, v in schema.items():
            if isinstance(v, type) or v in (int, float, str, bool):
                type_map[k] = v
            elif isinstance(v, str):
                field_map[k] = v

        try:
            data = json.loads(raw.content)
        except json.JSONDecodeError as e:
            logger.error(f"[JsonParser] JSON 解析失败: {e}")
            return ParsedData(
                source=raw.source,
                records=[],
                record_count=0,
                parse_errors=[f"JSON 解析失败: {str(e)}"],
            )

        # 兼容: 顶层是单个对象 → 包装为列表
        if isinstance(data, dict):
            data = [data]

        if not isinstance(data, list):
            logger.error(f"[JsonParser] 期望 JSON 数组或对象，实际: {type(data)}")
            return ParsedData(
                source=raw.source,
                records=[],
                record_count=0,
                parse_errors=[f"不支持的 JSON 结构: {type(data).__name__}"],
            )

        for i, item in enumerate(data):
            if not isinstance(item, dict):
                errors.append(f"第{i}行: 非字典类型 {type(item).__name__}，跳过")
                continue

            record = self._flatten_dict(item) if self._flatten else dict(item)

            # 应用字段映射（重命名）
            if field_map:
                record = {field_map.get(k, k): v for k, v in record.items()}

            # 应用类型强制
            if type_map:
                for fld, target_type in type_map.items():
                    if fld in record and record[fld] is not None:
                        try:
                            record[fld] = target_type(record[fld])
                        except (ValueError, TypeError):
                            errors.append(
                                f"第{i}行: 字段 '{fld}' 无法转换为 {target_type.__name__}"
                            )

            records.append(record)

        logger.info(
            f"[JsonParser] 解析 {len(data)} 条 → {len(records)} 条成功, "
            f"{len(errors)} 条错误"
        )

        return ParsedData(
            source=raw.source,
            records=records,
            record_count=len(records),
            parse_errors=errors,
        )

    def _flatten_dict(
        self, d: dict[str, Any], parent_key: str = ""
    ) -> dict[str, Any]:
        """展平嵌套字典，如 {"a": {"b": 1}} → {"a_b": 1}"""
        items: list[tuple[str, Any]] = []
        for k, v in d.items():
            new_key = f"{parent_key}{self._sep}{k}" if parent_key else k
            if isinstance(v, dict):
                items.extend(self._flatten_dict(v, new_key).items())
            elif isinstance(v, list):
                # 列表转 JSON 字符串存储（避免过度展开）
                items.append((new_key, json.dumps(v, ensure_ascii=False)))
            else:
                items.append((new_key, v))
        return dict(items)
