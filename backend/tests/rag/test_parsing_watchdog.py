"""parsing 僵尸看门狗的纯规则契约。"""

from datetime import datetime, timezone


def test_select_stale_parsing_uses_path_and_cutoff_and_skips_recent_rows():
    from backend.rag.indexing.parsing_watchdog import select_stale_parsing

    now = datetime(2026, 10, 3, 5, 0, tzinfo=timezone.utc)
    rows = [
        {"file_path": "/old", "doc_id": "old", "status": "parsing",
         "updated_at": "2026-10-03 03:59:59"},
        {"file_path": "/recent", "doc_id": "recent", "status": "parsing",
         "updated_at": "2026-10-03 04:00:01"},
        {"file_path": "/active", "doc_id": "active", "status": "active",
         "updated_at": "2026-10-03 01:00:00"},
    ]
    assert select_stale_parsing(rows, now=now, timeout_seconds=3600) == [rows[0]]


def test_select_stale_parsing_returns_copies_in_path_order():
    from backend.rag.indexing.parsing_watchdog import select_stale_parsing

    rows = [
        {"file_path": "/z", "doc_id": "z", "status": "parsing",
         "updated_at": "2026-10-01 00:00:00"},
        {"file_path": "/a", "doc_id": "a", "status": "parsing",
         "updated_at": "2026-10-01 00:00:00"},
    ]
    result = select_stale_parsing(rows, now=datetime(2026, 10, 3, tzinfo=timezone.utc),
                                  timeout_seconds=3600)
    result[0]["status"] = "failed"
    assert [row["file_path"] for row in result] == ["/a", "/z"]
    assert rows[1]["status"] == "parsing"
