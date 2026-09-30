"""评估体系数据模型 — 所有子模块共用的 Pydantic 类型定义。

可移植性：此文件零项目依赖，可复制到任何 Python 项目直接使用。
"""

from datetime import datetime
from typing import Any, Literal, Protocol, Callable, get_args
from pydantic import BaseModel, Field

ModuleKind = Literal["planner", "rag", "cs", "sql", "e2e", "travel", "travel-provider", "travel-commerce", "travel-booking"]

# 模块清单的唯一事实源就是上面的 Literal——这里只是它的运行期视图。
# 历史上「有哪些模块」被手写 5 份（CLI choices / service 的 all 清单 / MODULE_LABELS /
# 前端 AddToEvalModal），彼此口径已不一致（5 vs 9）。新增模块只改 Literal 一处，
# 其余全部经此常量派生（守护见 tests/evaluation/test_module_kind_single_source.py）。
MODULE_KINDS: tuple[ModuleKind, ...] = get_args(ModuleKind)

# `all` 默认跑的模块集：可离线完成的常规质量评测。
# 显式排除项各带理由，且以「差集」形式表达——新模块默认进来，
# 若要排除必须在这里写清楚为什么（避免又一次静默的口径漂移）：
#   - cs：结构 sanity 锁版校验，语义与质量评测不同，单独跑
#   - travel-provider / travel-commerce / travel-booking：探针，需 --live 才有意义
ALL_EXCLUDED_MODULES: frozenset[str] = frozenset(
    {"cs", "travel-provider", "travel-commerce", "travel-booking"}
)
ALL_RUN_MODULES: tuple[ModuleKind, ...] = tuple(
    m for m in MODULE_KINDS if m not in ALL_EXCLUDED_MODULES
)


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
    """单条测试用例的通用表示。expected 字段结构由各模块自行定义。

    V2.0 扩展字段（通过 metadata 传入）：
    - expected_answer: 期望的参考答案（用于生成层评估）
    - must_contain: 答案必须包含的关键词列表
    - must_not_contain: 答案不得包含的关键词列表
    - answer_type: 答案类型（factual/numeric/procedural/comparative）
    - query_type: 查询类型（single_doc/multi_hop/adversarial/negative/table/chunk_level/long_doc/...）
    - tier: 测试层级（smoke/core/hard/regression）
    - probe_type: 探测类型（用于失败分类）
    - ground_truth_verified: 是否已人工校验
    """
    id: str = Field(description="唯一标识，如 P001 / R003 / S010 / E005")
    question: str = Field(description="用户输入的自然语言问题")
    module: ModuleKind = Field(description="归属评估模块")
    expected: dict[str, Any] = Field(
        default_factory=dict,
        description="模块特定的预期输出，schema 由各 runner 校验"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="可选元数据: kb_id, tags, difficulty, allow_equivalent, "
                    "expected_answer, must_contain, must_not_contain, answer_type, "
                    "query_type, tier, probe_type, ground_truth_verified 等"
    )


class EvalResult(BaseModel):
    """单条用例的评估结果。"""
    case_id: str
    module: ModuleKind
    status: Literal["pass", "fail", "error", "skip"]
    expected: dict[str, Any]
    actual: dict[str, Any]
    metrics: dict[str, float | None] = Field(default_factory=dict)
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
    # 实际会注入嵌套结构（如 token_summary），不能声明为 float
    metrics: dict[str, Any] = Field(default_factory=dict)


class TierSummary(BaseModel):
    """单层级的评估汇总 — 用于 CI/CD 分层阈值判定。"""
    tier: str  # "smoke" | "core" | "hard" | "regression" | "all"
    total: int
    passed: int
    failed: int
    pass_rate: float = 0.0
    threshold: float = 0.85
    passed_threshold: bool = True


class EvalReport(BaseModel):
    """一次完整评估的报告。"""
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    module: str  # "all" | "planner" | "rag"
    mode: Literal["live", "offline"]
    smoke: bool = False
    tier: str = "all"  # V2: 当前评估的层级 ("all" 表示不分层)
    summaries: list[ModuleSummary]
    results: list[EvalResult]
    total_score: float | None = None  # 加权综合分，仅全量评估时计算
    tier_summaries: list[TierSummary] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="本次评测的 scope、数据版本和检索链配置快照",
    )
    prompt_versions: dict[str, int | None] = Field(
        default_factory=dict,
        description="评估时活跃的 prompt 版本快照 {key: version}",
    )
