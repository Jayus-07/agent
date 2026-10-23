"""发布前针对候选语料自动执行已配置业务题集，失败保留旧版。"""

from __future__ import annotations

import uuid

from backend.evaluation.dataset import imports as assets


def policy(kb_id: str):
    records = [r for r in assets.list_records("gatepolicies") if r["kb_id"] == kb_id]
    return max(records, key=lambda row: (row["created_at"], row["policy_id"])) if records else None


def configure(kb_id: str, binding_id: str, enabled: bool, actor: str) -> dict:
    binding, _ = assets.load_binding(binding_id)
    if binding["kb_id"] != kb_id:
        raise ValueError("发布门禁的评测关联不属于目标知识库")
    identity = uuid.uuid4().hex[:24]
    return assets.write_once(
        "gatepolicies",
        identity,
        {
            "policy_id": identity,
            "kb_id": kb_id,
            "binding_id": binding_id,
            "enabled": enabled,
            "actor": actor,
            "created_at": assets.now(),
        },
    )


def check_candidate(kb_id: str, *, stores, doc_id: str, old_doc_id: str, file_path: str, file_hash: str) -> dict | None:
    chosen = policy(kb_id)
    if not chosen or not chosen["enabled"]:
        return None
    from backend.security.task_authorization import resolve_task_authorization

    current = resolve_task_authorization(chosen["actor"].removeprefix("user:"), "default")
    if "super_admin" not in current.principal.roles:
        raise PermissionError("发布门禁配置者已失去管理权限，请重新配置")
    from pathlib import Path

    from backend.evaluation.corpus_snapshots import capture
    from backend.evaluation.managed_execution import execute_binding as _run_binding

    binding, _ = assets.load_binding(chosen["binding_id"])
    snapshot = capture(
        kb_id,
        chosen["actor"],
        candidate={
            "stores": stores,
            "doc_id": doc_id,
            "old_doc_id": old_doc_id,
            "generation": stores.generation,
            "file_path": file_path,
            "file_name": Path(file_path).name,
            "file_hash": file_hash,
        },
    )
    new_id = assets.digest({"policy": chosen["policy_id"], "snapshot": snapshot["snapshot_id"]})[:24]
    assets.write_once(
        "bindings",
        new_id,
        {
            **binding,
            "binding_id": new_id,
            "snapshot_id": snapshot["snapshot_id"],
            "publication_policy_id": chosen["policy_id"],
            "created_at": assets.now(),
        },
    )
    result = _run_binding(new_id, chosen["actor"])
    if result["validity"] != "VALID" or result["gate"] != "PASS":
        raise ValueError(f"候选发布评测未通过，旧版保持可用；run_id={result['run_id']}，gate={result['gate']}")
    return result
