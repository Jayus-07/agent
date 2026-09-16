"""R-P0-1 回归守卫：pipeline 模块导入必须预加载 langchain_text_splitters。

背景：Windows 上 chroma/doc_db 原生库加载后再惰性导入
langchain_text_splitters（→ sentence_transformers → torch）会确定性段错误
（exit 139）。修复 = backend/rag/pipeline.py 模块顶部预导入。
用子进程保证导入顺序干净（pytest 主进程里 lts 可能已被其他测试加载，
断言会假绿）。
"""
from __future__ import annotations

import subprocess
import sys


def test_pipeline_preimports_text_splitters_before_native_libs():
    code = (
        "import sys; import backend.rag.pipeline; "
        "assert 'langchain_text_splitters' in sys.modules, "
        "'pipeline.py 未在原生库加载前预导入 langchain_text_splitters（R-P0-1 回归）'"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, (
        f"rc={result.returncode}\nstdout={result.stdout[-500:]}\nstderr={result.stderr[-500:]}"
    )
