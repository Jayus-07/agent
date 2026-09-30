"""评测模块清单 —— 单一事实源守护。

同源病史（同 P1 那批）：同一份「平台有哪些评测模块」曾被手写 5 份，口径已经不一致：
  - `evaluation/models.py` 的 `ModuleKind` Literal（**唯一事实源**，9 个）
  - `evaluation/cli.py` 的 argparse choices（9 个，写死一份）
  - `evaluation/service.py` 的 `module == "all"` 清单（**只有 5 个**，漏 cs 与 4 个 travel 系）
  - `evaluation/report/builder.py` 的 `MODULE_LABELS`（**只有 4 个**，其余回落英文原始码）
  - `frontend-admin/.../AddToEvalModal.tsx` 的 `MODULES`（5 个，且与后端那 5 个不是同一组）

现全部派生自 `ModuleKind`：`MODULE_KINDS` 是它的运行期视图，`ALL_RUN_MODULES`
是「all 默认跑哪些」的显式差集（排除项逐个带理由），CLI choices 直接展开它。
前端是独立应用拿不到 Python 常量，改为 CI 拦截式守护（扫源码比对）。

本文件锁死上述性质；反向验证见提交说明。
"""
from __future__ import annotations

import re
from pathlib import Path

from backend.evaluation.cli import build_parser
from backend.evaluation.models import (
    ALL_EXCLUDED_MODULES,
    ALL_RUN_MODULES,
    MODULE_KINDS,
)
from backend.evaluation.report import MODULE_LABELS

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_FRONTEND_MODAL = (
    _BACKEND_DIR.parent / "frontend-admin" / "src" / "components"
    / "observability" / "trace" / "AddToEvalModal.tsx"
)


def test_module_kinds_is_the_literal_itself():
    """运行期视图必须就是 Literal 本体，不是另一份手写清单。"""
    assert MODULE_KINDS == (
        "planner", "rag", "cs", "sql", "e2e",
        "travel", "travel-provider", "travel-commerce", "travel-booking",
    )
    assert len(set(MODULE_KINDS)) == len(MODULE_KINDS), "ModuleKind 里有重复项"


def test_cli_choices_derived_from_module_kinds():
    """CLI 的 module choices 必须恰好是 ModuleKind + 'all'（修复前是手写一份）。"""
    parser = build_parser()
    action = next(a for a in parser._actions if a.dest == "module")
    assert list(action.choices) == ["all", *MODULE_KINDS]


def test_module_labels_cover_every_kind():
    """每个模块都要有中文名，否则报告回落英文原始码（静默的品质降级）。"""
    missing = [m for m in MODULE_KINDS if m not in MODULE_LABELS]
    assert not missing, f"MODULE_LABELS 缺这些模块的中文名：{missing}"
    assert not (set(MODULE_LABELS) - set(MODULE_KINDS)), (
        "MODULE_LABELS 有 ModuleKind 里不存在的键（清单又漂了）"
    )


def test_all_run_modules_is_explicit_subset():
    """`all` 的默认集：必须是 ModuleKind 的子集，且排除项逐个显式列出。"""
    assert set(ALL_RUN_MODULES) <= set(MODULE_KINDS)
    assert ALL_RUN_MODULES == ("planner", "rag", "sql", "e2e", "travel")
    assert set(ALL_RUN_MODULES) | set(ALL_EXCLUDED_MODULES) == set(MODULE_KINDS), (
        "有模块既不在 all 里、也没被显式排除——口径出现空白"
    )


def test_service_all_list_is_derived_not_handwritten():
    """防回退：service.py 不得再手写模块列表字面量。"""
    src = (_BACKEND_DIR / "evaluation" / "service.py").read_text(encoding="utf-8")
    assert "ALL_RUN_MODULES" in src, "service.py 没引用派生常量"
    assert '"planner", "rag", "sql", "e2e", "travel"' not in src, (
        "service.py 回退了手写模块列表"
    )


def test_frontend_modal_modules_match_backend():
    """跨端：管理端的模块下拉必须与后端 ModuleKind 完全一致。

    前端拿不到 Python 常量，只能复制一份——那就用测试把漂移挡住。
    修复前实测红：前端只有 5 个（cs/rag/sql/planner/e2e），少 4 个 travel 系。
    """
    src = _FRONTEND_MODAL.read_text(encoding="utf-8")
    m = re.search(r"const MODULES\s*=\s*\[(.*?)\]\s*as const", src, re.DOTALL)
    assert m, "没能在 AddToEvalModal.tsx 里定位 MODULES 数组字面量"
    frontend_modules = tuple(re.findall(r"[\"']([^\"']+)[\"']", m.group(1)))

    assert frontend_modules == MODULE_KINDS, (
        f"前端模块清单与后端 ModuleKind 不一致：\n  前端={frontend_modules}\n  后端={MODULE_KINDS}"
    )
