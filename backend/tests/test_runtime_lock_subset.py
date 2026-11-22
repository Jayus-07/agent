"""test_runtime_lock_subset.py — 生产运行时依赖子集契约守卫（任务③镜像瘦身）。

requirements-lock-runtime.txt 是 requirements-lock.txt 的「生产 import 闭包」子集：
Dockerfile 生产镜像只装子集；全量 lock 保留给 CI pip install 与依赖重锁。
本测试双向守卫：
  1. 子集 ⊆ 全量（name+版本完全一致，防止重锁后漂移）；
  2. 关键生产依赖必须在子集（OCR/PG/checkpoint/kafka/MCP/评测执行器等硬依赖）；
  3. 评测/CI 工具链必须不在子集（防止误加回生产镜像）。
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FULL_LOCK = REPO_ROOT / "requirements-lock.txt"
RUNTIME_LOCK = REPO_ROOT / "requirements-lock-runtime.txt"

_REQ_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.\-]*)==(.+)$")


def _parse_lock(path: Path) -> dict[str, str]:
    """name(PEP503 归一) → 版本;忽略空行与注释。"""
    entries: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = _REQ_RE.match(s)
        assert m, f"{path.name} 存在非 pin 格式行: {s!r}"
        name = m.group(1).lower().replace("-", "_").replace(".", "_")
        entries[name] = m.group(2).strip()
    assert entries, f"{path.name} 为空"
    return entries


def test_runtime_lock_is_subset_of_full_lock():
    runtime = _parse_lock(RUNTIME_LOCK)
    full = _parse_lock(FULL_LOCK)
    extra = sorted(set(runtime) - set(full))
    drift = sorted(
        name for name, ver in runtime.items()
        if name in full and full[name] != ver
    )
    assert not extra, f"runtime 子集出现全量 lock 没有的包: {extra}"
    assert not drift, f"runtime 子集与全量 lock 版本漂移: {drift}"


def test_runtime_lock_keeps_hard_production_dependencies():
    runtime = _parse_lock(RUNTIME_LOCK)

    # OCR 链路(pdf_parser 兜底)+ 向量库 + checkpoint + 消息队列 + MCP
    for name in (
        "torch", "transformers", "sentence_transformers",  # 本地模型显式模式
        "pymupdf", "python_docx", "pillow", "pyyaml",
        "rapidocr_onnxruntime", "opencv_python", "onnxruntime",
        "psycopg", "psycopg_binary", "pgvector", "sqlalchemy", "alembic",
        "langgraph_checkpoint_postgres", "kafka_python", "fastmcp",
        "opentelemetry_exporter_otlp_proto_http",
        "ragas", "crawl4ai", "celery", "redis", "uvicorn", "fastapi",
    ):
        assert name in runtime, (
            f"关键生产依赖 {name} 不在 runtime 子集——若已确认可移除, "
            f"请先从生产代码删除对应 import 并更新本白名单"
        )


def test_runtime_lock_excludes_eval_only_toolchain():
    runtime = _parse_lock(RUNTIME_LOCK)
    # 注意:matplotlib(business_report 图表)、faker(seed 种子数据)、
    # datasets(ragas 评测执行器硬依赖)是生产功能依赖,不在本名单;
    # chromadb 仅 backend/scripts 运维清理脚本使用,该目录已从镜像依赖
    # 闭包排除(需要时容器内临时安装)。
    for name in (
        "pytest", "coverage", "streamlit", "hanlp", "spacy", "unstructured",
        "chromadb", "py_spy",
        "tree_sitter", "kubernetes", "gitpython",
    ):
        assert name not in runtime, (
            f"评测/CI 工具链 {name} 误入 runtime 子集(生产代码无 import)"
        )
