# -*- coding: utf-8 -*-
"""黄金集全量过 validator 回归门（N-06 防盲区，2026-10-06）。

背景：函数正向白名单曾把 AND/OR/CASE 当函数拒绝、缺 sqlglot 类名键——
tests/sql 单元测试 WHERE 全是单条件，套件全绿但黄金集 12/42 被误拒
（审查代理实跑发现）。本门把「黄金集全部 gold_sql 必须通过 validator
（应拒绝条目除外）」固化为回归，任何判定面/白名单改动在此处暴露。
"""
from __future__ import annotations

import io
import json
import os

import pytest

from backend.sql.sql_validator import ValidationError, sql_validator

GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "..", "..", "evaluation", "datasets", "sql",
                      "golden_v2.jsonl")


def _cases():
    cases = []
    for line in io.open(GOLDEN, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        sql = (rec.get("expected") or {}).get("gold_sql")
        if sql:
            cases.append((rec["id"], sql,
                          bool((rec.get("expected") or {}).get("should_reject"))))
    return cases


def test_golden_sqls_pass_validator_or_reject_by_design():
    """全部 gold_sql：非 should_reject 条目必须通过 validator；should_reject
    条目必须被拒。任何一条反向=判定面/白名单回归。"""
    cases = _cases()
    assert len(cases) >= 40, f"黄金集条目异常: {len(cases)}"
    failures = []
    for cid, sql, should_reject in cases:
        try:
            sql_validator.validate(sql)
            if should_reject:
                failures.append(f"{cid}: should_reject 但通过")
        except ValidationError:
            if not should_reject:
                failures.append(f"{cid}: 被 validator 误拒（判定面/白名单回归）")
    assert not failures, "黄金集 validator 门失败:\n" + "\n".join(failures)
