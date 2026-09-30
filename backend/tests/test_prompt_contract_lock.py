"""Prompt 契约 lock 守卫测试（治理验收 #3）

两条防线：
1. DB 可达时：仓库 prompts.lock.json 必须与 PG 发布状态一致——发布/回滚
   后不带 lock 提交即红（与 Tool lock 同款原子提交纪律）。
2. 无 DB（CI）：lock 文件存在且结构完备（version/prompts/每项三字段）。

漂移语义见 gen_prompt_contract_lock.check：active_version 变化=发布未留痕；
同版本 template_hash 变化=版本表被手工 DML（不可变约束被绕过的信号）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LOCK_PATH = REPO_ROOT / "backend" / "prompts.lock.json"


def _db_reachable() -> bool:
    from backend.scripts.gen_prompt_contract_lock import derive_snapshot

    return derive_snapshot() is not None


class TestLockStructure:
    def test_lock_file_exists_and_wellformed(self):
        assert LOCK_PATH.exists(), (
            "backend/prompts.lock.json 缺失：运行 python -m backend.scripts."
            "gen_prompt_contract_lock 生成并提交")
        data = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
        assert data["version"] == 1
        assert data["prompt_count"] == len(data["prompts"]) > 0
        assert data["source"].startswith("postgres")
        for key, entry in data["prompts"].items():
            assert set(entry) == {"active_version", "template_hash", "change_kind"}, key
            assert isinstance(entry["active_version"], int)
            assert len(entry["template_hash"]) == 16


@pytest.mark.skipif(not _db_reachable(), reason="agent_memory PG 不可达（CI 环境）")
class TestLockInSyncWithDb:
    def test_lock_matches_db(self):
        from backend.scripts.gen_prompt_contract_lock import check, derive_snapshot

        existing = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
        verdict, issues = check(existing, derive_snapshot())
        assert verdict == "IN_SYNC", (
            "prompts.lock.json 与 DB 发布状态漂移：\n  - "
            + "\n  - ".join(issues)
            + "\n→ 发布/回滚后运行 gen_prompt_contract_lock 并随变更提交"
        )

    def test_check_detects_version_drift(self):
        """篡改 active_version → 检出漂移（防线自证）。"""
        from backend.scripts.gen_prompt_contract_lock import check, derive_snapshot

        current = derive_snapshot()
        key = sorted(current["prompts"])[0]
        tampered = json.loads(json.dumps(current))
        tampered["prompts"][key]["active_version"] += 999
        verdict, issues = check(tampered, current)
        assert verdict == "DRIFTED"
        assert any(key in i and "active_version" in i for i in issues)
