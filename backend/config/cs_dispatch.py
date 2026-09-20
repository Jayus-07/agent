"""config/cs_dispatch.py — 客服自动派单（P6）配置。

派单模式三态（方案 §八 风险控制项「自动派单算法异常」）：

- ``off``     不派单。默认值：新增组件先以关闭态上线，避免误派。
- ``shadow``  只计算候选与排序、不落库不广播（观察算法输出）。
- ``enforce`` 真实派单（写 assignment、改 handoff、广播定向事件）。

任一数据错误（重复绑定、超载、跨租户泄漏）应立即切回 ``off`` 并停
dispatcher 容器，见方案 §四「停止发布」。
"""
import os

from dotenv import load_dotenv

load_dotenv()

_VALID_MODES = ("off", "shadow", "enforce")

CS_DISPATCH_MODE = os.getenv("CS_DISPATCH_MODE", "off").strip().lower()
if CS_DISPATCH_MODE not in _VALID_MODES:
    CS_DISPATCH_MODE = "off"

# 接单超时（冻结决策 A5/Q3）：offer 到期后由重派/reaper 回收。
CS_OFFER_TIMEOUT_SECONDS = int(os.getenv("CS_OFFER_TIMEOUT_SECONDS", "30"))

# 单张工单最多自动重派次数（冻结决策 A5）。
# P6 只写入并递增 handoff.attempt_count；达到上限后的终态关闭与用户通知
# 由 P7 的 reaper 消费本值执行。
CS_MAX_DISPATCH_ATTEMPTS = int(os.getenv("CS_MAX_DISPATCH_ATTEMPTS", "5"))

# worker 轮询间隔（秒）——同时也是心跳写入间隔：默认 1s 远小于 30s TTL，
# 因此不需要单独的心跳周期配置。
CS_DISPATCH_INTERVAL_SECONDS = float(os.getenv("CS_DISPATCH_INTERVAL_SECONDS", "1.0"))

# dispatcher 心跳 TTL 30 秒（方案 §六 P6「心跳中断 30 秒内告警」）。
CS_DISPATCHER_HEARTBEAT_TTL_SECONDS = int(
    os.getenv("CS_DISPATCHER_HEARTBEAT_TTL_SECONDS", "30")
)

# 实例名：多副本下用于区分心跳 key（默认取容器 hostname）。
CS_DISPATCHER_INSTANCE_ID_ENV = "CS_DISPATCHER_INSTANCE_ID"

# 一次 run_once 最多枚举的租户数（按全局队列顺序取前 N 个租户的头工单）。
CS_DISPATCH_TENANT_SCAN_LIMIT = int(os.getenv("CS_DISPATCH_TENANT_SCAN_LIMIT", "20"))

# 单次事务内最多尝试的租户数上限，防止极端排队时空转。
CS_DISPATCH_MAX_TENANTS_PER_TICK = int(
    os.getenv("CS_DISPATCH_MAX_TENANTS_PER_TICK", "20")
)
