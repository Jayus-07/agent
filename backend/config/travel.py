"""config/travel.py — 旅游规划域配置

P0 口径：全部阈值可 env 覆盖，且校验器只读本文件，不散落魔数。
默认 TRAVEL_ENABLED=false —— 与 CS_ENABLED 同策略，避免未验收的域
被线上流量命中；验证通过后再开启。
"""
import os

from dotenv import load_dotenv

load_dotenv()

# =============================================
# 域总开关
# =============================================
TRAVEL_ENABLED = os.getenv("TRAVEL_ENABLED", "false").strip().lower() in ("1", "true", "yes")

# 专家调度循环上限（与 CS_EXPERT_MAX_LOOPS 同语义，兜底防御）
TRAVEL_MAX_STEPS = int(os.getenv("TRAVEL_MAX_STEPS", "12"))
# 校验失败后的局部修复轮数上限（防止 repair ↔ validate 死循环）
TRAVEL_MAX_REPAIR_ROUNDS = int(os.getenv("TRAVEL_MAX_REPAIR_ROUNDS", "2"))

# 独立子图运行时。
# **默认值由 TRAVEL_MAX_STEPS 派生，不能各自硬编码**：一个调度回合要花 2 个
# 图步（supervisor 自己 + 它跳到的那个节点），所以 recursion_limit 必须显著
# 大于 2×TRAVEL_MAX_STEPS，否则等在业务护栏前面的 LangGraph 兜底会先抛
# GraphRecursionError —— 用户看到的是「服务暂时不可用」且行程丢失，而不是
# 「已达步数上限，如实收尾」。原实现硬编码 25 配 MAX_STEPS=12：护栏要等
# step_count 到 12（≈第 24~26 图步）才判定，实测永远轮不到生效。
# 余量 10 步留给首尾节点（slot_filler / reporter）；25 是 LangGraph 默认下限。
TRAVEL_GRAPH_RECURSION_LIMIT = int(os.getenv(
    "TRAVEL_GRAPH_RECURSION_LIMIT", str(max(25, TRAVEL_MAX_STEPS * 2 + 10))))

# =============================================
# checkpointer（与 CS / 主图同策略）
# =============================================
# 默认关。开启后域图跨轮状态可回放 —— 旅游域的价值在**跨轮改单**
# （第一轮排完，第二轮说"第二天想轻松点"），这需要上一轮的行程还在状态里；
# 否则每轮都从头规划，用户会看到一份与上一轮无关的全新行程。
TRAVEL_CHECKPOINTER_ENABLED = os.getenv("TRAVEL_CHECKPOINTER_ENABLED", "false").strip().lower() in ("1", "true", "yes")
# postgres（生产，跨进程/重启保留）| memory（本地调试；进程内只增不减）
TRAVEL_CHECKPOINTER_BACKEND = os.getenv("TRAVEL_CHECKPOINTER_BACKEND", "postgres").strip().lower()
# TTL 清理天数。注意：checkpoints 三张表由主图 / 客服域 / 旅游域**共用**，
# 清理守护又是全进程单例（谁先启动谁的 TTL 生效），因此该值需与
# CS_CHECKPOINT_TTL_DAYS 保持一致，不要各自调成不同数字。
TRAVEL_CHECKPOINT_TTL_DAYS = int(os.getenv("TRAVEL_CHECKPOINT_TTL_DAYS", "7"))
# 强持久化策略（任务书 §10）：开启后，checkpointer 降级（postgres 不可用退到
# MemorySaver）时**拒绝复用跨轮产物**——每轮按全新规划处理并如实告知。
# 背景：多 worker 部署时 MemorySaver 各存一份，第二轮请求可能被路由到另一个
# worker，跨轮改单会静默失效（用户拿到与上一轮无关的新行程还以为改成功了）。
# 生产环境若要求「要么真持久、要么明说」，就打开这个开关。
TRAVEL_REQUIRE_PERSISTENCE = os.getenv("TRAVEL_REQUIRE_PERSISTENCE", "false").strip().lower() in ("1", "true", "yes")

# =============================================
# 槽位抽取
# =============================================
# P0 为纯规则抽取（无 LLM）：先把「问对问题」这件事的正确性钉死在可单测的
# 正则与词典上。P1 再引入 LLM 抽取兜底（识别「十一前后」「三四个人」这类
# 口语表达），届时在此处新增开关，而不是现在就留一个不会执行的配置项。

