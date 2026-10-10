"""travel/core/plan_diff.py — 版本变更记录派生（backend/travel/core/plan_diff.py 冻结）

change 由新旧版本**确定性 diff 派生**，禁止手写变更记录（G2）。回滚 =
以旧版本内容生成新版本并标记 type=rollback，永不删除/改写历史版本。
版本号盖章本体仍在 models/itinerary.py::stamp_version（生产资产不动），
本模块只负责派生 change 摘要（进 checkpoint history）。

输入一律是行程的 dict 形态（checkpoint 序列化纪律：dict 进 dict 出）。
"""
from __future__ import annotations


def poi_placements(itinerary: dict) -> dict[str, int]:
    """从行程 dict 提取 poi_id → day_index 映射（非 POI 项跳过）。"""
    placements: dict[str, int] = {}
    for day in itinerary.get("days") or []:
        day_index = day.get("day_index")
        if day_index is None:
            continue
        for item in day.get("items") or []:
            poi = item.get("poi")
            if not poi:
                continue
            poi_id = poi.get("poi_id")
            if poi_id:
                placements[poi_id] = day_index
    return placements


def diff_poi_placements(
    old: dict[str, int], new: dict[str, int]
) -> dict:
    """两版 POI 分布的确定性 diff（输出列表均排序，保证可复现）。"""
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    moved = sorted(
        (
            {"poi_id": poi_id, "from_day": old[poi_id], "to_day": new[poi_id]}
            for poi_id in set(old) & set(new)
            if old[poi_id] != new[poi_id]
        ),
        key=lambda m: (m["poi_id"], m["from_day"], m["to_day"]),
    )
    return {"added": added, "removed": removed, "moved": moved}


def build_change_record(
    *,
    new_version: int,
    parent_version: int,
    old_itinerary: dict | None,
    new_itinerary: dict | None,
    brief_changed_fields: list[str] | None = None,
    change_reason: str = "user_request",
    quality: str = "",
) -> dict:
    """构造进 history 的变更记录（backend/travel/core/plan_diff.py 冻结结构）。

    old_itinerary=None 表示首版（无 parent diff）；brief_changed_fields
    由指纹机制传入（brief_changed_fields 既有口径），本模块不重复推导。
    """
    if old_itinerary is None or new_itinerary is None:
        poi_changes: dict = {"added": [], "removed": [], "moved": []}
    else:
        poi_changes = diff_poi_placements(
            poi_placements(old_itinerary), poi_placements(new_itinerary)
        )
    return {
        "version": new_version,
        "parent_version": parent_version,
        "change": {
            **poi_changes,
            "brief_fields": sorted(brief_changed_fields or []),
        },
        "change_reason": change_reason,
        "quality": quality,
    }


def rollback_record(
    *,
    new_version: int,
    parent_version: int,
    change_reason: str = "user_rollback",
    restored_version: int | None = None,
) -> dict:
    """回滚 = 以旧版本内容生成新版本（内容还原由调用方经 stamp_version
    落实），change 标记 rollback 类型，历史版本永不删除。

    restored_version：实际内容来源版本。缺省等于 parent_version（紧邻上一版
    撤销的原始语义）；「恢复到任意历史版」时 parent 是当前最新版、内容来自
    更早的 restored_version，两者必须分开记录。"""
    return {
        "version": new_version,
        "parent_version": parent_version,
        "change": {
            "type": "rollback",
            "restored_version": restored_version if restored_version is not None else parent_version,
            "added": [],
            "removed": [],
            "moved": [],
            "brief_fields": [],
        },
        "change_reason": change_reason,
        "quality": "",
    }
