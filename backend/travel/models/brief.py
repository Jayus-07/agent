"""travel/models/brief.py — 需求契约 TravelBrief

旅游域的「输入契约」：由 slot_filler 负责填充，experts / validator /
reporter 全部只读消费。

设计约束：
  字段为 None / 空 表示「用户没说」，**必须走追问**，不允许专家自行假设。
  猜错日期或人数在旅游域是 P0 级错误（行程整体作废），而多问一句的代价
  只是 +1 轮对话 —— 这是与 RAG「猜不出就拒答」一致的成本取舍。
"""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

# 缺失即追问的槽位（其余槽位缺失时专家按默认值推进，并在 warnings 里标注）
REQUIRED_SLOTS: tuple[str, ...] = ("destination", "days")

# 节奏档位；未识别时落到 moderate
PACE_LEVELS: tuple[str, ...] = ("relaxed", "moderate", "intense")

# 档位 → 用户可读标签。单一事实源：reporter 与专家提示文案都取这里，
# 否则同一份行程里会出现「节奏 适中」与「按 moderate 节奏」两种写法。
PACE_LABELS: dict[str, str] = {
    "relaxed": "轻松",
    "moderate": "适中",
    "intense": "紧凑",
}

# 偏好标签 → 关键词（slot_filler 规则抽取用，也是 poi 专家的筛选语义）
PREFERENCE_KEYWORDS: dict[str, list[str]] = {
    "自然": ["自然", "山水", "爬山", "户外", "海边", "湖", "森林", "公园", "溪"],
    "人文": ["人文", "历史", "古迹", "博物馆", "文化", "寺庙", "古镇", "故居"],
    "美食": ["美食", "小吃", "夜市", "餐厅", "吃", "特色菜"],
    "亲子": ["亲子", "带娃", "孩子", "小孩", "儿童", "溜娃"],
    "购物": ["购物", "逛街", "商场", "买点", "伴手礼"],
    "夜生活": ["夜生活", "夜游", "酒吧", "夜景", "宵夜"],
    "摄影": ["摄影", "拍照", "出片", "打卡", "机位"],
}

# 节奏关键词 → 档位
PACE_KEYWORDS: dict[str, list[str]] = {
    "relaxed": ["轻松", "悠闲", "慢慢", "不赶", "放松", "慢节奏", "佛系", "休闲"],
    "intense": ["紧凑", "深度", "暴走", "尽可能多", "多逛", "高效", "特种兵"],
}

# 槽位追问话术（缺失槽位 → 向用户提的具体问题）
SLOT_QUESTIONS: dict[str, str] = {
    "destination": "去哪个城市（或区域）？",
    "days": "打算玩几天？",
    "start_date": "大概什么时候出发？（不确定可以不说，我按「第 1 天」排）",
    "party_size": "几个人一起？",
    "budget_cny": "有预算范围吗？",
}


class TravelBrief(BaseModel):
    """旅游需求契约

    字段语义（None 一律表示「用户未提供」，不表示默认值）：
      destination:  目的地城市名，需与 POI 数据源的城市键对齐
      origin:       出发地；P0 仅记录，跨城交通在 P2 接入
      start_date:   出发日期；缺失时行程用「第 N 天」表述
      days:         行程天数（>= 1）
      party_size:   同行人数（含本人）
      budget_cny:   总预算（元），为全程合计而非人均
      preferences:  偏好标签，取自 PREFERENCE_KEYWORDS 的键
      must_go:      用户点名必去的 POI 名（修复阶段不得静默删除）
      avoid:        用户明确避开的 POI 名 / 类别
      pace:         节奏档位，取自 PACE_LEVELS
    """

    destination: str = ""
    origin: str = ""
    start_date: date | None = None
    days: int | None = None
    party_size: int = 1
    budget_cny: float | None = None
    preferences: list[str] = Field(default_factory=list)
    must_go: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    pace: str = "moderate"
    # 需求版本号（任务书 §4 三层版本之一）：指纹变化触发重规划时 +1，
    # 承担「这版行程基于哪个需求」的追溯；指纹只做变更检测的快速通道。
    version: int = Field(default=1, ge=1, description="需求版本号，重规划时递增")

    def missing_slots(self) -> list[str]:
        """返回缺失的必填槽位（保持 REQUIRED_SLOTS 的稳定顺序）。

        单一事实源：slot_filler 据此决定是否追问，域图 supervisor 据此
        决定是否跳过规划直接进 reporter —— 两处不得各写一份判断。
        """
        missing: list[str] = []
        if not self.destination.strip():
            missing.append("destination")
        if self.days is None or self.days < 1:
            missing.append("days")
        return missing

    def is_ready(self) -> bool:
        """必填槽位齐备，可进入规划。"""
        return not self.missing_slots()

    def normalized_pace(self) -> str:
        """节奏档位归一到 PACE_LEVELS 内的合法值。"""
        return self.pace if self.pace in PACE_LEVELS else "moderate"

    def pace_label(self) -> str:
        """节奏档位的中文标签（面向用户展示）。"""
        return PACE_LABELS.get(self.normalized_pace(), self.normalized_pace())

    def resolved_days(self) -> int:
        """天数兜底：必填校验已保证非 None，此处仅防护非法值。"""
        return max(1, int(self.days or 1))
