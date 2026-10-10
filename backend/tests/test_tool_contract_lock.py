"""Tool 契约 lock 守卫测试（M1 / 台账 D1）

三条防线：
1. 仓库内 ``tool_contracts.lock.json`` 必须与运行时派生快照一致——
   改任何 Tool 契约（args/必填性/output_type）而不重新生成 lock，
   本测试即红。等价于 CI 漂移检测的本地形态。
2. 快照派生稳定性：同一进程两次派生 content_hash 逐 Tool 一致。
3. 变更分类规则穷举：BREAKING / DEGRADED / COMPATIBLE 每条规则至少 1 例，
   整体 classification 取最重。

分类规则契约：
BREAKING=删Tool/删参数/参数类型变/可选→必填；DEGRADED=新增必填/默认值变/
output_type 变；COMPATIBLE=新增Tool/新增可选/必填→可选/描述变/归属变。
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LOCK_PATH = REPO_ROOT / "backend" / "tool_contracts.lock.json"


@pytest.fixture(scope="module")
def snapshot() -> dict:
    from backend.scripts.gen_tool_contract_lock import derive_snapshot

    return derive_snapshot()


@pytest.fixture(scope="module")
def lock_file() -> dict:
    assert LOCK_PATH.exists(), (
        f"{LOCK_PATH} 不存在：先运行 python -m backend.scripts.gen_tool_contract_lock 并提交"
    )
    return json.loads(LOCK_PATH.read_text(encoding="utf-8"))


# ==================== 1. lock 与代码同步 ====================

class TestLockInSync:
    def test_lock_matches_runtime(self, snapshot, lock_file):
        """仓库 lock 必须与运行时派生一致（CI 漂移检测的本地等价物）。"""
        from backend.scripts.gen_tool_contract_lock import classify_lock_diff

        report = classify_lock_diff(lock_file["tools"], snapshot["tools"])
        assert report["classification"] == "IN_SYNC", (
            "tool_contracts.lock.json 与代码漂移：\n"
            + json.dumps(report["changed_tools"], ensure_ascii=False, indent=2)
            + "\n→ 运行 python -m backend.scripts.gen_tool_contract_lock 重新生成并随变更一并提交"
        )

    def test_lock_counts_registry(self, snapshot):
        """lock 覆盖的 Tool 集合与 tool_registry 完全一致（防幽灵/漏网）。"""
        import backend.skills  # noqa: F401
        import backend.tools  # noqa: F401
        from backend.tools.tool_registry import tool_registry

        assert set(snapshot["tools"]) == set(tool_registry.tool_names)
        assert snapshot["tool_count"] == len(snapshot["tools"]) > 0


# ==================== 2. 派生稳定性 ====================

class TestDerivationStability:
    def test_content_hash_stable(self, snapshot):
        from backend.scripts.gen_tool_contract_lock import derive_snapshot

        again = derive_snapshot()
        for name, entry in snapshot["tools"].items():
            assert entry["content_hash"] == again["tools"][name]["content_hash"], (
                f"{name} 两次派生 hash 不一致（快照非确定性）"
            )

    def test_repository_tool_modules_are_worktree_independent(self, snapshot):
        """lock 的仓库内模块来源不绑定某台机器或某个 worktree 绝对路径。"""
        for name, entry in snapshot["tools"].items():
            module = entry["module"]
            if module != "unknown":
                assert not Path(module).is_absolute(), name
                assert module.startswith("backend/"), name

    def test_args_schema_marks_required(self, snapshot):
        """required 语义抽查：已知契约（sql 必填 query；rag 可选 kb_id）。"""
        sql = snapshot["tools"].get("execute_sql_tool", {}).get("args_schema", {})
        assert sql.get("query", {}).get("required") is True


# ==================== 3. 分类规则穷举 ====================

def _entry(args: dict | None = None, **overrides) -> dict:
    base = {
        "module": "backend/tools/x.py",
        "description_hash": "d0" * 8,
        "args_schema": args if args is not None else {
            "q": {"schema": {"type": "string"}, "required": True, "default": "__unset__"},
        },
        "capabilities": ["x.query"],
        "output_types": {"x.query": "text"},
    }
    base.update(overrides)
    return base


def _classify(old_tools: dict, new_tools: dict) -> tuple[str, list[dict]]:
    from backend.scripts.gen_tool_contract_lock import classify_lock_diff

    report = classify_lock_diff(old_tools, new_tools)
    return report["classification"], report["changed_tools"]


class TestClassificationRules:
    def test_in_sync_when_identical(self):
        tools = {"t": _entry()}
        assert _classify(tools, copy.deepcopy(tools))[0] == "IN_SYNC"

    def test_tool_removed_breaking(self):
        cls, changes = _classify({"t": _entry()}, {})
        assert cls == "BREAKING" and changes[0]["changes"] == [{"kind": "tool_removed"}]

    def test_tool_added_compatible(self):
        cls, changes = _classify({}, {"t": _entry()})
        assert cls == "COMPATIBLE" and changes[0]["changes"] == [{"kind": "tool_added"}]

    def test_param_removed_breaking(self):
        old = {"t": _entry({"a": _entry()["args_schema"]["q"], "b": _entry()["args_schema"]["q"]})}
        cls, changes = _classify(old, {"t": _entry()})
        assert cls == "BREAKING"
        assert {"kind": "param_removed", "param": "b", "classification": "BREAKING"} in changes[0]["changes"]

    def test_param_type_changed_breaking(self):
        new_args = {"q": {"schema": {"type": "integer"}, "required": True, "default": "__unset__"}}
        cls, changes = _classify({"t": _entry()}, {"t": _entry(new_args)})
        assert cls == "BREAKING"
        assert any(c["kind"] == "param_type_changed" for c in changes[0]["changes"])

    def test_optional_to_required_breaking(self):
        old_args = {"q": {"schema": {"type": "string"}, "required": False, "default": "d"}}
        new_args = {"q": {"schema": {"type": "string"}, "required": True, "default": "__unset__"}}
        cls, changes = _classify({"t": _entry(old_args)}, {"t": _entry(new_args)})
        assert cls == "BREAKING"
        assert any(c["kind"] == "required_changed" for c in changes[0]["changes"])

    def test_required_to_optional_compatible(self):
        old_args = {"q": {"schema": {"type": "string"}, "required": True, "default": "__unset__"}}
        new_args = {"q": {"schema": {"type": "string"}, "required": False, "default": "d"}}
        cls, _ = _classify({"t": _entry(old_args)}, {"t": _entry(new_args)})
        assert cls == "COMPATIBLE"

    def test_added_required_param_degraded(self):
        new_args = {
            "q": {"schema": {"type": "string"}, "required": True, "default": "__unset__"},
            "n": {"schema": {"type": "string"}, "required": True, "default": "__unset__"},
        }
        cls, changes = _classify({"t": _entry()}, {"t": _entry(new_args)})
        assert cls == "DEGRADED"
        assert any(c["kind"] == "param_added" and c["classification"] == "DEGRADED"
                   for c in changes[0]["changes"])

    def test_added_optional_param_compatible(self):
        new_args = {
            "q": {"schema": {"type": "string"}, "required": True, "default": "__unset__"},
            "n": {"schema": {"type": "string"}, "required": False, "default": "x"},
        }
        cls, _ = _classify({"t": _entry()}, {"t": _entry(new_args)})
        assert cls == "COMPATIBLE"

    def test_default_changed_degraded(self):
        old_args = {"q": {"schema": {"type": "string"}, "required": False, "default": "a"}}
        new_args = {"q": {"schema": {"type": "string"}, "required": False, "default": "b"}}
        cls, _ = _classify({"t": _entry(old_args)}, {"t": _entry(new_args)})
        assert cls == "DEGRADED"

    def test_output_type_changed_degraded(self):
        new = _entry(output_types={"x.query": "structured"})
        cls, changes = _classify({"t": _entry()}, {"t": new})
        assert cls == "DEGRADED"
        assert any(c["kind"] == "output_type_changed" for c in changes[0]["changes"])

    def test_capability_and_description_compatible(self):
        new = _entry(capabilities=["y.query"], description_hash="ab" * 8)
        cls, changes = _classify({"t": _entry()}, {"t": new})
        assert cls == "COMPATIBLE"
        kinds = {c["kind"] for c in changes[0]["changes"]}
        assert {"capability_binding_changed", "description_changed"} <= kinds

    def test_overall_takes_most_severe(self):
        """两个 Tool 变更并存时整体取最重（BREAKING 压过 COMPATIBLE）。"""
        old = {"t1": _entry(), "t2": _entry()}
        new = {"t2": _entry(capabilities=["y.query"])}  # t1 从 new 移除=删除 BREAKING
        cls, changes = _classify(old, new)
        assert cls == "BREAKING"
        assert len(changes) == 2
