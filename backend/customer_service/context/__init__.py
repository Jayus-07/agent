"""customer_service/context/ — 上下文层（迁移 B11 结构整理）

成员：context.py（CSContext 类型）/ context_manager.py（订单槽位解析，设计方案 §4.1）/
context_resolver.py（多轮业务实体承接，缺陷9）/ understanding/（CSUnderstanding 统一理解层）。
根目录同名旧模块均为 sys.modules 别名 shim，monkeypatch 旧路径仍作用于真模块。
"""
from backend.customer_service.context.context import *  # noqa: F401,F403
