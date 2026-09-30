"""backend.domains — 域图自注册触发器

import 此包即触发所有域图向 domain_graph_registry 注册（import 副作用，
既有用法保持）。``register_all_domains()`` 是显式注册入口（P2-2，
2026-09-30 主架构改造）：builder 在 build_graph 时显式调用，幂等——
以 sys.modules 判定已导入的模块直接跳过（Python 模块缓存保证 register
副作用只发生一次，不触发 registry 的重复注册 warning）。

新增域图只需在 ``_DOMAIN_MODULES`` 加一行模块路径。
"""
import importlib
import sys

# 域图注册模块清单（import 即注册；包级触发与显式入口共用，单一事实源）
_DOMAIN_MODULES = (
    "backend.customer_service.register",
    "backend.travel.register",
    "backend.travel.commerce.register",   # 旅游商务域（STOP K，默认关，TRAVEL_COMMERCE_ENABLED）
    "backend.travel.booking.register",    # 旅游预订域（STOP L，默认关，TRAVEL_BOOKING_ENABLED）
    "backend.selection_funnel.register",  # 智能选品漏斗域（默认关，SELECTION_FUNNEL_ENABLED）
)

# 包级 import 触发注册（兼容测试/脚本直接 import backend.domains 的既有用法）
for _module_name in _DOMAIN_MODULES:
    importlib.import_module(_module_name)


def register_all_domains() -> int:
    """显式注册入口：幂等，返回本次实际导入的模块数（0 = 全部已注册）。

    builder.build_graph 显式调用本函数——与包级 import 构成双保险
    （两个入口同幂等），效果是注册强化而非行为变更。
    """
    imported = 0
    for name in _DOMAIN_MODULES:
        if name not in sys.modules:
            importlib.import_module(name)
            imported += 1
    return imported
