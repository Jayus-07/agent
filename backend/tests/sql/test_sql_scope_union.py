# -*- coding: utf-8 -*-
"""UNION 分支 scope 测试。

现状既有行为（fail-closed）：UNION 顶层是 sqlglot exp.Union（非
exp.Select），validator Layer 1 直接拒绝 —— 不存在「只给最外层注入一次
导致分支裸奔」的路径。同时直测注入引擎的 scope 分组逻辑：每个分支
SELECT 是独立 scope，各取各的表引用（未来放开 UNION 时语义已就绪）。
"""
import pytest

import sqlglot
from sqlglot import exp

from backend.sql.policy import (
    SQLPolicyGuard,
    _collect_scope_table_refs,
    _collect_table_refs,
)
from backend.sql.sql_validator import sql_validator, ValidationError

from tests.sql.conftest import FIXTURE_TENANT_TABLE, make_ctx

UNION_SQL = (
    'SELECT project_name FROM demo.tenant_projects WHERE id = 1 '
    'UNION ALL '
    'SELECT project_name FROM demo.tenant_projects WHERE id = 2'
)


class TestUnionRejectedToday:
    def test_union_rejected_by_validator_layer1(self):
        """现状：UNION 语句被 Layer 1 拒绝（fail-closed，分支不可能绕过
        scope 注入）。"""
        ctx = make_ctx("all", tenant_id="tenant-a")
        with pytest.raises(ValidationError) as ei:
            sql_validator.validate(UNION_SQL)
        assert ei.value.layer == 1

    def test_union_branch_cannot_reach_scope_injection(self):
        """Guard 组合既有 validator → UNION 整体不可达注入阶段。"""
        with pytest.raises(ValidationError):
            SQLPolicyGuard().validate_and_rewrite(
                UNION_SQL, make_ctx("all", tenant_id="tenant-a"))


class TestEngineScopeGrouping:
    """直测引擎 scope 分组：每个分支 SELECT 独立收集表引用。"""

    def test_each_branch_select_is_independent_scope(self):
        parsed = sqlglot.parse(UNION_SQL, read="postgres")
        stmt = parsed[0]
        assert isinstance(stmt, exp.Union)
        cte_names = {cte.alias.lower() for cte in stmt.find_all(exp.CTE)}
        branch_scopes = [
            _collect_scope_table_refs(sel, cte_names)
            for sel in stmt.find_all(exp.Select)
        ]
        # 两个分支各自收集到 fixture 表（表名，去别名后一致）
        assert len(branch_scopes) == 2
        for scope_refs in branch_scopes:
            tables = {q.split(".")[-1] for q, _ in scope_refs}
            assert tables == {"tenant_projects"}

    def test_global_table_refs_cover_all_branches(self):
        """全 AST 表引用收集覆盖 UNION 两分支（域判定不分分支遗漏）。"""
        parsed = sqlglot.parse(UNION_SQL, read="postgres")
        refs = _collect_table_refs(parsed[0], set())
        assert refs == {"demo.tenant_projects"}
