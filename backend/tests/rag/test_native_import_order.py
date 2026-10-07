"""R-P0-1 回归守卫：pipeline 模块导入必须预加载 langchain_text_splitters。

背景：Windows 上 chroma/doc_db 原生库加载后再惰性导入
langchain_text_splitters（→ sentence_transformers → torch）会确定性段错误
（exit 139）。修复 = backend/rag/pipeline.py 模块顶部预导入。
用子进程保证导入顺序干净（pytest 主进程里 lts 可能已被其他测试加载，
断言会假绿）。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_pipeline_preimports_text_splitters_before_native_libs():
    code = (
        "import sys; import backend.rag.pipeline; "
        "assert 'langchain_text_splitters' in sys.modules, "
        "'pipeline.py 未在原生库加载前预导入 langchain_text_splitters（R-P0-1 回归）'"
    )
    # 测试通常从 backend/ 目录运行；子进程需要项目根目录才能导入
    # 顶层 backend 包，不能依赖调用者恰好从仓库根目录启动 pytest。
    env = os.environ.copy()
    project_root = str(Path(__file__).resolve().parents[3])
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (project_root, env.get("PYTHONPATH", "")) if part
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )
    assert result.returncode == 0, (
        f"rc={result.returncode}\nstdout={result.stdout[-500:]}\nstderr={result.stderr[-500:]}"
    )
