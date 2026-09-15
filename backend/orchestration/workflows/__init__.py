"""workflows package — 具体 workflow 定义

注册约定（2026-09-16 归一，对齐 ``mcp_servers.servers.register_all``）：

  ``register_all()`` 是 workflow 的唯一注册入口。app/server.py 启动与
  evaluation 独立运行都调用它，禁止各处手写注册列表。

  此前 server.py 手写 4 条、evaluation/runners/e2e.py 手写 3 条，后者漏了
  MarketResearch（评测侧看不到该 workflow）——正是「注册列表散落多处」的
  典型漂移。与 Skill 节点自注册、域图自注册、MCP server register_all 保持
  同一治理形态：一处声明，多处调用。

新增 workflow 的步骤：
  1. 在 workflows/<name>.py 用 @workflow 装饰器声明 metadata（name 必填，
     且必须同时登记进 orchestration/router/capabilities.yaml 的 workflows 段，
     否则向量路由看不见它）
  2. 在下面 ``register_all()`` 里补一行延迟 import，并把它加进 ``classes`` 元组
"""
from __future__ import annotations

from backend.shared.logger import logger


def register_all() -> list[str]:
    """注册所有内置 Workflow（幂等：已注册的同名跳过）。

    返回本次调用后 registry 中已知的 workflow 名列表。

    延迟 import：market_research / selection_decision 会连带各自的业务模块
    （market_research.pipeline / selection_decision.*），只在真正调用注册时加载，
    避免导入 workflows 包就付出这份代价。
    """
    from backend.orchestration.workflow.meta import get_workflow_meta
    from backend.orchestration.workflow.registry import get_workflow_registry
    from backend.orchestration.workflows.daily_report import DailyReport
    from backend.orchestration.workflows.inventory_alert import InventoryAlert
    from backend.orchestration.workflows.market_research import MarketResearch
    from backend.orchestration.workflows.selection_decision import SelectionDecision

    # 全量注册清单（唯一事实源：新增 workflow 只改这里）
    classes = (DailyReport, InventoryAlert, MarketResearch, SelectionDecision)

    reg = get_workflow_registry()
    names: list[str] = []
    for cls in classes:
        meta = get_workflow_meta(cls)
        if meta is None:
            # register() 自己会抛，此处提前给出更可读的报错
            raise ValueError(f"{cls.__name__} 缺少 @workflow 装饰器，无法注册")
        name = meta.name
        if reg.get(name) is None:
            reg.register(cls)
        names.append(name)

    logger.info(f"[WorkflowRegistry] register_all 完成：{len(names)} 个（{', '.join(names)}）")
    return names
