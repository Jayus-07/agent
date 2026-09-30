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
# 2026-09-22 新增 weather 专家（transit→budget 之间多一步），默认 12→14
TRAVEL_MAX_STEPS = int(os.getenv("TRAVEL_MAX_STEPS", "14"))
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
# 用户决策中断（任务书 §13，Phase 6）：开启后，必去项冲突（decision_required）
# 不再走「出单+请决定」软处理，而是 LangGraph interrupt 暂停域图，等用户结构化
# 决策（保留风险 / 移除该点）后 Command(resume) 恢复。默认关——软处理是
# Phase 3 契约、评测基线依赖它；且 interrupt 依赖 checkpointer（暂停态持久化），
# 无持久化时即使开启也自动回退软处理。
TRAVEL_USER_DECISION_INTERRUPT = os.getenv("TRAVEL_USER_DECISION_INTERRUPT", "false").strip().lower() in ("1", "true", "yes")

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
# 城市消费档位（2026-09-22 P0-3 预算分项真实化）
# =============================================
# 门票与通勤已按 POI 真实数据核算；餐饮/住宿此前是全局定额，对杭州这类
# 消费明显偏高的城市会低估。按城市键给出日餐费与每晚房价，未登记的城市
# 回落全局定额 —— 新增城市只需在此补一行，不影响未配置城市的既有行为。
TRAVEL_CITY_COST_TIERS: dict[str, dict[str, float]] = {
    "福州": {"meal": 120.0, "lodging": 350.0},
    "厦门": {"meal": 130.0, "lodging": 400.0},
    "杭州": {"meal": 160.0, "lodging": 520.0},
}


def city_cost_tier(city: str) -> dict[str, float]:
    """城市 → {meal, lodging} 档位；未登记城市回落全局定额（单一兜底口径）。"""
    tier = TRAVEL_CITY_COST_TIERS.get((city or "").strip())
    if tier:
        return dict(tier)
    return {"meal": TRAVEL_MEAL_PER_DAY_CNY, "lodging": TRAVEL_LODGING_PER_NIGHT_CNY}


# =============================================
# 天气专家（2026-09-22 P0-2 接入域图）
# =============================================
TRAVEL_WEATHER_ENABLED = os.getenv("TRAVEL_WEATHER_ENABLED", "true").strip().lower() in ("1", "true", "yes")
# 天气 API 失败/超时的整体预算（秒）：天气检查是增强项，不能拖垮排程主链
# （Phase 4 按 v4 §9.3 时延预算 6→5s；fallback 链与 stale 兜底已存在）
TRAVEL_WEATHER_TIMEOUT_S = float(os.getenv("TRAVEL_WEATHER_TIMEOUT_S", "5"))
# 预报文本命中这些词判为「坏天气日」（腾讯天气字段为中文短语，如"中雨"）
TRAVEL_BAD_WEATHER_KEYWORDS: tuple[str, ...] = (
    "雨", "雪", "雷", "雹", "台风", "沙尘", "冻",
)

# =============================================
# 知识库检索（2026-09-22 P0-1 RAG 接入）
# =============================================
TRAVEL_RAG_ENABLED = os.getenv("TRAVEL_RAG_ENABLED", "true").strip().lower() in ("1", "true", "yes")
# 旅游域知识库 id：语料未灌入时检索返回空，risk 专家自动降级为免责声明
TRAVEL_RAG_KB_ID = os.getenv("TRAVEL_RAG_KB_ID", "travel")
TRAVEL_RAG_TOP_K = int(os.getenv("TRAVEL_RAG_TOP_K", "3"))

# =============================================
# 用户偏好持久化（2026-09-22 P1-1）
# =============================================
TRAVEL_PREFS_ENABLED = os.getenv("TRAVEL_PREFS_ENABLED", "true").strip().lower() in ("1", "true", "yes")

# =============================================
# 意图预过滤（Router 内，与 CS 预过滤同层）
# =============================================
# 命中 ≥ 该数量的旅游强信号词才判为旅游域，避免「周末」这类口语误命中
TRAVEL_DETECT_MIN_HITS = int(os.getenv("TRAVEL_DETECT_MIN_HITS", "2"))

# =============================================
# Pending Resume（STOP F2，2026-09-23）
# =============================================
# 旅游域追问后的纯槽位值回答（「8万日元」「住难波」）不含旅游/延续信号词，
# 预过滤与 ContinuationResolver 都接不住。开启后，活跃 travel 任务存在
# 结构化 pending 时，由 TravelPendingResolver 先行判定并短路回旅游域。
# 判定纯规则零 LLM（复用 slot_filler 抽取函数）；客服强信号仍优先放行。
TRAVEL_PENDING_RESUME_ENABLED = os.getenv("TRAVEL_PENDING_RESUME_ENABLED", "true").strip().lower() in ("1", "true", "yes")

# =============================================
# 交易挂起续填（Phase 5 / D2 两跳断修复，2026-09-30）
# =============================================
# 预订（travel_booking）/ 比价（travel_commerce）两子图**无 checkpointer**，
# 澄清期参数不在 PG —— 用户被问「哪天入住？」后答「10月3日」会掉域。
# 开启后，活跃域为交易两域且存在结构化 booking_intent 挂起时，由
# BookingPendingResolver 判定本句是否补上缺失槽位并短路回子图（子图是
# 唯一抽取点，本层只判「是否命中」）。判定纯规则零 LLM，抽取复用
# commerce/extract 的 merge_slot_values（与 prefilter 同源）。
#
# ⚠️ 关闭即退回 D2 两跳断片行为（仅作紧急回滚用，勿长期 off）。
BOOKING_PENDING_RESUME_ENABLED = os.getenv("BOOKING_PENDING_RESUME_ENABLED", "true").strip().lower() in ("1", "true", "yes")
