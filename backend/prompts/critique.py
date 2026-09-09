"""critique.py — Plan Critique 系统提示词

已迁移至 prompt_service（key: planner.critique）。
保留 PLAN_CRITIQUE_SYSTEM 作为向后兼容别名。
"""


def __getattr__(name: str) -> str:
    if name == "PLAN_CRITIQUE_SYSTEM":
        from backend.prompts.service import prompt_service
        return prompt_service.get_template_sync("planner.critique")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")