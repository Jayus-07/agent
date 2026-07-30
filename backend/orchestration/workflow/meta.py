"""workflow/meta.py — Workflow 元数据 Schema（强类型）

设计原则：
- 用 dataclass 不用 dict（类型安全 + IDE 提示）
- WorkflowMeta 由 @workflow 装饰器写入类属性
- StepConfig 由 @step 装饰器写入方法属性

Router Index 构建时扫描这些元数据 → 自动生成 Router 表。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Any


@dataclass
class WorkflowMeta:
    """Workflow 级别的元数据（用于 Router Index + Dashboard 展示）"""
    name: str
    description: str = ""
    # 业务对象词典：路由时用于"业务对象"维度匹配
    # 例：["daily_report", "sales", "operation"]
    objects: list[str] = field(default_factory=list)
    # 动作类型：用于"动作"维度匹配
    # 例：["generate", "send", "export"]
    actions: list[str] = field(default_factory=list)
    # 示例问句：用于 embedding 相似度匹配 + Router LLM 兜底时的 few-shot
    # 例：["生成今天的经营日报", "跑一下今天的销售日报"]
    examples: list[str] = field(default_factory=list)
    # 默认 RAG 检索的 kb_ids（workflow 内 RAG 检索时默认范围）
    # 例：["analytics", "operations"]
    default_kbs: list[str] = field(default_factory=list)


@dataclass
class StepConfig:
    """Step 级别的配置（嵌入方法属性）

    字段职责分离：
    - depends_on：DAG 拓扑（构建 DAG 时使用）
    - retry/timeout_sec/on_error：Runtime 行为（执行时使用）
    """
    # DAG 边：依赖哪些 step 的输出
    depends_on: list[str] = field(default_factory=list)
    # Runtime：失败时重试次数
    retry: int = 0
    # Runtime：单次执行超时（秒）
    timeout_sec: int = 60
    # Runtime：失败处理策略 — abort / skip / agent_degrade
    on_error: str = "abort"


# Type alias: workflow class 上的 meta 属性类型
WORKFLOW_META_ATTR = "_workflow_meta"
STEP_CONFIG_ATTR = "_step_config"


def get_workflow_meta(cls: type) -> WorkflowMeta | None:
    """从类读取 WorkflowMeta（由 @workflow 装饰器写入）"""
    return getattr(cls, WORKFLOW_META_ATTR, None)


def get_step_config(fn: Callable) -> StepConfig | None:
    """从方法读取 StepConfig（由 @step 装饰器写入）"""
    return getattr(fn, STEP_CONFIG_ATTR, None)


def collect_step_methods(cls: type) -> dict[str, tuple[Callable, StepConfig]]:
    """扫描类，收集所有带 @step 装饰的方法

    Returns:
        {method_name: (callable, StepConfig)}
    """
    steps: dict[str, tuple[Callable, StepConfig]] = {}
    for attr_name in dir(cls):
        if attr_name.startswith("_"):
            continue
        attr = getattr(cls, attr_name, None)
        if attr is None or not callable(attr):
            continue
        config = get_step_config(attr)
        if config is not None:
            steps[attr_name] = (attr, config)
    return steps