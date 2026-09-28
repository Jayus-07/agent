"""RAG 评估 snapshot 的纯加载与校验。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_SNAPSHOT_DIR = Path(__file__).parent / "snapshots"


def load_rag_snapshot(name: str) -> dict[str, Any]:
    """加载固定 RAG snapshot，拒绝从当前 data/docs 目录推断评估范围。"""
    if not name or Path(name).name != name or Path(name).suffix:
        raise ValueError(f"未知 RAG snapshot: {name!r}")
    path = _SNAPSHOT_DIR / f"{name}.json"
    if not path.is_file():
        raise ValueError(f"RAG snapshot 不存在: {name}")
    try:
        with path.open("r", encoding="utf-8") as file:
            snapshot = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"RAG snapshot 无法读取: {name}") from exc
    if not isinstance(snapshot, dict):
        raise ValueError(f"RAG snapshot 必须是对象: {name}")
    required = ("kb_id", "fixture_set", "version_id", "cases_path")
    missing = [key for key in required if not str(snapshot.get(key) or "").strip()]
    if missing:
        raise ValueError(f"RAG snapshot 缺少字段: {', '.join(missing)}")
    return snapshot
