"""backend/domains — 域图自注册触发器

import 此包即触发所有域图向 domain_graph_registry 注册。
新增域图只需在此文件加一行 import。
"""
import backend.customer_service.register  # noqa: F401
import backend.travel.register  # noqa: F401
import backend.selection_funnel.register  # noqa: F401  # 智能选品漏斗域（默认关闭，SELECTION_FUNNEL_ENABLED）
