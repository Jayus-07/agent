"""tests/travel/test_plan_diff.py — 版本变更记录派生守护测试（v4 §3.2）

强断言：diff 输出确定性（同输入同输出）、结构冻结、回滚永不删史。
"""
from backend.travel.core.plan_diff import (
    build_change_record,
    diff_poi_placements,
    poi_placements,
    rollback_record,
)


def _itinerary(poi_days: dict[str, int]) -> dict:
    """按 day_index 组装行程 dict（非 POI 项混入，验证被跳过）。"""
    days: dict[int, list] = {}
    for poi_id, day in poi_days.items():
        days.setdefault(day, []).append({"title": poi_id, "kind": "visit",
                                         "poi": {"poi_id": poi_id}})
    return {
        "days": [
            {
                "day_index": day,
                "items": days.get(day, [])
                + [{"title": "午餐", "kind": "meal", "poi": None}],
            }
            for day in sorted(days) or [1]
        ]
    }


class TestPoiPlacements:
    def test_extraction(self):
        it = _itinerary({"poi_a": 1, "poi_b": 1, "poi_c": 2})
        assert poi_placements(it) == {"poi_a": 1, "poi_b": 1, "poi_c": 2}

    def test_empty_itinerary(self):
        assert poi_placements({"days": []}) == {}


class TestDiff:
    def test_added_removed_moved(self):
        old = {"a": 1, "b": 1, "c": 2, "d": 3}
        new = {"a": 1, "c": 1, "e": 2, "d": 3}
        result = diff_poi_placements(old, new)
        assert result["added"] == ["e"]
        assert result["removed"] == ["b"]
        assert result["moved"] == [{"poi_id": "c", "from_day": 2, "to_day": 1}]

    def test_deterministic(self):
        old = {"a": 2, "b": 1}
        new = {"a": 1, "b": 2, "z": 3, "y": 3}
        assert diff_poi_placements(old, new) == diff_poi_placements(old, new)
        # 排序保证：added 不依赖输入顺序
        assert diff_poi_placements(old, new)["added"] == ["y", "z"]


class TestChangeRecord:
    def test_first_version_no_diff(self):
        record = build_change_record(
            new_version=1, parent_version=0,
            old_itinerary=None, new_itinerary=None,
        )
        assert record["version"] == 1
        assert record["change"] == {"added": [], "removed": [], "moved": [],
                                    "brief_fields": []}

    def test_derived_not_handwritten(self):
        old = _itinerary({"disney": 2, "museum": 3})
        new = _itinerary({"museum": 3, "park": 1})
        record = build_change_record(
            new_version=2, parent_version=1,
            old_itinerary=old, new_itinerary=new,
            brief_changed_fields=["lodging", "days"],
            quality="PASS",
        )
        # 「不去迪士尼」的冻结示例：removed 由 diff 派生，不手写
        assert record["change"]["removed"] == ["disney"]
        assert record["change"]["added"] == ["park"]
        assert record["change"]["brief_fields"] == ["days", "lodging"]  # 排序
        assert record["parent_version"] == 1
        assert record["quality"] == "PASS"

    def test_json_serializable(self):
        import json

        record = build_change_record(
            new_version=2, parent_version=1,
            old_itinerary=_itinerary({"a": 1}), new_itinerary=_itinerary({"b": 1}),
        )
        json.dumps(record, ensure_ascii=False)


class TestRollback:
    def test_rollback_record(self):
        record = rollback_record(new_version=5, parent_version=3)
        assert record["change"]["type"] == "rollback"
        assert record["change"]["restored_version"] == 3
        # 回滚也是新版本：parent 指向被回滚前的当前版，历史不删
        assert record["version"] == 5
        assert record["change"]["added"] == []
        assert record["change"]["removed"] == []
