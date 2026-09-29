"""travel/core/ — 旅游域共享底座（v3 §1 / v4 冻结）

底座只被上层（Agent/Service/Tool）import，禁止反向依赖任何域内业务模块。
Phase 1 仅立契约与纯函数（零行为）：contracts / agent_base / actions /
plan_lifecycle / plan_diff / events。supervisor 的意图层与 stage 机在
Phase 3 随 travel/supervisor.py git mv 落入本包。
"""
