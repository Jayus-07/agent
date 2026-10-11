"""API Router 的导入聚合不应重复，避免再引入多余注册维护噪声。

纯 AST 守卫：不 import 服务、数据库、网络或启动主图。
"""
from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path


def test_route_module_import_list_contains_no_duplicates():
    repo_root = Path(__file__).resolve().parents[3]
    router_file = repo_root / "backend" / "app" / "api" / "router.py"
    tree = ast.parse(router_file.read_text(encoding="utf-8"), filename=str(router_file))

    modules = [
        alias.name
        for stmt in tree.body
        if isinstance(stmt, ast.ImportFrom) and stmt.module == "backend.app.api.routes"
        for alias in stmt.names
    ]
    assert modules, "route module aggregate import was not found"
    duplicates = sorted(name for name, count in Counter(modules).items() if count > 1)
    assert duplicates == [], f"重复导入 API 路由模块: {duplicates}"
