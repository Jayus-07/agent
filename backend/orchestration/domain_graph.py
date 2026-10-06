"""orchestration/domain_graph.py — 域图协议定义

DomainGraph 是独立子图（如 CS Graph）接入 Main Graph 的标准契约。
每个域图通过 DomainGraphRegistry 自注册，Main Graph 自动发现并布线。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from backend.orchestration.runtime_types import RuntimeType


@dataclass(frozen=True)
class DomainGraph:
    """域图注册描述符

    Attributes:
        name: 路由模式标识，与 router_node 输出的 route_mode 对应
        node_name: Main Graph 中的 LangGraph 节点名
        label: 可视化标签（前端展示用）
        adapter: 适配器函数 (state: dict) -> dict，负责状态转换 + 子图调用 + 结果映射
        domain: 所属顶级域（2026-09-30 归属单一事实源化）。None = 本图即顶级域；
            非 None = 本图是 domain 的子流图（如 travel 的 booking / commerce）。
            回写层登记 active_domain、执行层选图、注册表自校验三处**全部由本字段
            派生**，不再各自手写（守护见
            tests/orchestration/test_domain_semantic_consistency.py）。
        subflow: 独立生命周期的子流标签（STOP E 展示语义，**已冻结**）。None = 本图
            即顶级域本身；非 None 表示本图是某顶级域的子流（如 travel 的
            commerce/booking）。语义严格限于「独立生命周期的子流」，**不承载**顶级域
            自身的活动标签（见下 decision_subflow）——这是 STOP E §6.2 的既定取舍，
            勿再往里塞标签。
        decision_subflow: 决策层归一的 subflow 展示位。None 时回退 ``subflow``。
            只为**顶级域自身的活动标签**而存在（travel→"planning"、
            selection_funnel→"funnel"）——STOP E §6.2 原先把它们只放在 Router
            归一里，2026-09-30 归属单一事实源化后随图注册声明，避免决策层再留手写
            标签表。纯展示/对账字段，不参与调度选图。
    """

    name: str
    node_name: str
    label: str
    adapter: Callable[[dict], dict]
    subflow: str | None = None
    domain: str | None = None
    decision_subflow: str | None = None
    runtime_id: str | None = None
    runtime_type: RuntimeType = RuntimeType.WORKFLOW
    aliases: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    supports_checkpoint: bool = False
    supports_interrupt: bool = False
    supports_streaming: bool = True
    result_contract_version: str = "v1"
    entry_modes: tuple[str, ...] = ("execute",)
    continuation_policy: str | None = None

    def __post_init__(self) -> None:
        """以域名作为默认 Runtime ID，兼容旧注册调用。"""

        if self.runtime_id is None:
            object.__setattr__(self, "runtime_id", self.name)
