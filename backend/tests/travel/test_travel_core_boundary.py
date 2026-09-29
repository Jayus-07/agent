"""tests/travel/test_travel_core_boundary.py — 域边界守护测试（v3 Phase 1）

锁定 Phase 1 的目录映射，防止新文件再堆回域根（对标
test_domain_semantic_consistency 风格）。Phase 2/3 扩目录时**有意地**
更新本测试（预期集合变更是设计决策，不是测试放宽）。
"""
import os
from pathlib import Path

TRAVEL_DIR = Path(__file__).resolve().parents[2] / "travel"
CORE_DIR = TRAVEL_DIR / "core"

# Phase 1 冻结的 core/ 文件集（Phase 3 将有意追加 supervisor.py）
EXPECTED_CORE_FILES = {
    "__init__.py",
    "actions.py",
    "agent_base.py",
    "contracts.py",
    "events.py",
    "plan_diff.py",
    "plan_lifecycle.py",
}


def _py_files(directory: Path) -> set[str]:
    return {
        f for f in os.listdir(directory)
        if f.endswith(".py") and f != "__pycache__"
    }


class TestCoreBoundary:
    def test_core_file_set_locked(self):
        actual = _py_files(CORE_DIR)
        unexpected = actual - EXPECTED_CORE_FILES
        assert not unexpected, (
            f"core/ 出现未登记文件 {unexpected}——若是设计决策请同步更新本测试"
        )

    def test_planning_package_must_not_shadow_module(self):
        # travel/planning.py 是 must_go 契约（names_match 唯一事实源）。
        # 在它迁移前创建 travel/planning/ 包会同名遮蔽炸掉存量 import。
        assert (TRAVEL_DIR / "planning.py").exists(), "存量契约文件不得消失"
        assert not (TRAVEL_DIR / "planning").exists(), (
            "planning/ 包必须与 planning.py 迁移同 commit（Phase 2，含 shim）"
        )

    def test_experts_base_untouched(self):
        from backend.travel.experts import base

        assert hasattr(base, "run_expert_safely"), "生产资产执行框架不得消失"

    def test_new_packages_importable(self):
        import backend.travel.evaluation  # noqa: F401
        import backend.travel.memory  # noqa: F401
        import backend.travel.services  # noqa: F401
        from backend.travel.tools import spec  # noqa: F401
