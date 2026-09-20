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
# P7：reaper 与派单共用本 tick，1s 轮询满足「过期后 2 秒内释放并重派」。
CS_DISPATCH_INTERVAL_SECONDS = float(os.getenv("CS_DISPATCH_INTERVAL_SECONDS", "1.0"))

# 坐席 offer 冷却期（秒）：刚超时/刚拒绝过某工单的坐席在该工单上冷却，
# 避免「同一坐席-同一工单」死循环（方案 §六 P7「排除刚超时客服」）。
CS_AGENT_OFFER_COOLDOWN_SECONDS = int(
    os.getenv("CS_AGENT_OFFER_COOLDOWN_SECONDS", "60")
)

# reaper 每轮最多处理的工单数（多副本下用 SKIP LOCKED 分批，避免长事务）。
CS_REAPER_BATCH_LIMIT = int(os.getenv("CS_REAPER_BATCH_LIMIT", "200"))

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

# ── worker 阶段开关（P7/P8 拆分）──────────────────────────────
# ``CS_DISPATCH_MODE`` 只管「派单算法」这一个阶段。回收过期 offer 与投递持久
# 事件是**恢复/投递路径**，不是被灰度门控的算法：即使派单被切成 ``off``，
# 已经在途的 offer 仍必须被释放、已落库的事件仍必须被投递，否则把派单关掉
# 反而会把用户永久卡在 ``agent_offered``。因此这两段各有独立开关，
# 默认开启；需要彻底停机时用「``off`` + 停 dispatcher 容器」组合。
CS_REAPER_ENABLED = os.getenv("CS_REAPER_ENABLED", "true").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)

CS_OUTBOX_RELAY_ENABLED = os.getenv(
    "CS_OUTBOX_RELAY_ENABLED", "true"
).strip().lower() in ("1", "true", "yes", "on")

# relay 每轮最多投递的事件数（按 id 升序，保证同会话事件顺序）。
CS_OUTBOX_RELAY_BATCH_LIMIT = int(
    os.getenv("CS_OUTBOX_RELAY_BATCH_LIMIT", "200")
)

# outbox lag 告警阈值（秒）：P99 目标 < 2 秒（方案 §六 P8 完成标准）。
CS_OUTBOX_LAG_ALERT_SECONDS = float(
    os.getenv("CS_OUTBOX_LAG_ALERT_SECONDS", "2.0")
)
