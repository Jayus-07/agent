"""通过新快照实现条目编辑、归档、差异及版本标签。"""

from __future__ import annotations

import json

from backend.evaluation.dataset import imports as assets


def revise(identity: str, version: str, changes: dict, actor: str) -> dict:
    original = assets.read("imports", identity)
    by_id = {case["id"]: case for case in original["cases"]}
    for key, change in changes.items():
        if change is None:
            if key not in by_id:
                raise ValueError(f"条目不存在: {key}")
            by_id[key] = {**by_id[key], "archived": True}
        else:
            if change.get("id") != key:
                raise ValueError("条目标识与修改键不一致")
            by_id[key] = change
    payload = {
        "manifest": {**original["manifest"], "version": version},
        "cases": list(by_id.values()),
    }
    validation = assets.validate_upload("revision.json", json.dumps(payload).encode(), {})
    if not validation["valid"]:
        raise ValueError(json.dumps(validation["errors"], ensure_ascii=False))
    if not any(not case.get("archived") for case in validation["cases"]):
        raise ValueError("至少保留一条未归档用例")
    result = assets.create_import(validation, actor)
    if result["import_id"] == identity:
        raise ValueError("修订必须生成新版本")
    assets.write_once(
        "lineage",
        result["import_id"],
        {
            "parent_import_id": identity,
            "actor": actor,
            "created_at": assets.now(),
        },
    )
    return result


def diff(left: str, right: str) -> dict:
    before, after = assets.read("imports", left), assets.read("imports", right)
    if before["manifest"]["dataset_key"] != after["manifest"]["dataset_key"]:
        raise ValueError("只能比较同一数据集的版本")
    old = {item["id"]: item for item in before["cases"]}
    new = {item["id"]: item for item in after["cases"]}
    return {
        "changes": [
            {"id": key, "before": old.get(key), "after": new.get(key)}
            for key in sorted(old.keys() | new.keys())
            if old.get(key) != new.get(key)
        ],
        "manifest_before": before["manifest"],
        "manifest_after": after["manifest"],
    }


def tag(identity: str, label: str, actor: str) -> dict:
    record = assets.approved_import(identity)
    # 标签也是不可变记录。移动标签必须使用新的标签，CI 始终固定 import_id。
    key = assets.digest({"dataset": record["manifest"]["dataset_key"], "label": label})[:24]
    value = assets.write_once(
        "labels",
        key,
        {
            "dataset_key": record["manifest"]["dataset_key"],
            "label": label,
            "import_id": identity,
            "actor": actor,
            "created_at": assets.now(),
        },
    )
    if value["import_id"] != identity:
        raise ValueError("标签已固定到其他版本，请使用新标签")
    return value
