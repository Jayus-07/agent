"""PG 表名标识符白名单守卫测试（audit-remediation-r1 P3/A2）。

背景：多个 PG store 用 ``*_TABLE_PREFIX`` 环境变量拼表名后 f-string 插入
DDL。标识符不可参数化，统一收口到 ``backend/shared/pg_identifier.py``
白名单校验（对齐 price_page_monitor / operation_log registry_table 既有口径）。

本文件锁定：
1. 助手行为——默认/合法前缀放行，注入形态前缀 fail-closed；
2. 五个 store 源码级防回退——表名组合必须走助手，不允许绕过。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.shared.pg_identifier import pg_table_name

BACKEND = Path(__file__).resolve().parents[1]

# 五个 A2 分诊确认的 f-string 表名拼接点（扫描告警 finding 逐条对应）
_GUARDED_STORES = [
    "rag/indexing/operation_log_pg.py",
    "rag/preprocessing/keyword_store_pg.py",
    "observability/llm_usage_store_pg.py",
    "observability/trace_store_pg.py",
    "observability/analytics_store_pg.py",
]


def test_default_empty_prefix_returns_suffix_only(monkeypatch):
    monkeypatch.delenv("OBS_DB_PG_TABLE_PREFIX", raising=False)
    assert pg_table_name("OBS_DB_PG_TABLE_PREFIX", "trace_store") == "trace_store"


def test_valid_prefix_is_composed(monkeypatch):
    monkeypatch.setenv("RAG_STORES_PG_TABLE_PREFIX", "test_")
    assert (
        pg_table_name("RAG_STORES_PG_TABLE_PREFIX", "keyword_rules")
        == "test_keyword_rules"
    )


@pytest.mark.parametrize(
    "prefix",
    [
        'x"; DROP TABLE trace_store; --',  # 经典注入尾巴
        "t;drop",  # 分号
        "t drop",  # 空白
        "sch.t",  # 点号（schema 限定本就被 idx_{t}_* 命名规则排除）
        't"',  # 裸引号
        "t-",  # 连字符
        "1t",  # 数字开头
    ],
)
def test_injection_like_prefix_fails_closed(monkeypatch, prefix):
    monkeypatch.setenv("OBS_DB_PG_TABLE_PREFIX", prefix)
    with pytest.raises(ValueError, match="不是合法标识符"):
        pg_table_name("OBS_DB_PG_TABLE_PREFIX", "trace_store")


def test_all_guarded_stores_compose_table_via_helper():
    """源码级防回退：五个 store 不允许出现直拼 os.getenv 前缀的表名。"""
    for rel in _GUARDED_STORES:
        src = (BACKEND / rel).read_text(encoding="utf-8")
        assert "pg_table_name(" in src, f"{rel} 未接入 pg_table_name 白名单助手"
        # 直拼形态：self._table = os.getenv("<PREFIX>", "") + "..."
        leftover = re.search(
            r"self\._table\s*=\s*os\.getenv\(", src
        )
        assert leftover is None, f"{rel} 仍存在绕过助手的表名直拼"
