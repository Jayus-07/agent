"""LLM JSON 解析失败指标测试（2026-10-06 批次 C）。

口径锁定：
  - 全策略链失败才 +1（任一层成功不计）；
  - source 为空不计数（调用方未声明来源不产生 unknown 噪声）；
  - strict 语义抛错同时计数；or_empty 与宽松语义同口径。
"""
import prometheus_client
import pytest

from backend.observability.llm_output_metrics import llm_json_parse_fail_total
from backend.shared.json_extractor import (
    extract_json,
    extract_json_or_empty,
    extract_json_strict,
)

_METRIC = "llm_json_parse_fail_total"


def _count(source: str) -> float:
    value = prometheus_client.REGISTRY.get_sample_value(
        _METRIC, {"source": source},
    )
    return value or 0.0


def test_failed_parse_increments_counter():
    before = _count("unit.fail")
    assert extract_json("这不是 JSON", source="unit.fail") is None
    assert _count("unit.fail") == before + 1


def test_successful_parse_does_not_count():
    before = _count("unit.ok")
    assert extract_json('{"a": 1}', source="unit.ok") == {"a": 1}
    assert _count("unit.ok") == before


def test_empty_source_does_not_count():
    before = _count("unknown")
    assert extract_json("垃圾输出") is None
    assert _count("unknown") == before


def test_or_empty_counts_on_failure():
    before = _count("unit.or_empty")
    assert extract_json_or_empty("垃圾输出", source="unit.or_empty") == {}
    assert _count("unit.or_empty") == before + 1


def test_strict_raises_and_counts():
    before = _count("unit.strict")
    with pytest.raises(ValueError):
        extract_json_strict("垃圾输出", source="unit.strict")
    assert _count("unit.strict") == before + 1


def test_repaired_json_counts_nothing():
    # Layer 3 修复（未加引号 key）成功 → 不算失败
    before = _count("unit.repair")
    assert extract_json('{a: 1}', source="unit.repair") == {"a": 1}
    assert _count("unit.repair") == before
