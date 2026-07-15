"""评估体系数据模型 — 所有子模块共用的 Pydantic 类型定义。

可移植性：此文件零项目依赖，可复制到任何 Python 项目直接使用。
"""

from datetime import datetime
from typing import Any, Literal, Protocol, Callable
from pydantic import BaseModel, Field

ModuleKind = Literal["planner", "rag", "sql", "e2e"]


class RunnerFunc(Protocol):
    """Runner 函数协议：接收测试用例列表和可选参数，返回评估结果列表。

    新项目对接时只需实现符合此协议的函数，然后通过 registry.register_runner() 注册。
    """
    def __call__(self, cases: list["TestCase"], **kwargs: Any) -> list["EvalResult"]: ...


class RunnerEntry:
    """已注册的 runner 元数据。"""
    def __init__(self, func: RunnerFunc, needs_live: bool = True):
        self.func = func
        self.needs_live = needs_live


class TestCase(BaseModel):
    """单条测试用例的通用表示。expected 字段结构由各模块自行定义。"""
    id: str = Field(description="唯一标识，如 P001 / R003 / S010 / E005")
    question: str = Field(description="用户输入的自然语言问题")
    module: ModuleKind = Field(description="归属评估模块")
    expected: dict[str, Any] = Field(
        default_factory=dict,
        description="模块特定的预期输出，schema 由各 runner 校验"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="可选元数据: kb_id, tags, difficulty, allow_equivalent 等"
    )


class EvalResult(BaseModel):
    """单条用例的评估结果。"""
    case_id: str
    module: ModuleKind
    status: Literal["pass", "fail", "error", "skip"]
    expected: dict[str, Any]
    actual: dict[str, Any]
    metrics: dict[str, float] = Field(default_factory=dict)
    duration_ms: int = 0
    error_msg: str | None = None


class ModuleSummary(BaseModel):
    """单个模块的汇总指标。"""
    module: ModuleKind
    total: int
    passed: int
    failed: int
    errors: int
    skipped: int
    pass_rate: float = 0.0
    metrics: dict[str, float] = Field(default_factory=dict)


class EvalReport(BaseModel):
    """一次完整评估的报告。"""
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    module: str  # "all" | "planner" | "rag" | "sql" | "e2e"
    mode: Literal["live", "offline"]
    smoke: bool = False
    summaries: list[ModuleSummary]
    results: list[EvalResult]
    total_score: float | None = None  # 加权综合分，仅全量评估时计算
