"""travel/core/ — 旅游域共享底座（travel-domain-design-v5.md §9）

底座只被上层（Agent/Service/Tool）import，禁止反向依赖任何域内业务模块。
本包维护共享契约、生命周期、差异、事件和 Agent 基础抽象；具体实现以当前
模块和测试为准。
"""
