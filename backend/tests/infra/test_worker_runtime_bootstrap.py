"""Worker 索引运行时启动契约。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_rag_upload_import_does_not_require_mcp_package():
    """索引 Worker 导入上传实现时不应被 MCP 顶层包阻断。"""
    repo_root = Path(__file__).resolve().parents[3]
    code = """
import builtins

real_import = builtins.__import__

def blocked_import(name, *args, **kwargs):
    if name == "mcp_servers" or name.startswith("mcp_servers."):
        raise ModuleNotFoundError("blocked for worker isolation")
    return real_import(name, *args, **kwargs)

builtins.__import__ = blocked_import
from backend.app.api.routes.rag_upload import _do_index_sync  # noqa: F401
print("worker-import-ok")
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root)
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "worker-import-ok" in result.stdout


def test_worker_runtime_registry_bootstrap_refreshes_database(monkeypatch):
    """Worker 启动刷新后，DB 角色覆盖才能进入子进程热路径。"""
    import importlib

    celery_module = importlib.import_module("backend.tasks.celery_app")

    calls: list[str] = []

    def fake_refresh() -> bool:
        calls.append("refresh")
        return True

    monkeypatch.setattr(celery_module, "refresh_worker_model_registry", fake_refresh)

    celery_module._on_worker_process_init()

    assert calls == ["refresh"]
