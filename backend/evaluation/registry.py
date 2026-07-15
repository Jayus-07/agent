"""Runner 注册表 — 将评估模块与 runner 函数解耦。

可移植性：此文件零项目依赖。新项目复制评估框架后，
只需 import backend.evaluation.registry 并调用 register_runner() 注册自己的 runner。

Usage:
    from backend.evaluation.registry import register_runner

    def my_rag_runner(cases, **kwargs):
        # 调自己的检索系统...
        return [EvalResult(...), ...]

    register_runner("rag", my_rag_runner, needs_live=False)
"""

from backend.evaluation.models import ModuleKind, RunnerFunc, RunnerEntry

_registry: dict[ModuleKind, RunnerEntry] = {}


def register_runner(module: ModuleKind, func: RunnerFunc, needs_live: bool = True) -> None:
    """注册一个模块的 runner 函数。

    Args:
        module: 模块标识 ("planner" | "rag" | "sql" | "e2e")
        func: runner 函数，签名为 (cases: list[TestCase], **kwargs) -> list[EvalResult]
        needs_live: 此 runner 是否需要 --live 模式（False 表示离线模式也可运行）
    """
    _registry[module] = RunnerEntry(func=func, needs_live=needs_live)


def get_runner(module: ModuleKind) -> RunnerEntry | None:
    """获取已注册的 runner，未注册返回 None。"""
    return _registry.get(module)


def list_registered() -> list[ModuleKind]:
    """列出所有已注册的模块。"""
    return list(_registry.keys())


def clear_registry() -> None:
    """清空注册表（测试用）。"""
    _registry.clear()