# =============================================
# 时间约束（校验轴一）
# =============================================
TRAVEL_DAY_START = os.getenv("TRAVEL_DAY_START", "08:30")
TRAVEL_DAY_END = os.getenv("TRAVEL_DAY_END", "21:30")
# 单个通勤段时长上限（分钟）：超过 warn 线记 warning，超过 err 线记 error
TRAVEL_MAX_LEG_MINUTES_WARN = int(os.getenv("TRAVEL_MAX_LEG_MINUTES_WARN", "60"))
TRAVEL_MAX_LEG_MINUTES_ERR = int(os.getenv("TRAVEL_MAX_LEG_MINUTES_ERR", "105"))
# 到达后等待开门超过此时长记为提示（等候不是游玩，但会实打实占掉半天）
TRAVEL_LONG_WAIT_MINUTES = int(os.getenv("TRAVEL_LONG_WAIT_MINUTES", "45"))
# 午餐窗口（插入用餐项，让预算与时长核算有真实载体）
TRAVEL_LUNCH_WINDOW = (
    os.getenv("TRAVEL_LUNCH_START", "12:00"),
    os.getenv("TRAVEL_LUNCH_END", "13:00"),
)

# =============================================
# 地理约束（校验轴二）
# =============================================
# 单日在途通勤总时长上限（分钟）—— 直接度量「一天有多少时间耗在车上」。
# 比「单日跨度 km」更贴近真实体感：跨城但地铁直达可以接受，同城反复横跳
# 才是问题，而后者一定表现为通勤时长堆积。
TRAVEL_DAY_MAX_TRANSIT_MINUTES = int(os.getenv("TRAVEL_DAY_MAX_TRANSIT_MINUTES", "150"))
# 步行/机动速度模型（km/h），用于把距离折算成通勤分钟
TRAVEL_WALK_KMH = float(os.getenv("TRAVEL_WALK_KMH", "4.5"))
TRAVEL_DRIVE_KMH = float(os.getenv("TRAVEL_DRIVE_KMH", "22"))
# 直线距离 → 实际路程的绕行系数
TRAVEL_ROUTE_DETOUR_FACTOR = float(os.getenv("TRAVEL_ROUTE_DETOUR_FACTOR", "1.35"))
# 超过此距离不再步行，改乘车
TRAVEL_WALK_MAX_KM = float(os.getenv("TRAVEL_WALK_MAX_KM", "1.5"))
# 出行日期距今超过该天数时，当日实时路况对那天不再可信，通勤强制本地估算
# 并标注 fallback_reason（providers/travel 远期降级策略，Phase 1）
TRAVEL_TRANSIT_FAR_TRIP_DAYS = int(os.getenv("TRAVEL_TRANSIT_FAR_TRIP_DAYS", "14"))

# =============================================
# 体力约束（校验轴三：日均强度）
# =============================================
# 各节奏档的单日「在途活动分钟」（不含通勤）上限
TRAVEL_PACE_MINUTES = {
    "relaxed": int(os.getenv("TRAVEL_PACE_MINUTES_RELAXED", "240")),
    "moderate": int(os.getenv("TRAVEL_PACE_MINUTES_MODERATE", "360")),
    "intense": int(os.getenv("TRAVEL_PACE_MINUTES_INTENSE", "480")),
}
# 各节奏档的单日 POI 数上限
TRAVEL_PACE_MAX_POIS = {
    "relaxed": int(os.getenv("TRAVEL_PACE_MAX_POIS_RELAXED", "4")),
    "moderate": int(os.getenv("TRAVEL_PACE_MAX_POIS_MODERATE", "5")),
    "intense": int(os.getenv("TRAVEL_PACE_MAX_POIS_INTENSE", "7")),
}

# =============================================
# 预算约束（校验轴四）
# =============================================
# 费用估算口径（P0 为城市均值占位；P1 接入真实供给数据后替换）
TRAVEL_MEAL_PER_DAY_CNY = float(os.getenv("TRAVEL_MEAL_PER_DAY_CNY", "150"))
TRAVEL_LODGING_PER_NIGHT_CNY = float(os.getenv("TRAVEL_LODGING_PER_NIGHT_CNY", "420"))
TRAVEL_TRANSIT_BASE_CNY = float(os.getenv("TRAVEL_TRANSIT_BASE_CNY", "10"))
TRAVEL_TRANSIT_PER_KM_CNY = float(os.getenv("TRAVEL_TRANSIT_PER_KM_CNY", "3.0"))
# 达到预算该比例即提前预警（不等超支才报）
TRAVEL_BUDGET_WARN_RATIO = float(os.getenv("TRAVEL_BUDGET_WARN_RATIO", "0.9"))

# =============================================
# 意图预过滤（Router 内，与 CS 预过滤同层）
# =============================================
# 命中 ≥ 该数量的旅游强信号词才判为旅游域，避免「周末」这类口语误命中
TRAVEL_DETECT_MIN_HITS = int(os.getenv("TRAVEL_DETECT_MIN_HITS", "2"))
