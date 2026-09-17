"""config/selection_funnel.py — 智能选品漏斗域配置

口径与 config/travel.py 完全一致：
  - 所有阈值集中在本文件，域内节点只读这里，不散落魔数
  - 支持 env 覆盖；SELECTION_FUNNEL_ENABLED 默认 false（与 CS/TRAVEL 同策略：
    未验收的域不接线上流量，验收通过后再开）
  - 类目差异化阈值走 SELECTION_FUNNEL_CATEGORY_RULES（JSON，env 可覆盖），
    不同类目的合理竞争度/毛利差很远，绝不允许写死在业务代码里
"""
import json
import os

from dotenv import load_dotenv

load_dotenv()


def _flag(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes")


# =============================================
# 域总开关
# =============================================
SELECTION_FUNNEL_ENABLED = _flag("SELECTION_FUNNEL_ENABLED", "false")

# 图步数护栏（线性漏斗 7 节点，正常 14 图步封顶；25 为 LangGraph 默认下限）
SELECTION_FUNNEL_GRAPH_RECURSION_LIMIT = int(
    os.getenv("SELECTION_FUNNEL_GRAPH_RECURSION_LIMIT", "25"))

# =============================================
# 意图预过滤（Router 内，与 travel_prefilter 同层）
# =============================================
# 命中 ≥ N 个选品强信号词才判为选品域（「选品」1 个词即可，但显式留档）
SELECTION_FUNNEL_DETECT_MIN_HITS = int(
    os.getenv("SELECTION_FUNNEL_DETECT_MIN_HITS", "1"))

# =============================================
# 漏斗层二/三：建池与初筛阈值
# =============================================
# 候选池数据源及优先级（逗号分隔，顺序即优先级）：
#   import    — 批量导入通道（生意参谋/竞品工具导出表格，import_pool.py）
#   watchlist — 竞品监控快照池（兜底；受反爬预算约束量级极小）
# 2026-09-17 用户拍板：监控池只能养 1-2 个对象（GLOBAL_DAILY_BUDGET=40 次/天），
# 撑不起海选语义；导入池为主源，watchlist 降级为兜底补充源。
SELECTION_FUNNEL_POOL_SOURCES = tuple(
    s.strip() for s in os.getenv("SELECTION_FUNNEL_POOL_SOURCES", "import,watchlist").split(",")
    if s.strip()
)
# 口碑线：评分低于此值直接淘汰（快照缺 rating 时保留并记 notes，不静默丢）
SELECTION_FUNNEL_MIN_RATING = float(os.getenv("SELECTION_FUNNEL_MIN_RATING", "4.0"))
# 热度线：评价数低于此值淘汰（缺数据保留 + notes）
SELECTION_FUNNEL_MIN_REVIEWS = int(os.getenv("SELECTION_FUNNEL_MIN_REVIEWS", "10"))
# 候选池大小上限（保护下游评分与 LLM 理由的量级）
SELECTION_FUNNEL_MAX_POOL = int(os.getenv("SELECTION_FUNNEL_MAX_POOL", "200"))

# =============================================
# 漏斗层五：单位经济测算默认参数
# =============================================
# 平台扣点比例（综合各平台常见类目扣点；brief 可覆盖）
SELECTION_FUNNEL_PLATFORM_FEE_RATE = float(
    os.getenv("SELECTION_FUNNEL_PLATFORM_FEE_RATE", "0.055"))
# 单件物流成本（元）
SELECTION_FUNNEL_LOGISTICS_FEE_CNY = float(
    os.getenv("SELECTION_FUNNEL_LOGISTICS_FEE_CNY", "5.0"))
# 推广费占售价比例（直通车/多多搜索常规投放口径）
SELECTION_FUNNEL_ADS_RATIO = float(os.getenv("SELECTION_FUNNEL_ADS_RATIO", "0.15"))
# 进货成本占售价比例的默认估计（无 brief.max_unit_cost 时使用）
SELECTION_FUNNEL_DEFAULT_COST_RATIO = float(
    os.getenv("SELECTION_FUNNEL_DEFAULT_COST_RATIO", "0.45"))
# 毛利率及格线（低于即淘汰；brief.target_margin 可覆盖）
SELECTION_FUNNEL_MIN_MARGIN = float(os.getenv("SELECTION_FUNNEL_MIN_MARGIN", "0.30"))

# =============================================
# 输出
# =============================================
# 最终推荐条数
SELECTION_FUNNEL_TOP_N = int(os.getenv("SELECTION_FUNNEL_TOP_N", "5"))

# =============================================
# 类目差异化阈值（JSON 覆盖，键 = 类目名）
# 例：{"宠物零食": {"min_rating": 4.2, "min_margin": 0.35}}
# 解析失败时 fail-safe 回落默认阈值并记 warning，不让配置错误炸掉域图
# =============================================
_SERules_RAW = os.getenv("SELECTION_FUNNEL_CATEGORY_RULES", "")


def _parse_category_rules(raw: str) -> dict[str, dict]:
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


CATEGORY_RULES: dict[str, dict] = _parse_category_rules(_SERules_RAW)


def rules_for(category: str) -> dict:
    """合并该类目的覆盖阈值（调用方再与本文件默认值逐键 merge）。"""
    if not category:
        return {}
    return dict(CATEGORY_RULES.get(category, {}))
