"""test_file_events.py — P0 file 事件契约（events.py file 事件提取，接手批次 2 会话 WIP）

覆盖：路径提取（dict key 命中 / str 正则 / URL 查询串不误match / 去重保序）、
make_file_event 结构、_build_skill_events 集成（log + file 双事件产出）。
"""
from __future__ import annotations

from backend.orchestration.graph.events import (
    _build_skill_events,
    _extract_file_paths,
    make_file_event,
)


# ── _extract_file_paths ──

def test_extract_from_dict_file_path_key():
    out = {"file_path": r"D:\data\export\report.csv", "status": "ok"}
    assert _extract_file_paths(out) == [r"D:\data\export\report.csv"]


def test_extract_from_plain_string_text():
    out = "已导出文件 D:/data/out.csv 和 C:\\tmp\\summary.xlsx，共 2 个"
    assert _extract_file_paths(out) == ["D:/data/out.csv", "C:\\tmp\\summary.xlsx"]


def test_url_query_string_not_matched():
    # URL 查询串里的 .csv= 不应被当成落盘路径（\b 词边界 + 需盘符）
    out = {"output": "fetch https://x.com/a.csv?download=1 done"}
    assert _extract_file_paths(out) == []


def test_dedupe_preserves_order():
    out = {"path": r"D:\a.csv", "output": r"written D:\a.csv ok"}
    assert _extract_file_paths(out) == [r"D:\a.csv"]


def test_non_path_output_returns_empty():
    assert _extract_file_paths({"row_count": 5}) == []
    assert _extract_file_paths("没有任何路径") == []


# ── make_file_event ──

def test_no_files_returns_none():
    assert make_file_event("export", "s1", {"row_count": 3}) is None


def test_event_structure():
    evt = make_file_event("export_skill", "s2", {"file_path": r"D:\out\rep.md"})
    assert evt["event"] == "file"
    assert evt["data"]["node"] == "export_skill"
    assert evt["data"]["step_id"] == "s2"
    assert evt["data"]["files"] == [r"D:\out\rep.md"]
    assert isinstance(evt["data"]["ts"], float)


# ── _build_skill_events 集成：log + file 双事件 ──

def _sr(output):
    return {"step_id": "s1", "status": "success", "description": "导出", "output": output}


def test_skill_events_emits_file_after_log():
    out = {"step_results": {"s1": _sr({"file_path": r"D:\out\a.csv"})}}
    events = list(_build_skill_events("export_skill", out, lambda sr, **kw: {}))
    kinds = [e["event"] for e in events]
    assert kinds == ["log", "file"]  # log 在前，file 紧随
    assert events[1]["data"]["step_id"] == "s1"


def test_skill_events_no_file_no_file_event():
    out = {"step_results": {"s1": _sr({"row_count": 7})}}
    kinds = [e["event"] for e in _build_skill_events("sql_skill", out, lambda sr, **kw: {})]
    assert "file" not in kinds
