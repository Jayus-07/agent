"""shared — 最小化共享层（PR-2.x 清理后）。

规则:
  - 允许: types/enums/constants/logger/exceptions
  - 禁止: utils/helper（容易腐化为垃圾桶）

当前: logger + exceptions + monitoring 兼容 shim
"""
