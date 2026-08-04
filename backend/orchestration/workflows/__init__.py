"""workflows package — 具体 workflow 定义

用法：
    from orchestration.workflow.registry import get_workflow_registry
    from orchestration.workflows import daily_report, inventory_alert

    reg = get_workflow_registry()
    reg.register(daily_report.DailyReport)
    reg.register(inventory_alert.InventoryAlert)
"""